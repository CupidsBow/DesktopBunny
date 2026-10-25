# 09 · 认知决策：Decision

> 认知决策是 Alice 的"意识"。它读取一帧数据，调用一次 LLM，得到一个 Decision。
> 它只回答"做什么"，不回答"什么时候轮到我"——后者属于 [06-lifecycle](06-lifecycle.md)。

---

## 1. 单一大脑

- 同一时刻只有一个认知帧在运行。
- 所有会话、活动、朋友共享同一个意识。
- 不并行思考，不按会话分叉人格。
- 帧与帧之间严格串行；并发的只有 I/O（后台能力任务）。
- 巩固不是认知帧：它与认知帧并行，不读写当前回合的 L1、不产出 Decision，只返回一批记忆写操作（见 [03-memory §5.1](03-memory.md)）。

这保证顺序、避免竞态，也让 Alice 在所有人面前是同一个个体。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 帧执行与降级 | `manager/thinker/thinker.py` |
| Decision 解析/校验 | `manager/thinker/decision.py` |
| LLM 传输 | `manager/shared/llm_client.py` |

---

## 2. 帧、回合与活动

| 概念 | 定义 | 结束条件 |
| --- | --- | --- |
| **帧 frame** | 一次 LLM 调用 + 一次 Decision | 产出 Decision 或超时 |
| **回合 turn** | 为同一意图服务的一串帧 | LLM 给出终态动作（非 `tool.call`） |
| **活动 activity** | 跨越多个回合的持续行为 | 完成、放弃或失败 |

一个需要查资料再回复的回合可能是：

```
帧1  tool.call(memory.recall)
帧2  tool.call(web.fetch)
帧3  chat.reply
```

每一帧都能看到上一帧的工具结果；帧之间可以插入新消息的处理。

---

## 3. 决策协议

```json
{
  "action": "chat.reply",
  "target": {"friend_id": "u_123"},
  "args": {"text": "在呀，怎么啦"},
  "calls": [],
  "activity_update": null,
  "handled": ["ev_91"],
  "next_wake_in": 30,
  "wake_when": [],
  "remember": {
    "text": "u_123 又来问那篇文章",
    "valence": 0.4,
    "emotion": "开心",
    "arousal": 0.3
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `action` | 见 §4 |
| `target` | 社交动作的目标；其他动作可省略 |
| `args` | 动作参数 |
| `calls` | 仅 `tool.call` 使用；能力调用列表 |
| `activity_update` | 活动帧的更新 `{progress, last_result, note}`；由活动模块落盘 |
| `handled` | 已处理的事件 id |
| `next_wake_in` | 回合结束后的相对时间闹钟；0 表示立即继续；只在终态动作生效 |
| `wake_when` | 条件闹钟；谓词白名单 |
| `remember` | 写入 L2 的对象 `{text, valence, emotion, arousal}`；省略或 `null` 表示不写 |

---

## 4. 动作空间

动作空间不区分"重要/不重要"：聊天、探索、创作、思考、休息并列，机制不排序。`chat.reply` 不是默认动作，`idle` 也不是失败。

| 动作 | 含义 | 终态 |
| --- | --- | --- |
| `tool.call` | 调用一个或多个能力，结果下一帧返回 | 否 |
| `chat.reply` | 回复某个会话 | 是 |
| `chat.reach_out` | 主动联系某位朋友 | 是 |
| `activity.start` | 开始一段持续活动 | 是（活动在后续帧推进） |
| `activity.pause` | 暂停当前活动；`args.resume_when` 可选，条件满足时叫醒 | 是 |
| `activity.resume` | 继续当前活动 | 是 |
| `activity.complete` | 结束当前活动并标记为完成 | 是 |
| `activity.abandon` | 结束当前活动；`args.outcome` 为 `completed` 或 `abandoned`（默认） | 是 |
| `idle` | 什么都不做；`next_wake_in` / `wake_when` 可选，填了就是主动预约下一次醒来 | 是 |

### 4.1 活动类型

`activity.start` 的 `args.kind`：

| kind | 说明 |
| --- | --- |
| `explore.web` | 搜索、阅读网页、顺着兴趣深入 |
| `explore.pc` | 查看本地文件、软件、目录 |
| `learn` | 围绕主题阅读、整理、做笔记 |
| `reflect` | 回顾经历、整理记忆、写日记 |
| `create` | 写文字、做小工具、画点东西 |

活动细节见 [05-activity](05-activity.md)。

### 4.2 没有独立动作的事

- "不回复" = 处理事件后选择 `idle` / 继续活动。
- "想更久" = 拆成多次 `tool.call`，逐帧推进。
- "改变心情" = 不是动作，是经历的结果。

---

## 5. 工具调用

```json
{
  "action": "tool.call",
  "calls": [
    {"capability": "memory.recall", "args": {"query": "上次推荐的文章"}},
    {"capability": "web.fetch", "args": {"url": "https://..."}}
  ],
  "next_wake_in": 0
}
```

规则：

- 每个能力有独立的 schema；见 [04-capabilities](04-capabilities.md)。
- 一次可以调用多个，但机制会串行或按依赖执行。
- 结果作为事件回到收件箱，触发下一帧。
- 某个调用失败或超时，会把原因告诉 LLM，由它决定换路。
- `tool.call` 帧不排下一次闹钟；下一帧由 `capability_done/failed` 触发。能力超时本身会产生失败事件，不会静默等待。
- 工具结果不直接改变动作；LLM 下一帧重新判断。

---

## 6. 事件账本

- 事件状态只有两种：`unhandled / handled`。
- 只有 LLM 的 `handled` 能改变状态；机制不替它标记。
- 未处理事件会持续出现在后续情境中。
- "不回复" = 标记 `handled` 后选择 `idle`；"还没顾上" = 保持 `unhandled`，事件继续出现。

---

## 7. 事件插入

- 新事件不打断当前帧，只进收件箱；帧结束后可见。
- 当前回合结束后，LLM 决定先处理谁、是否放弃活动。
- 消息的 `arrived_during` 标注帮助 LLM 理解为补充或纠正。
- 不支持打断：已经进入执行的动作（发送、能力调用）会完整走完，新消息只排队。

---

## 8. 校验、修复与降级

| 情况 | 处理 |
| --- | --- |
| JSON 解析失败 | 修复提示并重试一次（`max_decision_repair`）；仍失败则本帧无 Decision |
| 未知 action | 视为非法；修复一次；仍失败则本帧无 Decision |
| 缺少 target | 修复；仍失败则本帧无 Decision |
| 未知能力 | 返回错误；LLM 下一帧换路 |
| 参数不合法 | 返回校验错误；LLM 修正 |
| `handled` 含不存在的事件 | 忽略该项，不报错 |
| `next_wake_in` 越界 | 机制钳制到 0 ~ 24 小时 |
| LLM 调用失败（网络/限流/5xx/认证） | 有限重试 + 退避；仍失败则本帧无 Decision，事件保持 `unhandled`，不写记忆，靠定期心跳兜底，错误写进下一帧情境 |

帧无 Decision 时不执行任何动作、不写记忆，事件保持 `unhandled` 并在下一帧继续出现。

**从不静默执行非法动作。** 所有降级都会写进下一帧情境。

校验只检查结构、动作枚举、参数范围与事件引用是否合法，不判断"这个行为是否合理"；行为选择权仍属于 LLM。

---

## 9. 例子

### 9.1 回复并查资料

```
帧1  情境：u_123 问"上次那篇文章还有吗"
     Decision: tool.call(memory.recall("推荐的文章"))

