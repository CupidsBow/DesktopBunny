"""Alice 的身体：内感受与稳态。

饱食度以桌宠本体为权威，本模块只读 ``BunnySnapshot``；其余维度按简单稳态演化。
身体不产生行为，也不写记忆。
"""

from __future__ import annotations

import logging
from typing import Callable

from manager.shared.config import BrainConfig
from manager.shared.timeutil import circadian_value, clamp, now_local
from manager.shared.types import BodyMode, BodyState, BunnySnapshot, Perception

logger = logging.getLogger(__name__)

_AWAKE_ENERGY_HALF_LIFE = 16 * 3600.0
_REST_ENERGY_HALF_LIFE = 6 * 3600.0
_ALERTNESS_HALF_LIFE = 20 * 60.0
_SOCIAL_RECOVERY_HALF_LIFE = 6 * 3600.0


class Body:
    """维护 ``BodyState``；所有更新都是状态演化，不触发任何行为。"""

    def __init__(
        self,
        config: BrainConfig,
        *,
        now_fn: Callable = now_local,
    ) -> None:
        self.config = config
        self._now_fn = now_fn
        self.state = BodyState(
            satiety=80.0,
            energy=0.72,
            alertness=0.3,
            social_battery=0.7,
            circadian=0.5,
            presence=False,
            mode=BodyMode.AWAKE,
        )
        self.satiety_stale = True
        self.satiety_updated_at = 0.0

    # ------------------------------------------------------------------
    # 演化
    # ------------------------------------------------------------------
    def tick(
        self,
        perception: Perception | None,
        *,
        interactions: int = 0,
        dt: float = 0.0,
        bunny_snapshot: BunnySnapshot | None = None,
    ) -> BodyState:
        dt = max(0.0, float(dt))
        state = self.state
        now = perception.now if perception is not None else self._now_fn()
        state.circadian = circadian_value(
            now.hour + now.minute / 60.0, self.config.circadian_curve
        )
        if perception is not None:
            state.presence = bool(perception.someone_present)

        self._update_satiety(bunny_snapshot, now)
        self._update_energy(dt)
        self._update_alertness(dt, interactions)
        self._update_social(dt, interactions)
        return state

    def _update_satiety(
        self, snapshot: BunnySnapshot | None, now
    ) -> None:
        if snapshot is None:
            self.satiety_stale = True
            return
        self.state.satiety = clamp(float(snapshot.satiety), 0.0, 100.0)
        self.satiety_stale = False
        self.satiety_updated_at = now.timestamp()

    def _update_energy(self, dt: float) -> None:
        if dt <= 0:
            return
        if self.state.mode is BodyMode.REST:
            self.state.energy += dt / _REST_ENERGY_HALF_LIFE
        else:
            # 白天消耗更快；深夜仍醒着也算额外消耗
            drain = dt / _AWAKE_ENERGY_HALF_LIFE * (
                0.6 + 0.8 * self.state.circadian
            )
            self.state.energy -= drain
        self.state.energy = clamp(self.state.energy, 0.0, 1.0)

    def _update_alertness(self, dt: float, interactions: int) -> None:
        target = 0.15 if self.state.mode is BodyMode.REST else 0.3
        if dt > 0:
            factor = 0.5 ** (dt / _ALERTNESS_HALF_LIFE)
            self.state.alertness = target + (
                self.state.alertness - target
            ) * factor
        if interactions:
            self.state.alertness = clamp(
                self.state.alertness + 0.08 * interactions, 0.0, 1.0
            )
        self.state.alertness = clamp(self.state.alertness, 0.0, 1.0)

    def _update_social(self, dt: float, interactions: int) -> None:
        if interactions:
            self.state.social_battery -= 0.03 * interactions
        elif dt > 0:
            self.state.social_battery += dt / (2 * _SOCIAL_RECOVERY_HALF_LIFE)
        self.state.social_battery = clamp(self.state.social_battery, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 外部改变
    # ------------------------------------------------------------------
    def set_mode(self, mode: BodyMode | str) -> None:
        try:
            self.state.mode = BodyMode(mode)
        except ValueError:
            logger.warning("忽略未知 body mode: %r", mode)

    def stimulate(self, amount: float = 0.2) -> None:
        """突然的刺激（点击、喂食、桌面事件）。"""
        self.state.alertness = clamp(self.state.alertness + amount, 0.0, 1.0)

    def note_interaction(self, count: int = 1) -> None:
        self.state.alertness = clamp(self.state.alertness + 0.05 * count, 0.0, 1.0)
        self.state.social_battery = clamp(
            self.state.social_battery - 0.02 * count, 0.0, 1.0
        )

    @property
    def mode(self) -> BodyMode:
        return self.state.mode

    def snapshot(self) -> BodyState:
        return BodyState.from_dict(self.state.to_dict())
