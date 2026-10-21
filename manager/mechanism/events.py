"""收件箱与事件账本：只追加，只有 ``handled`` 改变状态。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Iterable, Mapping

from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
    ArrivedDuring,
    Event,
    EventKind,
    EventSource,
    EventStatus,
)

logger = logging.getLogger(__name__)


class EventLedger:
    def __init__(
        self,
        path: str,
        config: BrainConfig,
        *,
        now_fn=now_local,
    ) -> None:
        self.path = path
        self.config = config
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._loaded = False
        self._events: dict[str, Event] = {}
        self._order: list[str] = []
        self._counter = 0

    # ------------------------------------------------------------------
    def load(self) -> list[Event]:
        with self._lock:
            if self._loaded:
                return self._snapshot()
            self._loaded = True
            if not os.path.exists(self.path):
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = Event.from_dict(json.loads(line))
                        except (ValueError, KeyError, TypeError) as exc:
                            logger.warning("跳过损坏的事件行：%s", exc)
                            continue
                        if not event.id:
                            continue
                        if event.id not in self._events:
                            self._order.append(event.id)
                        self._events[event.id] = event
                        self._counter = max(
                            self._counter, _numeric_suffix(event.id)
                        )
            except OSError as exc:
                logger.warning("读取事件账本失败：%s", exc)
            return self._snapshot()

    def all(self) -> list[Event]:
        with self._lock:
            self.load()
            return self._snapshot()

    def unhandled(self) -> list[Event]:
        return [
            event
            for event in self.all()
            if event.status is EventStatus.UNHANDLED
        ]

    def unhandled_ids(self) -> set[str]:
        return {event.id for event in self.unhandled()}

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            self.load()
            return self._events.get(event_id)

    # ------------------------------------------------------------------
    def add(
        self,
        kind: EventKind | str,
        *,
        source: EventSource | Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        arrived_during: ArrivedDuring | None = None,
        at: datetime | None = None,
    ) -> Event:
        with self._lock:
            self.load()
            self._counter += 1
            event = Event(
                id=f"ev_{self._counter:06d}",
                kind=_coerce_kind(kind),
                at=to_iso(at or self.now_fn()),
                source=(
                    source
                    if isinstance(source, EventSource)
                    else EventSource.from_dict(source)
                ),
                payload=dict(payload or {}),
                arrived_during=arrived_during,
                status=EventStatus.UNHANDLED,
            )
            self._store(event)
            self._append(event)
            return event

    def add_event(self, event: Event) -> Event:
        with self._lock:
            self.load()
            if not event.id:
                self._counter += 1
                event.id = f"ev_{self._counter:06d}"
            else:
                self._counter = max(self._counter, _numeric_suffix(event.id))
            self._store(event)
            self._append(event)
            return event

    def mark_handled(self, event_ids: Iterable[str]) -> list[Event]:
        updated: list[Event] = []
        with self._lock:
            self.load()
            for event_id in event_ids:
                event = self._events.get(event_id)
                if event is None or event.status is EventStatus.HANDLED:
                    continue
                event.status = EventStatus.HANDLED
                updated.append(event)
                self._append(event)
            return updated

    # ------------------------------------------------------------------
    def _snapshot(self) -> list[Event]:
        return [
            Event.from_dict(self._events[event_id].to_dict())
            for event_id in self._order
            if event_id in self._events
        ]

    def _store(self, event: Event) -> None:
        if event.id not in self._events:
            self._order.append(event.id)
        self._events[event.id] = event

    def _append(self, event: Event) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
                )
        except OSError as exc:
            logger.warning("追加事件账本失败：%s", exc)


def _coerce_kind(kind: EventKind | str) -> EventKind:
    if isinstance(kind, EventKind):
        return kind
    try:
        return EventKind(str(kind))
    except ValueError:
        return EventKind.SYSTEM


def _numeric_suffix(event_id: str) -> int:
    suffix = event_id.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


__all__ = ["EventLedger"]
