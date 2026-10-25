"""Alice 的身体：内感受与稳态。

饱食度以桌宠本体为权威，本模块只读 ``BunnySnapshot``；其余维度按简单稳态演化。
疲惫只由这些连续量体现，不设醒/睡二元状态。身体不产生行为，也不写记忆。
身体状态启动时从 ``state/body.json`` 读取，调用 ``save()`` 时落盘。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

from manager.shared.config import BrainConfig
from manager.shared.timeutil import clamp, now_local
from manager.shared.types import BodyState, BunnySnapshot, EnvironmentState

logger = logging.getLogger(__name__)


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
        self.state = self._load()
        self.satiety_stale = True
        self.satiety_updated_at = 0.0

    # ------------------------------------------------------------------
    # 演化
    # ------------------------------------------------------------------
    def tick(
        self,
        environment: EnvironmentState | None,
        *,
        interactions: int = 0,
        dt: float = 0.0,
        bunny_snapshot: BunnySnapshot | dict | None = None,
        busy_seconds: float = 0.0,
    ) -> BodyState:
        dt = max(0.0, float(dt))
        busy_seconds = min(dt, max(0.0, float(busy_seconds)))
        state = self.state
        now = environment.now if environment is not None else self._now_fn()
        if environment is not None:
            state.presence = bool(environment.someone_present)

        self._update_satiety(bunny_snapshot, now)
        self._update_energy(dt, busy_seconds)
        self._update_social(dt, interactions)
        return state

    def _update_satiety(
        self,
        snapshot: BunnySnapshot | dict | None,
        now,
    ) -> None:
        # 饱食度权威源是 bot/bridge.py 的 BunnySnapshot；未注入快照时保持 stale，沿用上次值。
        if snapshot is None:
            self.satiety_stale = True
            return
        if isinstance(snapshot, dict):
            snapshot = BunnySnapshot.from_dict(snapshot)
        self.state.satiety = clamp(float(snapshot.satiety), 0.0, 100.0)
        self.satiety_stale = False
        self.satiety_updated_at = now.timestamp()

    def _update_energy(self, dt: float, busy_seconds: float) -> None:
        # busy_seconds 由组合根汇总活动/在途能力的忙碌时长；为 0 时精力只回复不消耗。
        if dt <= 0:
            return
        idle_seconds = dt - busy_seconds
        self.state.energy = clamp(
            self.state.energy
            + idle_seconds / self.config.energy_full_recovery_seconds
            - busy_seconds / self.config.energy_drain_seconds,
            0.0,
            1.0,
        )

    def _update_social(self, dt: float, interactions: int) -> None:
        if interactions:
            self.state.social_battery -= (
                self.config.social_cost_per_interaction * interactions
            )
        elif dt > 0:
            self.state.social_battery += dt / self.config.social_full_recovery_seconds
        self.state.social_battery = clamp(self.state.social_battery, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 只读快照
    # ------------------------------------------------------------------
    def snapshot(self) -> BodyState:
        return BodyState.from_dict(self.state.to_dict())

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> BodyState:
        path = self.config.paths.body_state_path
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return BodyState.from_dict(data)
        except FileNotFoundError:
            return self._default_state()
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("无法读取身体状态 %s：%s，使用默认初始值", path, exc)
            return self._default_state()

    @staticmethod
    def _default_state() -> BodyState:
        return BodyState(
            satiety=80.0,
            energy=0.72,
            social_battery=0.7,
            presence=False,
        )

    def save(self) -> None:
        """把当前身体状态原子写入 ``state/body.json``。"""
        path = self.config.paths.body_state_path
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.state.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
