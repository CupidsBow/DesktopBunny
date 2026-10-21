# 06 · 执行：聊天与多消息

> 执行层把 Decision 落地为输出。它不修改 Decision，也不决定说什么。
> 它负责：怎么发、拆不拆、间隔多久、失败怎么办。

---

## 1. 输出通路

| 通路 | 实现 | 说明 |
| --- | --- | --- |
| 语言 | `qq_client.send_*` | 单聊，被动或主动 |
| 记忆 | `memory/*` | 情景写入、语义更新 |
| 状态 | body / activities / mechanism | 需求、活动进度、事件账本 |

执行层只负责语言与记忆的"落地动作"；状态更新由各模块自己完成。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| Decision → 输出的总调度 | `manager/effector/effector.py` |
| 被动/主动发送与投递状态 | `manager/effector/chat_sender.py` |
| 多消息拆分 | `manager/effector/message_splitter.py` |

---

## 2. 聊天发送

### 2.1 被动与主动

| 类型 | 条件 | QQ 侧行为 |
| --- | --- | --- |
| 被动回复 | 有触发消息的 `msg_id` | 带 `msg_id` 回复，受时间窗口限制 |
| 主动消息 | 无 `msg_id` 或被动失败 | 直接发送，不受回复窗口限制 |
| 主动联系 | `chat.reach_out` | 走主动消息 |

实现建议：

1. 优先带 `msg_id` 被动回复。
2. 失败则降级为主动消息，并记录日志。
3. `chat.reach_out` 直接走主动消息。

### 2.2 会话类型

- **c2c**：`user_openid`。
- 发送目标以 Decision 的 `target.conversation_id` 为准。

### 2.3 消息类型

- 纯文本：`msg_type=0`。
- Markdown：`msg_type=2`；仅当内容确实需要 Markdown 时使用。
- 是否 Markdown 由执行层根据内容判断，不改变 LLM 的文本。

---

## 3. 多消息拆分

### 3.1 原则

**客户端拆分，模型不负责。** 模型照常输出一段完整回复；执行层在自然边界拆成多条发出。

理由：

- 不污染模型输出格式。
- 不要求模型理解 QQ 的发送机制。
- 拆分策略可以独立调参。

### 3.2 拆分规则

- 优先在句末、段落、换行处切分。
- 单条上限建议 500 字以内。
- 最少 1 条，最多建议 3~4 条；过短的内容不拆。
- 含表格、代码块、列表的 Markdown **不拆**，避免结构被撕断。
- 简单判断：出现 `|`、```、`- ` 列表等结构时保持单条。

### 3.3 节奏

- 分片之间加 0.5~1 秒延迟，像真人在打字。
- 所有分片属于**同一个回合**，不回灌收件箱，不触发新的认知帧。
- 发送顺序严格按拆分顺序。

### 3.4 流式（可选）

- 长回复可以边说边发：第一句先送出，后续继续生成。
- 流式发送的分片仍然属于同一回合。
- 发送过程不支持打断；期间收到的新消息只进收件箱，发完再处理。

---

## 4. 记忆写回

- Decision 的 `remember` 字段（`text` + `valence`/`emotion`/`arousal`）→ 提交一条 L2 情景给 memory worker；`speaker`/`conversation`/`time` 由机制填充。
- `tool.call(memory.write)` → 提交 L3 语义文件更新。
- 所有记忆写入统一进 memory worker 队列，串行落盘。
- 执行层只提交写操作，不决定内容与去重策略。
- 写入失败进入重试队列；不阻塞聊天发送。

---

## 5. Decision 到输出的映射

| Decision | 执行层动作 |
| --- | --- |
| `chat.reply` | 拆分 → 发送 → 写记忆 |
| `chat.reach_out` | 主动消息 → 写记忆 |
| `activity.start/pause/resume/complete/abandon` | 更新活动状态；`pause` 保存 `args.resume_when`；不直接发消息 |
| `activity_update` | 更新活动进度、`last_result`、`note` |
| `tool.call` | 交给 capability 层 |
| `eat` | 把 `args.food` 交给桌宠主线程；饱食度仍由 `components/bunny.py` 权威更新 |
| `rest` | 设置 `body.mode = rest`；降低唤醒频率 |
| `idle` | 不产生输出；`next_wake_in` / `wake_when` 可选；保持当前 `mode` |

执行顺序：

```
校验 Decision
  → 写记忆（不阻塞）
  → 发消息
  → 进食请求交给桌宠主线程
  → 更新活动与事件账本
  → 调度下一次唤醒
```

发送失败不回滚记忆；但会在下一帧情境的 `delivery` 里标注"上一条可能未送达"。

```json
{"ok": false, "parts_sent": 1, "detail": "被动回复超时，主动消息也失败"}
```

`DeliveryStatus` 由 `chat_sender.py` 返回，`effector.py` 保存最近一次结果，情境装配只读。

---

## 6. 错误处理

| 情况 | 处理 |
| --- | --- |
| 被动回复超时 | 降级主动消息；记录 |
| 网络失败 | 有限重试；仍失败则标注未送达 |
| 分片部分失败 | 停止后续分片，把失败写进情境 |
| 记忆写入失败 | 队列重试；超过阈值告警 |
| 目标会话无效 | 返回错误；LLM 下一帧决定 |

---

## 7. 边界

- 不修改 Decision 的内容、目标或动作。
- 不替 LLM 决定"这条要不要发"。
- 不把分片回灌成新事件。
- 不在主线程做网络发送。
- 不因为发送失败而静默丢弃。

---

## 8. 可调参数

| 参数 | 含义 |
| --- | --- |
| `MESSAGE_SPLIT_MAX_CHARS` | 单条分片上限 |
| `MESSAGE_SPLIT_MAX_PARTS` | 单回合最多分片数 |
| `MESSAGE_SEND_INTERVAL` | 分片间隔 |
| `PASSIVE_REPLY_TIMEOUT` | 被动回复窗口 |
| `SEND_RETRY_LIMIT` | 发送重试次数 |
| `STREAMING_ENABLED` | 是否启用流式发送 |
