"""连续信号的死区、迟滞与合并：只报告变化，不解释重要性。

从旧 ``manager/mechanism/signals.py`` 移植，按新身体/环境模型裁剪：
- 删除警觉度（``alertness`` 已从 ``BodyState`` 移除）；
- 前台从「应用类别」改为「窗口标题」原文比较；
- 感知类型从 ``Perception`` 改为 ``EnvironmentState``。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from typing import Deque

from manager.shared.config import BrainConfig
from manager.shared.types import BodyState, EnvironmentState, SignalChange, SignalName

logger = logging.getLogger(__name__)


class SignalDetector:
    """跨 tick 比较身体与环境，输出穿过死区的显著变化。"""

    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self._last_emit: dict[str, float] = {}
        self._energy_samples: Deque[tuple[float, float]] = deque()
        self._last_presence_edge = 0.0

    def evaluate(
        self,
        prev_body: BodyState | None,
        body: BodyState,
        prev_environment: EnvironmentState | None,
        environment: EnvironmentState,
        now: datetime,
    ) -> list[SignalChange]:
        changes: list[SignalChange] = []
        if prev_body is not None:
            self._numeric(changes, SignalName.SATIETY, prev_body.satiety, body.satiety)
            self._energy(changes, prev_body.energy, body.energy)
            self._social(changes, prev_body.social_battery, body.social_battery)
            self._circadian(changes, prev_body.circadian, body.circadian)
        if prev_environment is not None:
            self._presence(changes, prev_environment, environment)
            self._foreground(changes, prev_environment, environment)
        self._energy_samples.append((time.monotonic(), body.energy))
        cutoff = time.monotonic() - 600
        while self._energy_samples and self._energy_samples[0][0] < cutoff:
            self._energy_samples.popleft()
        return self._coalesce(changes)

    def _coalesce(self, changes: list[SignalChange]) -> list[SignalChange]:
        now = time.monotonic()
        kept: list[SignalChange] = []
        for change in changes:
            key = str(change.signal)
            if now - self._last_emit.get(key, 0.0) < self.config.signal_coalesce_window:
                continue
            self._last_emit[key] = now
            kept.append(change)
        return kept

    # ------------------------------------------------------------------
    def _numeric(
        self,
        changes: list[SignalChange],
        name: SignalName,
        previous: float,
        current: float,
    ) -> None:
        boundaries = self.config.wake_deadbands.get(name.value, ())
        crossed = [value for value in boundaries if previous >= value > current]
        if not crossed:
            return
        boundary = max(crossed) if previous > current else min(crossed)
        changes.append(
            SignalChange(
                signal=name,
                from_value=round(previous, 2),
                to_value=round(current, 2),
                detail=f"{name.value} crossed {boundary:g}",
            )
        )

    def _energy(self, changes: list[SignalChange], previous: float, current: float) -> None:
        before = len(changes)
        self._numeric(changes, SignalName.ENERGY, previous, current)
        if len(changes) > before:
            return
        if self._energy_samples:
            oldest_time, oldest_value = self._energy_samples[0]
            if time.monotonic() - oldest_time >= 240:
                delta = current - oldest_value
                if abs(delta) >= 0.15:
                    changes.append(
                        SignalChange(
                            signal=SignalName.ENERGY,
                            from_value=round(oldest_value, 2),
                            to_value=round(current, 2),
                            detail=f"energy changed {delta:+.2f} in 10m",
                        )
                    )

    def _social(self, changes: list[SignalChange], previous: float, current: float) -> None:
        self._numeric(changes, SignalName.SOCIAL_BATTERY, previous, current)

    def _circadian(
        self, changes: list[SignalChange], previous: float, current: float
    ) -> None:
        if (previous < 0.5 <= current) or (previous >= 0.5 > current):
            changes.append(
                SignalChange(
                    signal=SignalName.CIRCADIAN,
                    from_value=round(previous, 2),
                    to_value=round(current, 2),
                    detail="昼夜相位切换",
                )
            )

    def _presence(
        self,
        changes: list[SignalChange],
        previous: EnvironmentState,
        current: EnvironmentState,
    ) -> None:
        if (
            previous.someone_present is None
            or current.someone_present is None
            or previous.someone_present == current.someone_present
        ):
            return
        now = time.monotonic()
        if now - self._last_presence_edge < self.config.presence_edge_debounce:
            return
        self._last_presence_edge = now
        changes.append(
            SignalChange(
                signal=SignalName.PRESENCE,
                from_value=previous.someone_present,
                to_value=current.someone_present,
                detail="有人来到电脑前" if current.someone_present else "人离开了电脑",
            )
        )

    def _foreground(
        self,
        changes: list[SignalChange],
        previous: EnvironmentState,
        current: EnvironmentState,
    ) -> None:
        if not previous.foreground_title or not current.foreground_title:
            return
        if previous.foreground_title == current.foreground_title:
            return
        dwell = float((current.system_state or {}).get("foreground_dwell") or 0)
        if dwell < self.config.foreground_dwell_seconds:
            return
        changes.append(
            SignalChange(
                signal=SignalName.FOREGROUND_TITLE,
                from_value=previous.foreground_title,
                to_value=current.foreground_title,
                detail="前台窗口改变",
            )
        )


__all__ = ["SignalDetector"]
