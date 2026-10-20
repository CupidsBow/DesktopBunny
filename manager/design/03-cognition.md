# 03 · 认知：情境、决策与动作空间

> 认知层是 Alice 的"意识"。它读取一份结构化情境，调用一次 LLM，得到一个 Decision。
> 它只回答"做什么"，不回答"什么时候轮到我"——后者属于 [07-mechanism](07-mechanism.md)。

---

## 1. 单一大脑

- 同一时刻只有一个认知帧在运行。
- 所有会话、活动、朋友共享同一个意识。
- 不并行思考，不按会话分叉人格。
- 帧与帧之间严格串行；并发的只有 I/O（后台能力任务）。

这保证顺序、避免竞态，也让 Alice 在所有人面前是同一个个体。

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

## 3. 情境装配

每帧交给 LLM 的是一份结构化报告，而不是单一消息。

```json
{
  "now": "2026-10-20T21:03:11+08:00",
  "wake_reason": {"kind": "event", "detail": "message from u_123"},

  "self": {
    "identity": "L0 核心",
    "mood": 0.4,
    "mood_label": "轻快",
    "urges": {"饥饿": 0.7, "孤独": 0.2, "睡意": 0.1}
  },

  "body": {
    "satiety": 62, "energy": 0.71, "arousal": 0.35,
    "social_battery": 0.6, "circadian": 0.8
  },

  "environment": {
    "someone_present": true,
    "idle_seconds": 42,
    "foreground_app": "code",
    "at_night": false
  },

  "activity": {
    "id": "act_17",
    "kind": "explore.web",
    "goal": "了解一下最近的开源模型",
    "progress": 0.4,
    "steps_used": 3,
    "steps_left": 7,
    "last_result": "读到一篇关于小模型的综述"
  },

  "turn": {
    "intent": "回复 u_123 关于文章的询问",
    "frames_used": 1,
    "pending_tool_results": []
  },

  "inbox": [
    {"id": "ev_91", "kind": "message", "friend_id": "u_123",
     "conversation_id": "g_456", "text": "在吗",
     "arrived_during": {"type": "activity", "id": "act_17"},
     "status": "unhandled"},
    {"id": "ev_92", "kind": "message", "friend_id": "u_123",
     "conversation_id": "g_456", "text": "上次那篇文章还有吗",
     "status": "unhandled"}
  ],

  "memories": {
    "self": "……self.md 摘要……",
    "about_speakers": ["……朋友档案摘要……"],
    "relevant_episodes": ["……最多 5 条……"]
  },

  "capabilities": ["web.search", "web.fetch",
                   "fs.read", "fs.list", "memory.recall",
                   "memory.write", "pc.observe", "time.now"],

  "constraints": {
    "budget_remaining": 0.73,
    "permissions": {"fs_roots": ["..."], "network": "fetch-only"},
    "max_idle_seconds": 1800,
    "frame_timeout_seconds": 60
  }
}
```

装配原则：

- 只给摘要，避免上下文膨胀；LLM 可用能力展开。
- 自动召回是感知增强，不是行为选择；召回为空也如实标注。
- 所有未处理事件都出现，不按重要性过滤。
- `wake_reason` 只陈述事实，不写建议。

---

## 4. 提示词组装

一次 LLM 调用的 messages 由五部分组成：

1. **L0 核心**：身份、气质、边界。
2. **情境报告**：上面的 JSON。
3. **能力清单与 schema**：可调用什么、参数是什么、权限范围。
4. **约束**：预算、超时、权限、允许的动作枚举。
5. **决策指令**：输出格式与行为准则。

决策指令要点：

```
你是 Alice。基于情境自行决定下一步做什么。
- 所有行为选择都由你做出；机制不会替你行动。
- 你可以选择回应、沉默、等待、探索、学习、休息或做身体动作。
- 你可以调用能力获取更多信息；结果会在下一帧返回。
- 标记 handled 的事件表示你已经处理；未标记的会继续出现。
- 只输出一个 JSON Decision，不要输出其他内容。
```

提示词里不写具体台词、不写"心情差就不回"的规则。

---

## 5. 决策协议

```json
{
  "thought": "简短内心独白（可选）",
  "action": "chat.reply",
  "target": {"friend_id": "u_123", "conversation_id": "g_456"},
  "args": {"text": "在呀，怎么啦"},
  "calls": [],
  "handled": ["ev_91"],
  "body": {"gesture": "look", "emotion": "好奇"},
  "next_wake_in": 30,
  "wake_when": [],
  "remember": "u_123 又来问那篇文章"
}
```

| 字段 | 说明 |
| --- | --- |
| `thought` | 可选；可进入记忆，用于调试与自我叙事 |
| `action` | 见 §6 |
| `target` | 社交动作的目标；其他动作可省略 |
| `args` | 动作参数 |
| `calls` | 仅 `tool.call` 使用；能力调用列表 |
| `handled` | 已处理的事件 id |
| `body` | 附带的身体动作/表情 |
| `next_wake_in` | 回合结束后的时间闹钟；0 表示立即继续 |
| `wake_when` | 条件闹钟；谓词白名单 |
| `remember` | 值得写入 L2 的一句话；空表示不写 |

---

## 6. 动作空间

