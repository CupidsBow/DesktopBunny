# 06 · 生命循环：状态时钟、唤醒与信号检测

> 生命循环是非决策性的基础设施。它让 Alice 能持续、安全地活着，但不替她选择行为。
> 它唯一的"判断"是：什么时候叫醒她。

---

## 1. 职责清单

| 职责 | 说明 |
| --- | --- |
| 状态时钟 | 低频 tick（每 2 秒）推进身体/环境、派生自我状态 |
| 唤醒聚合 | 收集到期闹钟、显著变化信号与定期心跳，产出唤醒理由 |
| 闹钟求值 | `next_wake_in`、`wake_when` 与 `resume_when` 谓词 |
| 显著变化检测 | 对连续信号做死区、迟滞、合并；只报告变化 |
| 定期心跳 | `periodic_wake_seconds` 兜底唤醒 |

事件入队、账本与持久化属于 [07-event_queue](07-event_queue.md)。认知帧的执行与校验属于 [09-thinker](09-thinker.md)。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 生命循环、唤醒聚合 | `manager/lifecycle/lifecycle.py` |
| 闹钟与谓词 | `manager/lifecycle/alarms.py` |
| 显著变化检测 | `manager/lifecycle/signals.py` |

---

## 2. 状态时钟

每 `state_tick_interval`（2 秒）：

1. 更新环境快照（空闲时长、前台窗口、在场感）。
2. 更新身体（饱食度读桌宠快照、精力、社交电量、昼夜）。
3. 派生自我状态（心情、态度、欲望）。
4. 求值显著变化检测。
5. 更新桌宠身体动画的底层参数。

状态时钟**不调用 LLM**。身体状态按 `body_save_interval`（60 秒）节流落盘。

---

## 3. 唤醒策略

一句话：**事件一律唤醒 + LLM 自定闹钟与恢复条件 + 机制定期心跳兜底 + 身体/环境只做显著变化检测。**

唤醒只回答"什么时候让 Alice 想一次"，不回答"她该做什么"。

| 来源 | 触发条件 | 条件由谁定 | 性质 |
| --- | --- | --- | --- |
| 事件 | 任一离散事件到达 | 世界 | 一律唤醒 |
| LLM 闹钟 | `next_wake_in` 到点 / `wake_when` 条件满足 | LLM | 自定 |
| 活动恢复条件 | `paused` 活动的 `resume_when` 满足 | LLM | 自定 |
| 定期心跳 | 超过 `periodic_wake_seconds` | 机制硬约束 | 兜底 |
| 身体/环境 | 显著变化检测命中 | 机制规则，可配置 | 只报告变化 |

### 3.1 事件一律唤醒

- 朋友消息（单聊/群聊）
- 后台能力任务完成或失败
- 活动检查点到达
- 闹钟到点
- 系统事件（休眠恢复、网络恢复等）
- 巩固任务完成

不做重要性筛选；全部进入事件队列并唤醒。

### 3.2 LLM 自定闹钟

```json
{
  "next_wake_in": 600,
  "wake_when": [
    {"type": "satiety_below", "args": {"value": 40}},
    {"type": "presence_changed", "args": {}}
  ]
}
```

- `next_wake_in`：相对秒数，钳制到 0 ~ `next_wake_max_seconds`（86400 秒 = 24 小时）。
- `wake_when`：条件闹钟；谓词白名单（`allowed_wake_predicates`）：
  - `satiety_below` / `energy_below`
  - `presence_changed`
  - `at_time`（绝对或相对时间点）
  - `activity_checkpoint`（见下方注意）
- 两者都可为空；为空表示只靠事件与定期心跳唤醒。
- 同一列表内多个谓词之间是 **OR**：任一满足即触发。

谓词参数：

| 谓词 | `args` | 说明 |
| --- | --- | --- |
| `satiety_below` | `{"value": 0~100}` | 饱食度首次低于阈值时触发 |
| `energy_below` | `{"value": 0~1}` | 精力首次低于阈值时触发 |
| `presence_changed` | `{}` | 在场布尔值边沿 |
| `at_time` | `{"time": "HH:MM"}` 或 `{"time": "2026-10-21T08:00:00+08:00", "repeat": "once"}` | `HH:MM` 按本地时区取下一次；ISO8601 必须带时区 |
| `activity_checkpoint` | `{"id": "act_17"}` | **预留但未实现**（见下） |

