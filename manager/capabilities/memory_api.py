"""把记忆门面适配成能力；工具层不接触记忆实现。"""

from __future__ import annotations

from typing import Any

from manager.memory.memory import Memory

_ALLOWED_PREFIXES = ("friends/",)
_ALLOWED_FILES = {"self.md", "goals.md", "preferences.md"}


class MemoryApi:
    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def recall(self, query: str, limit: int = 8) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        limit = max(1, min(int(limit or 8), 20))
        return {"query": query, "results": self.memory.recall(query, limit)}

    def read(self, target: str) -> dict[str, Any]:
        target = self._normalize(target)
        return {"target": target, "content": self.memory.read_semantic(target)}

    def write(self, target: str, content: str) -> dict[str, Any]:
        target = self._normalize(target)
        content = (content or "").strip()
        if not content:
            raise ValueError("content 不能为空")
        op_id = self.memory.write_semantic(target, content)
        return {"submitted": op_id, "target": target, "chars": len(content)}

    def friend(self, friend_id: str) -> dict[str, Any]:
        profile = self.memory.friend(friend_id)
        return profile.to_dict()

    @staticmethod
    def _normalize(target: str) -> str:
        text = str(target or "").replace("\\", "/").strip().lstrip("/")
        if text.startswith("memory/"):
            text = text[len("memory/") :]
        if text.startswith("episodes/") or "/episodes/" in f"/{text}":
            raise ValueError("情景记忆只能通过 remember 写入")
        if text not in _ALLOWED_FILES and not text.startswith(_ALLOWED_PREFIXES):
            raise ValueError(
                "target 只能是 self.md、goals.md、preferences.md 或 friends/<id>.md"
            )
        if ".." in text.split("/"):
            raise ValueError("非法 target")
        if text.startswith("friends/") and not text.endswith(".md"):
            raise ValueError("好友档案必须以 .md 结尾")
        return text


__all__ = ["MemoryApi"]
