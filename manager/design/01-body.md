# 01 · 感知、身体与自我状态

> 本文回答三个问题：Alice 感知到什么？她身体里发生着什么？这些状态如何变成她看到的"自己"？
> 所有内容都是**输入**，不直接决定行为。

---

## 1. 职责边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `perception.py` | 把环境转成结构化快照与事件 | 判断重要性、排序消息 |
| `body.py` | 维护内感受与稳态 | 决定行为、触发动作 |
| `self_state.py` | 从身体与记忆派生情绪、态度、欲望 | 阈值触发、隐藏开关 |

三者都只产出数据。行为选择属于 [03-cognition](03-cognition.md)。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 感知聚合 | `manager/perception/perception.py` |
| 在场 / 空闲 | `manager/perception/presence.py` |
| 前台分类 | `manager/perception/foreground.py` |
| 身体与稳态 | `manager/body/body.py` |
| 心情 / 态度 / 欲望 | `manager/body/self_state.py` |

数据流：

```text
Perception.tick(bunny_snapshot, qq_status) → Perception
Body.tick(perception, interactions, dt)     → BodyState
SelfState.derive(body, memory_view, now)    → SelfState
```

三步都由状态时钟驱动，不调用 LLM；输出只进入情境装配与显著变化检测。

---

## 2. 感知 perception.py

### 2.1 数据

```python
@dataclass
class Perception:
    now: datetime
    idle_seconds: float          # 电脑前多久没人操作
    someone_present: bool        # idle_seconds < PRESENCE_IDLE_THRESHOLD
    foreground_app: str | None   # 前台窗口的粗分类
    at_night: bool               # 昼夜相位
    qq_connected: bool           # QQ 网关是否在线
    system_state: dict           # 网络、休眠恢复等
```

### 2.2 信号来源

| 信号 | 来源 | 说明 |
| --- | --- | --- |
| 消息 | QQ 网关 → `bot/qq_adapter.py` | 由适配器归一化为事件直接进收件箱；感知层不处理消息内容 |
| 是否有人在 | `GetLastInputInfo`（ctypes） | 只回答"电脑前有没有人"，不猜测身份 |
| 前台应用 | `pywinctl` | 只取粗分类：写代码 / 浏览 / 看视频 / 游戏 / 其他 |
| 时间 | 系统时钟 | 绝对时间、昼夜相位 |
| 系统事件 | OS / 进程 | 休眠恢复、网络恢复、关机请求 |

### 2.3 隐私

- 窗口标题**不落盘**，只保留粗分类。
- 文件内容由 [05-capabilities](05-capabilities.md) 的 `fs.*` 读取；感知层只保留粗分类。
- 感知层不记录键盘输入、剪贴板、密码框内容。

### 2.4 感知不做的事

- 不计算"这条消息重要吗"。
- 不决定是否唤醒 Alice。
- 不丢弃任何消息。
- 不对朋友做身份推断（亲疏来自记忆）。

---

## 3. 身体 body.py

### 3.1 数据

```python
@dataclass
class BodyState:
    satiety: float          # 0~100，饱食度
    energy: float           # 0~1，精力
    alertness: float        # 0~1，警觉度
    social_battery: float   # 0~1，社交电量
    circadian: float        # 0~1，昼夜活动基线
    presence: bool          # 电脑前是否有人
    mode: str               # awake / rest
```

### 3.2 各维度的含义

| 维度 | 升高 | 降低 | 主要影响（作为输入） |
| --- | --- | --- | --- |
| `satiety` | 吃东西 | 时间流逝 | 饥饿感 |
| `energy` | 休息、睡眠 | 活动、熬夜 | 愿不愿意做长活动、回复长短 |
| `alertness` | 刺激、强情绪、突然事件 | 时间衰减 | 警觉程度、反应倾向 |
| `social_battery` | 独处、安静 | 社交互动 | 想不想说话、社交耐受 |
| `circadian` | 白天 | 深夜 | 活动量、睡眠倾向 |
| `presence` | 有人回到电脑前 | 人离开 | 是否适合主动开口 |

### 3.3 稳态模型

```
setpoint_i = 该维度的舒适点
deficit_i  = clamp(setpoint_i - state_i, 0, 1)
d(state_i)/dt = recovery_i * (1 - state_i) - consumption_i(activity, interaction)
```

- **饱食度以 `components/bunny.py` 为权威**：每 216 秒 -1，喂食或自主进食 +20。`body.py` 只读快照，不另建计数器。
- **精力**：白天缓慢消耗，深夜快速恢复；持续活动额外消耗。
- **社交电量**：每完成一次社交互动扣一点，独处时恢复。
- **警觉度**：受刺激后上升，按分钟级半衰期衰减。
- **昼夜节律**：平缓曲线，深夜约 0.2，白天约 0.9，傍晚小高峰。

### 3.4 身体的边界

- 身体不产生行为：饿了不会自动说话，困了不会自动睡。
- 身体不写记忆：状态只被读取，不被固化成语义结论。
- 身体可以被外部或自己改变：喂食、点击互动、拖动文件等桌宠事件，以及 LLM 的 `eat` 动作，都会更新身体量。
- `mode` 记录醒着还是睡着：`rest` 设为 `rest`；聊天、活动等任何非休息动作恢复为 `awake`；`idle` 保持原状。

---

## 4. 自我状态 self_state.py

自我状态是从身体和记忆中**派生**的只读视图，不落盘。

