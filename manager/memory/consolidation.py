"""记忆整理：定时把情景记忆压成自我/目标/偏好/好友档案。

这是一个独立的 LLM 任务，不属于认知帧；它只产出 ``MemoryWriteOp``，
不直接改文件，也不参与任何一帧的决策。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from manager.memory.memory import Memory
from manager.shared.config import BrainConfig
from manager.shared.jsonutil import extract_json
from manager.shared.llm_client import LLMClient
from manager.shared.timeutil import now_local, parse_iso, to_iso
from manager.shared.types import MemoryMetadata, MemoryWriteOp, MemoryWriteType

logger = logging.getLogger(__name__)

_ALLOWED_OPS = {
    MemoryWriteType.UPDATE_SEMANTIC,
    MemoryWriteType.UPDATE_INDEX,
    MemoryWriteType.ARCHIVE,
}

_SYSTEM_PROMPT = """你是 Alice 的记忆整理程序，不是 Alice 本人。
你只做一件事：把最近的情景记忆整理成长期记忆，并输出 JSON。

规则：
1. 只保留跨天仍有用的内容：稳定的事实、偏好、关系变化、长期目标。
2. 不编造。没有依据的内容不要写。
3. 合并旧内容，不要简单堆叠；同一件事只留一句结论。
4. self.md 写“我是谁”；goals.md 写长期目标；preferences.md 写稳定偏好；
   friends/<id>.md 写对某个人的印象与共同经历。
5. 每条 content 都是完整的 Markdown 文件内容，会覆盖旧文件。
6. 最多输出 12 个操作；没有值得更新的就返回空的 operations。

