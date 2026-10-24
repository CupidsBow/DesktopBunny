"""时间闹钟与条件闹钟的存储和求值。

从旧 ``manager/mechanism/alarms.py`` 移植并裁剪：
- 去掉 ``PersistenceWorker`` 依赖，直接原子写盘（``.tmp`` + ``os.replace``）；
- 去掉 ``ResumeWatcher``（活动恢复条件属于 ``activity`` 模块）；
- 感知类型从 ``Perception`` 改为 ``EnvironmentState``；
- 不带活动状态，``satiety_above / energy_above / activity_checkpoint`` 条件
  由 ``activity`` 模块负责，本模块不评估它们。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Callable

from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local, parse_time_arg
from manager.shared.types import (
    Alarm,
    AlarmKind,
    AlarmPredicate,
    BodyState,
    EnvironmentState,
    WakePredicateType,
)

logger = logging.getLogger(__name__)


class AlarmStore:
    def __init__(
        self,
        path: str,
        config: BrainConfig,
        *,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.path = path
        self.config = config
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._loaded = False
        self._alarms: list[Alarm] = []
        self._counter = 0

    # ------------------------------------------------------------------
    def load(self) -> list[Alarm]:
        with self._lock:
            if self._loaded:
                return self.pending()
            self._loaded = True
            if not os.path.exists(self.path):
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning("读取闹钟失败：%s", exc)
                return []
            if isinstance(data, dict):
                data = data.get("alarms") or []
            for item in data if isinstance(data, list) else []:
                try:
                    alarm = Alarm.from_dict(item)
                except (ValueError, TypeError):
                    continue
                self._alarms.append(alarm)
                self._counter = max(self._counter, _numeric_suffix(alarm.id))
            return self.pending()

    def pending(self) -> list[Alarm]:
        with self._lock:
            return [Alarm.from_dict(item.to_dict()) for item in self._alarms]

    def clear_source(self, prefix: str) -> None:
        with self._lock:
            self.load()
            self._alarms = [
                alarm for alarm in self._alarms if not alarm.source.startswith(prefix)
            ]
            self._save()

    # ------------------------------------------------------------------
    def schedule_time(
        self,
        seconds: float,
        *,
        source: str = "system",
        note: str = "",
        now: datetime | None = None,
    ) -> Alarm:
        """安排一个相对时间闹钟（不清理既有闹钟）。"""
        now = now or self.now_fn()
        with self._lock:
            self.load()
            alarm = self._add(
                Alarm(
                    id=self._next_id(),
                    kind=AlarmKind.TIME,
                    created_at=now.timestamp(),
                    fire_at=now.timestamp() + max(0.0, float(seconds)),
                    source=source,
                    note=note,
                )
            )
            self._save()
            return alarm

    def schedule_predicate(
        self,
        predicate: AlarmPredicate,
        *,
        source: str = "llm:wake_when",
        now: datetime | None = None,
    ) -> Alarm | None:
        now = now or self.now_fn()
        with self._lock:
            self.load()
            if predicate.type is WakePredicateType.AT_TIME:
                target = parse_time_arg(predicate.args, now)
                if target is None:
                    logger.debug("跳过无效 at_time：%s", predicate.args)
                    return None
                alarm = self._add(
                    Alarm(
                        id=self._next_id(),
                        kind=AlarmKind.TIME,
                        created_at=now.timestamp(),
                        fire_at=target.timestamp(),
                        source=source,
                        note="at_time",
                    )
                )
            else:
                alarm = self._add(
                    Alarm(
                        id=self._next_id(),
                        kind=AlarmKind.PREDICATE,
                        created_at=now.timestamp(),
                        predicate=AlarmPredicate(
                            type=predicate.type, args=dict(predicate.args)
                        ),
                        source=source,
                    )
                )
            self._save()
            return alarm

    def _add(self, alarm: Alarm) -> Alarm:
        self._alarms.append(alarm)
        return alarm

    def _next_id(self) -> str:
        self._counter += 1
        return f"al_{self._counter:05d}"

    # ------------------------------------------------------------------
    def due(
        self,
        now: datetime,
        body: BodyState,
        environment: EnvironmentState,
    ) -> list[Alarm]:
        with self._lock:
            self.load()
            fired: list[Alarm] = []
            remaining: list[Alarm] = []
            for alarm in self._alarms:
                if self._is_due(alarm, now, body, environment):
                    fired.append(alarm)
                else:
                    remaining.append(alarm)
            if fired:
                self._alarms = remaining
                self._save()
            return fired

    def _is_due(
        self,
        alarm: Alarm,
        now: datetime,
        body: BodyState,
        environment: EnvironmentState,
    ) -> bool:
        if alarm.kind is AlarmKind.TIME:
            return alarm.fire_at is not None and alarm.fire_at <= now.timestamp()
        predicate = alarm.predicate
        if predicate is None:
            return False
        ptype = predicate.type
        args = predicate.args or {}
        if ptype is WakePredicateType.SATIETY_BELOW:
            return body.satiety < float(args.get("value", 0))
        if ptype is WakePredicateType.ENERGY_BELOW:
            return body.energy < float(args.get("value", 0))
        if ptype is WakePredicateType.PRESENCE_CHANGED:
            baseline = args.get("_baseline_presence")
            if baseline is None:
                predicate.args["_baseline_presence"] = environment.someone_present
                return False
            return environment.someone_present != baseline
        if ptype is WakePredicateType.AT_TIME:
            # 调度时已转成时间闹钟；这里兜底
            target = parse_time_arg(args, now)
            return target is not None and target <= now
        # satiety_above / energy_above / activity_checkpoint 由 activity 模块负责
        return False

    def _save(self) -> None:
        payload = {"alarms": [alarm.to_dict() for alarm in self._alarms]}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp = f"{self.path}.tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        except OSError as exc:
            logger.warning("保存闹钟失败：%s", exc)


def _numeric_suffix(value: str) -> int:
    suffix = value.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


__all__ = ["AlarmStore"]
