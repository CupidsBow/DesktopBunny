# Alice 大脑(brain)架构总纲

> 三份文档的整合层:`memory_manager.md`(记忆/情感)、`multiple_message.md`(连续多消息)、`initiative.md`(主动性/多消息)之上,用一个薄状态机把它们缝成一个"决策者"。

## 0. 定位与分层

- **记忆是"状态"(数据,被动),不是"决策者"**——它只提供"Alice 现在记得什么、对谁什么态度、心情如何"。
- **状态机管"机制",LLM 管"判断"**:状态机决定"现在轮不轮到做决定、什么时候做",LLM 读记忆决定"做什么"。**绝不硬编码**"心情<0 就不回"这类规则,否则会推翻人格从记忆涌现的设计。

三层:

```
数据层(被动)  memory/        存、召回、巩固,不决定任何事
控制层(机制)  brain.py       薄状态机 + 队列 + debounce + 需求 tick
判断层(意识)  chat_manager.py LLM 读记忆 → 产出"动作 + 内容"
```

## 1. 模块划分(每个 .py 的职责)

```
manager/
├── brain.py            # 决策控制器:状态机 + 每会话队列 + debounce + 编排
├── chat_manager.py     # 执行一次 LLM turn(工具循环),返回 {action, content}
├── drives.py           # 需求系统(累积/阈值/冷却)
├── affect.py           # 心情 + 态度(从情景记忆 valence 派生)
├── personality.py      # L0 核心种子 + 组装 messages
└── memory/
    ├── store.py        # 落盘 + MEMORY.md 索引 + 巩固(L2↔L3)
    └── recall.py       # 召回(索引 → 相关记忆)
```

### brain.py —— 唯一的"指挥者"

```python
class SessionContext:                     # 每会话一份
    session_id: str
    state: "IDLE|DEBOUNCING|THINKING|SENDING"
    queue: deque[Message]                 # 待处理输入(FIFO)
    batch: list[Message]                  # 当前 debounce 批
    debounce_until: float                 # debounce 截止时间
    working_memory: deque                 # L1 工作窗口(该会话最近 N 轮原文)
    last_sent: list[str]                  # 最近发出的内容(供打断/纠正上下文)
    pending_segments: list[str]           # 多消息未发分片

class Brain:
    def on_message(self, session_id, kind, target_id, msg_id, text): ...  # 消息入口
    def tick(self): ...                  # 需求 tick + 各会话状态推进(debounce 到期等)
    def _advance(self, ctx): ...         # 状态机主循环(见 §3)
    def _run_decision(self, ctx, directive): ...  # 组装 → chat_manager → 动作
    def _dispatch(self, ctx, action): ...         # 输出/沉默/排队(见 §7)
```

### chat_manager.py —— 执行一次 turn(改造)

```python
class ChatManager:
    def run_turn(self, messages: list[dict], tools: list) -> TurnResult:
        # 内部仍是 LLM + 工具循环(最多 MAX_TOOL_ROUNDS 轮)
        # 返回 TurnResult {action, content}   ← action 由决策指令决定
```

> 改造点:不再自己管记忆/历史,只做"给定 messages,跑出结果"。历史由 brain 传入。

### memory/store.py —— 数据层(被动)

```python
def add_episode(frontmatter: dict, body: str) -> None      # 写一条 L2 情景记忆
def read(path: str) -> str                                  # 读任意记忆文件
def get_index() -> list[tuple[str, str]]                    # 读 MEMORY.md 索引(常驻)
def update_semantic(path: str, content: str) -> None        # 更新 L3 语义文件(self/friends/...)
def consolidate() -> None                                   # 周期巩固:L2 情景 → L3 语义
```

### memory/recall.py —— 召回

```python
def recall(context: dict) -> MemoryBundle:
    # 扫 MEMORY.md 索引(一行行 description),挑 ≤5 相关情景 + 朋友档案 + self.md
    # 返回 MemoryBundle {self, episodes:[...], friend_profile, mood, attitude}
```

### affect.py —— 心情/态度(派生值,不存盘)

```python
def mood(episodes, now) -> float                          # 近窗口 valence 聚合,短半衰期
def attitude(friend_id, episodes, now) -> (value, confidence, level)
    # 加权衰减聚合 → (态度值, 置信度, 关系等级:陌生/认识/熟络/亲近/亲密)
```

### drives.py —— 需求系统