输出格式（只输出 JSON，不要解释）：
{"operations": [
  {"op": "update_semantic", "target": "self.md", "content": "# 我是谁\\n..."},
  {"op": "update_semantic", "target": "friends/12345.md", "content": "## 印象\\n..."},
  {"op": "archive", "target": "episodes/旧文件.md"}
]}
"""


class Consolidator:
    def __init__(
        self,
        memory: Memory,
        llm: LLMClient,
        config: BrainConfig,
        *,
        now_fn=now_local,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.config = config
        self.now_fn = now_fn
        self._last_run: datetime | None = self._load_last_run()
        self._running = False

    # ------------------------------------------------------------------
    def due(self, now: datetime | None = None) -> bool:
        if self._running:
            return False
        now = now or self.now_fn()
        if self._last_run is None:
            return True
        return (now - self._last_run).total_seconds() >= self.config.consolidation_interval

    def mark_ran(self, when: datetime | None = None) -> None:
        self._last_run = when or self.now_fn()
        self._save_last_run()

    def run(self, now: datetime | None = None) -> list[MemoryWriteOp]:
        """同步执行一次整理；返回提交给写队列的操作。"""
        if self._running:
            return []
        self._running = True
        now = now or self.now_fn()
        try:
            episodes = self.memory.recent_episodes(
                7 * 86400.0, limit=40, now=now
            )
            if not episodes:
                self.mark_ran(now)
                return []
            prompt = self._build_prompt(episodes, now)
            try:
                raw = self.llm.chat(
                    [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                )
            except Exception as exc:
                logger.warning("记忆整理调用失败：%s", exc)
                return []

            ops = self._parse_ops(raw)
            for op in ops:
                self.memory.submit(op)
            self.mark_ran(now)
            logger.info("记忆整理完成：%s 条情景，%s 个写操作", len(episodes), len(ops))
            return ops
        finally:
            self._running = False

    # ------------------------------------------------------------------
    def _build_prompt(self, episodes, now: datetime) -> str:
        parts: list[str] = []
        parts.append(f"现在：{to_iso(now)}")
        parts.append("\n## 当前长期记忆\n")
        for label, path in (
            ("自我", self.memory.paths.self_path),
            ("目标", self.memory.paths.goals_path),
            ("偏好", self.memory.paths.preferences_path),
        ):
            text = self.memory._read_summary(path)
            parts.append(f"### {label}\n{text or '（空）'}\n")

        friends = self._list_friends()
        parts.append("### 已知好友\n" + ("、".join(friends) if friends else "（无）"))
        for friend_id in friends[:12]:
            profile = self.memory.friend(friend_id)
            if profile.impression or profile.shared_history:
                parts.append(
                    f"- {friend_id}：{profile.impression or ''}"
                    f"{('；' + profile.shared_history) if profile.shared_history else ''}"
                )

        parts.append("\n## 最近的情景记忆\n")
        for episode in episodes:
            when = parse_iso(episode.metadata.time)
            stamp = f"{when:%m-%d %H:%M}" if when else "未知时间"
            speaker = episode.metadata.speaker or "alice"
            body = " ".join(episode.body.split())
            if len(body) > 500:
                body = body[:500] + "…"
            parts.append(
                f"- [{stamp}] {speaker} · {episode.metadata.emotion}"
                f"（v={episode.metadata.valence:+.1f} a={episode.metadata.arousal:.1f}）"
                f" {episode.description} :: {body}"
            )
        parts.append(
            "\n请输出整理后的长期记忆操作。目标路径可选：self.md、goals.md、"
            "preferences.md、friends/<id>.md；档案类内容请用 "
            "“## 印象”和“## 共同经历”两节。"
        )
        return "\n".join(parts)

    def _list_friends(self) -> list[str]:
        directory = self.memory.paths.friends_dir
        if not os.path.isdir(directory):
            return []
        return sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(directory)
            if name.endswith(".md")
        )

    def _parse_ops(self, raw: str) -> list[MemoryWriteOp]:
        try:
            data = extract_json(raw)
        except ValueError as exc:
            logger.warning("记忆整理输出不是 JSON：%s", exc)
            return []
        items: Any
        if isinstance(data, dict):
            items = data.get("operations") or data.get("ops") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []

        ops: list[MemoryWriteOp] = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            try:
                op = MemoryWriteOp.from_dict(item)
            except (ValueError, TypeError) as exc:
                logger.debug("跳过非法记忆操作：%s", exc)
                continue
            if op.op not in _ALLOWED_OPS:
                continue
            if op.op is not MemoryWriteType.ARCHIVE:
                op.target = self.memory.store.normalize_target(op.target)
                if not op.target:
                    continue
                limit = self._content_limit(op.target)
                op.content = (op.content or "").strip()
                if not op.content:
                    continue
                if len(op.content.encode("utf-8")) > limit:
                    op.content = _truncate_bytes(op.content, limit)
            else:
                try:
                    op.target = self.memory.store.normalize_target(op.target)
                except ValueError:
                    continue
                if not op.target:
                    continue
            if not op.metadata.time:
                op.metadata.time = to_iso(self.now_fn())
            ops.append(op)
        return ops

    def _content_limit(self, target: str) -> int:
        if target == "self.md":
            return self.config.self_max_bytes
        if target == "goals.md":
            return self.config.goals_max_bytes
        if target == "preferences.md":
            return self.config.preferences_max_bytes
        if target.startswith("friends/"):
            return self.config.self_max_bytes
        return self.config.episode_max_bytes

    # ------------------------------------------------------------------
    def _state_path(self) -> str:
        return os.path.join(self.memory.paths.state_dir, "consolidation.json")

    def _load_last_run(self) -> datetime | None:
        path = self._state_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return parse_iso(data.get("last_run"))
        except (OSError, ValueError):
            return None

    def _save_last_run(self) -> None:
        if self._last_run is None:
            return
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"last_run": to_iso(self._last_run)}, handle)
        except OSError as exc:
            logger.warning("保存整理时间失败：%s", exc)


def _truncate_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore")


__all__ = ["Consolidator", "MemoryMetadata"]
