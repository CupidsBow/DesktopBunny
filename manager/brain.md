# Alice 智能生物架构总纲

> Alice 是一只生活在 PC 里的智能生物。一个 LLM 是她的决策中心；根据当前的内部因素与外部因素决定做什么。
> 本文从**全局**到**局部**：先讲整体工作流与模块边界，再给出模块地图与跨模块契约。每个模块的细节见 `design/` 下的独立文档。

---

## 0. 文档地图

```
manager/
├── brain.md                 ← 总纲：原则、全局工作流、模块地图、契约、生命周期
├── new_brain.md             ← 模块划分与接口速览（与本文一致）
└── design/                 # 每个子模块一份文档，按数据流排序
    ├── 01-environment.md    环境：在场、空闲、前台窗口
    ├── 02-body.md           身体与自我状态：稳态、情绪、态度、欲望
    ├── 03-memory.md         记忆：情感痕迹、关系、召回、巩固
    ├── 04-capabilities.md   能力：注册、异步、审计
    ├── 05-activity.md       活动：持续行为、探索、学习、反思、主动性
    ├── 06-lifecycle.md      生命循环：状态时钟、唤醒、闹钟、信号
    ├── 07-event_queue.md    事件队列：事件、收件箱、账本、恢复
    ├── 08-frame_generator.md 帧生成：情境装配与 prompt 组装
    ├── 09-thinker.md        认知决策：Decision 协议与动作空间
    └── 10-executor.md       执行：聊天发送、记忆写回
```

> 注：设计文档已按当前 11 个模块重排：`environment/` 与 `body/` 各成一篇，`cognition/` 拆成 `frame_generator/` + `thinker/`，`activities/` 改 `activity/`，`effector/` 改 `executor/`，`mechanism/` 拆成 `lifecycle/` + `event_queue/`。本文与各文档均以**当前代码**为准。

---

## 1. 定位

Alice 不是"会聊天的机器人"，而是一个**持续存在**的个体：

- 她有身体：饱食度、精力、社交电量。疲惫只由这些连续量体现，不设醒/睡二元状态。
- 她有记忆：具体经历、对每位朋友的态度、逐渐长成的自我认知。
- 她有生活：聊天、上网、探索本地、学习、整理记忆、发呆。
- 她只有**一个意识**：同一时刻只想一件事，所有行为都出自同一个决策中心。

**一个 LLM 决定她做什么。** 机制层不替她选择行为，只负责让她能持续、安全地活着。

---

## 2. 设计原则

1. **LLM 是唯一的决策者。** 做什么、对谁、何时、多久、继续还是放弃，全部由 LLM 判断。
2. **机制不做行为决策。** 机制只做事件投递、唤醒、执行、约束、持久化。
3. **聊天是行为之一。** 社交、探索、学习、内省、创造并列。
4. **所有对话者都是朋友。** 没有预设的更高身份；亲疏与信任从记忆里生长。
5. **状态只作为输入。** 情绪、欲望、精力是情境里的信息，不是隐藏的开关。
6. **身体是事实来源。** 饱食度等生理量来自桌宠本体，不由记忆层编造。
7. **沉默与行动同等合法。** `idle` 是明确的选择。
8. **机制只保留执行边界。** 超时、取消、急停不可协商；没有权限裁剪，选择权属于 LLM。

### 决策与约束的边界

| 决策（LLM） | 约束（机制） |
| --- | --- |
| 做什么、对谁、何时、多久 | 除超时外不设约束 |
| 继续、暂停还是放弃活动 | 工具与帧的超时、取消 |
| 想多深、用哪些能力 | 线程与进程生命周期 |
| 下一次什么时候醒来 | 事件投递与串行帧 |
| 要不要把发现分享出去 | 持久化、崩溃恢复、审计 |

机制只执行约束，不利用约束反向影响选择。

### 行为平等

聊天不是主路径，也不是默认行为。所有行为在架构里都是同一种东西：LLM 在当前帧选择的一个动作，或它发起的一段活动。

| 行为 | 表达方式 | 执行者 |
| --- | --- | --- |
| 发消息 / 主动联系 | `chat.reply` / `chat.reach_out` | executor → QQ |
| 上网 | `activity.start(explore.web)` + `web.*` | activity + capabilities |
| 探索电脑 | `activity.start(explore.pc)` + `fs.*` / `pc.observe` | activity + capabilities |
| 学习 / 整理 | `activity.start(learn)` + `web.*` / `memory.*` | activity + capabilities |
| 创作 | `activity.start(create)` + `fs.write` / `sandbox.run` | activity + capabilities |
| 思考 / 内省 | `activity.start(reflect)` + `artifact.read` | activity + memory |
| 吃点东西 | `tool.call(eat)` | capability → 桌宠主线程 |
| 什么都不做 | `idle`（`next_wake_in` / `wake_when` 可选） | executor（无输出） |