### 4.1 心情 mood

```
mood = Σ valence_i * decay(age_i) / Σ decay(age_i)
```

- 聚合最近窗口内的情景记忆（建议 4 小时）。
- 半衰期约 2 小时：心情是短时的。
- 输出 `[-1, 1]` 的连续值与一个语义标签（开心 / 平静 / 低落 / 烦躁 / 兴奋）。
- 与态度分离：对某位朋友不满，不代表整体心情差。

### 4.2 态度 attitude

```
w_i = intensity_i * decay(age_i)          # intensity = |valence| * (0.5 + arousal)
attitude(friend) = Σ valence_i * w_i / Σ w_i
confidence      = Σ w_i
```

- 只针对**说话人**，不针对会话。
- 半衰期约 7 天：亲疏的记忆尺度。
- 置信度表示证据量：一次好评不足以定义关系。
- 连续值映射到离散关系等级：

先看置信度，再看连续值：

| 等级 | 条件 |
| --- | --- |
| 陌生 | `confidence < 0.2`（没有足够证据） |
| 疏远 | `attitude < -0.3` |
| 认识 | `-0.3 ~ 0` |
| 熟络 | `0 ~ 0.3` |
| 亲近 | `0.3 ~ 0.6` |
| 亲密 | `> 0.6` |

阈值可调；等级只是给 LLM 的语义提示，不是行为触发器。

### 4.3 欲望 urges

欲望是"缺口"的强度，不是命令。

```python
@dataclass
class Urge:
    name: str          # 饥饿 / 孤独 / 想念 / 分享 / 睡意 / 日常
    level: float       # 0~1
    reason: str        # 一句话说明来源
    target: str | None # None 表示不指向特定朋友
```

| 欲望 | 来源 | 说明 |
| --- | --- | --- |
| 饥饿 | `(80 - satiety) / 80` | 直接来自桌宠饱食度 |
| 孤独 | 有人在场但长时间无互动，或长时间无人 | 社交需求缺口 |
| 想念 | 某位高态度朋友长期未出现 | 需要 L2 情景时间戳 |
| 分享 | 最近有新发现/新记忆 | 由活动结果抬升 |
| 睡意 | 深夜 + 低刺激 + 低精力 | 与昼夜节律相关 |
| 日常 | 昼夜相位 | 早安/晚安等时间锚点 |

欲望的累积可以是：

```
level += rate * deficit * circadian_gain * dt
```

但**是否需要回报、是否要说话、做什么**，全部由 LLM 决定。

### 4.4 自我状态不做的事

- 不在阈值处触发行为。
- 不修改调度。
- 不替 LLM 产生"你该去吃东西了"这类建议。
- 只把数字、标签和来源放进情境。

---

## 5. 显著变化信号

身体与环境的连续信号不逐帧唤醒认知。以下信号由 [07-mechanism](07-mechanism.md) 做显著变化检测：

| 信号 | 建议检测 |
| --- | --- |
| `satiety` | 跨过 80 / 60 / 40 / 20 分段边界 |
| `energy` | 跨过 0.5 / 0.25，或 10 分钟变化超过 0.15 |
| `alertness` | 短窗口上升 ≥ 0.3，或突破 0.7 |
| `social_battery` | 跨过 0.3 |
| `circadian` | 昼夜相位切换 |
| `presence` | 布尔边沿（无人 ↔ 有人） |
| `foreground_app` | 粗分类切换且持续 ≥ 30 秒 |

本文只定义"哪些信号存在"；检测规则、死区、迟滞与合并策略属于机制层。

---

## 6. 在情境中的样子

```json
{
  "self": {
    "mood": 0.4,
    "mood_label": "轻快",
    "urges": {
      "饥饿": {"level": 0.7, "reason": "饱食度 62"},
      "孤独": {"level": 0.2, "reason": "今天还没人说话"},
      "睡意": {"level": 0.1, "reason": "下午"}
    }
  },
  "body": {
    "satiety": 62,
    "energy": 0.71,
    "alertness": 0.35,
    "social_battery": 0.6,
    "circadian": 0.8,
    "presence": true,
    "mode": "awake"
  },
  "environment": {
    "someone_present": true,
    "idle_seconds": 42,
    "foreground_app": "code",
    "at_night": false
  }
}
```

这些数字只影响 LLM 的判断，不改变任何机制行为。

---

## 7. 失败与降级

| 情况 | 处理 |
| --- | --- |
| 读取不到空闲时长 | `someone_present = None`，情境里标注未知 |
| 前台窗口无法识别 | 分类为 `unknown` |
| 饱食度快照缺失 | 使用上一次值并标注 `stale` |
| 系统时间跳变 | 重新计算昼夜相位，不补算欲望 |

降级信息必须如实出现在情境里，不伪造状态。

---

## 8. 可调参数

| 参数 | 含义 |
| --- | --- |
| `PRESENCE_IDLE_THRESHOLD` | 多久无操作算"无人" |
| `MOOD_WINDOW` / `MOOD_HALF_LIFE` | 心情窗口与半衰期 |
| `ATTITUDE_HALF_LIFE` | 态度半衰期 |
| `RELATION_THRESHOLDS` | 关系等级切分 |
| `URGE_RATES` | 各欲望的累积速率 |
| `CIRCADIAN_CURVE` | 昼夜曲线 |
| `WAKE_DEADBANDS` | 显著变化的分段边界（由机制使用） |
