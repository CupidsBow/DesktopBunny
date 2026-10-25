# 10 · 执行：聊天与输出

> 执行层把 Decision 落地为输出。它不修改 Decision，也不决定说什么。
> 它负责：怎么发、发没发出去、失败怎么办、以及记忆与唤醒的落地动作。
> 本模块现为 `manager/executor/`（原 `effector/`）。

---

## 1. 输出通路

| 通路 | 实现 | 说明 |
| --- | --- | --- |
| 语言 | `transport.send_message` | 私聊，被动或主动 |
| 记忆 | `memory/*` | 情景写入、语义更新 |
| 状态 | body / activity / lifecycle | 活动进度、事件账本、唤醒排程 |

执行层只负责语言与记忆的"落地动作"；状态更新由各模块自己完成。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| Decision → 输出的总调度 | `manager/executor/executor.py` |
| 被动/主动发送与投递状态 | `manager/executor/chat_sender.py` |

> 旧版的 `message_splitter.py` 已删除：当前不拆消息，一条回复整段发出；需要拆分时再补回。

---

## 2. 聊天发送

### 2.1 被动与主动

| 类型 | 条件 | 行为 |
| --- | --- | --- |
| 被动回复 | `target.msg_id` 存在 | 带 `msg_id` 回复 |
| 主动消息 | 无 `msg_id`，或被动发送失败 | 不带 `msg_id` 直接发送 |
| 主动联系 | `chat.reach_out` | 走主动消息（`active=True`） |

发送流程：

1. 优先带 `msg_id` 被动回复。
2. 失败则降级为主动消息（不带 `msg_id`）。
3. `chat.reach_out` 直接走主动消息。
4. 有限重试（`send_retry_limit`，默认 1 次 → 最多 2 次尝试）。

### 2.2 会话类型

- 只支持私聊：`friend_id == user_openid`，发送目标即 Decision 的 `target.friend_id`。

### 2.3 消息类型

- 纯文本：`msg_type=0`。
- Markdown：`msg_type=2`；仅当内容确实需要 Markdown（含代码块或表格）时使用。
- 是否 Markdown 由执行层按内容判断，不改变 LLM 的文本。

---

## 3. 记忆写回

- Decision 的 `remember` 字段（`text` + `valence`/`emotion`/`arousal`）→ 提交一条 L2 情景给 memory worker；`speaker`/`time` 由机制填充。
- `tool.call(memory.write)` → 提交 L3 语义文件更新。
- 所有记忆写入统一进 memory worker 队列，串行落盘。
- 执行层只提交写操作，不决定内容与去重策略。
- 写入失败进入重试队列；不阻塞聊天发送。

---

## 4. Decision 到输出的映射

| Decision | 执行层动作 |
| --- | --- |
| `chat.reply` | 发送 → 写记忆 |
| `chat.reach_out` | 主动消息 → 写记忆 |
| `activity.start/pause/resume/complete/abandon` | 更新活动状态；`pause` 保存 `args.resume_when`；不直接发消息 |
| `activity_update` | 更新活动进度、`last_result`、`note` |
| `tool.call` | 交给 capability 层 |
| `idle` | 不产生输出；`next_wake_in` / `wake_when` 可选 |

执行顺序：

```
校验 Decision
  → 写记忆（不阻塞）
  → 发消息
  → 能力调用 / 更新活动
  → 排下一次唤醒（终态）
```

发送失败不回滚记忆；但会在下一帧情境的 `delivery` 里标注"上一条可能未送达"。

```json
{"ok": false, "parts_sent": 0, "detail": "发送失败"}
```

`DeliveryStatus` 由 `chat_sender.py` 返回，`executor.py` 保存最近一次结果（`last_delivery`），情境装配只读。

---

## 5. 错误处理

| 情况 | 处理 |
| --- | --- |
| 被动回复超时/失败 | 降级主动消息；记录 |
| 网络失败 | 有限重试；仍失败则标注未送达 |
| 记忆写入失败 | 队列重试；超过阈值告警 |
| 目标会话无效 | 返回错误；LLM 下一帧决定 |

---

## 6. 边界

- 不修改 Decision 的内容、目标或动作。
- 不替 LLM 决定"这条要不要发"。
- 不把发送结果回灌成新事件。
- 不在主线程做网络发送。
- 不因为发送失败而静默丢弃。

---

## 7. 可调参数

| 参数 | 含义 |
| --- | --- |
| `passive_reply_timeout` | 被动回复窗口（5s） |
| `send_retry_limit` | 发送重试次数（1） |
| `streaming_enabled` | 是否启用流式发送（默认关闭，暂未实现） |