“平等”的含义是：没有哪个动作被机制特殊对待。区别只来自 LLM 当下的判断。

---

## 3. 全局架构

```
                          ┌──────────────────────┐
                          │  世界                 │
                          │ 朋友消息 / 网络 / PC  │
                          └──────────┬───────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────┐
│ 生命维持（lifecycle + event_queue + brain 循环）                 │
│ 事件入队 · 唤醒理由 · 帧串行 · 持久化 · 安全 · 审计              │
└───────────────────────────────┬──────────────────────────────────┘
                                │ 唤醒理由 + 情境 + prompt
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ 认知（frame_generator 装帧 + thinker 调 LLM）                    │
│ 读情境 → 选择一个动作                                             │
└───────────────────────────────┬──────────────────────────────────┘
                                │ Decision
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ 执行 executor                                                     │
│ 聊天 · 能力调用 · 活动 · 记忆写入 · 排下一次唤醒                   │
└───────────────────────────────┬──────────────────────────────────┘
                                │ 结果 / 新事件
                                └──────────────▶ 回到事件队列

支撑输入：
  环境 environment ──▶ 身体 body ──▶ 自我状态 self_state
  记忆 memory（L0–L3）──▶ 情境装配
```

**一句话数据流**：
世界 → 事件 → 事件队列 → 唤醒 → 情境（环境 + 身体 + 自我状态 + 记忆 + 活动 + 约束）→ LLM 决策 → 执行 → 新事件/记忆/状态 → 下一次唤醒。

---

## 4. 全局工作流

### 4.1 三个时间尺度

| 概念 | 定义 | 典型长度 |
| --- | --- | --- |
| **帧 frame** | 一次 LLM 调用：看情境 → 产出一个 Decision | 1~10 秒 |
| **回合 turn** | 为同一个意图服务的一串帧（可能包含若干次工具调用） | 数秒到一分钟 |
| **活动 activity** | 跨越多个回合的持续行为（探索、学习、反思） | 几十秒到数小时 |

- 帧与帧之间严格串行，一次只有一个意识。
- 工具调用的结果会作为事件回到事件队列，并立即触发下一帧。
- 活动是帧的序列，不是一次长调用；帧之间可以处理消息。

### 4.2 从一次唤醒到下一次唤醒

```
① 状态时钟（每 2 秒，由 main 主线程调 brain.tick）
   推进环境、身体；检测显著变化信号。

② 事件产生
   消息 / 能力结果 / 活动检查点 / 系统事件 → 事件队列。

③ 唤醒理由
   事件 / 闹钟（next_wake_in、wake_when）/ 信号 /
   启动 / 定期心跳（periodic_wake_seconds）。

④ 情境装配
   frame_generator 从各模块取：环境、身体、自我状态、记忆视图、
   当前活动、未处理事件、能力清单、约束、唤醒理由。

⑤ 认知
   thinker 调 LLM 读情境，产出 Decision：
   tool.call / chat.reply / chat.reach_out /
   activity.start / activity.pause / activity.resume /
   activity.complete / activity.abandon / idle

⑥ 校验与执行
   校验 Decision；非法则修复一次，仍失败则本帧失败。
   executor 落地：发消息、派发能力、更新活动、写记忆。

⑦ 状态与记忆更新
   活动进度、事件账本、情感痕迹、语义记忆。

⑧ 排下一次唤醒
   终态动作可给 next_wake_in / wake_when；
   activity.pause 可带 args.resume_when。
   机制钳制上下限；事件永远可以提前唤醒。

⑨ 等待
   帧结束。认知 worker 回到 wait（0.5 秒轮询），期间不调用 LLM。
```

### 4.3 一次具体例子

```
21:03  朋友 u_123 连发三条消息，逐条进入事件队列，触发唤醒。

21:03  frame_generator 装帧：三条消息 + 对 u_123 的态度 + 精力/社交电量。
       此时 Alice 正在 explore.web 的第 3 步。

21:04  LLM 看到：活动进度 40% + 三条新消息（标注 arrived_during）
       + 情境里已经自动召回的那次推荐文章。

21:04  LLM 决定 chat.reply，内容包含文章名；handled 标记三条消息全部处理。

21:05  executor 发出回复（一条消息，不拆分）。
       终态帧结束，机制自动写一条情景："回复 u_123：那篇文章是……"。

21:05  LLM 决定 activity.resume。
       下一帧继续探索。

21:30  活动完成，LLM 把发现写进记忆；next_wake_in=600，等待下一次醒来。
```

---

## 5. 运行时视图

### 5.1 时钟

| 时钟 | 频率 | 做什么 | 调 LLM |
| --- | --- | --- | --- |
| 状态时钟 | 2 秒（`state_tick_interval`） | 推进环境/身体，检测显著变化 | 否 |
| 认知 worker 轮询 | 0.5 秒（`_POLL_INTERVAL`） | 检查到期闹钟、取唤醒理由 | 否（只在有理由时调） |
| 认知帧 | 不固定 | 一次帧决策 | 是 |
| 巩固循环 | 60 秒检查一次（`consolidation_interval`=6h） | 记忆蒸馏 | 是 |
| 定期心跳 | 1800 秒（`periodic_wake_seconds`） | 兜底唤醒 | 是 |

