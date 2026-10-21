"""时间闹钟、条件闹钟与活动恢复条件的存储和求值。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from manager.activities.activities import ActivityService
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local, parse_time_arg, to_iso
from manager.shared.types import (
    Activity,
    ActivityStatus,
    Alarm,
    AlarmKind,
    AlarmPredicate,
    BodyState,
    Decision,
    Perception,
    WakePredicateType,
)

logger = logging.getLogger(__name__)


class AlarmStore:
    def __init__(
        self,
        path: str,
        config: BrainConfig,
        *,
        persistence=None,
        now_fn=now_local,
    ) -> None:
        self.path = path
        self.config = config
        self.persistence = persistence
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
    def schedule_decision(self, decision: Decision, now: datetime) -> list[Alarm]:
        with self._lock:
            self.load()
            self.clear_source("llm")
            created: list[Alarm] = []
            if decision.next_wake_in is not None:
                fire_at = now.timestamp() + max(0, int(decision.next_wake_in))
                created.append(
                    self._add(
                        Alarm(
                            id=self._next_id(),
                            kind=AlarmKind.TIME,
                            created_at=now.timestamp(),
                            fire_at=fire_at,
                            source="llm:next_wake_in",
                            note=f"{decision.next_wake_in}s",
                        )
                    )
                )
            for predicate in decision.wake_when:
                alarm = self._predicate_alarm(predicate, now, "llm:wake_when")
                if alarm is not None:
                    created.append(alarm)
            self._save()
            return created

    def schedule_time(
        self,
        seconds: float,
        *,
        source: str = "system",
        note: str = "",
        now: datetime | None = None,
    ) -> Alarm:
        """直接安排一个相对时间闹钟（不清理既有 llm 闹钟）。"""
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

    def _predicate_alarm(
        self, predicate: AlarmPredicate, now: datetime, source: str
    ) -> Alarm | None:
        if predicate.type is WakePredicateType.AT_TIME:
            target = parse_time_arg(predicate.args, now)
            if target is None:
                logger.debug("跳过无效 at_time：%s", predicate.args)
                return None
            return self._add(
                Alarm(
                    id=self._next_id(),
                    kind=AlarmKind.TIME,
                    created_at=now.timestamp(),
                    fire_at=target.timestamp(),
                    source=source,
                    note="at_time",
                )
            )
        return self._add(
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
        perception: Perception,
        *,
        checkpoint_id: str | None = None,
    ) -> list[Alarm]:
        with self._lock:
            self.load()
            fired: list[Alarm] = []
            remaining: list[Alarm] = []
            for alarm in self._alarms:
                if self._is_due(alarm, now, body, perception, checkpoint_id):
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
        perception: Perception,
        checkpoint_id: str | None,
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
                predicate.args["_baseline_presence"] = perception.someone_present
                return False
            return perception.someone_present != baseline
        if ptype is WakePredicateType.ACTIVITY_CHECKPOINT:
            target = str(args.get("id") or "")
            return bool(target) and checkpoint_id == target
        if ptype is WakePredicateType.AT_TIME:
            # 调度时已转成时间闹钟；这里兜底
            target = parse_time_arg(args, now)
            return target is not None and target <= now
        return False

    def _save(self) -> None:
        payload = {"alarms": [alarm.to_dict() for alarm in self._alarms]}
        if self.persistence is not None:
            self.persistence.submit_json(self.path, payload)
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp = f"{self.path}.tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        except OSError as exc:
            logger.warning("保存闹钟失败：%s", exc)


# ----------------------------------------------------------------------
class ResumeWatcher:
    """暂停活动的 ``resume_when``：只产生闹钟，不自动恢复。"""

    def __init__(self, config: BrainConfig) -> None:
        self.config = config

    def prepare_baseline(
        self,
        activity: Activity,
        body: BodyState,
        perception: Perception,
        now: datetime,
    ) -> dict[str, Any]:
        at_time: dict[str, float] = {}
        for index, predicate in enumerate(activity.resume_when):
            if predicate.type is not WakePredicateType.AT_TIME:
                continue
            target = parse_time_arg(predicate.args, now)
            if target is None:
                continue
            if target <= now:
                target += timedelta(days=1)
            at_time[str(index)] = target.timestamp()
        return {
            "satiety": float(body.satiety),
            "energy": float(body.energy),
            "presence": perception.someone_present,
            "at_time": at_time,
        }

    def check(
        self,
        activities: ActivityService,
        body: BodyState,
        perception: Perception,
        now: datetime,
    ) -> list[dict[str, Any]]:
        fired: list[dict[str, Any]] = []
        for activity in activities.all():
            if activity.status is not ActivityStatus.PAUSED:
                continue
            if not activity.resume_when:
                continue
            baseline = activity.checkpoint.get("resume_baseline")
            if not isinstance(baseline, dict):
                # 旧数据没有基线：以当前状态为基线，避免刚加载就触发
                baseline = self.prepare_baseline(
                    activity, body, perception, now
                )
                activities.set_resume_baseline(activity.id, baseline)
                continue
            predicate = self._first_satisfied(
                activity.resume_when, baseline, body, perception, now
            )
            if predicate is None:
                continue
            activities.clear_resume_when(activity.id)
            fired.append(
                {
                    "alarm_id": f"resume:{activity.id}",
                    "type": "predicate",
                    "source": "activity_resume",
                    "activity_id": activity.id,
                    "predicate": {
                        "type": predicate.type.value,
                        "args": predicate.args,
                    },
                    "fired_at": to_iso(now),
                }
            )
        return fired

    def _first_satisfied(
        self,
        predicates: Iterable[AlarmPredicate],
        baseline: dict[str, Any],
        body: BodyState,
        perception: Perception,
        now: datetime,
    ) -> AlarmPredicate | None:
        for index, predicate in enumerate(predicates):
            args = predicate.args or {}
            if predicate.type is WakePredicateType.SATIETY_ABOVE:
                threshold = float(args.get("value", 100))
                if float(baseline.get("satiety", 0)) < threshold and body.satiety >= threshold:
                    return predicate
            elif predicate.type is WakePredicateType.ENERGY_ABOVE:
                threshold = float(args.get("value", 1))
                if float(baseline.get("energy", 0)) < threshold and body.energy >= threshold:
                    return predicate
            elif predicate.type is WakePredicateType.PRESENCE_CHANGED:
                if perception.someone_present != baseline.get("presence"):
                    return predicate
            elif predicate.type is WakePredicateType.AT_TIME:
                targets = baseline.get("at_time") or {}
                target = targets.get(str(index))
                if target is not None and now.timestamp() >= float(target):
                    return predicate
        return None


def _numeric_suffix(value: str) -> int:
    suffix = value.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


__all__ = ["AlarmStore", "ResumeWatcher"]
