# 02 · 身体与自我状态

> 身体层维护 Alice 的内感受与稳态；自我状态从身体和记忆中派生情绪、态度、欲望。
> 两者都只产出**数据**，不直接决定行为。

---

## 1. 职责边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `body.py` | 维护内感受与稳态 | 决定行为、触发动作 |
| `self_state.py` | 从身体与记忆派生情绪、态度、欲望 | 阈值触发、隐藏开关 |

行为选择属于 [09-thinker](09-thinker.md)。

实现文件：

| 内容 | 实现文件 |
| --- | --- |
| 身体与稳态 | `manager/body/body.py` |
| 心情 / 态度 / 欲望 | `manager/body/self_state.py` |

数据流：

```text
body.tick(env, interactions, dt, busy_seconds) → BodyState
self_state.derive(body, memory_view, now)      → SelfState
```

由状态时钟（每 2 秒）驱动，不调用 LLM；输出只进入情境装配与显著变化检测。

---

## 2. 身体 body.py

### 2.1 数据

```python
@dataclass
class BodyState:
    satiety: float          # 0~100，饱食度（桌宠本体权威）
    energy: float           # 0~1，精力
    social_battery: float   # 0~1，社交电量
    presence: bool          # 电脑前是否有人
```

> 旧版曾有 `alertness`（警觉度）与 `mode`（awake/rest）两个字段，已随醒/睡二元状态的移除一并删除。疲惫只由 `energy` 体现。

### 2.2 各维度的含义

| 维度 | 升高 | 降低 | 主要影响（作为输入） |
| --- | --- | --- | --- |
| `satiety` | 吃东西（`eat` 能力） | 时间流逝 | 饥饿感 |
| `energy` | 静默、空闲 | 活动、忙碌 | 愿不愿意做长活动、回复长短 |
| `social_battery` | 独处、安静 | 社交互动 | 想不想说话、社交耐受 |
| `presence` | 有人回到电脑前 | 人离开 | 是否适合主动开口 |

### 2.3 稳态模型

- **饱食度以 `components/bunny.py` 为权威**：`body.py` 只读 `BunnySnapshot`，未注入快照时保持 `stale`、沿用上次值。
- **精力**：`idle_seconds`（dt - busy_seconds）回复，`busy_seconds` 消耗；`energy_full_recovery_seconds` 与 `energy_drain_seconds` 各 6 小时。
- **社交电量**：每次互动扣 `social_cost_per_interaction`（0.03），独处时按 `social_full_recovery_seconds` 回复。

### 2.4 身体的边界

- 身体不产生行为：饿了不会自动说话，困了不会自动睡。
- 身体不写记忆：状态只被读取，不被固化成语义结论。
- 身体可以被外部或自己改变：喂食、点击互动、拖放文件等桌宠事件，以及 `tool.call(eat)` 能力，都会更新饱食度（经桌宠快照回流）。
- 没有醒/睡二元状态；"休息"不是动作，只是精力恢复的自然结果。

---

## 3. 自我状态 self_state.py

自我状态是从身体和记忆中**派生**的只读视图，不落盘。

### 3.1 心情 mood

```
mood = Σ valence_i * decay(age_i) / Σ decay(age_i)
```

- 聚合最近窗口内的情景记忆（`mood_window`，4 小时）。
- 半衰期 `mood_half_life` 约 2 小时：心情是短时的。
- 输出 `[-1, 1]` 的连续值与一个语义标签（`mood_label`）。
- 与态度分离：对某位朋友不满，不代表整体心情差。

### 3.2 态度 attitude

```
w_i = intensity_i * decay(age_i)          # intensity = |valence| * (0.5 + arousal)
attitude(friend) = Σ valence_i * w_i / Σ w_i
confidence      = Σ w_i
```

- 只针对**说话人**，不针对会话。
- 半衰期 `attitude_half_life` 约 7 天：亲疏的记忆尺度。
- 置信度表示证据量；`acquaintance_min_age`（24 小时）内关系最高只到"认识"，避免一见如故。
- 连续值映射到离散关系等级（`RelationLevel`）：

先看置信度，再看连续值：

| 等级 | 条件 |
| --- | --- |
| 陌生 | `confidence < min_confidence`（0.2） |
| 疏远 | `attitude < -0.3` |
| 认识 | `-0.3 ~ 0` |
| 熟络 | `0 ~ 0.3` |
| 亲近 | `0.3 ~ 0.6` |
| 亲密 | `> 0.6` |

阈值集中在 `config.RelationThresholds`；等级只是给 LLM 的语义提示，不是行为触发器。

### 3.3 欲望 urges

欲望是"缺口"的强度，不是命令。

```python
@dataclass
class Urge:
    name: str          # 饥饿 / 孤独 / 想念 / 分享 / 日常
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
| 日常 | 时钟时刻 | 早安/晚安等时间锚点 |

但**是否回报、是否说话、做什么**，全部由 LLM 决定。

### 3.4 自我状态不做的事

- 不在阈值处触发行为。
- 不修改调度。
- 不替 LLM 产生"你该去吃东西了"这类建议。
- 只把数字、标签和来源放进情境。

---

## 4. 显著变化信号

身体与环境的连续信号不逐帧唤醒认知。本文只定义"身体提供哪些信号"；检测规则、死区、迟滞与合并策略属于 [06-lifecycle](06-lifecycle.md)。

| 信号 | 检测规则（由 lifecycle 执行） |
| --- | --- |
| `satiety` | 跨过 80 / 60 / 40 / 20 分段边界（向下） |
| `energy` | 跨过 0.5 / 0.25，或 10 分钟变化超过 0.15 |
| `social_battery` | 跨过 0.3 |

---

## 5. 在情境中的样子

```json
{
  "self": {
    "identity": "Alice",
    "mood": 0.4,
    "mood_label": "轻快",
    "urge_details": [
      {"name": "饥饿", "level": 0.7, "reason": "饱食度 62"},
      {"name": "孤独", "level": 0.2, "reason": "今天还没人说话"}
    ]
  },
  "body": {
    "satiety": "62/100",
    "energy": "0.71/1",
    "social_battery": "0.6/1",
    "presence": true
  },
  "environment": {
    "someone_present": true,
    "idle_seconds": 42,
    "foreground_title": "Visual Studio Code"
  }
}
```

这些数字只影响 LLM 的判断，不改变任何机制行为。

---

## 6. 失败与降级

| 情况 | 处理 |
| --- | --- |
| 饱食度快照缺失 | 使用上一次值并标注 `stale` |

降级信息必须如实出现在情境里，不伪造状态。

---

## 7. 可调参数

| 参数 | 含义 |
| --- | --- |
| `mood_window` / `mood_half_life` | 心情窗口与半衰期 |
| `attitude_half_life` | 态度半衰期 |
| `acquaintance_min_age` | 关系封顶"认识"的最短相识时长 |
| `relation_thresholds` | 关系等级切分 |
| `energy_full_recovery_seconds` / `energy_drain_seconds` | 精力回满/耗尽时长 |
| `social_cost_per_interaction` / `social_full_recovery_seconds` | 社交电量消耗/回复 |
| `wake_deadbands` | 显著变化的分段边界（由 lifecycle 使用） |
