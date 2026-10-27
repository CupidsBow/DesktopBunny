# 05 · 持续活动与主动性

> 聊天只是 Alice 的行为之一。她还上网、探索这台电脑、学习、整理记忆、写东西、发呆、睡觉。
> 活动是"跨越多个回合的持续行为"，由 LLM 自己开始、推进、暂停和放弃。

---

## 1. 为什么需要活动

一次聊天是一个短回合，一次探索却可能持续几十分钟到几小时。它需要：

- 有目标与进度，不是一次工具调用。
- 能暂停、恢复、跨重启。
- 能在步骤之间处理朋友消息。
- 能把发现沉淀成记忆，并在之后分享。

活动不是机制给 Alice 安排的任务，而是她可以用 `activity.start` 选择的**行为**。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 生命周期、检查点 | `manager/activity/activity.py` |
| `activities.json` 持久化 | `manager/activity/store.py` |

---

## 2. 活动模型

```python
@dataclass
class Activity:
    id: str
    kind: str              # explore.web / explore.pc / learn / reflect / create
    goal: str              # LLM 写下的目标
    state: dict            # 进度、已读内容摘要、下一步计划
    created_at: float
    updated_at: float
    steps_used: int
    steps_limit: int       # LLM 自设的计划步数；到达时触发检查点，不强制停止
    status: str            # active / paused / completed / abandoned / failed
    checkpoint: dict       # 恢复所需的最小状态
    last_result: str       # 最近一步的产出摘要
    resume_when: list      # 暂停时约定的恢复条件；只在一段暂停里有意义
```

- `goal` 与 `state` 由 LLM 维护；机制只做持久化。
- `checkpoint` 是可恢复的最小上下文，不保存完整网页或文件原文。
- 活动与朋友、会话无关：它属于 Alice 自己。

---

## 3. 生命周期

```
activity.start
      │
      ▼
   ┌────────┐  步骤完成/后台结果   ┌────────────┐
   │ active │────────────────────▶│ 下一帧决策  │
   └───┬────┘                      └─────┬──────┘
       │                                 │
       │ activity.pause                  │ activity.resume
       ▼                                 │
   ┌────────┐ ──────────────────────────┘
   │ paused │
   └───┬────┘
       │ activity.complete / activity.abandon / 失败
       ▼
 completed / abandoned / failed
```

### 3.1 开始

```json
{
  "action": "activity.start",
  "args": {
    "kind": "explore.web",
    "goal": "了解小模型的最新进展",
    "steps_limit": 8,
    "note": "先找综述，再看两三个具体项目"
  },
  "next_wake_in": 0
}
```

### 3.2 推进

- 每个活动步骤至少是一帧；如果一步需要多次工具调用，它就是一个回合（多帧）。
- 帧里可以调用能力；能力结果作为事件返回，触发下一帧。
- 活动帧可以带 `activity_update: {progress, last_result, note}`，由活动模块落盘。
- `steps_used` 由机制在每次活动帧自增；达到 `steps_limit` 时发出 `activity_checkpoint`，它不是停止条件，LLM 决定继续、收尾还是放弃。
- 机制在步骤完成或检查点间隔到达时也会发出 `activity_checkpoint` 事件：

```json
{"kind": "activity_checkpoint",
 "payload": {"activity_id": "act_17", "kind": "explore.web",
             "progress": 0.4, "steps_used": 3, "reason": "step_completed"}}
```

- LLM 每步自己决定：继续、换方向、暂停、放弃。
- 步骤之间意识是自由的：新消息可以插入处理。

### 3.3 暂停与恢复

- `activity.pause`：保存当前 checkpoint，活动进入 `paused`。
- `activity.resume`：恢复活动，下一帧带上 `state` 与 `last_result`。
- 暂停不丢失已完成的工作；恢复不需要重读全部内容。
- 机制不自动暂停；是否暂停是 LLM 的选择。

#### 3.3.1 `resume_when`：暂停时约好回来的条件

