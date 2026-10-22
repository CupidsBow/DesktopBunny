"""Alice 大脑的运行参数与磁盘路径。

这里是 ``constants/constants.py`` 之外唯一允许放可调数值的地方：
感知、身体、记忆、认知、能力、执行、机制层都通过 ``BrainConfig`` 读取，
不在各自模块里散落魔法数字。

本模块不读取环境变量、不建立连接；``ensure_directories()`` 是显式调用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from constants import constants

#: Alice 自己的数据根目录；桌宠存档仍在 DEFAULT_SAVE_DIR 下。
DEFAULT_BRAIN_DIR = os.path.join(constants.DEFAULT_SAVE_DIR, "Alice")


@dataclass(frozen=True)
class BrainPaths:
    """大脑的磁盘布局。"""

    root: str = DEFAULT_BRAIN_DIR

    @property
    def memory_dir(self) -> str:
        return os.path.join(self.root, "memory")

    @property
    def episodes_dir(self) -> str:
        return os.path.join(self.memory_dir, "episodes")

    @property
    def friends_dir(self) -> str:
        return os.path.join(self.memory_dir, "friends")

    @property
    def index_path(self) -> str:
        return os.path.join(self.memory_dir, "MEMORY.md")

    @property
    def self_path(self) -> str:
        return os.path.join(self.memory_dir, "self.md")

    @property
    def goals_path(self) -> str:
        return os.path.join(self.memory_dir, "goals.md")

    @property
    def preferences_path(self) -> str:
        return os.path.join(self.memory_dir, "preferences.md")

    @property
    def state_dir(self) -> str:
        return os.path.join(self.root, "state")

    @property
    def l1_path(self) -> str:
        return os.path.join(self.state_dir, "l1.json")

    @property
    def events_path(self) -> str:
        return os.path.join(self.state_dir, "events.jsonl")

    @property
    def alarms_path(self) -> str:
        return os.path.join(self.state_dir, "alarms.json")

    @property
    def activities_path(self) -> str:
        return os.path.join(self.state_dir, "activities.json")

    @property
    def inflight_path(self) -> str:
        return os.path.join(self.state_dir, "pending_capabilities.json")

    @property
    def artifacts_dir(self) -> str:
        return os.path.join(self.root, "artifacts")

    @property
    def scratch_dir(self) -> str:
        """沙盒默认工作目录；Alice 的临时草稿都落在这里，不污染项目根目录。"""
        return os.path.join(self.root, "scratch")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.root, "logs")

    @property
    def audit_path(self) -> str:
        return os.path.join(self.logs_dir, "audit.jsonl")

    def ensure_directories(self) -> None:
        """创建所有必须存在的目录；已存在时不做任何事。"""
        for path in (
            self.root,
            self.memory_dir,
            self.episodes_dir,
            self.friends_dir,
            self.state_dir,
            self.artifacts_dir,
            self.scratch_dir,
            self.logs_dir,
        ):
            os.makedirs(path, exist_ok=True)


@dataclass(frozen=True)
class RelationThresholds:
    """连续态度 → 离散关系等级的切分。见 01-body.md §4.2。"""

    min_confidence: float = 0.2
    distant_below: float = -0.3
    acquaintance_from: float = -0.3
    familiar_from: float = 0.0
    close_from: float = 0.3
    intimate_from: float = 0.6


#: 连续信号的显著变化分段边界。机制只报告跨段，不解释重要性。
DEFAULT_WAKE_DEADBANDS: dict[str, tuple[float, ...]] = {
    "satiety": (20.0, 40.0, 60.0, 80.0),
    "energy": (0.25, 0.5),
    "social_battery": (0.3,),
}


#: 各欲望的基础累积速率（每秒）；实际累积还要乘缺口与昼夜系数。
DEFAULT_URGE_RATES: dict[str, float] = {
    "饥饿": 1.0 / 600.0,
    "孤独": 1.0 / 3600.0,
    "想念": 1.0 / 7200.0,
    "分享": 1.0 / 1800.0,
    "睡意": 1.0 / 3600.0,
    "日常": 1.0 / 86400.0,
}


#: 昼夜曲线控制点：(小时, 活动基线 0~1)。
DEFAULT_CIRCADIAN_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (6.0, 0.3),
    (9.0, 0.9),
    (12.0, 0.8),
    (14.0, 0.85),
    (18.0, 0.9),
    (22.0, 0.5),
    (24.0, 0.2),
)


@dataclass(frozen=True)
class BrainConfig:
    """整颗大脑的默认参数。

    这些值都是可调的；文档里的“建议值”在这里有唯一实现。
    """

    paths: BrainPaths = field(default_factory=BrainPaths)

    # --- 机制：时钟、唤醒、帧 ---
    state_tick_interval: float = 2.0
    settle_window: float = 1.0
    max_idle_seconds: float = 1800.0
    max_idle_seconds_rest: float = 7200.0
    next_wake_max_seconds: int = 86400
    frame_timeout_seconds: float = 60.0
    wake_deadbands: dict[str, tuple[float, ...]] = field(
        default_factory=lambda: dict(DEFAULT_WAKE_DEADBANDS)
    )
    wake_hysteresis: float = 0.02
    presence_edge_debounce: float = 2.0
    foreground_dwell_seconds: float = 30.0
    signal_coalesce_window: float = 5.0
    allowed_wake_predicates: tuple[str, ...] = (
        "satiety_below",
        "energy_below",
        "presence_changed",
        "at_time",
        "activity_checkpoint",
    )

    # --- 认知：LLM 与决策 ---
    llm_retry_limit: int = 2
    llm_retry_backoff_seconds: float = 1.0
    max_decision_repair: int = 1

    # --- 感知 ---
    presence_idle_threshold: float = 60.0

    # --- 身体与自我状态 ---
    mood_window: float = 4 * 3600.0
    mood_half_life: float = 2 * 3600.0
    attitude_half_life: float = 7 * 86400.0
    #: 认识不满这么久（秒）的人，关系最高只到 ACQUAINTANCE，避免一见如故。
    acquaintance_min_age: float = 24 * 3600.0
    relation_thresholds: RelationThresholds = field(
        default_factory=RelationThresholds
    )
    urge_rates: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_URGE_RATES)
    )
    circadian_curve: tuple[tuple[float, float], ...] = DEFAULT_CIRCADIAN_CURVE

    # --- 记忆 ---
    l1_context_max_chars: int = 12000
    l1_summary_max_chars: int = 2000
    l1_compress_retry_seconds: float = 60.0
    l1_hard_max_lines: int = 1000
    memory_index_max_lines: int = 200
    memory_index_max_bytes: int = 25 * 1024
    recall_max_episodes: int = 12
    episode_max_bytes: int = 2048
    self_max_bytes: int = 4096
    goals_max_bytes: int = 4096
    preferences_max_bytes: int = 4096
    consolidation_interval: float = 6 * 3600.0
    consolidation_salience_weight: float = 0.5

    # --- 活动 ---
    max_concurrent_activities: int = 1
    activity_checkpoint_interval: float = 300.0
    activity_max_consecutive_failures: int = 3

    # --- 能力 ---
    capability_timeout_default: float = 30.0
    capability_max_result_bytes: int = 16 * 1024
    max_concurrent_capabilities: int = 3
    fs_max_read_bytes: int = 256 * 1024

    # --- 出站 ---
    message_split_max_chars: int = 500
    message_split_max_parts: int = 4
    message_send_interval: float = 0.8
    passive_reply_timeout: float = 5.0
    send_retry_limit: int = 1
    streaming_enabled: bool = False

    def ensure_directories(self) -> None:
        """在启动组合根里显式调用。"""
        self.paths.ensure_directories()


__all__ = [
    "DEFAULT_BRAIN_DIR",
    "DEFAULT_CIRCADIAN_CURVE",
    "DEFAULT_URGE_RATES",
    "DEFAULT_WAKE_DEADBANDS",
    "BrainPaths",
    "RelationThresholds",
    "BrainConfig",
]