### 5.2 线程

| 线程 | 职责 |
| --- | --- |
| pygame 主线程 | 动画、桌面交互、`brain.tick()` 的轻量部分 |
| QQ 接收线程 | 网关连接、事件接收 → `brain.submit_event` |
| 认知 worker | 轮询唤醒理由、调 LLM、解析 Decision；一次只有一个 |
| 能力执行 | 网页、文件、系统调用等耗时任务；可并发 |
| 巩固 worker | 记忆蒸馏；只提交写操作 |
| 记忆 worker | 统一串行执行 L2/L3 与索引写入 |

主线程永远不做网络、模型或大文件操作。

### 5.3 状态所有权

| 状态 | 拥有者 | 读取者 |
| --- | --- | --- |
| 环境快照 | environment | 情境装配 |
| 身体状态 | body | 自我状态、情境装配 |
| 情绪/态度/欲望 | self_state（派生，不落盘） | 情境装配 |
| L0–L3 记忆 | memory | 情境装配、巩固 |
| 事件与账本 | event_queue | 情境装配、认知 |
| 当前回合 | brain（`_turn`） | 情境装配 |
| 活动记录 | activity | 情境装配、executor |
| 出站消息状态 | executor | 情境装配 |
| 闹钟 | lifecycle | 情境装配 |

模块之间不直接写对方的状态，通过明确的接口交换数据。

---

## 6. 模块地图

| 模块 | 文档 | 负责 | 绝不负责 |
| --- | --- | --- | --- |
| 环境 environment | [01-environment](design/01-environment.md) | 聚合周围环境快照 | 判断重要性 |
| 身体 body | [02-body](design/02-body.md) | 内感受与稳态 | 决定行为 |
| 自我状态 self_state | [02-body](design/02-body.md) | 派生情绪、态度、欲望 | 阈值触发行为 |
| 记忆 memory | [03-memory](design/03-memory.md) | 存取、召回、巩固、关系 | 参与控制流 |
| 能力 capabilities | [04-capabilities](design/04-capabilities.md) | 工具注册、异步、审计 | 决定调用什么 |
| 活动 activity | [05-activity](design/05-activity.md) | 持续行为的进度、暂停、恢复 | 自行发起新行为 |
| 生命循环 lifecycle | [06-lifecycle](design/06-lifecycle.md) | 状态时钟、闹钟、信号 | 任何行为决策 |
| 事件队列 event_queue | [07-event_queue](design/07-event_queue.md) | 事件入队、账本、持久化 | 决定何时被处理 |
| 帧生成 frame_generator | [08-frame_generator](design/08-frame_generator.md) | 组装情境与 prompt | 修改各模块状态 |
| 认知决策 thinker | [09-thinker](design/09-thinker.md) | 调用 LLM、产出 Decision | 决定何时被调用 |
| 执行 executor | [10-executor](design/10-executor.md) | 把 Decision 落地为输出 | 修改 Decision |

### 6.1 目录树

```text
manager/
├── brain.py                    # 组合根与对外门面：装配、线程、帧循环
├── save_manager.py             # 本地存档的 JSON 混淆读写
├── design/                     # 01–10 设计文档
├── shared/
│   ├── types.py                # 数据类、枚举、错误（跨模块契约）
│   ├── config.py               # BrainConfig / BrainPaths 与全部可调参数
│   ├── logging_setup.py        # 日志配置
│   ├── timeutil.py             # 时间、ISO、衰减、钳制
│   ├── jsonutil.py             # JSON 提取与容错
│   └── llm_client.py           # LLM HTTP 传输（重试/超时/JSON 模式）
├── environment/
│   ├── environment.py          # EnvironmentState 聚合
│   ├── presence.py             # 空闲时长与在场
│   └── foreground.py           # 前台窗口粗分类
├── body/
│   ├── body.py                 # BodyState 与稳态
│   └── self_state.py           # 心情、态度、欲望（派生）
├── memory/
│   ├── memory.py               # 记忆门面与 L1
│   ├── store.py                # L2/L3 文件读写
│   ├── index.py                # MEMORY.md 索引
│   ├── recall.py               # 关键词召回（装配层每帧调用）
│   ├── compression.py          # L1 超限压缩并归档到 L2
│   ├── consolidation.py        # 巩固任务（只产出写操作）
│   └── worker.py               # 记忆写入串行队列
├── capabilities/
│   ├── registry.py             # 能力注册与 schema
│   ├── executor.py             # 参数校验、异步、超时、结果截断
│   ├── audit.py                # 审计日志
│   ├── artifacts.py            # artifact 存取
├── activity/
│   ├── activity.py             # ActivityService 生命周期与检查点
│   └── store.py                # activities.json
├── lifecycle/
│   ├── lifecycle.py            # 状态时钟、唤醒排布、due()
│   ├── alarms.py               # next_wake_in / wake_when 存储与求值
│   └── signals.py              # 显著变化检测
├── event_queue/
│   └── event_queue.py          # 事件账本与持久化恢复
├── frame_generator/
│   ├── frame_generator.py      # 帧生产者：聚合唤醒理由与只读视图
│   ├── situation.py            # SituationBuilder
│   ├── personality.py          # 人设卡：personality.md 的读取与默认值
│   └── prompt.py               # 人设 + 机制层 + 情境（能力与协议走 tools 参数）
├── thinker/
│   ├── thinker.py              # 调 LLM、解析并校验 Decision
│   └── decision.py             # DecisionParser / repair_message
└── executor/
    ├── executor.py             # Decision → 输出与状态变化
    └── chat_sender.py          # 被动/主动发送与投递状态
```

