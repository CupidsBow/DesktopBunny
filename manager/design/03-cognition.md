# 03 · 认知：情境、决策与动作空间

> 认知层是 Alice 的"意识"。它读取一份结构化情境，调用一次 LLM，得到一个 Decision。
> 它只回答"做什么"，不回答"什么时候轮到我"——后者属于 [07-mechanism](07-mechanism.md)。

---

## 1. 单一大脑

- 同一时刻只有一个认知帧在运行。
- 所有会话、活动、朋友共享同一个意识。
- 不并行思考，不按会话分叉人格。
- 帧与帧之间严格串行；并发的只有 I/O（后台能力任务）。
- 巩固不是认知帧：它与认知帧并行，不读写当前回合的 L1、不产出 Decision，只返回一批记忆写操作（见 [02-memory §5.1](02-memory.md)）。

这保证顺序、避免竞态，也让 Alice 在所有人面前是同一个个体。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 帧执行与降级 | `manager/cognition/cognition.py` |
| 情境装配 | `manager/cognition/situation.py` |
| prompt 组装 | `manager/cognition/prompt.py` |
| Decision 解析/校验 | `manager/cognition/decision.py` |
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

## 3. 情境装配

每帧交给 LLM 的是一份结构化报告，而不是单一消息。

```json
{
  "now": "2026-10-20T21:03:11+08:00",
  "wake_reasons": [
    {"kind": "event", "detail": "message from u_123"}
  ],

  "self": {
    "identity": "L0 核心",
    "mood": 0.4,
    "mood_label": "轻快",
    "urges": {"饥饿": 0.7, "孤独": 0.2, "睡意": 0.1}
  },

  "body": {
    "satiety": 62, "energy": 0.71, "alertness": 0.35,
    "social_battery": 0.6, "circadian": 0.8,
    "presence": true, "mode": "awake"
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
     "conversation_id": "c_123", "text": "在吗",
     "arrived_during": {"type": "activity", "id": "act_17"},
     "status": "unhandled"},
    {"id": "ev_92", "kind": "message", "friend_id": "u_123",
     "conversation_id": "c_123", "text": "上次那篇文章还有吗",
     "status": "unhandled"}
  ],

  "memories": {
    "self": "……self.md 摘要……",
    "goals": "……goals.md 摘要……",
    "about_speaker": "……当前说话人的朋友档案摘要……",
    "index": ["……MEMORY.md 的一行（带 valence/arousal）……"]
  },

  "capabilities": ["web.search", "web.fetch",
                   "fs.read", "fs.list", "memory.recall",
                   "memory.write", "pc.observe", "time.now"],

  "delivery": {
    "last_ok": true,
    "detail": null
  },

  "constraints": {
    "max_idle_seconds": 1800,
    "rest_max_idle_seconds": 7200,
    "next_wake_max_seconds": 86400,
    "frame_timeout_seconds": 60
  }
}
```

装配原则：

- 只给摘要，避免上下文膨胀；LLM 可用能力展开。
- 相关情景不由机制挑选：只给 `MEMORY.md` 的索引行，由 LLM 判断哪些相关，再用 `memory.recall` 展开；索引为空也如实标注。
- 所有未处理事件都出现，不按重要性过滤。
- `wake_reasons` 只陈述事实，不写建议；单原因时数组长度为 1。
- `delivery` 只陈述上一条出站消息是否送达；不写建议，也不替 LLM 决定要不要重发。

---

## 4. 提示词组装

一次 LLM 调用的 messages 由五部分组成：

1. **L0 核心**：身份、气质、边界。
2. **情境报告**：上面的 JSON。
3. **能力清单与 schema**：可调用什么、参数是什么、返回格式。
4. **约束**：超时、允许的动作枚举。
5. **决策指令**：输出格式与行为准则。

决策指令要点：

```
你是 Alice。基于情境自行决定下一步做什么。
- 所有行为选择都由你做出；机制不会替你行动。
- 你可以选择回应、沉默、探索、学习或休息。
- 你可以调用能力获取更多信息；结果会在下一帧返回。
- 标记 handled 的事件表示你已经处理；未标记的会继续出现。
- 记忆索引逐行列出可召回条目；需要细节时用 memory.recall 展开。
- 只输出一个 JSON Decision，不要输出其他内容。
```

提示词里不写具体台词、不写"心情差就不回"的规则。

---

## 5. 决策协议

