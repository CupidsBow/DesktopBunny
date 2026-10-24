"""环境快照聚合：把外部世界变成一份只读的结构化数据。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from manager.environment.foreground import ForegroundTracker
from manager.environment.presence import get_idle_seconds
from manager.shared.config import BrainConfig
from manager.shared.timeutil import is_night, now_local
from manager.shared.types import BunnySnapshot, EnvironmentState

logger = logging.getLogger(__name__)


class Environment:
    """每个状态时钟聚合一次环境快照；不做重要性判断，也不处理消息内容。"""

    def __init__(
        self,
        config: BrainConfig,
        *,
        idle_fn: Callable[[], float | None] | None = None,
        foreground: ForegroundTracker | None = None,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.config = config
        self._idle_fn = idle_fn or get_idle_seconds
        self._foreground = foreground or ForegroundTracker(
            config.foreground_dwell_seconds
        )
        self._now_fn = now_fn
        self.last: EnvironmentState | None = None

    def tick(
        self,
        bunny_snapshot: BunnySnapshot | dict | None = None,
    ) -> EnvironmentState:
        now = self._now_fn()
        idle_seconds = self._idle_fn()
        if idle_seconds is None:
            someone_present: bool | None = None
        else:
            someone_present = idle_seconds < self.config.presence_idle_threshold

        foreground = self._foreground.tick(now)
        snapshot = _coerce_snapshot(bunny_snapshot)

        system_state: dict = {
            "foreground_dwell": round(foreground.dwell, 1),
        }
        if snapshot is None:
            system_state["bunny_snapshot"] = "missing"

        environment_state = EnvironmentState(
            now=now,
            idle_seconds=None if idle_seconds is None else round(idle_seconds, 1),
            someone_present=someone_present,
            foreground_title=foreground.title,
            at_night=is_night(now),
            system_state=system_state,
        )
        self.last = environment_state
        return environment_state


def _coerce_snapshot(
    value: BunnySnapshot | dict | None,
) -> BunnySnapshot | None:
    if value is None:
        return None
    if isinstance(value, BunnySnapshot):
        return value
    if isinstance(value, dict) and "satiety" in value:
        return BunnySnapshot.from_dict(value)
    return None
