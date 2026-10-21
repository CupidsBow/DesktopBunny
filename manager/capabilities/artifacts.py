"""大结果的 artifact 存储：``artifact://<kind>/<id>``。"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"^artifact://(?P<kind>[A-Za-z0-9_\-]+)/(?P<id>\d+)$")
_SAFE_KIND = re.compile(r"[^A-Za-z0-9_\-]")


class ArtifactStore:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def save(self, kind: str, data: Any) -> str:
        kind = _SAFE_KIND.sub("_", kind or "misc")[:40] or "misc"
        with self._lock:
            next_id = self._next_id(kind)
            self._counters[kind] = next_id
            path = os.path.join(self.directory, kind, f"{next_id}.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "kind": kind,
                "id": next_id,
                "content": data if _jsonable(data) else str(data),
            }
            temp = path + ".tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(temp, path)
        return f"artifact://{kind}/{next_id}"

    def read(
        self, ref: str, offset: int = 0, limit: int = 4000
    ) -> dict[str, Any]:
        kind, artifact_id = self.parse_ref(ref)
        path = os.path.join(self.directory, kind, f"{artifact_id}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"artifact 不存在: {ref}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        content = payload.get("content")
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 4000), 200_000))
        chunk = text[offset : offset + limit]
        next_offset = offset + len(chunk)
        return {
            "ref": ref,
            "content": chunk,
            "offset": offset,
            "next_offset": next_offset,
            "total_chars": len(text),
            "eof": next_offset >= len(text),
        }

    @staticmethod
    def parse_ref(ref: str) -> tuple[str, int]:
        match = _REF_RE.match((ref or "").strip())
        if not match:
            raise ValueError(f"非法 artifact 引用: {ref!r}")
        return match.group("kind"), int(match.group("id"))

    def _next_id(self, kind: str) -> int:
        known = self._counters.get(kind, 0)
        folder = os.path.join(self.directory, kind)
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                stem, ext = os.path.splitext(name)
                if ext == ".json" and stem.isdigit():
                    known = max(known, int(stem))
        return known + 1


def _jsonable(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
        return True
    except (TypeError, ValueError):
        return False


__all__ = ["ArtifactStore"]
