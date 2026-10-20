# 连续多消息处理设计

> 用户在 QQ 里连发多条消息、而 Alice 还在回复上一条时,应该怎么处理。

## 1. 问题

用户经常一次性连发好几条,比如:

```
小明: 在吗
小明: 帮我写个 python
小明: 算了不用了
```

如果这三条在 Alice 还在回复"在吗"时就全部到达,怎么处理?

## 2. 现状(需要改)

当前 `bot/qq_client.py` 的 `_WorkerPool` 是**每条消息一个 worker 并发跑**:同一用户连发 3 条 → 3 个 worker 并行 `chat()`。后果:

- **无顺序**:3 条回复可能乱序到达。
- **无共享工作记忆**:每条都看不到上一条的回复,也看不到"Alice 刚说了什么"。
- **语义错乱**:模型可能把"算了不用了"当成对上一条的独立问题,而不是对"帮我写 python"的撤销。

## 3. 参考:Claude Code 的做法

Claude Code 对"上一轮没答完、用户又输入下一段"的处理是**默认排队、不打断**:

1. **排队(queue)**:输入在 Claude 还在跑时进队列,等当前工具调用块/整轮结束再依次投喂;`/` 命令和 `!` shell 命令等整轮结束才执行。
2. **显式打断(Esc)**:中止当前 turn、保留已完成工作,再把排队消息送进去。
3. **send-now 中断协议**:较新版本可把排队消息立即送达但不 abort 整轮,把可移动工作挪后台。

**已知坑(设计时要避开)**:

- 排队消息被模型误解成"对刚生成回复的回应"([Issue #57624](https://github.com/anthropics/claude-code/issues/57624))——应给消息打"这是回复期间追加的"标记。
- 排队顺序 LIFO、无文档([Issue #54031](https://github.com/anthropics/claude-code/issues/54031))。
- 竞态/吞消息:Esc 后新消息被静默忽略([Issue #30118](https://github.com/anthropics/claude-code/issues/30118));回复结束瞬间输入报 "Previous query still processing" 且文本丢失([Issue #5817](https://github.com/anthropics/claude-code/issues/5817));Esc 一次只吐一条排队消息([Issue #93659](https://github.com/anthropics/claude-code/issues/93659))。

## 4. 目标设计

对"一个活生生的 Alice",Claude Code 的**单流模型**比现状的并发模型更自然——人不可能和同一个朋友**并行**进行两段对话,一定是按顺序消化。

### 4.1 按 session 串行化 + 队列

- 同一个 QQ 号(`session_id`)的消息进**该会话的队列**,一条处理完(回复发出)再处理下一条。
- **保序**:严格 FIFO,先到的先处理。
- 不同会话之间仍用线程池**并行**(不同朋友互不干扰)。
- 与记忆模块的 L1 工作记忆天然对齐:工作窗口"每会话一份",串行了才不会有竞争。

### 4.2 给"追加消息"打标记

若一条消息到达时 Alice 还在回复上一条,标注类似:

> 这条是主人趁 Alice 还在回复时追加的

让模型把它理解成**对进行中话题的补充/纠正**,而不是对刚刚那条回复的回应(借鉴 Issue #57624 的思路)。

### 4.3 v1 不做"打断/插队"

Claude Code 的 Esc / send-now 复杂度高、且是 bug 重灾区。v1 只做"队列 + 保序 + 追加标记";等有"主人要打断 Alice"的强需求再加。

### 4.4 机制草图

```
session_id(qq号) → 该会话的消息队列
                  └─ 单 worker 串行取出 → dispatcher → chat(带该会话工作记忆)
不同 session_id → 各自独立队列,可并行
```

## 5. 与"一问一答"的关系

仍严格一问一答,但拆成三层理解:

1. **处理层**:每条用户消息 → 恰好一次模型回复,同一会话串行、保序。不是"一条消息回两条",也不是"两条并行生成两条"。
2. **输入层**:用户可以连珠炮发送,消息进队列缓冲,**不必等 Alice 回完**。从用户视角可连发,从 Alice 视角串行消化。
3. **单条内部**:一条消息可能内部走多轮工具调用(最多 5 轮)才给出最终回复。"一问"对外是一答,对内是多步推理。

**边界**:此设计下 Alice 是纯**应答式**的——只在被发消息时说话,不主动开口,也不在答完后自己追加。

## 6. 未来扩展(正交,不在本设计内)

- **主动性**:Alice 不被打扰也能主动发消息(如"主人,我饿了")。
- **多消息回复**:一次回复拆成多条。

这两项与"排队 + 串行 + 记忆"正交,是独立的开关。

## 7. 参考

- [Interactive mode — Claude Code 官方文档](https://code.claude.com/docs/en/interactive-mode)
- [Issue #57624 — 排队消息被误解为对刚生成回复的回应](https://github.com/anthropics/claude-code/issues/57624)
- [Issue #54031 — 排队消息顺序 LIFO 且无文档](https://github.com/anthropics/claude-code/issues/54031)
- [Issue #93659 — Esc 一次只吐一条排队消息](https://github.com/anthropics/claude-code/issues/93659)
- [Issue #30118 — Esc 后消息被吞](https://github.com/anthropics/claude-code/issues/30118)
