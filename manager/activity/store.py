"""``activities.json`` 的读写与重启恢复。"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from manager.shared.types import Activity, ActivityStatus

logger = logging.getLogger(__name__)


class ActivityStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._loaded = False
        self._activities: list[Activity] = []
        self.restored_ids: set[str] = set()

    def load(self) -> list[Activity]:
        with self._lock:
            if self._loaded:
                return [Activity.from_dict(item.to_dict()) for item in self._activities]
            self._loaded = True
            self._activities = self._read()
            restored: list[Activity] = []
            for activity in self._activities:
                if activity.status is ActivityStatus.ACTIVE:
                    activity.status = ActivityStatus.PAUSED
                    activity.checkpoint["restored_from_active"] = True
                    self.restored_ids.add(activity.id)
                    restored.append(activity)
            if restored:
                self._write(self._activities)
                logger.info("恢复了 %s 个未完成活动（转为 paused）", len(restored))
            return [Activity.from_dict(item.to_dict()) for item in self._activities]

    def all(self) -> list[Activity]:
        with self._lock:
            self.load()
            return [Activity.from_dict(item.to_dict()) for item in self._activities]

    def get(self, activity_id: str) -> Activity | None:
        with self._lock:
            self.load()
            for activity in self._activities:
                if activity.id == activity_id:
                    return Activity.from_dict(activity.to_dict())
        return None

    def upsert(self, activity: Activity) -> None:
        with self._lock:
            self.load()
            for index, existing in enumerate(self._activities):
                if existing.id == activity.id:
                    self._activities[index] = Activity.from_dict(activity.to_dict())
                    break
            else:
                self._activities.append(Activity.from_dict(activity.to_dict()))
            self._write(self._activities)

    def next_id(self) -> str:
        with self._lock:
            self.load()
            maximum = 0
            for activity in self._activities:
                if activity.id.startswith("act_"):
                    suffix = activity.id[4:]
                    if suffix.isdigit():
                        maximum = max(maximum, int(suffix))
            return f"act_{maximum + 1}"

    # ------------------------------------------------------------------
    def _read(self) -> list[Activity]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data: Any = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("读取 activities.json 失败：%s", exc)
            return []
        if isinstance(data, dict):
            data = data.get("activities") or []
        if not isinstance(data, list):
            return []
        activities: list[Activity] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                activities.append(Activity.from_dict(item))
            except (ValueError, TypeError) as exc:
                logger.warning("跳过损坏的活动记录：%s", exc)
        return activities

    def _write(self, activities: list[Activity]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"activities": [item.to_dict() for item in activities]}
        temp = f"{self.path}.tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)


__all__ = ["ActivityStore"]
