# 07 · 生命维持：事件、唤醒、时钟与持久化

> 机制层是非决策性的基础设施。它让 Alice 能持续、安全地活着，但不替她选择行为。
> 它唯一的"判断"是：什么时候叫醒她。

---

## 1. 职责清单

| 职责 | 说明 |
| --- | --- |
| 事件投递 | QQ 事件、后台任务结果、系统事件 → 收件箱 |
| 批量窗口 | 连珠炮消息的短稳定窗口（传输层） |
| 唤醒 | 事件 / LLM 闹钟 / 最长间隔 / 显著变化 |
| 显著变化检测 | 对连续信号做死区、迟滞、合并；只报告变化 |
| 闹钟求值 | `next_wake_in`、`wake_when` 与 `resume_when` 谓词 |
| 帧串行化 | 同一时刻只有一个认知帧 |
| 后台任务 | 能力的线程、超时、取消 |
| 持久化 | 活动、事件账本、记忆、崩溃恢复 |
| 审计与日志 | 能力访问、决策、异常 |

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 运行时循环 | `manager/mechanism/mechanism.py` |
| 收件箱与账本 | `manager/mechanism/events.py` |
| 唤醒理由聚合 | `manager/mechanism/wake.py` |
| 闹钟与谓词 | `manager/mechanism/alarms.py` |
| 显著变化检测 | `manager/mechanism/signals.py` |
| 状态时钟 / 巩固周期 | `manager/mechanism/clocks.py` |
| 落盘与恢复 | `manager/mechanism/persistence.py` |
| 运行/急停 | `manager/mechanism/control.py` |

---

## 2. 运行时

### 2.1 线程

| 线程 | 职责 |
| --- | --- |
| pygame 主线程 | 动画、桌面交互、轻量状态更新 |
| QQ 接收线程 | 网关连接、事件接收 → 收件箱 |
| 认知 worker | 一次一个认知帧；调用 LLM、解析 Decision |
| 能力 worker | 网页、文件、系统调用；可并发 |
| 巩固 worker | 记忆蒸馏；只提交写操作 |
| 记忆 worker | 统一串行执行 L2/L3 与索引写入 |
| 持久化 worker | 活动、账本、记忆的落盘 |

主线程永远不做网络、模型或大文件操作。

### 2.2 状态时钟

每 1~5 秒：

1. 更新感知快照（空闲时长、前台分类、在场感）。
2. 更新身体（饱食度读桌宠快照、精力、警觉、社交电量、昼夜）。
3. 派生自我状态（心情、态度、欲望）。
4. 求值显著变化检测。
5. 更新桌宠身体动画的底层参数。

状态时钟**不调用 LLM**。

---

## 3. 事件与收件箱