分层是单向的：`shared` ← 状态模块 ← `frame_generator`/`thinker`/`executor` ← `brain` ← `main`。`bot/` 只与 `brain.submit_event` 和 `bridge` 交互。

### 6.2 依赖方向

```text
main ──▶ brain ──▶ event_queue / lifecycle ──▶ frame_generator ──▶ thinker ──▶ llm_client
  │        │            │
  │        │            ├──▶ environment / body / self_state
  │        │            ├──▶ memory / activity
  │        │            ├──▶ capabilities ──▶ tools
  │        │            └──▶ executor ──▶ bot / body
  │        └──▶ shared（所有模块都可依赖）
  └──▶ bot（QQ 传输）──▶ qq_adapter ──▶ brain.submit_event
```

规则：

1. 依赖只能向下：`shared` 不导入业务模块；状态模块不导入 `brain`。
2. `frame_generator` / `thinker` 只读：不写记忆、不改活动、不标事件；只返回 Decision。
3. `executor` 只写输出：不改写 Decision，不重新判断。
4. `brain` 只决定"何时跑一帧"，不决定"跑完做什么"。
5. `tools/` 的纯函数不导入 `manager/`；记忆不提供能力，全部由装配层与巩固任务前置处理。
6. 禁止循环依赖：`memory/consolidation.py` 只依赖 `shared/llm_client.py`、`memory/store.py` 与 `memory/worker.py`。

### 6.3 文件职责与输入输出

**入口与身体**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `main.py` | 应用生命周期、pygame 窗口、托盘、每帧循环；把桌面输入交给 Brain | pygame/OS 事件、存档、`.env` | 渲染帧、`bridge` 快照、Brain 的启动/停止/tick 调用 |
| `constants/constants.py` | 读取 `.env`，提供身份、路径、模型与运行参数 | `.env`、文件系统 | 模块级常量 |
| `components/bunny.py` | Alice 的动画、物理与**饱食度权威**；自主播放动画 | `delta`、喂食/自主进食/拖放、身体状态 | 精灵帧、饱食度快照 |
| `manager/save_manager.py` | 本地存档的 JSON 混淆读写 | `dict` / 文件 | `dict` / 文件 |

**shared**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `shared/types.py` | 所有跨模块数据类与枚举：`Event`、`Decision`、`BodyState`、`EnvironmentState`、`SelfState`、`Activity`、`Capability`、`CapabilityResult`、`MemoryWriteOp`、`MemoryView`、`BunnySnapshot`、`AuditRecord`、`WakeReason`、错误；附带 `to_dict`/`from_dict` | 构造参数 / JSON dict | 类型实例 / JSON dict |
| `shared/config.py` | 从 constants 组装 `BrainConfig` / `BrainPaths`，提供全部可调参数 | constants / env | `BrainConfig` / `BrainPaths` |
| `shared/llm_client.py` | LLM HTTP 传输：超时、有限重试、退避、JSON 模式（模型不支持时自动降级） | `messages`、超时、`json_mode` | 文本内容或 `LLMError` |

**environment / body**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `environment/environment.py` | 每个状态时钟聚合一次环境快照 | `bunny_snapshot`、OS API | `EnvironmentState` |
| `environment/presence.py` | 电脑前是否有人 | `GetLastInputInfo` | `idle_seconds`、`someone_present` |
| `environment/foreground.py` | 前台窗口粗分类与持续时长 | `pywinctl` 窗口信息 | `foreground_title`、dwell |
| `body/body.py` | 维护 `BodyState`；读取饱食度快照，更新精力/社交 | 环境、饱食度快照、互动、`dt`、忙碌时长 | `BodyState` |
| `body/self_state.py` | 从身体与记忆派生心情/态度/欲望；只读 | `BodyState`、记忆视图、当前时间 | `SelfState` |

