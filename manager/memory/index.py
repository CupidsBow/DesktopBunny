"""``MEMORY.md`` 索引：一行一个可召回单元。"""

from __future__ import annotations

import logging
import os
import re
import threading

from manager.shared.config import BrainConfig
from manager.shared.types import MemoryIndexEntry

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(
    r"^-\s+\[(?P<name>[^\]]+)\]\((?P<path>[^)]+)\)\s*—\s*(?P<rest>.*)$"
)
_TRAILER_RE = re.compile(
    r"\s*·\s*v=(?P<valence>-?\d+(?:\.\d+)?)\s+a=(?P<arousal>-?\d+(?:\.\d+)?)\s*$"
)


def parse_line(line: str) -> MemoryIndexEntry | None:
    match = _LINE_RE.match(line.strip())
    if not match:
        return None
    rest = match.group("rest").strip()
    valence = arousal = None
    trailer = _TRAILER_RE.search(rest)
    if trailer:
        valence = float(trailer.group("valence"))
        arousal = float(trailer.group("arousal"))
        rest = rest[: trailer.start()].strip()
    return MemoryIndexEntry(
        name=match.group("name").strip(),
        path=match.group("path").strip(),
        description=rest,
        valence=valence,
        arousal=arousal,
    )


def render_line(entry: MemoryIndexEntry) -> str:
    """索引文件的存储格式（含路径），解析回来要一一对应，别随意改。"""
    description = " ".join((entry.description or "").split())
    line = f"- [{entry.name}]({entry.path}) — {description}"
    if entry.valence is not None and entry.arousal is not None:
        line += f" · v={entry.valence:g} a={entry.arousal:g}"
    return line


def render_brief(entry: MemoryIndexEntry) -> str:
    """给认知看的一行摘要：日期 + 描述 + 情绪强度。

    不带路径——每行能省七八十字，而她在对话里引用不了文件。真正需要原文时
    由召回按条目取。
    """
    description = " ".join((entry.description or "").split())
    name = (entry.name or "").strip()
    date = name[:10] if len(name) >= 10 and name[:4].isdigit() else ""
    label = f"{date} {description}".strip() if date else (description or name)
    line = f"- {label}"
    if entry.valence is not None and entry.arousal is not None:
        line += f" · v={entry.valence:g} a={entry.arousal:g}"
    return line


class MemoryIndex:
    """索引的解析、增删改与体积约束。"""

    def __init__(self, path: str, config: BrainConfig) -> None:
        self.path = path
        self.max_lines = config.memory_index_max_lines
        self.max_bytes = config.memory_index_max_bytes
        self._lock = threading.RLock()
        self._entries: list[MemoryIndexEntry] | None = None

    def load(self) -> list[MemoryIndexEntry]:
        with self._lock:
            if self._entries is not None:
                return list(self._entries)
            entries: list[MemoryIndexEntry] = []
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as handle:
                        for line in handle:
                            entry = parse_line(line)
                            if entry is not None:
                                entries.append(entry)
                except OSError as exc:
                    logger.warning("读取记忆索引失败：%s", exc)
            self._entries = entries
            return list(entries)

    def entries(self) -> list[MemoryIndexEntry]:
        return self.load()

    def add(self, entry: MemoryIndexEntry) -> None:
        with self._lock:
            current = self.load()
            for index, existing in enumerate(current):
                if existing.path == entry.path:
                    current[index] = entry
                    break
            else:
                current.append(entry)
            self._entries = current
            self._enforce_limits()
            self.save()

    def update_description(
        self,
        path: str,
        description: str,
        *,
        valence: float | None = None,
        arousal: float | None = None,
    ) -> None:
        with self._lock:
            current = self.load()
            for entry in current:
                if entry.path == path:
                    entry.description = description
                    if valence is not None:
                        entry.valence = valence
                    if arousal is not None:
                        entry.arousal = arousal
                    break
            else:
                name = os.path.splitext(os.path.basename(path))[0]
                current.append(
                    MemoryIndexEntry(
                        name=name,
                        path=path,
                        description=description,
                        valence=valence,
                        arousal=arousal,
                    )
                )
            self._entries = current
            self._enforce_limits()
            self.save()

    def remove(self, path: str) -> None:
        with self._lock:
            current = [entry for entry in self.load() if entry.path != path]
            self._entries = current
            self.save()

    def rebuild(self, entries: list[MemoryIndexEntry]) -> None:
        with self._lock:
            self._entries = list(entries)
            self._enforce_limits()
            self.save()

    def save(self) -> None:
        with self._lock:
            entries = self._entries or []
            lines = ["# Alice 的记忆索引", ""]
            lines.extend(render_line(entry) for entry in entries)
            text = "\n".join(lines).rstrip() + "\n"
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp = f"{self.path}.tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temp, self.path)

    def _enforce_limits(self) -> None:
        entries = self._entries or []
        while len(entries) > self.max_lines:
            entries.pop(0)
        while entries and self._size(entries) > self.max_bytes:
            entries.pop(0)
        self._entries = entries

    @staticmethod
    def _size(entries: list[MemoryIndexEntry]) -> int:
        return len(
            ("\n".join(render_line(entry) for entry in entries)).encode("utf-8")
        )