| 动作 | 含义 | 终态 |
| --- | --- | --- |
| `tool.call` | 调用一个或多个能力，结果下一帧返回 | 否 |
| `chat.reply` | 回复某个会话 | 是 |
| `chat.reach_out` | 主动联系某位朋友 | 是 |
| `activity.start` | 开始一段持续活动 | 是（活动在后续帧推进） |
| `activity.pause` | 暂停当前活动 | 是 |
| `activity.resume` | 继续当前活动 | 是 |
| `activity.abandon` | 放弃当前活动 | 是 |
| `body.do` | 身体动作，不发言 | 是 |
| `rest` | 睡觉或低活动休息 | 是 |
| `wait` | 什么都不做，设定下次醒来 | 是 |
| `idle` | 什么都不做，不设定额外闹钟 | 是 |

### 6.1 活动类型

`activity.start` 的 `args.kind`：

| kind | 说明 |
| --- | --- |
| `explore.web` | 搜索、阅读网页、顺着兴趣深入 |
| `explore.pc` | 在权限范围内查看本地文件、软件、目录 |
| `learn` | 围绕主题阅读、整理、做笔记 |
| `reflect` | 回顾经历、整理记忆、写日记 |
| `create` | 写文字、做小工具、画点东西 |

活动细节见 [04-activities](04-activities.md)。

### 6.2 没有独立动作的事

- "不回复" = 处理事件后选择 `idle` / `wait` / 继续活动。
- "想更久" = 在当前帧使用更多 token，或拆成多次 `tool.call`。
- "改变心情" = 不是动作，是经历的结果。

---

## 7. 工具调用

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

- 每个能力有独立的 schema 与权限；见 [05-capabilities](05-capabilities.md)。
- 一次可以调用多个，但机制会串行或按依赖执行。
- 结果作为事件回到收件箱，触发下一帧。
- 某个调用被拒绝/超时，会把原因告诉 LLM，由它决定换路。
- 工具结果不直接改变动作；LLM 下一帧重新判断。

---

## 8. 事件账本

- 事件状态：`unhandled / handled / deferred`。
- 只有 LLM 的 `handled` 能改变状态；机制不替它标记。
- 未处理事件会持续出现在后续情境中。
- `deferred` 需要 LLM 显式给出；它表示"我看到了，但现在不做"。
- 这保证"不回复"和"还没顾上"是两件不同的事。

---

## 9. 中断

- 新事件不打断当前帧，只进收件箱；帧结束后可见。
- 当前回合结束后，LLM 决定先处理谁、是否放弃活动。
- 明确的"停 / 别发了 / 听我说"由机制中止在途能力调用与未发分片；之后仍由 LLM 决定如何回应。
- 消息的 `arrived_during` 标注帮助 LLM 理解为补充或纠正。

---

## 10. 校验、修复与降级

| 情况 | 处理 |
| --- | --- |
| JSON 解析失败 | 一次修复提示；仍失败则降级为 `wait` |
| 未知 action | 视为非法；修复一次；仍失败则 `wait` |
| 缺少 target | 修复；无法修复则 `wait` 并保留事件 |
| 能力不在白名单 | 拒绝并返回原因；LLM 下一帧换路 |
| 参数不合法 | 返回校验错误；LLM 修正 |
| `handled` 含不存在的事件 | 忽略该项，不报错 |
| `next_wake_in` 越界 | 机制钳制到上下限 |
| 帧超时 | 取消本次调用，降级为 `wait`，记录日志 |

**从不静默执行非法动作。** 所有降级都会写进下一帧情境。

---

## 11. 流式（可选）

- 长回复可以边说边发：第一句先送出，后续继续生成。
- 流式属于执行层能力，不改变决策归属。
- 被打断时保留已发送部分，把未发送内容写入下一帧情境。

---

## 12. 例子

### 12.1 回复并查资料

```
帧1  情境：u_123 问"上次那篇文章还有吗"
     Decision: tool.call(memory.recall("推荐的文章"))

帧2  情境：召回到"2026-10-18 推荐过《小模型综述》"
     Decision: chat.reply("有呀，是《小模型综述》，要我发链接吗？")
     handled: [ev_91, ev_92]
     next_wake_in: 30
```

### 12.2 开始探索

```
Decision:
{
  "thought": "最近总看到小模型的讨论，想弄明白",
  "action": "activity.start",
  "args": {"kind": "explore.web", "goal": "了解小模型的最新进展", "steps_limit": 8},
  "next_wake_in": 0,
  "remember": "我对小模型产生了兴趣"
}
```

### 12.3 主动联系

```
Decision:
{
  "thought": "刚才看到的东西，u_123 应该会喜欢",
  "action": "chat.reach_out",
  "target": {"friend_id": "u_123", "conversation_id": "c_123"},
  "args": {"text": "看到一篇讲小模型的文章，想起你上次问过～"},
  "next_wake_in": 600
}
```

### 12.4 选择沉默

```
Decision:
{
  "thought": "只是句晚安，不必回复",
  "action": "wait",
  "handled": ["ev_101"],
  "next_wake_in": 1800
}
```

---

## 13. 边界

- 不预设"应该回复"的默认行为。
- 不把情绪写成条件分支。
- 不代替机制决定唤醒时机。
- 不越过权限调用能力。
- 不在帧内做长时间阻塞操作。
- 不输出 Decision 之外的内容。

---

## 14. 可调参数

| 参数 | 含义 |
| --- | --- |
| `FRAME_TIMEOUT_SECONDS` | 单帧硬超时 |
| `MAX_TOOL_ROUNDS_PER_TURN` | 单回合工具帧上限 |
| `MAX_DECISION_REPAIR` | Decision 修复尝试次数 |
| `LLM_MAX_TOKENS` | 单帧输出上限 |
| `CALL_BUDGET_PER_HOUR` | 认知帧频率上限（机制执行） |
