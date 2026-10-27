"""关键词召回：按词元扫描索引与记忆正文，返回摘要与引用。

由情境装配每帧自动调用（见 ``frame_generator/situation.py``），不再是能力。
不做向量检索，也不替 LLM 判断相关性；只给候选项。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from manager.memory.index import MemoryIndex
from manager.memory.store import MemoryStore
from manager.shared.config import BrainConfig

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[一-鿿]")


def _tokens(text: str) -> set[str]:
    raw = [token.lower() for token in _WORD_RE.findall(text or "")]
    tokens = set(raw)
    for left, right in zip(raw, raw[1:]):
        if len(left) == 1 and len(right) == 1 and _is_cjk(left) and _is_cjk(right):
            tokens.add(left + right)
    return tokens


def _is_cjk(char: str) -> bool:
    return "一" <= char <= "鿿"


class Recall:
    def __init__(
        self, store: MemoryStore, index: MemoryIndex, config: BrainConfig
    ) -> None:
        self.store = store
        self.index = index
        self.config = config

    def recall(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = limit or self.config.recall_max_episodes
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        candidates: list[dict[str, Any]] = []
        entries = self.index.entries()
        if not entries:
            # 索引损坏/为空时退化为扫描情景文件
            for episode in self.store.list_episodes():
                entries.append(
                    _entry_from_episode(episode)
                )

        for entry in entries:
            haystack = f"{entry.name} {entry.description} {entry.path}"
            score = self._score(query, query_tokens, haystack)
            summary = entry.description
            if score > 0:
                body = self._read_body(entry.path)
                if body:
                    score += self._score(query, query_tokens, body) * 0.35
                    if not summary:
                        summary = body.strip().splitlines()[0][:160]
            if score <= 0:
                continue
            salience = abs(entry.valence or 0.0) * (
                0.5 + (entry.arousal or 0.0)
            )
            candidates.append(
                {
                    "ref": entry.path,
                    "name": entry.name,
                    "summary": summary or entry.name,
                    "valence": entry.valence,
                    "arousal": entry.arousal,
                    "score": round(score + salience * 0.05, 3),
                }
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:limit]

    def _read_body(self, path: str) -> str:
        try:
            return self.store.read_text(path)
        except (OSError, ValueError) as exc:
            logger.debug("读取记忆 %s 失败：%s", path, exc)
            return ""

    @staticmethod
    def _score(query: str, query_tokens: set[str], haystack: str) -> float:
        text = (haystack or "").lower()
        if not text:
            return 0.0
        hits = sum(1 for token in query_tokens if token in text)
        if hits == 0:
            return 0.0
        score = hits / math.sqrt(len(query_tokens))
        if query.lower() in text:
            score += 2.0
        return score


def _entry_from_episode(episode) -> Any:
    from manager.shared.types import MemoryIndexEntry

    return MemoryIndexEntry(
        name=episode.name,
        path=episode.path,
        description=episode.description,
        valence=episode.metadata.valence,
        arousal=episode.metadata.arousal,
    )
