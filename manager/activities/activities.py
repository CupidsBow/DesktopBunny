"""活动生命周期：开始、推进、暂停、恢复、完成与失败。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Iterable, Mapping

from manager.activities.store import ActivityStore
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import (
    Activity,
    ActivityCheckpoint,
    ActivityError,
    ActivityStatus,
    ActivityUpdate,
    AlarmPredicate,
)

logger = logging.getLogger(__name__)


class ActivityService:
    def __init__(
        self,
        store: ActivityStore,
        config: BrainConfig,
        *,
        now_fn=now_local,
    ) -> None:
        self.store = store
        self.config = config
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._last_checkpoint: dict[str, float] = {}
        self._step_checked: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def all(self) -> list[Activity]:
        return self.store.all()

    def open_activities(self) -> list[Activity]:
        return [item for item in self.all() if item.is_open]

    def active(self) -> Activity | None:
        for activity in self.all():
            if activity.status is ActivityStatus.ACTIVE:
                return activity
        return None

    def paused(self) -> Activity | None:
        for activity in self.all():
            if activity.status is ActivityStatus.PAUSED:
                return activity
        return None

    def get(self, activity_id: str) -> Activity | None:
        return self.store.get(activity_id)

    def is_restored(self, activity: Activity) -> bool:
        return activity.id in self.store.restored_ids

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(
        self,
        kind: str,
        goal: str,
        *,
        steps_limit: int = 8,
        note: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> Activity:
        kind = (kind or "").strip()
        goal = (goal or "").strip()
        if not kind:
            raise ActivityError("activity.start 缺少 kind")
        if not goal:
            raise ActivityError("activity.start 缺少 goal")
        with self._lock:
            current = self.active()
            if current is not None:
                raise ActivityError(
                    f"已有进行中的活动 {current.id}（{current.kind}），"
                    "请先暂停或放弃它"
                )
            limit = 8 if steps_limit is None else int(steps_limit)
            limit = max(1, min(limit, 100))
            now = self.now_fn().timestamp()
            data = dict(state or {})
            data.setdefault("progress", 0.0)
            if note:
                data["note"] = note
            activity = Activity(
                id=self.store.next_id(),
                kind=kind[:80],
                goal=goal[:2000],
                state=data,
                created_at=now,
                updated_at=now,
                steps_used=0,
                steps_limit=limit,
                status=ActivityStatus.ACTIVE,
                checkpoint={"progress": float(data.get("progress", 0.0))},
                last_result="",
            )
            self.store.upsert(activity)
            self._last_checkpoint[activity.id] = now
            logger.info("活动开始 %s：%s", activity.id, activity.goal)
            return activity

    def pause(
        self,
        activity_id: str | None = None,
        *,
        note: str | None = None,
        resume_when: Iterable[AlarmPredicate] | None = None,
    ) -> Activity:
        with self._lock:
            activity = self._require(activity_id, {ActivityStatus.ACTIVE})
            activity.status = ActivityStatus.PAUSED
            activity.updated_at = self.now_fn().timestamp()
            activity.checkpoint.update(
                {
                    "progress": activity.progress,
                    "last_result": activity.last_result,
                    "note": note or activity.state.get("note", ""),
                }
            )
            if note:
                activity.state["note"] = note
            activity.resume_when = list(resume_when or [])
            self._last_checkpoint.pop(activity.id, None)
            self.store.upsert(activity)
            logger.info(
                "活动暂停 %s，resume_when=%s", activity.id, len(activity.resume_when)
            )
            return activity

    def resume(self, activity_id: str | None = None) -> Activity:
        with self._lock:
            activity = self._require(activity_id, {ActivityStatus.PAUSED})
            activity.status = ActivityStatus.ACTIVE
            activity.updated_at = self.now_fn().timestamp()
            activity.resume_when = []
            activity.checkpoint.pop("restored_from_active", None)
            self._last_checkpoint[activity.id] = activity.updated_at
            self.store.upsert(activity)
            logger.info("活动恢复 %s", activity.id)
            return activity

    def complete(
        self,
        activity_id: str | None = None,
        *,
        outcome: str = "completed",
        summary: str | None = None,
    ) -> Activity:
        with self._lock:
            activity = self._require(activity_id, None)
            status = (
                ActivityStatus.COMPLETED
                if outcome == "completed"
                else ActivityStatus.ABANDONED
            )
            if outcome == "failed":
                status = ActivityStatus.FAILED
            activity.status = status
            activity.updated_at = self.now_fn().timestamp()
            activity.resume_when = []
            if summary:
                activity.last_result = summary[:2000]
            activity.checkpoint["outcome"] = status.value
            self._last_checkpoint.pop(activity.id, None)
            self.store.upsert(activity)
            logger.info("活动结束 %s：%s", activity.id, status.value)
            return activity

    def abandon(
        self,
        activity_id: str | None = None,
        *,
        outcome: str = "abandoned",
        summary: str | None = None,
    ) -> Activity:
        if outcome == "completed":
            return self.complete(activity_id, outcome="completed", summary=summary)
        return self.complete(activity_id, outcome="abandoned", summary=summary)

    def fail(self, activity_id: str, reason: str = "") -> Activity:
        with self._lock:
            activity = self._require(activity_id, None)
            activity.status = ActivityStatus.FAILED
            activity.updated_at = self.now_fn().timestamp()
            activity.resume_when = []
            if reason:
                activity.checkpoint["failure_reason"] = reason[:500]
            self._last_checkpoint.pop(activity.id, None)
            self.store.upsert(activity)
            logger.warning("活动失败 %s：%s", activity.id, reason)
            return activity

    # ------------------------------------------------------------------
    # 推进
    # ------------------------------------------------------------------
    def update(
        self, update: ActivityUpdate, activity_id: str | None = None
    ) -> Activity | None:
        if update is None:
            return self.active()
        with self._lock:
            activity = activity_id and self.store.get(activity_id) or self.active()
            if activity is None:
                return None
            if update.progress is not None:
                progress = max(0.0, min(1.0, float(update.progress)))
                activity.state["progress"] = progress
            if update.last_result is not None:
                activity.last_result = str(update.last_result)[:4000]
                activity.state["last_result"] = activity.last_result
            if update.note:
                activity.state["note"] = str(update.note)[:2000]
            activity.updated_at = self.now_fn().timestamp()
            activity.checkpoint.update(
                {
                    "progress": activity.progress,
                    "last_result": activity.last_result,
                }
            )
            self.store.upsert(activity)
            return activity

    def record_step(self, activity_id: str) -> bool:
        """自增步数；返回是否刚好到达 steps_limit（用于发检查点）。"""
        with self._lock:
            activity = self.store.get(activity_id)
            if activity is None or activity.status is not ActivityStatus.ACTIVE:
                return False
            activity.steps_used += 1
            activity.updated_at = self.now_fn().timestamp()
            self.store.upsert(activity)
            return activity.steps_used == activity.steps_limit

    def record_failure(self, activity_id: str) -> Activity | None:
        with self._lock:
            activity = self.store.get(activity_id)
            if activity is None:
                return None
            activity.consecutive_failures += 1
            activity.updated_at = self.now_fn().timestamp()
            if (
                activity.consecutive_failures
                >= self.config.activity_max_consecutive_failures
            ):
                activity.status = ActivityStatus.FAILED
                activity.resume_when = []
                self._last_checkpoint.pop(activity.id, None)
            self.store.upsert(activity)
            return activity

    def record_success(self, activity_id: str) -> Activity | None:
        with self._lock:
            activity = self.store.get(activity_id)
            if activity is None:
                return None
            if activity.consecutive_failures:
                activity.consecutive_failures = 0
                activity.updated_at = self.now_fn().timestamp()
                self.store.upsert(activity)
            return activity

    def set_resume_baseline(
        self, activity_id: str, baseline: dict[str, Any]
    ) -> Activity | None:
        with self._lock:
            activity = self.store.get(activity_id)
            if activity is None:
                return None
            activity.checkpoint["resume_baseline"] = dict(baseline)
            self.store.upsert(activity)
            return activity

    def clear_resume_when(self, activity_id: str) -> Activity | None:
        with self._lock:
            activity = self.store.get(activity_id)
            if activity is None:
                return None
            activity.resume_when = []
            activity.checkpoint.pop("resume_baseline", None)
            self.store.upsert(activity)
            return activity

    # ------------------------------------------------------------------
    # 检查点
    # ------------------------------------------------------------------
    def checkpoint_for(
        self, activity: Activity, reason: str
    ) -> ActivityCheckpoint:
        self._last_checkpoint[activity.id] = self.now_fn().timestamp()
        return ActivityCheckpoint(
            activity_id=activity.id,
            kind=activity.kind,
            progress=activity.progress,
            steps_used=activity.steps_used,
            reason=reason,
        )

    def due_checkpoint(
        self, now: datetime | None = None
    ) -> ActivityCheckpoint | None:
        activity = self.active()
        if activity is None:
            return None
        now = now or self.now_fn()
        stamp = now.timestamp()
        last = self._last_checkpoint.get(activity.id, activity.updated_at)
        if stamp - last < self.config.activity_checkpoint_interval:
            return None
        return self.checkpoint_for(activity, "interval")

    def step_checkpoint(self, activity_id: str) -> ActivityCheckpoint | None:
        activity = self.store.get(activity_id)
        if activity is None or not activity.steps_used:
            return None
        if activity.steps_used != activity.steps_limit:
            return None
        if self._step_checked.get(activity_id) == activity.steps_used:
            return None
        self._step_checked[activity_id] = activity.steps_used
        return self.checkpoint_for(activity, "steps_limit")

    # ------------------------------------------------------------------
    def _require(
        self, activity_id: str | None, allowed: set[ActivityStatus] | None
    ) -> Activity:
        if activity_id:
            activity = self.store.get(activity_id)
        else:
            activity = self.active() or self.paused()
        if activity is None:
            raise ActivityError("没有可操作的活动")
        if allowed is not None and activity.status not in allowed:
            raise ActivityError(
                f"活动 {activity.id} 当前状态为 {activity.status.value}"
            )
        return activity


__all__ = ["ActivityService"]