`activity.pause` 的 `args` 可以带 `resume_when`，语义与 `wake_when` 相同：到条件时把 Alice 叫醒。

```json
{
  "action": "activity.pause",
  "args": {
    "note": "先歇一会儿，精力和人都回来再继续",
    "resume_when": [
      {"type": "energy_above", "args": {"value": 0.6}},
      {"type": "presence_changed", "args": {}}
    ]
  },
  "next_wake_in": 1800
}
```

规则：

- 条件保存在 `Activity.resume_when`，随 `activities.json` 落盘，跨重启保留。
- 只在 `paused` 状态求值；活动恢复、完成或放弃后清空。
- 多个谓词之间是 **OR**：任一满足即触发。`wake_when` 同样如此。
- 满足时机制产生一个 `alarm` 事件（payload 带 `activity_id` 与 `source: "activity_resume"`）并唤醒，**不会自动把状态改成 `active`**；是否 `activity.resume` 仍由 LLM 决定。
- 条件触发一次后失效；需要重新约定时在下次 `activity.pause` 里再写。
- 阈值类条件以暂停时的取值为基线，只在之后从不满足变为满足时触发；暂停当刻已经满足的条件被忽略，避免刚暂停就被自己叫醒。`at_time` 例外，到点即触发（已过则取下一次）。
- `next_wake_in` 与 `resume_when` 互不替代：前者是时间兜底，后者是「条件到了叫我」。

谓词白名单：

| 谓词 | `args` | 说明 |
| --- | --- | --- |
| `satiety_above` | `{"value": 0~100}` | 饱食度首次高于阈值 |
| `energy_above` | `{"value": 0~1}` | 精力首次高于阈值 |
| `presence_changed` | `{}` | 在场布尔值边沿 |
| `at_time` | `{"time": "HH:MM"}` 或 ISO8601 | 到点触发 |

`resume_when` 只用于 `activity.pause`；其他动作携带它会被判定为非法 Decision。

### 3.4 结束

| 状态 | 触发 |
| --- | --- |
| `completed` | `activity.complete`，或 `activity.abandon` 带 `outcome: completed` |
| `abandoned` | `activity.abandon`（默认 `outcome: abandoned`） |
| `failed` | 连续失败 |

结束时把活动摘要写入 L2 情景记忆；有价值的结论进入 L3。

---

## 4. 活动类型

| kind | 目标 | 典型能力 |
| --- | --- | --- |
| `explore.web` | 搜索、阅读、顺着兴趣深入 | `web.search`, `web.fetch` |
| `explore.pc` | 了解本地文件、项目、软件、目录结构 | `fs.list`, `fs.read`, `fs.search`, `pc.observe` |
| `learn` | 围绕主题阅读、整理、做笔记 | `web.*`, `fs.*`, `artifact.read` |
| `reflect` | 回顾经历、整理记忆、写日记 | `artifact.read`, `fs.write`（召回与语义记忆已在情境里） |
| `create` | 写文字、做小工具、画点东西 | `fs.write`, `sandbox.run` |

活动类型不是封闭枚举；新增类型只需要新的 kind 与能力组合。

---

## 5. 后台任务

耗时的能力调用由机制放到后台线程执行，完成或失败后作为事件回到收件箱。

```
帧 N   LLM 选择 tool.call(web.fetch)
          │
          ▼
       机制派发后台任务
          │
       帧 N 结束（意识空闲）
          │
          ├── 朋友消息到达 → 可以立即开新帧处理
          │
          ▼
       任务完成 → capability_done 事件 → 触发帧 N+1
```

原则：

- 认知帧本身不做长阻塞。
- 后台任务有超时与取消。
- 任务结果只作为事件，不直接改变活动状态。
- LLM 在下一帧看到结果后决定下一步。

---

## 6. 主动性

主动性不是"需求越阈值就发消息"，而是 Alice 在某个唤醒帧里，基于记忆与当下状态，自己选择 `chat.reach_out`。

### 6.1 常见来源