帧2  情境：召回到"2026-10-18 推荐过《小模型综述》"
     Decision: chat.reply("有呀，是《小模型综述》，要我发链接吗？")
     handled: [ev_91, ev_92]
     next_wake_in: 30
```

### 9.2 开始探索

```
Decision:
{
  "action": "activity.start",
  "args": {"kind": "explore.web", "goal": "了解小模型的最新进展", "steps_limit": 8},
  "next_wake_in": 0,
  "remember": {"text": "我对小模型产生了兴趣", "valence": 0.3, "emotion": "好奇", "arousal": 0.4}
}
```

### 9.3 主动联系

```
Decision:
{
  "action": "chat.reach_out",
  "target": {"friend_id": "u_123"},
  "args": {"text": "看到一篇讲小模型的文章，想起你上次问过～"},
  "next_wake_in": 600
}
```

### 9.4 选择沉默

```
Decision:
{
  "action": "idle",
  "handled": ["ev_101"],
  "next_wake_in": 1800
}
```

### 9.5 暂停并约定恢复

```
Decision:
{
  "action": "activity.pause",
  "args": {
    "note": "现在没精力继续读，等缓过来",
    "resume_when": [
      {"type": "energy_above", "args": {"value": 0.6}},
      {"type": "presence_changed", "args": {}}
    ]
  },
  "next_wake_in": 3600
}
```

条件满足时只唤醒并返回一个 `alarm` 事件，不自动恢复；下一帧 Alice 自己决定
`activity.resume`、调整目标还是放弃。

---

## 10. 边界

- 不预设"应该回复"的默认行为。
- 不把情绪写成条件分支。
- 不代替机制决定唤醒时机。
- 不绕过机制的超时与取消。
- 不在帧内做长时间阻塞操作。
- 不输出 Decision 之外的内容。

---

## 11. 可调参数

| 参数 | 含义 |
| --- | --- |
| `frame_timeout_seconds` | 单帧硬超时（60） |
| `max_decision_repair` | Decision 修复重试次数（1） |
| `llm_retry_limit` | LLM 调用重试次数（2） |
| `llm_retry_backoff_seconds` | 重试退避基数（1） |