> **注意**：`activity_checkpoint` 是白名单里的占位谓词，当前 `AlarmStore._is_due` 对它直接返回 False（由活动模块自行负责检查点）。活动检查点实际由活动模块通过 `brain._emit_checkpoint` 发事件触发唤醒，**不走这个谓词**。LLM 写 `wake_when:[{"activity_checkpoint": ...}]` 会得到一个永不触发的谓词闹钟。当前真正可用的条件谓词是 `satiety_below` / `energy_below` / `presence_changed` / `at_time`。

规则：

- 条件闹钟触发一次后失效；需要重复时由 LLM 下一次决策重新设置。
- `next_wake_in` 只在终态动作后生效；`tool.call` 帧不排闹钟。
- 谓词是有限、可审计的白名单，不接受任意代码。
- 机制只求值，不解释意图。

### 3.3 活动的恢复条件

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

### 3.4 定期心跳

`periodic_wake_seconds`（1800 秒 = 30 分钟）保证即使没有事件、LLM 也选择 idle，Alice 仍会定期睁眼一次。

这是硬约束，不是行为规则；醒来后做什么仍由 LLM 决定。

> 旧版用 `MAX_IDLE_SECONDS` 与睡眠态的 `MAX_IDLE_SECONDS_REST`；醒/睡二元状态已移除，现在只有单一的 `periodic_wake_seconds`。

### 3.5 显著变化检测

连续信号不逐帧唤醒，只在变化足够显著时报告：

| 信号 | 检测规则 | 例子 |
| --- | --- | --- |
| `satiety` | 跨过 80 / 60 / 40 / 20 分段边界 | 61 → 59 跨过 60，唤醒 |
| `energy` | 跨过 0.5 / 0.25，或 10 分钟变化超过 0.15 | 精力明显下滑 |
| `social_battery` | 跨过 0.3 | 社交电量见底 |
| `circadian` | 昼夜相位切换 | 白天 → 夜晚 |
| `presence` | 布尔边沿（无人 ↔ 有人） | 有人回到电脑前 |
| `foreground_title` | 窗口标题改变且持续 ≥ 30 秒 | 写代码 → 看视频 |

规则：

- **死区 + 迟滞**：避免阈值附近反复唤醒。
- **合并**：同一窗口内多个变化合并为一次唤醒，情境里逐条列出。
- **只报告变化，不解释重要性**：不说"这很重要"，不建议动作。
- **原因可见**：情境里带 `wake_reasons`，例如 `[{"kind":"signal","detail":"satiety crossed 60"}]`。
- **可配置**：阈值集中在 `config.wake_deadbands`，不写成隐藏行为逻辑。

这是生命周期层唯一的行为相关判断。它必须保持最小、透明、可解释，并随时可被 LLM 的条件闹钟取代。

---

## 4. 唤醒理由

生命周期把到期闹钟、显著变化信号与定期心跳封装成 `WakeReason`（`EVENT` / `ALARM` / `SIGNAL` / `STARTUP` / `PERIODIC`），交给认知 worker 处理。事件入队由 [07-event_queue](07-event_queue.md) 负责；生命周期只负责聚合与"到期"判断。

---

## 5. 生命周期不做的事

- 不计算注意力分数、不排序消息重要性。
- 不做动作竞争、不按阈值触发任何行为。
- 不因为心情/精力替 LLM 改选择。
- 不把显著变化检测扩展成重要性判断。
- 不把唤醒理由包装成行为建议。

生命周期"代决定"的地方只有唤醒时机：何时叫醒认知。

---

## 6. 可调参数

| 参数 | 含义 |
| --- | --- |
| `state_tick_interval` | 状态时钟周期（2s） |
| `body_save_interval` | 身体状态落盘节流（60s） |
| `periodic_wake_seconds` | 定期心跳间隔（1800s） |
| `next_wake_max_seconds` | `next_wake_in` 上限（86400） |
| `wake_deadbands` | 连续信号死区与分段 |
| `wake_hysteresis` | 防抖迟滞（0.02） |
| `presence_edge_debounce` | 在场边沿去抖（2s） |
| `foreground_dwell_seconds` | 前台窗口持续时长（30s） |
| `signal_coalesce_window` | 多信号合并窗口（5s） |
| `allowed_wake_predicates` | 条件闹钟谓词白名单 |
