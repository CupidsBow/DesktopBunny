# 08 · 帧生成：情境与提示词

> 帧生成器把唤醒理由与各模块的只读视图聚合成**一帧**：一份结构化情境 + 组装好的 prompt。
> 它是认知决策的输入侧；它不调用 LLM，也不做决定。

---

## 1. 职责边界

| 负责 | 不负责 |
| --- | --- |
| 聚合唤醒理由与只读视图 | 调用 LLM、产出 Decision（属于 [09-thinker](09-thinker.md)） |
| 装配情境 JSON | 修改各模块状态 |
| 组装 prompt（L0 + 情境 + 能力 + 约束 + 指令） | 决定做什么 |

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 帧生产（聚合唤醒理由与只读视图） | `manager/frame_generator/frame_generator.py` |
| 情境装配 | `manager/frame_generator/situation.py` |
| prompt 组装 | `manager/frame_generator/prompt.py` |

---

## 2. 帧

一帧 = 一次 LLM 调用的输入。帧生成器取出当前一个或多个唤醒理由，与身体、环境、记忆、能力、活动信息聚合，整理成一帧交给 [09-thinker](09-thinker.md)。

帧与帧之间严格串行；并发的只有 I/O（后台能力任务）。

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
    "identity": "Alice",
    "mood": 0.4,
    "mood_label": "轻快",
    "urges": {"饥饿": 0.7, "孤独": 0.2, "睡意": 0.1}
  },

  "body": {
    "satiety": 62, "energy": 0.71,
    "social_battery": 0.6, "circadian": 0.8,
    "presence": true
  },

  "environment": {
    "someone_present": true,
    "idle_seconds": 42,
    "foreground_title": "Visual Studio Code",
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
    "periodic_wake_seconds": 1800,
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
- 现在没有值得做的事时，用 idle 安静结束这一帧，别硬找话聊。
- 只输出一个 JSON Decision，不要输出其他内容。
```

提示词里不写具体台词、不写"心情差就不回"的规则。
