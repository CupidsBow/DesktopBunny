"""能力审计日志：JSONL，参数脱敏。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Mapping

from manager.shared.timeutil import to_iso
from manager.shared.types import AuditRecord

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
)
_MAX_VALUE = 200


class AuditLog:
    def __init__(self, path: str, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()

    def record(
        self,
        capability: str,
        args: Mapping[str, Any] | None,
        *,
        ok: bool,
        duration_ms: float,
        caller: str = "",
        at: datetime | None = None,
    ) -> AuditRecord:
        entry = AuditRecord(
            time=to_iso(at or datetime.now()),
            capability=capability,
            args_summary=summarize_args(args),
            ok=ok,
            duration_ms=round(float(duration_ms), 1),
            caller=caller,
        )
        if self.enabled:
            self._append(entry)
        return entry

    def _append(self, entry: AuditRecord) -> None:
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            logger.warning("写审计日志失败：%s", exc)

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()[-max(1, int(limit)) :]
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except ValueError:
                continue
        return result


def summarize_args(args: Mapping[str, Any] | None) -> str:
    if not args:
        return "{}"
    cleaned: dict[str, Any] = {}
    for key, value in args.items():
        key_text = str(key)
        if any(marker in key_text.lower() for marker in _SENSITIVE_KEYS):
            cleaned[key_text] = "***"
            continue
        cleaned[key_text] = _truncate(value)
    try:
        return json.dumps(cleaned, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _truncate(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE else value[:_MAX_VALUE] + "…"
    if isinstance(value, Mapping):
        return {
            str(key): _truncate(item, depth + 1)
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_truncate(item, depth + 1) for item in list(value)[:30]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(str(value), depth + 1)


__all__ = ["AuditLog", "summarize_args"]