| 来源 | 例子 |
| --- | --- |
| 欲望 | 饥饿、孤独、想念、分享、日常问候 |
| 记忆 | 想起某位朋友最近提过的事 |
| 活动结果 | 发现一篇对方会喜欢的文章 |
| 环境 | 有人回到电脑前，想打个招呼 |

这些都只是情境里的信息。是否联系、联系谁、说什么，由 LLM 决定。

### 6.2 防打扰

- 对方不在活跃会话中，不代表不能联系；Alice 自己判断是否合适。
- 连续主动后没有回应，记忆会记录这种经历，影响之后的判断。
- 机制不设"每小时最多一次"这类行为规则；联系与否完全由 Alice 判断。

### 6.3 分享闭环

```
探索 → 发现 → 写入 L2 → 巩固进 L3 → 之后某帧想起 → chat.reach_out
```

主动性的质量来自"真的发现了什么"，而不是定时器。

---

## 7. 与消息的关系

| 情况 | 行为 |
| --- | --- |
| 活动进行中收到消息 | 事件入收件箱；当前帧结束后可见 |
| 消息需要立即处理 | LLM 选择暂停活动，先处理消息 |
| 消息只是寒暄 | LLM 可选择继续活动，稍后统一处理 |
| 消息是纠正 | `arrived_during` 帮助理解；机制不支持打断，当前动作会完整执行 |

活动不会因为普通消息被强制中断。是否暂停是 LLM 的决定。

---

## 8. 持久化与恢复

- 活动记录落盘到 `activities.json`。
- 进程重启后，`active` 恢复为 `paused`，`paused` 保持 `paused`；未触发的 `resume_when` 一并保留；机制不替 Alice 继续推进。
- 第一帧情境标注“已恢复的活动”，由 LLM 决定 `activity.resume`、调整或放弃。
- 重启时在途的后台任务标记为 `unknown`；LLM 决定重试还是放弃。
- checkpoint 不保存敏感原文；只保存摘要与指针。

---

## 9. 约束

| 约束 | 说明 |
| --- | --- |
| 系统访问 | 不设权限层，见 [04-capabilities](04-capabilities.md) |
| 同时活动数 | `max_concurrent_activities = 1`；已有 `active` 时 `activity.start` 返回错误，LLM 需先暂停或放弃旧的 |

---

## 10. 失败处理

| 情况 | 处理 |
| --- | --- |
| 能力超时 | 事件返回失败；LLM 决定重试/换路/放弃 |
| 连续失败 | 活动标记 `failed`，摘要写进记忆 |
| 结果无关 | LLM 可以调整 goal 或放弃 |

---

## 11. 一天的样子

```
08:10  醒来，看时间与天气页，记下今天想看的东西
08:40  读一篇技术文章，发现一个有趣的点，记进记忆
09:05  朋友发消息问问题 → 回复 → 继续读
09:30  读完 → 决定把有趣的点分享给昨天聊过这个话题的朋友
11:00  电脑前没人 → 自己逛了逛本地项目目录
12:30  有点无聊 → reflect：整理记忆、写几句日记
14:00  有人回到电脑前 → 抬头、跳一下，但不说话
15:20  朋友连发三条消息 → 一次看完，按顺序理解，回复
18:00  精力下降 → 趴下休息
23:40  夜深 → 进入休息；有消息才醒，否则最多 2 小时睁眼一次
```

---

## 12. 边界

- 活动是 LLM 的选择，不是机制的排程。
- 活动同样受超时与取消约束。
- 活动不直接产生消息；分享是另一个 Decision。
- 活动失败是正常结果，不是系统错误。

---

## 13. 可调参数

| 参数 | 含义 |
| --- | --- |
| `max_concurrent_activities` | 同时活动数（1） |
| `activity_checkpoint_interval` | 检查点最小间隔 |
| `activity_max_consecutive_failures` | 连续失败多少次标记 `failed` |
| `activities_path` | 活动持久化路径 |