```python
class Drive:
    name: str          # 饥饿/孤独/想念/分享/日常
    accumulator: float # 当前累积值
    threshold: float   # 触发阈值
    cooldown: float    # 触发后的冷却
    target: str        # 目标(主人 / 某朋友)
    prompt: str        # 驱动说明("你饿了,想对主人说...")

class Drives:
    def tick(self, now) -> list[Drive]:   # 返回已越阈值且未冷却的
    def on_consumed(self, drive) -> None  # 触发后重置累加器 + 冷却
```

### personality.py —— L0 核心种子 + 组装

```python
L0_CORE = "..."   # 身份(Alice/桌宠兔兔) + 气质基线(活泼亲昵) + 行为边界。基因种子,极小

def build_messages(directive, memory_bundle, working_memory, user_text) -> list[dict]:
    # 组装:core + self + 朋友档案(含态度/关系) + 心情 + 相关情景 + 工作窗口 + 当前消息
```

## 2. 数据流图

```
[QQ 网关]                    [pygame 主循环]
   │ 消息事件                    │ 需求 tick
   ▼                            ▼
Brain.on_message()          Drives.tick() ──越阈值──▶ 注入"主动 turn"
   │                            │
   └──────────┬─────────────────┘
              ▼
      ┌──────────────────────────────┐
      │  Brain 决策控制器             │
      │  每会话状态机 + 队列 + debounce│
      └──────────────────────────────┘
              │ 到决策点(THINKING)
              ▼
      recall.py ──读──▶ memory/store.py (MEMORY.md 索引 / 正文)
              │  self + ≤5 情景 + 朋友档案
      affect.py ──读──▶ 情景记忆 valence ──▶ mood / attitude
              │
              ▼
      personality.py 组装 messages
              │
              ▼
      chat_manager.py 执行 turn(LLM + 工具循环)
              │  TurnResult {action, content}
              ▼
      Brain._dispatch ──▶ [qq_client 发送 / 沉默 / 排队 / 继续]
              │
              ▼
      memory/store.py 回写(工作窗口 + 情景 + 巩固)
```

## 3. 状态机

### 3.1 每会话状态

```
┌────────┐  收到消息   ┌───────────┐  窗口到期   ┌─────────┐  动作=reply/speak  ┌─────────┐
│  IDLE  │───────────▶│ DEBOUNCING│───────────▶│ THINKING│───────────────────▶│ SENDING │
└────────┘            └───────────┘            └─────────┘                    └─────────┘
     ▲                     │ ▲                      │  ▲                         │
     │                     │ │(新消息:重置窗口)       │  │(动作=continue,≤2 次)      │
     │                     └─┘                      └──┘                         │
     │                                                                            │
     │   动作=ignore (THINKING → 直接回 IDLE,不发送)                                 │
     └────────────────────────────────────────────────────────────────────────────┘
     SENDING 发完(单条或多条) → 回 IDLE
```

全局需求(任意状态,目标会话):越阈值 → 若目标会话 IDLE 则直接 THINKING(directive=speak);否则入该会话队列,等 IDLE。

### 3.2 流转规则表

| 当前状态 | 触发 | 条件 | 下一状态 | 动作 |
| --- | --- | --- | --- | --- |
| IDLE | 队列非空 | — | DEBOUNCING | 弹出队首为一批,启动 debounce 计时 |
| DEBOUNCING | 新消息到达 | 窗口内 | DEBOUNCING | 追加进当前批,重置窗口 |
| DEBOUNCING | 窗口到期 | — | THINKING | 当前批冻结为一个 turn 的输入 |
| THINKING | 决策 = ignore | LLM 判定低信号/不想回 | IDLE | 不发送 |
| THINKING | 决策 = reply/speak | — | SENDING | 输出(单条或多条) |
| THINKING | 决策 = continue | 还有话说,且 <2 次 | THINKING | 再跑一轮(带"你刚说了 X,还想补充吗") |
| SENDING | 分片发完,无 continue | — | IDLE | 结束 |
| THINKING/SENDING | 新消息到达 | — | 不变 | 只入队,不打断(见 §7) |

## 4. 三个子系统如何影响决策

决策发生在 **THINKING**。LLM 收到一份**决策指令** + **记忆上下文**,输出结构化动作。各子系统的影响:

