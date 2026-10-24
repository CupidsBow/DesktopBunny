# 01 · 环境：Alice 周围的世界

> 环境层把 Alice 身边的世界聚合成一份结构化快照。它只回答"周围现在是什么样"，
> 不判断重不重要、也不决定要不要叫醒她。所有内容都是**输入**。

---

## 1. 职责边界

| 负责 | 不负责 |
| --- | --- |
| 聚合周围环境成结构化快照 | 判断重要性、排序消息 |
| 检测在场 / 空闲 / 前台窗口 | 决定是否唤醒 Alice |
| 提供昼夜相位与系统状态 | 丢弃任何消息、推断朋友身份 |

环境层只产出数据。行为选择属于 [09-thinker](09-thinker.md)。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 环境聚合 | `manager/environment/environment.py` |
| 在场 / 空闲 | `manager/environment/presence.py` |
| 前台窗口 | `manager/environment/foreground.py` |

数据流：

```text
environment.tick(bunny_snapshot)   → EnvironmentState
body.tick(env, interactions, dt, busy_seconds) → BodyState   （见 02-body）
self_state.derive(body, memory_view, now)      → SelfState   （见 02-body）
```

tick 由状态时钟（每 2 秒）驱动，不调用 LLM；输出只进入情境装配与显著变化检测（见 [06-lifecycle](06-lifecycle.md)）。

---

## 2. 环境快照

```python
@dataclass
class EnvironmentState:
    now: datetime
    idle_seconds: float | None       # 电脑前多久没人操作
    someone_present: bool | None     # idle_seconds < presence_idle_threshold
    foreground_title: str | None     # 前台窗口标题（不落盘）
    at_night: bool                   # 昼夜相位
    system_state: dict               # 前台持续时长、网络、休眠恢复等
```

---

## 3. 信号来源

| 信号 | 来源 | 说明 |
| --- | --- | --- |
| 消息 | QQ 网关 → `bot/qq_adapter.py` | 由适配器归一化为事件直接进事件队列；环境层不处理消息内容 |
| 是否有人在 | `GetLastInputInfo`（ctypes） | 只回答"电脑前有没有人"，不猜测身份 |
| 前台窗口 | `pywinctl` | 只取窗口标题原文；不落盘，只作粗比较 |
| 时间 | 系统时钟 | 绝对时间、昼夜相位 |

---

## 4. 隐私

- 窗口标题**不落盘**，只用于检测"前台窗口改变"。
- 文件内容由 [04-capabilities](04-capabilities.md) 的 `fs.*` 读取；环境层不保留。
- 环境层不记录键盘输入、剪贴板、密码框内容。

---

## 5. 环境不做的事

- 不计算"这条消息重要吗"。
- 不决定是否唤醒 Alice。
- 不丢弃任何消息。
- 不对朋友做身份推断（亲疏来自记忆）。

---

## 6. 显著变化信号

环境与身体的连续信号不逐帧唤醒认知，只在变化足够显著时报告。本文只定义"环境提供哪些信号"；检测规则、死区、迟滞与合并属于 [06-lifecycle](06-lifecycle.md)。

| 信号 | 定义 |
| --- | --- |
| `presence` | 布尔边沿（无人 ↔ 有人），带 `presence_edge_debounce` 去抖 |
| `foreground_title` | 窗口标题改变且持续 ≥ `foreground_dwell_seconds`（30 秒） |

---

## 7. 失败与降级

| 情况 | 处理 |
| --- | --- |
| 读取不到空闲时长 | `someone_present = None`，情境里标注未知 |
| 前台窗口无法识别 | `foreground_title` 为空，不触发改变检测 |
| 系统时间跳变 | 重新计算昼夜相位，不补算欲望 |

降级信息必须如实出现在情境里，不伪造状态。

---

## 8. 可调参数

| 参数 | 含义 |
| --- | --- |
| `presence_idle_threshold` | 多久无操作算"无人"（60s） |
| `presence_edge_debounce` | 在场边沿去抖（2s，由 lifecycle 使用） |
| `foreground_dwell_seconds` | 前台窗口持续时长（30s，由 lifecycle 使用） |