### 3.1 事件信封

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
- 状态只能由 LLM 的 `handled` 改变。
- 各 `kind` 的 `payload` 最小字段见 [brain.md §7.1](../brain.md#71-事件信封)。

### 3.2 收件箱语义

- 所有会话共用一个收件箱。
- 严格按到达顺序记录；不并行处理。
- 未处理事件会持续出现在后续情境中。
- 机制不按重要性过滤、不丢弃、不延迟投递。

### 3.3 批量窗口

连珠炮消息在到达后经过一个很短的稳定窗口（约 0.8~1.5 秒），合并为一次唤醒：

- 每条事件的原文、时间、说话人、间隔都完整保留。
- 情境里标注"这是一串连续消息"。
- 窗口只影响唤醒次数，不影响事件内容。

---

## 4. 唤醒策略

一句话：**事件一律唤醒 + LLM 自定闹钟与恢复条件 + 机制兜最长间隔 + 身体/环境只做显著变化检测。**

唤醒只回答"什么时候让 Alice 想一次"，不回答"她该做什么"。

| 来源 | 触发条件 | 条件由谁定 | 性质 |
| --- | --- | --- | --- |
| 事件 | 任一离散事件到达 | 世界 | 一律唤醒 |
| LLM 闹钟 | `next_wake_in` 到点 / `wake_when` 条件满足 | LLM | 自定 |
| 活动恢复条件 | `paused` 活动的 `resume_when` 满足 | LLM | 自定 |
| 最长间隔 | 超过 `MAX_IDLE_SECONDS` | 机制硬约束 | 兜底 |
| 身体/环境 | 显著变化检测命中 | 机制规则，可配置 | 只报告变化 |

### 4.1 事件一律唤醒

- 朋友消息（单聊）
- 后台能力任务完成或失败
- 活动检查点到达
- 闹钟到点
- 系统事件（休眠恢复、网络恢复等）
- 巩固任务完成

不做重要性筛选；全部进入收件箱并唤醒。

### 4.2 LLM 自定闹钟

```json
{
  "next_wake_in": 600,
  "wake_when": [
    {"type": "satiety_below", "args": {"value": 40}},
    {"type": "presence_changed", "args": {}},
    {"type": "activity_checkpoint", "args": {"id": "act_17"}}
  ]
}
```

- `next_wake_in`：时间闹钟，钳制到 0 ~ 86400 秒（24 小时）。
- `wake_when`：条件闹钟；v1 只支持这些谓词：
  - `satiety_below` / `energy_below`
  - `presence_changed`
  - `at_time`（绝对或相对时间点）
  - `activity_checkpoint`（指定活动到达检查点）
- 两者都可为空；为空表示只靠事件与最长间隔唤醒。
- 同一列表内多个谓词之间是 **OR**：任一满足即触发。

谓词参数：

| 谓词 | `args` | 说明 |
| --- | --- | --- |
| `satiety_below` | `{"value": 0~100}` | 饱食度首次低于阈值时触发 |
| `energy_below` | `{"value": 0~1}` | 精力首次低于阈值时触发 |
| `presence_changed` | `{}` | 在场布尔值边沿 |
| `at_time` | `{"time": "HH:MM"}` 或 `{"time": "2026-10-21T08:00:00+08:00", "repeat": "once"}` | `HH:MM` 按本地时区取下一次；ISO8601 必须带时区 |
| `activity_checkpoint` | `{"id": "act_17"}` | 指定活动到达检查点 |

- 条件闹钟触发一次后失效；需要重复时由 LLM 下一次决策重新设置。
- `next_wake_in` 只在终态动作后生效；`tool.call` 帧不排闹钟。
- 谓词是有限、可审计的白名单，不接受任意代码。
- 机制只求值，不解释意图。

### 4.3 活动的恢复条件

`activity.pause` 的 `args.resume_when` 是绑定在当前暂停活动上的一组条件，不是 `wake_when` 的别名。

| 谓词 | `args` | 说明 |
| --- | --- | --- |
| `satiety_above` | `{"value": 0~100}` | 饱食度首次高于阈值 |
| `energy_above` | `{"value": 0~1}` | 精力首次高于阈值 |
| `presence_changed` | `{}` | 在场布尔值边沿 |
| `at_time` | `{"time": "HH:MM"}` 或 ISO8601 | 到点触发 |

- 条件保存在 `Activity.resume_when`，随 `activities.json` 落盘，跨重启保留。
- 只在活动 `paused` 时求值；多个谓词之间是 OR。
- 满足时产生一条 `alarm` 事件：`payload` 带 `alarm_id=resume:<activity_id>`、`type=predicate`、`source="activity_resume"`、`activity_id` 与 `predicate`；触发一次后清空。
- **只唤醒，不自动恢复。** 状态仍是 `paused`，是否 `activity.resume` 由 LLM 决定。
- `activity.resume` / `complete` / `abandon` 都会清空未触发的条件。
- 阈值类与 `presence_changed` 以暂停时的取值为基线，只在之后由不满足变为满足时触发；暂停当刻已满足的条件被忽略。`at_time` 到点即触发（已过则取下一次）。
- 与 `wake_when` 共用谓词求值器，但白名单不同：恢复条件只接受上表四种。

### 4.4 最长间隔

`MAX_IDLE_SECONDS`（建议 20~30 分钟）保证即使没有事件、LLM 也想睡，Alice 仍会定期睁眼一次。

`body.mode == rest` 时改用 `MAX_IDLE_SECONDS_REST`（建议 2 小时）：她可以真正睡过夜，不会被半小时一次的兜底叫醒；事件仍能立即唤醒。

这是硬约束，不是行为规则；醒来后做什么仍由 LLM 决定。

### 4.5 显著变化检测

连续信号不逐帧唤醒，只在变化足够显著时报告：

| 信号 | 检测规则 | 例子 |
| --- | --- | --- |
| `satiety` | 跨过 80 / 60 / 40 / 20 分段边界 | 61 → 59 跨过 60，唤醒 |
| `energy` | 跨过 0.5 / 0.25，或 10 分钟变化超过 0.15 | 精力明显下滑 |
| `alertness` | 短窗口上升 ≥ 0.3，或突破 0.7 | 被突然刺激 |
| `social_battery` | 跨过 0.3 | 社交电量见底 |
| `circadian` | 昼夜相位切换 | 白天 → 夜晚 |
| `presence` | 布尔边沿（无人 ↔ 有人） | 有人回到电脑前 |
| `foreground_app` | 粗分类切换且持续 ≥ 30 秒 | 写代码 → 看视频 |

规则：

- **死区 + 迟滞**：避免阈值附近反复唤醒。
- **合并**：同一窗口内多个变化合并为一次唤醒，情境里逐条列出。
- **只报告变化，不解释重要性**：不说"这很重要"，不建议动作。
- **原因可见**：情境里带 `wake_reasons`，例如 `[{"kind":"signal","detail":"satiety crossed 60"}]`。
- **可配置**：阈值集中在 constants，不写成隐藏行为逻辑。

这是机制层唯一的行为相关判断。它必须保持最小、透明、可解释，并随时可被 LLM 的条件闹钟取代。

---

## 5. 帧串行与超时

- 认知 worker 一次只处理一个帧。
- 巩固不是帧：由巩固 worker 并行执行，不读写当前回合的 L1、不产出 Decision，只返回一批记忆写操作。
- 新事件在帧运行期间只入队，不抢占。
- 帧有 `FRAME_TIMEOUT_SECONDS` 硬超时；超时取消本次调用，降级为 `idle`。
- 帧结束后才允许下一帧开始。

### 5.1 并发语义

- 不同会话不并行思考；同一时刻只有一个意识。
- 能力 I/O 可以并发；结果以事件返回。
- 所有记忆写入经 memory worker 串行执行，认知帧与巩固 worker 都只提交写操作。
- 后台任务不持有认知锁。

---

## 6. 持久化与恢复

### 6.1 落盘内容

| 内容 | 格式 | 频率 |
| --- | --- | --- |
| 事件账本 | `events.jsonl`，一行一个 Event | 每次变化 |
| 活动记录 | `activities.json`，Activity 数组 | 每次更新 |
| 闹钟 | `alarms.json`，闹钟数组 | 定期与每次排程 |
| 记忆 | Markdown + frontmatter（由 memory worker 写） | 写入时 |
| 审计日志 | `audit.jsonl`，一行一次调用 | 每次能力调用 |

写入统一由持久化 worker 串行执行，使用“临时文件 + 原子替换”，避免半写文件。

### 6.2 崩溃恢复

1. 加载事件账本：未处理事件继续有效。
2. 加载活动：`active` 恢复为 `paused`，由 LLM 决定是否继续。
3. 在途能力任务标记 `unknown`，结果不可信。
4. 重新计算身体状态与心情/态度。
5. 触发一次唤醒，情境里说明"上次异常退出"。

---

## 7. 安全

| 机制 | 说明 |
| --- | --- |
| 审计 | 能力访问可追溯 |
| 急停 | 用户可从托盘/配置立即停止认知循环与在途任务 |

安全动作只中止执行，不替 LLM 决定后续行为。

---

## 8. 机制不允许做的事

- 不计算注意力分数、不排序消息重要性。
- 不做动作竞争、不按阈值触发任何行为。
- 不因为心情/精力替 LLM 改选择。
- 不丢弃它认为不重要的消息。
- 不决定什么时候回复、回不回、说多少。
- 不把显著变化检测扩展成重要性判断。
- 不把唤醒理由包装成行为建议。
- 不并行思考、不按会话分身。

机制"代决定"的地方只有两类，且都如实写进下一帧情境：

1. **安全与资源边界**：超时、取消。
2. **唤醒时机**：何时叫醒认知。

---

## 9. 启动与关闭

### 9.1 启动

1. 加载 L0、L3、索引。
2. 恢复事件账本、活动、闹钟。
3. 初始化感知与身体（读取桌宠饱食度）。
4. 启动 QQ 接收、能力 worker、巩固 worker、持久化 worker。
5. 进入认知循环；第一帧的情境标注"启动/恢复"。

### 9.2 关闭

1. 停止接收新事件。
2. 取消或等待在途能力任务。
3. 保存活动、账本、记忆。
4. 停止线程，关闭网关。
5. 记录关闭原因。

---

## 10. 可调参数

| 参数 | 含义 |
| --- | --- |
| `STATE_TICK_INTERVAL` | 状态时钟周期 |
| `SETTLE_WINDOW` | 连珠炮消息的稳定窗口 |
| `MAX_IDLE_SECONDS` | 最长发呆间隔 |
| `MAX_IDLE_SECONDS_REST` | 睡眠时的最长间隔（建议 2 小时） |
| `NEXT_WAKE_MAX_SECONDS` | `next_wake_in` 上限（86400） |
| `FRAME_TIMEOUT_SECONDS` | 单帧硬超时 |
| `WAKE_DEADBANDS` | 连续信号死区与分段 |
| `WAKE_HYSTERESIS` | 防抖迟滞 |
| `PRESENCE_EDGE_DEBOUNCE` | 在场边沿去抖 |
| `FOREGROUND_DWELL_SECONDS` | 前台分类持续时长 |
| `SIGNAL_COALESCE_WINDOW` | 多信号合并窗口 |
| `ALLOWED_WAKE_PREDICATES` | 条件闹钟谓词白名单（v1：`satiety_below` / `energy_below` / `presence_changed` / `at_time` / `activity_checkpoint`） |
| `RESUME_PREDICATES` | 活动恢复条件白名单（`satiety_above` / `energy_above` / `presence_changed` / `at_time`） |
| `CAPABILITY_TIMEOUT_DEFAULT` | 能力默认超时 |
| `EVENT_LEDGER_PATH` | 事件账本路径 |
| `ACTIVITY_PERSIST_PATH` | 活动持久化路径 |