**memory / capabilities**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `memory/memory.py` | 记忆门面：L1 工作记忆、读取、召回、提交写入；不判断内容价值 | 查询、写操作、当前回合 | 记忆视图、召回结果、写操作 id |
| `memory/store.py` | L2/L3 文件布局、frontmatter 解析、原子写 | 路径、内容、元数据 | 记录对象、写入结果 |
| `memory/index.py` | `MEMORY.md` 的解析与增删改 | 索引行、写操作 | 索引列表、更新后的文件 |
| `memory/recall.py` | 按查询扫描索引、L2、朋友档案 | `query`、`limit` | `[{ref, summary, valence, arousal}]` |
| `memory/consolidation.py` | 组装并执行巩固 LLM 任务；只返回写操作 | 近期 L2、L3、prompt | `list[MemoryWriteOp]` |
| `memory/worker.py` | 所有记忆写入的串行队列：去重、索引、重试 | `MemoryWriteOp` | 写入结果 |
| `capabilities/registry.py` | 注册能力，输出 schema 清单 | handler、schema、超时 | `Capability` 表、能力清单 |
| `capabilities/executor.py` | 参数校验、同步/异步派发、超时、结果截断 | `CapabilityCall` | `CapabilityResult` |
| `capabilities/audit.py` | 追加式审计日志 | 调用元数据 | JSONL 行 |
| `capabilities/artifacts.py` | 大结果的存取与引用解析 | 文本/字节、`artifact://` ref | ref / 分片 |
| `capabilities/audit.py` | 追加式审计日志 | 调用元数据 | JSONL 行 |

**activity / lifecycle / event_queue**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `activity/activity.py` | 活动生命周期：start/pause/resume/complete/abandon、`steps_used`、检查点 | 活动动作、`activity_update`、当前时间 | 当前 `Activity`、检查点 |
| `activity/store.py` | `activities.json` 的读写与恢复 | `Activity` 列表 | 文件 / 恢复后的列表 |
| `lifecycle/lifecycle.py` | 状态时钟、唤醒排布（`schedule`）、到期理由（`due`） | `delta`、`next_wake_in`、`wake_when` | 信号变化、唤醒理由 |
| `lifecycle/alarms.py` | `next_wake_in`、`wake_when` 的存储和谓词求值 | 闹钟、身体、环境 | 到期闹钟 |
| `lifecycle/signals.py` | 连续信号的死区、迟滞、合并 | 前后 `BodyState`/`EnvironmentState` | `signal` 变化 |
| `event_queue/event_queue.py` | 事件账本；只追加，只有 `handled` 改变状态 | `Event`、`handled` 列表 | 未处理事件列表、账本 |

**frame_generator / thinker / executor / brain**

| 文件 | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `frame_generator/frame_generator.py` | 聚合唤醒理由与只读视图，产出一帧 | 唤醒理由、各模块只读视图 | 一帧数据 |
| `frame_generator/situation.py` | 装配情境：环境、身体、自我、记忆、活动、收件箱、能力、约束 | 各模块只读视图 | `Situation` |
| `frame_generator/prompt.py` | 组装 L0 与情境 JSON | `Situation`、人设 | `messages` |
| `shared/tools_spec.py` | 把能力与动作编译成工具表；`tool_calls` → Decision 字典 | 能力清单、`tool_calls` | 工具表、Decision 字典 |
| `thinker/thinker.py` | 执行一次认知：调 LLM、解析并校验 Decision | 一帧 | `Decision` + 帧状态 |
| `thinker/decision.py` | 从文本提取 JSON，校验字段/动作/参数，钳制范围 | 原始文本、事件 id 集合 | `Decision` 或 `DecisionError` |
| `executor/executor.py` | 按固定顺序把 Decision 落地；不修改 Decision | `Decision` | 发送、活动/状态更新、记忆提交、唤醒排程 |
| `executor/chat_sender.py` | 被动回复优先、失败降级主动、投递状态 | 目标、文本、`msg_id` | `DeliveryStatus` |
| `manager/brain.py` | 组合根与对外门面：创建对象、注入依赖、启动线程、帧循环 | 事件、tick、生命周期调用 | 已装配的运行时、帧调度、事件接收 |

**Brain 对外接口（组合根）**

```python
class Brain:
    def start(self) -> None: ...
    def stop(self, reason: str) -> None: ...
    def tick(self, delta: float) -> None: ...          # 主线程，轻量，不调 LLM
    def submit_event(self, event: Event) -> Event: ... # 任意线程，只入队并唤醒
    def snapshot(self) -> dict: ...                    # satiety 等只读快照
```

- `submit_event` 只入队并通知唤醒；不直接跑帧。
- `tick` 只推进状态时钟；不调用 LLM。
- `start/stop` 管理线程、持久化与恢复。
- `main.py` 与 `bot/` 只依赖这几个方法；不直接碰内部模块。

### 6.4 数据流

**A. 外部消息进入**

