"""状态时钟与最长间隔。"""

from __future__ import annotations

from datetime import datetime

from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import BodyMode


class StateClock:
    def __init__(self, config: BrainConfig, *, now_fn=now_local) -> None:
        self.config = config
        self.now_fn = now_fn
        self._last_tick: datetime | None = None

    def interval(self, mode: BodyMode) -> float:
        base = self.config.state_tick_interval
        return base * 2.5 if mode is BodyMode.REST else base

    def max_idle(self, mode: BodyMode) -> float:
        if mode is BodyMode.REST:
            return self.config.max_idle_seconds_rest
        return self.config.max_idle_seconds

    def due(self, mode: BodyMode, now: datetime | None = None) -> bool:
        now = now or self.now_fn()
        if self._last_tick is None:
            return True
        return (now - self._last_tick).total_seconds() >= self.interval(mode)

    def mark(self, now: datetime | None = None) -> None:
        self._last_tick = now or self.now_fn()

    @property
    def last_tick(self) -> datetime | None:
        return self._last_tick


__all__ = ["StateClock"]
