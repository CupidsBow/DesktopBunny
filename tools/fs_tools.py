"""文件系统工具（纯函数，不导入 manager）。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def list_dir(path: str = ".", limit: int = 200) -> dict[str, Any]:
    directory = Path(path or ".").expanduser()
    if not directory.is_dir():
        raise FileNotFoundError(f"目录不存在: {directory}")
    limit = max(1, min(int(limit or 200), 1000))
    entries: list[dict[str, Any]] = []
    for child in sorted(
        directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
    )[:limit]:
        try:
            stat = child.stat()
            size = stat.st_size
            modified = int(stat.st_mtime)
        except OSError:
            size = None
            modified = None
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir(),
                "size": size,
                "modified": modified,
            }
        )
    return {"path": str(directory), "entries": entries}


def read_file(
    path: str,
    offset: int = 0,
    limit: int | None = None,
    max_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    target = Path(path or "").expanduser()
    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"这是目录: {target}")
    max_bytes = max(1, int(max_bytes or 256 * 1024))
    offset = int(offset or 0)
    with open(target, "rb") as handle:
        if offset > 0:
            handle.seek(offset)
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    if limit is not None:
        text = text[: max(0, int(limit))]
    return {
        "path": str(target),
        "content": text,
        "offset": offset,
        "truncated": truncated,
        "bytes": len(raw),
    }


_GLOB_CHARS = set("*?[")


def search_files(
    path: str = ".",
    pattern: str = "*",
    limit: int = 100,
    max_depth: int = 8,
) -> dict[str, Any]:
    root = Path(path or ".").expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {root}")
    limit = max(1, min(int(limit or 100), 500))
    pattern = pattern or "*"

    matches: list[dict[str, Any]] = []
    if set(pattern) & _GLOB_CHARS:
        iterator = root.rglob(pattern)
        for item in iterator:
            if _depth(root, item) > max_depth:
                continue
            matches.append(_entry(item))
            if len(matches) >= limit:
                break
    else:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"非法正则: {exc}") from exc
        for item in root.rglob("*"):
            if _depth(root, item) > max_depth:
                continue
            if regex.search(item.name):
                matches.append(_entry(item))
                if len(matches) >= limit:
                    break
    return {"path": str(root), "pattern": pattern, "matches": matches}


def _entry(item: Path) -> dict[str, Any]:
    try:
        size = item.stat().st_size
    except OSError:
        size = None
    return {"path": str(item), "is_dir": item.is_dir(), "size": size}


def _depth(root: Path, item: Path) -> int:
    try:
        return len(item.relative_to(root).parts)
    except ValueError:
        return 999


def write_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
    target = Path(path or "").expanduser()
    if not str(target):
        raise ValueError("path 不能为空")
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    data = content if isinstance(content, str) else str(content)
    with open(target, mode, encoding="utf-8", newline="") as handle:
        handle.write(data)
    return {
        "path": str(target),
        "bytes": len(data.encode("utf-8")),
        "appended": bool(append),
        "size": os.path.getsize(target),
    }


__all__ = ["list_dir", "read_file", "search_files", "write_file"]