```text
QQ 网关
  └─ QQClient 收到 C2C_MESSAGE_CREATE
       └─ QQAdapter.message(raw)
            → Event(kind="message", source={friend_id},
                    payload={text, msg_id})
            → Brain.submit_event(event)
                 ├─ EventQueue.add(event)             # 未处理
                 └─ _notify(EVENT)                    # 立即唤醒认知 worker
```

- Adapter 不判断重要性、不过滤、不合并内容；只做协议归一化。
- `msg_id` 保留在 payload，供被动回复使用。
- `friend_id` 即 `user_openid`，一条消息只对应一个会话。

**B. 状态时钟**

```text
brain.tick(delta)  # 每 2 秒，由 main 主线程驱动
  → lifecycle.tick(delta)
       ├─ environment.tick(snapshot)     → EnvironmentState
       ├─ body.tick(env, interactions, dt, busy_seconds) → BodyState
       └─ signals.evaluate(prev, curr)   → [SignalChange]
  → brain 把 SignalChange 转成 Event(kind="signal") 入队
```

- 状态时钟不调用 LLM。
- `main.py` 每帧把饱食度写入 `bridge` 快照；`body` 只读它，不另建计数器。

**C. 唤醒与一帧**

```text
认知 worker 循环（每 0.5 秒）：
  reasons = lifecycle.due() + _take_pending_reasons()
  if reasons: brain._run_frame(reasons, unhandled)

_run_frame:
  → frame_generator.build(reasons, 只读视图)   → 帧（situation + messages）
  → thinker.run(帧)                            → Decision
  → executor.apply(decision)                   → effects
  → event_queue.mark_handled(decision.handled)
  → (executor 内部) lifecycle.schedule(next_wake_in, wake_when)
```

- 帧失败（网络/限流/超时/解析失败）：事件保持 `unhandled`，不写记忆，错误写进下一帧情境；同时按 `5s / 15s / 60s / 180s` 退避排一次重试唤醒（用尽后退回定期心跳，避免持续故障时烧钱）。
- `next_wake_in` / `wake_when` 只在**终态动作**后生效；`tool.call` 帧由能力结果触发下一帧。

**D. Decision 执行顺序**

```text
1. 校验 Decision
2. chat.reply / chat.reach_out → ChatSender → QQ
3. tool.call       → CapabilityExecutor
4. activity.* / activity_update → ActivityService
5. handled         → EventQueue
6. next_wake_in / wake_when     → Lifecycle.schedule
7. 终态帧结束后：机制自动写一条 L2 情景（frame_episode）
```

- 发送失败不回滚记忆；`executor.last_delivery` 会进入下一帧情境的 `delivery` 字段。
- 能力异步完成/失败 → `capability_done` / `capability_failed` 事件 → 事件队列 → 下一次唤醒。

**E. 记忆写入与巩固**

```text
机制自动情景（终态帧）/ consolidation（L3 语义）
  → memory worker.enqueue(op)
       ├─ store.write(...)          # L2/L3
       ├─ index.update(...)         # MEMORY.md
       └─ 失败重试；持续失败发 system 事件
```

- 巩固是独立 LLM 任务：不读写当前回合 L1，不产出 Decision，只提交写操作批次。
- L1 由情境装配读取；摘要与最近对话同步落盘到 `state/l1.json`。

**F. 持久化与恢复**

```text
event_queue / lifecycle / activity / body 各自原子写盘
（.tmp + os.replace）

启动：
  加载账本、闹钟、身体状态
  → 活动 active 恢复为 paused
  → 在途能力标记 unknown
  → 第一帧情境带 system 事件“启动/恢复”
```

- 记忆由 memory worker 自己落盘，不经过独立持久化 worker。
- 审计日志由 `capabilities/audit.py` 追加，不参与恢复。

### 6.5 线程、队列与状态所有权

| 线程 | 拥有者 | 入口 | 出口 |
| --- | --- | --- | --- |
| pygame 主线程 | `main.py` | pygame 事件 | 渲染、`bridge` 快照、`brain.tick` |
| QQ 接收线程 | `bot/qq_client.py` | WebSocket | 原始 dispatch → `qq_adapter` |
| 认知 worker | `brain` + `thinker` | 唤醒信号 | Decision → `executor` |
| 能力执行 | `capabilities/executor.py` | 能力派发 | `capability_done/failed` 事件 |
| 巩固 worker | `memory/consolidation.py` | 巩固周期 | `MemoryWriteOp` 批次 |
| 记忆 worker | `memory/worker.py` | 记忆写队列 | L2/L3 与索引落盘 |

| 队列 | 生产者 | 消费者 | 语义 |
| --- | --- | --- | --- |
| 事件账本 `event_queue` | QQ adapter、能力执行器、活动、闹钟、信号 | 情境装配 | 只追加；`unhandled/handled` |
| 记忆写队列 | 终态帧情景、L1 压缩、巩固 worker | 记忆 worker | 串行、有序 |
| 唤醒信号 `_pending_reasons` | adapter、闹钟、信号、时钟 | 认知 worker | 合并后的唤醒 |

