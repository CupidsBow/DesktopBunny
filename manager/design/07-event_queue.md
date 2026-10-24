# 07 · 事件队列：事件、收件箱与账本

> 事件队列存储任何可以触发 Alice 思考的事件，负责收件箱语义与账本的持久化、崩溃恢复。
> 它不决定什么时候处理、也不决定哪些重要——那些是 LLM 与生命周期的事。

---

## 1. 职责边界

| 负责 | 不负责 |
| --- | --- |
| 事件入队、账本持久化、崩溃恢复 | 判断重要性、排序、过滤 |
| 未处理事件的收件箱视图 | 决定何时唤醒（属于 [06-lifecycle](06-lifecycle.md)） |
| `handled` 状态的落盘 | 替 LLM 标记处理 |

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 事件队列、收件箱、账本 | `manager/event_queue/event_queue.py` |

---

## 2. 事件信封

```json
{
  "id": "ev_000123",
  "kind": "message | capability_done | capability_failed |
           activity_checkpoint | alarm | signal | system",
  "at": "2026-10-20T21:03:11+08:00",
  "source": {"friend_id": "u_123", "conversation_id": "c_123"},
  "payload": {"text": "在吗"},
  "arrived_during": {"type": "activity", "id": "act_17"},
  "status": "unhandled | handled"
}
```

- 事件只追加，不删除。
- `arrived_during` 标出事件到达时 Alice 正在做什么。
- 状态只能由 LLM 的 `handled` 改变（见 [09-thinker](09-thinker.md)）。
- 各 `kind` 的 `payload` 最小字段见 [brain.md §7.1](../brain.md#71-事件信封)。

---

## 3. 收件箱语义

- 所有会话共用一个收件箱。
- 严格按到达顺序记录；不并行处理。
- 未处理事件会持续出现在后续情境中。
- 事件队列不按重要性过滤、不丢弃、不延迟投递。

> 旧版曾有"批量窗口"（settle window）合并连珠炮消息；当前架构不合并，消息逐条入队，逐条在情境中呈现。

---

## 4. 持久化

| 内容 | 格式 | 频率 |
| --- | --- | --- |
| 事件账本 | `events.jsonl`，一行一个 Event | 每次变化 |

写入使用"临时文件 + 原子替换"，避免半写文件。

---

## 5. 崩溃恢复

1. 加载事件账本：未处理事件继续有效。
2. 在途能力任务标记 `unknown`，结果不可信。
3. 触发一次唤醒，情境里说明"上次异常退出"。

活动、闹钟与记忆的恢复分别由各自模块负责。

---

## 6. 事件队列不做的事

- 不计算注意力分数、不排序消息重要性。
- 不丢弃它认为不重要的消息。
- 不把事件包装成行为建议。
- 不决定什么时候唤醒（唤醒聚合属于 [06-lifecycle](06-lifecycle.md)）。