| 子系统 | 注入什么 | 影响什么 |
| --- | --- | --- |
| 记忆(self.md,常驻) | "我是谁"的底色 | 每轮必带,决定语气与立场的锚 |
| 记忆(相关情景 ≤5) | 上下文事实 | 决定内容;聊到某话题可能触发 continue |
| 记忆(朋友档案 + 态度) | 关系等级/亲疏 | 态度高→热络;态度低→克制、更可能 ignore |
| 记忆(工作窗口) | 本轮对话原文 | 直接是对话上下文 |
| 心情(mood) | "你现在心情:开心(0.4)" | 软提示:心情低→更可能简短/ignore,心情好→更可能 continue/主动 |
| 需求(drives) | "你饿了,想对主人说…" | 触发 speak 动作的入口(不经过消息/队列) |

**决策指令(给 LLM 的 system 片段)**:

```
你现在需要决定下一步做什么。可选动作:
- reply:  回复这条消息(默认)
- ignore: 不回复(对方消息很低信号,如"嗯""好",或你此刻不想回)
- continue: 你刚说完,但还有话想补充
- speak:  (仅需求触发时)主动对目标说话
结合你的记忆、心情、对对方的态度,只输出 JSON:{"action": "...", "content": "..."}
```

> 注意:`wait`(等待补充)在 v1 由 debounce 确定性实现,LLM 不直接选;后续可让 LLM 建议延长窗口。

## 5. 决策协议(TurnResult)

```json
{
  "action": "reply | ignore | continue | speak",
  "content": "回复正文"          // 多消息拆分由客户端做(initiative.md),模型不负责
}
```

`brain._dispatch` 按 action 分发:

- reply/speak → 拆段 → SENDING
- ignore → 直接 IDLE(无输出)
- continue → 再跑一轮 THINKING

## 6. 用户输入的聚合(单个 / 多个)

- 所有输入统一进**每会话 FIFO 队列**。
- **单个输入**:IDLE → 弹出 → DEBOUNCING(等一个短窗口,防"话没说完")→ THINKING。
- **多个输入(连发/连珠炮)**:
  1. 都在窗口内到达 → 不断追加进**同一批**、重置窗口;
  2. 窗口到期 → **整批冻结成一个 turn**,带"这些是主人连续发来的,按顺序理解"的语义;
  3. 一批内若既有内容又有纠正(如"帮我写 X" + "算了不用"),模型按顺序理解,天然处理"追加/撤销"。
- **在 THINKING/SENDING 期间到达的**新消息:入队,等本轮结束再进入下一轮 DEBOUNCING(见 §7),并带"这是 Alice 刚才在忙时主人追加的"标记。

> debounce 窗口默认固定(约 2~3 秒);消息看起来明显没说完时,可让 LLM 建议延长(可选增强)。

## 7. 自身发送被打断的处理

### 7.1 单条发送中被新消息打断

1. 新消息入该会话队列,**不打断**当前发送。
2. 当前这条发完 → SENDING 结束 → 回 IDLE。
3. 队列非空 → 进入 DEBOUNCING 处理新消息。
4. 处理时注入上下文:`你刚才给 X 发了"<上一条内容>",主人现在追加"<新消息>"` —— 让模型识别这是纠正/补充。

### 7.2 多条发送中被新消息打断

1. 新消息入队列,**不打断**。
2. 检查新消息是否含**打断信号**(如"停 / 别发了 / 听我说 / 不对")——轻量判定(关键词或一次廉价 LLM 判断):
   - **无信号**:发完剩余分片 → 回 IDLE → 再处理新消息。
   - **有信号**:丢弃剩余未发分片 → 回 IDLE → 立即处理新消息(把"已发部分"写进上下文)。
3. (可选)丢弃的剩余分片存为一条"没说完的话",供后续 continue 使用。

### 7.3 THINKING 中被打断

- 新消息入队列,不影响正在进行的工具循环。
- THINKING 完成后:先发已生成的回复,再回 IDLE 处理队列里的新消息(保持简单;若新消息是明确纠正,后续版本可并入本轮)。

## 8. 关键边界(反复强调)

- **状态机只做机制**:谁在忙、消息该不该排队、turn 生命周期、需求何时触发。状态少、可枚举。
- **LLM 做判断**:回 / 不回 / 等 / 主动说 / 继续说,由模型读记忆决定,不写死阈值规则。
- **记忆只提供状态**:不参与控制流,只被读、被写。
- 三者的分界一旦混淆(比如在 brain 里写"mood<-0.3 就 ignore"),整个"人格从记忆涌现"的设计就塌了。