状态所有权：

| 状态 | 拥有者 | 只读者 |
| --- | --- | --- |
| `EnvironmentState` | environment | situation |
| `BodyState` | body | self_state、situation |
| `SelfState` | self_state（派生，不落盘） | situation |
| L1 | memory | situation |
| L2/L3 | memory worker | memory / situation（索引） |
| 事件与账本 | event_queue | situation |
| 活动 | activity | situation、executor（通过动作） |
| 闹钟 | lifecycle | situation |
| Decision | thinker（瞬态） | executor |

---

## 7. 跨模块契约

### 7.1 事件信封

```json
{
  "id": "ev_000123",
  "kind": "message | capability_done | capability_failed |
           activity_checkpoint | alarm | signal | system",
  "at": "2026-10-20T21:03:11+08:00",
  "source": {"friend_id": "u_123"},
  "payload": {"text": "在吗"},
  "arrived_during": {"type": "activity", "id": "act_17"},
  "status": "unhandled | handled"
}
```

- `arrived_during` 标出"这条消息是在 Alice 忙什么时追加的"。
- 事件只被追加，不被机制删除；状态由 LLM 在 Decision 的 `handled` 中标记。

| `kind` | `payload` 最小字段 | 产生者 |
| --- | --- | --- |
| `message` | `{text, msg_id?}` | `qq_adapter` |
| `capability_done` | `{call_id, capability, ok: true, result_summary, result_ref?}` | `capabilities/executor` |
| `capability_failed` | `{call_id, capability, ok: false, error, reason, detail}` | `capabilities/executor` |
| `activity_checkpoint` | `{activity_id, kind, progress, steps_used, reason}` | `activity` / `brain` |
| `alarm` | `{alarm_id, type, fired_at}` | `lifecycle` |
| `signal` | `{signal, from, to}` | `lifecycle/signals` |
| `system` | `{event}`（启动、关闭、异常、能力结果） | `brain` |

### 7.2 决策信封

模型不再输出这段 JSON：它**调用工具**，`shared/tools_spec.py` 把 `tool_calls` 翻译成下面这个内部结构，再交给 `DecisionParser` 校验。信封留在内部，是为了让校验与执行只有一份实现。

```json
{
  "action": "chat.reply",
  "target": {"friend_id": "u_123"},
  "args": {},
  "calls": [],
  "activity_update": null,
  "handled": ["ev_000123"],
  "next_wake_in": 30,
  "wake_when": []
}
```

动作空间（9 个）：

| 动作 | 含义 | 终态 |
| --- | --- | --- |
| `tool.call` | 调用一个或多个能力，结果下一帧返回 | 否 |
| `chat.reply` | 回复某个会话 | 是 |
| `chat.reach_out` | 主动联系某位朋友 | 是 |
| `activity.start` | 开始一段持续活动 | 是 |
| `activity.pause` | 暂停当前活动；`args.resume_when` 可选 | 是 |
| `activity.resume` | 继续当前活动 | 是 |
| `activity.complete` | 结束当前活动并标记完成 | 是 |
| `activity.abandon` | 结束当前活动；`args.outcome` 为 `completed`/`abandoned` | 是 |
| `idle` | 什么都不做；`next_wake_in`/`wake_when` 可选 | 是 |

- 工具调用使用 `action: "tool.call"` 与 `calls`；结果以事件返回，触发下一帧。
- 终态动作不再携带工具调用。
- `next_wake_in` / `wake_when` 只在**终态动作**后生效；`tool.call` 帧不排闹钟，下一帧由 `capability_done/failed` 触发。
- `idle` 的 `next_wake_in` / `wake_when` 可选：填了表示主动预约下一次醒来；都不填则只靠事件与定期心跳唤醒。
- `activity.pause` 的 `args.resume_when` 可选：满足时只产生 `alarm` 事件并唤醒，不自动恢复。
- 决策里没有记忆字段：情景由机制在终态帧结束后自动写入，见 7.3。
- `activity_update` 只对活动帧有意义；没有当前活动时忽略。

### 7.3 状态与记忆写入

- **状态**：身体量由身体模块更新；情绪/态度/欲望是派生值，不落盘。
- **情景**：终态帧结束后，`memory/frame_episode.py` 从"这一帧真正发生的事"拼一条 L2：回复了谁、说了什么、结束了什么活动、处理了哪些事件。情绪打分取自当帧派生的 `SelfState`（`mood` / `mood_label` / 最强欲望的 `level`），不由模型自报。纯发呆（没有事件、没有动作）不写；启动帧也不写（否则每次重启都留一条"开机说了句话"）。只用工具翻东西、最后 `idle` 收尾的回合**会**写一条"探索：fs.list …"，并附上过程行——否则一整轮探索会随 idle 蒸发。
- **语义**：`memory.*` 能力可以改 `self.md` / `goals.md` / `preferences.md` / 好友档案，但不能直接写 `episodes/`。
- 所有写入经 memory worker 串行落盘与更新索引。
- **活动**：由活动模块持久化；LLM 通过 `activity.*` 动作改变其状态。