```json
{
  "action": "chat.reply",
  "target": {"friend_id": "u_123", "conversation_id": "c_123"},
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
| `action` | 见 §6 |
| `target` | 社交动作的目标；其他动作可省略 |
| `args` | 动作参数 |
| `calls` | 仅 `tool.call` 使用；能力调用列表 |
| `activity_update` | 活动帧的更新 `{progress, last_result, note}`；由活动模块落盘 |
| `handled` | 已处理的事件 id |
| `next_wake_in` | 回合结束后的时间闹钟；0 表示立即继续 |
| `wake_when` | 条件闹钟；谓词白名单 |
| `remember` | 写入 L2 的对象 `{text, valence, emotion, arousal}`；省略或 `null` 表示不写 |

---

## 6. 动作空间

动作空间不区分“重要/不重要”：聊天、探索、创作、思考、休息并列，机制不排序。`chat.reply` 不是默认动作，`idle` 也不是失败。

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
| `eat` | 自己吃点东西；`args.food` 可选，饱食度由桌宠身体更新 | 是 |
| `rest` | 睡觉或低活动休息 | 是 |
| `idle` | 什么都不做；`next_wake_in` / `wake_when` 可选，填了就是主动预约下一次醒来 | 是 |

### 6.1 活动类型

`activity.start` 的 `args.kind`：

| kind | 说明 |
| --- | --- |
| `explore.web` | 搜索、阅读网页、顺着兴趣深入 |
| `explore.pc` | 查看本地文件、软件、目录 |
| `learn` | 围绕主题阅读、整理、做笔记 |
| `reflect` | 回顾经历、整理记忆、写日记 |
| `create` | 写文字、做小工具、画点东西 |

活动细节见 [04-activities](04-activities.md)。

### 6.2 没有独立动作的事

- "不回复" = 处理事件后选择 `idle` / 继续活动。
- "想更久" = 拆成多次 `tool.call`，逐帧推进。
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

- 每个能力有独立的 schema；见 [05-capabilities](05-capabilities.md)。
- 一次可以调用多个，但机制会串行或按依赖执行。
- 结果作为事件回到收件箱，触发下一帧。
- 某个调用失败或超时，会把原因告诉 LLM，由它决定换路。
- `tool.call` 帧不排下一次闹钟；下一帧由 `capability_done/failed` 触发。能力超时本身会产生失败事件，不会静默等待。
- 工具结果不直接改变动作；LLM 下一帧重新判断。

---

## 8. 事件账本

- 事件状态只有两种：`unhandled / handled`。
- 只有 LLM 的 `handled` 能改变状态；机制不替它标记。
- 未处理事件会持续出现在后续情境中。
- "不回复" = 标记 `handled` 后选择 `idle`；"还没顾上" = 保持 `unhandled`，事件继续出现。

---

## 9. 事件插入

- 新事件不打断当前帧，只进收件箱；帧结束后可见。
- 当前回合结束后，LLM 决定先处理谁、是否放弃活动。
- 消息的 `arrived_during` 标注帮助 LLM 理解为补充或纠正。
- 不支持打断：已经进入执行的动作（发送、能力调用）会完整走完，新消息只排队。

---

## 10. 校验、修复与降级

| 情况 | 处理 |
| --- | --- |
| JSON 解析失败 | 一次修复提示；仍失败则降级为 `idle` |
| 未知 action | 视为非法；修复一次；仍失败则 `idle` |
| 缺少 target | 修复；无法修复则 `idle` 并保留事件 |
| 未知能力 | 返回错误；LLM 下一帧换路 |
| 参数不合法 | 返回校验错误；LLM 修正 |
| `handled` 含不存在的事件 | 忽略该项，不报错 |
| `next_wake_in` 越界 | 机制钳制到 0 ~ 24 小时 |
| 帧超时 | 取消本次调用，降级为 `idle`，记录日志 |
| LLM 调用失败（网络/限流/5xx/认证） | 有限重试 + 退避；仍失败则本帧失败，事件保持 `unhandled`，不写记忆，按最长间隔兜底，错误写进下一帧情境 |

**从不静默执行非法动作。** 所有降级都会写进下一帧情境。

校验只检查结构、动作枚举、参数范围与事件引用是否合法，不判断“这个行为是否合理”；行为选择权仍属于 LLM。

---

## 11. 流式（可选）

- 长回复可以边说边发：第一句先送出，后续继续生成。
- 流式属于执行层能力，不改变决策归属。
- 发送过程不支持打断；新消息在发送结束后进入下一帧情境。

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
  "action": "activity.start",
  "args": {"kind": "explore.web", "goal": "了解小模型的最新进展", "steps_limit": 8},
  "next_wake_in": 0,
  "remember": {"text": "我对小模型产生了兴趣", "valence": 0.3, "emotion": "好奇", "arousal": 0.4}
}
```

### 12.3 主动联系

```
Decision:
{
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
  "action": "idle",
  "handled": ["ev_101"],
  "next_wake_in": 1800
}
```

### 12.5 暂停并约定恢复

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

## 13. 边界

- 不预设"应该回复"的默认行为。
- 不把情绪写成条件分支。
- 不代替机制决定唤醒时机。
- 不绕过机制的超时与取消。
- 不在帧内做长时间阻塞操作。
- 不输出 Decision 之外的内容。

---

## 14. 可调参数

| 参数 | 含义 |
| --- | --- |
| `FRAME_TIMEOUT_SECONDS` | 单帧硬超时 |
| `MAX_DECISION_REPAIR` | Decision 修复尝试次数 |
| `LLM_RETRY_LIMIT` | LLM 调用重试次数 |
| `LLM_RETRY_BACKOFF_SECONDS` | 重试退避基数 |