持久化文件：

| 文件 | 格式 | 内容 |
| --- | --- | --- |
| `l1.json` | JSON 对象 | L1 摘要与最近对话；每次变化原子写 |
| `body.json` | JSON 对象 | 身体状态 |
| `events.jsonl` | 一行一个 Event | 事件账本；追加写 |
| `alarms.json` | JSON 数组 | 时间闹钟与条件闹钟 |
| `activities.json` | JSON 数组 | 活动记录 |
| `pending_capabilities.json` | JSON 数组 | 在途能力（崩溃恢复用） |
| `audit.jsonl` | 一行一次调用 | 能力审计 |
| `MEMORY.md` / `episodes/` / `friends/` | Markdown + frontmatter | 记忆 |

### 7.4 唤醒契约

唤醒理由 `WakeReason.kind` 有五种：

| kind | 来源 | 说明 |
| --- | --- | --- |
| `event` | 消息 / 能力结果 / 活动检查点 / 系统事件 | 事件一律唤醒 |
| `alarm` | `next_wake_in` 到点 / `wake_when` 条件满足 / 活动恢复条件 | LLM 自定 |
| `signal` | 身体/环境显著变化 | 机制检测，只报告变化 |
| `startup` | 启动 | 第一帧 |
| `periodic` | `periodic_wake_seconds`（1800s） | 兜底心跳 |

- 机制可以"叫醒"认知，但不能决定认知醒后做什么。
- 唤醒理由只陈述事实；单原因时数组长度为 1。
- 事件永远可以提前唤醒。
- 详细规则见 [06-lifecycle](design/06-lifecycle.md)。

---

## 8. 生命周期

| 阶段 | 发生什么 |
| --- | --- |
| **启动** | 加载 L0/L3、恢复活动与事件账本、初始化身体与环境、启动线程、触发 STARTUP 帧 |
| **空闲** | 只跑状态时钟；按唤醒策略偶尔调用 LLM |
| **对话** | 消息触发帧；LLM 决定回复、忽略或继续等待；回合结束后回到空闲/活动 |
| **活动** | 活动按检查点推进；消息可插入；LLM 决定暂停、继续或放弃 |
| **关闭** | 停止接收、保存身体/活动/账本/记忆、等待在途任务或取消 |
| **崩溃恢复** | 从持久化恢复事件账本、闹钟、身体；`active` 活动恢复为 `paused`；在途能力标记 `unknown` |

---

## 9. 边界与反模式

1. **机制不得做行为决策。** 只做投递、执行、约束、持久化。
2. **状态不得变成隐藏开关。** 情绪与欲望是情境里的数字，不是代码分支。
3. **不要有特殊用户。** 所有人都是朋友，关系从记忆里长。
4. **聊天不是全部。** 没有消息时，Alice 依然有生活。
5. **沉默是合法输出。** 不回应、不行动都是选择。
6. **不要让长任务卡住意识。** 能力异步执行，帧保持短。
7. **不要用成本限制表达。** 机制不做 token 或调用次数预算。
8. **不设权限裁剪。** Alice 可以访问整台电脑；隐私只约束记忆写入。
9. **记忆不参与控制流。** 它只影响 LLM 看到什么、怎么理解。
10. **人格从记忆生长。** L0 只给身份、气质与边界，不写死喜好与台词。
11. **唤醒不是决策。** 唤醒策略只决定"何时叫醒"，不决定"醒来做什么"。
12. **单一大脑。** 不并行思考；不同会话共享同一个意识。
13. **行为不排序。** 聊天、探索、创作、思考在机制眼里没有优先级。

---

## 10. 术语表

| 术语 | 含义 |
| --- | --- |
| **事件 event** | 世界中发生的离散事情，进入事件队列 |
| **唤醒 wake** | 机制调用认知的时机 |
| **帧 frame** | 一次 LLM 调用与决策 |
| **回合 turn** | 为同一意图服务的一串帧 |
| **活动 activity** | 跨越多个回合的持续行为 |
| **情境 situation** | 交给 LLM 的结构化报告 |
| **决策 decision** | LLM 输出的结构化动作 |
| **欲望 urge** | 由身体与记忆派生的需求强度，仅作输入 |
| **能力 capability** | 可调用的工具 |
| **生命循环 lifecycle** | 非决策性的生命维持基础设施 |
| **artifact** | 大结果的磁盘引用；情境只带摘要与 ref |
| **唤醒理由 wake reason** | 机制叫醒 Alice 的事实原因 |
| **记忆 worker** | 串行执行所有 L2/L3 与索引写入的线程 |
