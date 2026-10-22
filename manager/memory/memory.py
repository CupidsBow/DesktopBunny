"""记忆门面：给认知层一份只读视图，给执行层一个写入入口。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Iterable

from manager.memory.compression import L1Compressor
from manager.memory.index import MemoryIndex
from manager.memory.recall import Recall
from manager.memory.store import MemoryStore
from manager.memory.worker import MemoryWorker
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.llm_client import LLMClient
from manager.shared.timeutil import now_local, parse_iso, to_iso
from manager.shared.types import (
    Episode,
    FriendProfile,
    MemoryMetadata,
    MemoryView,
    MemoryWriteOp,
    MemoryWriteType,
    Remember,
)

logger = logging.getLogger(__name__)

_INDEX_VIEW_LIMIT = 60
_SUMMARY_LIMIT = 4096


class Memory:
    """L0/L1 的读取者，L2/L3 的异步写入者。"""

    def __init__(
        self,
        paths: BrainPaths,
        config: BrainConfig,
        *,
        llm: LLMClient | None = None,
        now_fn=now_local,
    ) -> None:
        self.paths = paths
        self.config = config
        self.now_fn = now_fn
        self.store = MemoryStore(paths, config)
        self.index = MemoryIndex(paths.index_path, config)
        self.recall_service = Recall(self.store, self.index, config)
        self.worker = MemoryWorker(self.store, self.index)
        self._recent_turns: list[str] = []
        self._l1_summary = ""
        self._buffer_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._stopped = False
        self.compressor = (
            L1Compressor(self, llm, config, now_fn=now_fn)
            if llm is not None
            else None
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._stopped = False
        self.store.ensure_layout()
        self.index.load()
        self._load_l1()
        if self.compressor is not None:
            self.compressor.start()
            if self.l1_overflow():
                self.compressor.notify()
        self.worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        # L1 每次变化都会落盘；这里再写一次，确保关闭前的最后状态完整。
        self._save_l1()
        self._stopped = True
        if self.compressor is not None:
            self.compressor.stop(min(2.0, timeout))
        self.worker.stop(timeout)

    def flush(self, timeout: float = 5.0) -> bool:
        return self.worker.flush(timeout)

    # ------------------------------------------------------------------
    # L1 存档：state/l1.json（摘要 + 最近对话 + 保存时间）
    # ------------------------------------------------------------------
    def _load_l1(self) -> None:
        """启动时恢复上一次的 L1；不按保存时间做任何过期判断。"""
        path = self.paths.l1_path
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("L1 存档读取失败，按空记忆启动：%s", exc)
            return
        if not isinstance(data, dict):
            logger.warning("L1 存档格式不正确，按空记忆启动：%s", path)
            return

        raw_summary = data.get("summary")
        summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
        limit = max(50, int(self.config.l1_summary_max_chars))
        if len(summary) > limit:
            summary = summary[:limit].rstrip() + "…"

        raw_turns = data.get("turns")
        turns = (
            [line for line in raw_turns if isinstance(line, str)]
            if isinstance(raw_turns, list)
            else []
        )
        hard_limit = max(1, int(self.config.l1_hard_max_lines))
        if len(turns) > hard_limit:
            dropped = len(turns) - hard_limit
            turns = turns[-hard_limit:]
            logger.warning("L1 存档超过行数上限，丢弃最早的 %s 行对话", dropped)

        with self._buffer_lock:
            self._l1_summary = summary
            self._recent_turns = turns
        logger.info(
            "已恢复 L1 工作记忆：摘要 %s 字，对话 %s 行（保存于 %s）",
            len(summary),
            len(turns),
            data.get("saved_at") or "未知",
        )

    def _save_l1(self) -> None:
        with self._save_lock:
            with self._buffer_lock:
                data = {
                    "summary": self._l1_summary,
                    "turns": list(self._recent_turns),
                    "saved_at": to_iso(self.now_fn()),
                }
            path = self.paths.l1_path
            temp_path = path + ".tmp"
            try:
                os.makedirs(self.paths.state_dir, exist_ok=True)
                with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                os.replace(temp_path, path)
            except OSError as exc:
                logger.warning("L1 存档写入失败：%s", exc)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def view(
        self,
        about_speaker: str | None = None,
        *,
        index_limit: int = _INDEX_VIEW_LIMIT,
    ) -> MemoryView:
        entries = self.index.entries()[-max(1, index_limit) :]
        profile = self.friend(about_speaker) if about_speaker else None
        return MemoryView(
            self_summary=self._read_summary(self.paths.self_path),
            goals_summary=self._read_summary(self.paths.goals_path),
            preferences_summary=self._read_summary(self.paths.preferences_path),
            about_speaker=self._about(about_speaker, profile),
            speaker_first_seen=profile.first_seen if profile else None,
            speaker_last_seen=profile.last_seen if profile else None,
            index=entries,
        )

    def _read_summary(self, path: str) -> str:
        try:
            text = self.store.read_text(path)
        except (OSError, ValueError) as exc:
            logger.debug("读取 %s 失败：%s", path, exc)
            return ""
        return text.strip()[:_SUMMARY_LIMIT]

    def _about(
        self, speaker: str | None, profile: FriendProfile | None = None
    ) -> str:
        if not speaker:
            return ""
        profile = profile or self.friend(speaker)
        parts: list[str] = []
        if profile.impression:
            parts.append(profile.impression)
        if profile.shared_history:
            parts.append("共同经历：" + profile.shared_history)
        text = "\n".join(parts).strip()
        if not text:
            # 第一次接触：没有档案或档案为空时，明确告诉 Alice 这是陌生人，
            # 避免把新人当成旧友，过于自来熟。
            return "（第一次接触，还不了解对方。）"
        return text[:_SUMMARY_LIMIT]

    def friend(self, friend_id: str) -> FriendProfile:
        try:
            return self.store.read_friend(friend_id)
        except (OSError, ValueError) as exc:
            logger.warning("读取好友档案 %s 失败：%s", friend_id, exc)
            return FriendProfile(friend_id=friend_id)

    def first_seen_map(self, friend_ids: Iterable[str]) -> dict[str, str]:
        """批量取好友的 ``first_seen``，供关系计算使用。"""
        result: dict[str, str] = {}
        for friend_id in friend_ids:
            first_seen = self.friend(friend_id).first_seen
            if first_seen:
                result[friend_id] = first_seen
        return result

    def recent_episodes(
        self,
        window_seconds: float,
        *,
        limit: int = 20,
        now: datetime | None = None,
    ) -> list[Episode]:
        now = parse_iso(now) or self.now_fn()
        episodes: list[Episode] = []
        for episode in reversed(self.store.list_episodes()):
            when = parse_iso(episode.metadata.time)
            if when is None:
                continue
            if (now - when).total_seconds() > window_seconds:
                break
            episodes.append(episode)
            if len(episodes) >= limit:
                break
        episodes.reverse()
        return episodes

    def recall(self, query: str, limit: int | None = None) -> list[dict]:
        return self.recall_service.recall(query, limit)

    def read_semantic(self, target: str) -> str:
        try:
            return self.store.read_text(target)
        except (OSError, ValueError) as exc:
            logger.warning("读取语义记忆 %s 失败：%s", target, exc)
            return ""

    def recent_context(self, max_chars: int | None = None) -> str:
        """L1：较早对话的压缩摘要 + 最近几轮对话原文，只存在内存。"""
        with self._buffer_lock:
            summary = self._l1_summary
            lines = list(self._recent_turns)
        parts: list[str] = []
        if summary:
            parts.append("[较早对话摘要]\n" + summary)
        if lines:
            parts.append("[最近对话]\n" + "\n".join(lines))
        if not parts:
            return ""
        text = "\n\n".join(parts)
        max_chars = max_chars or self.config.l1_context_max_chars
        if len(text) > max_chars:
            # 兜底：压缩不可用或持续失败时，宁可截断最旧内容也不撑爆提示词。
            text = text[-max_chars:]
            newline = text.find("\n")
            if newline >= 0:
                text = text[newline + 1 :]
        return text

    # ------------------------------------------------------------------
    # L1 压缩（由 compression.L1Compressor 在后台调用）
    # ------------------------------------------------------------------
    def l1_overflow(self) -> bool:
        with self._buffer_lock:
            return (
                self._l1_chars(self._l1_summary, self._recent_turns)
                > self.config.l1_context_max_chars
            )

    def l1_compression_snapshot(self) -> tuple[str, list[str]] | None:
        """返回（已有摘要, 待压缩的最早一段对话）；未超限时返回 None。"""
        with self._buffer_lock:
            lines = list(self._recent_turns)
            summary = self._l1_summary
            total = self._l1_chars(summary, lines)
            if total <= self.config.l1_context_max_chars or not lines:
                return None
            # 目标：压缩后回到上限的一半左右；单次最多带一个上限的原文，
            # 避免一次调用塞进过长的提示词。
            need = max(1, total - self.config.l1_context_max_chars // 2)
            chunk: list[str] = []
            size = 0
            for line in lines:
                chunk.append(line)
                size += len(line) + 1
                if size >= need or size >= self.config.l1_context_max_chars:
                    break
            if not chunk:
                return None
            return summary, chunk

    def apply_l1_compression(self, chunk: list[str], summary: str) -> bool:
        """用新摘要替换最早的一段对话；对话已变化时放弃本次结果。"""
        text = (summary or "").strip()
        if not chunk or not text:
            return False
        limit = max(50, int(self.config.l1_summary_max_chars))
        if len(text) > limit:
            text = text[:limit].rstrip() + "…"
        with self._buffer_lock:
            if self._stopped:
                return False
            if self._recent_turns[: len(chunk)] != chunk:
                return False
            del self._recent_turns[: len(chunk)]
            self._l1_summary = text
        self._save_l1()
        return True

    def archive_l1_summary(
        self, summary: str, *, at: datetime | None = None
    ) -> str:
        """把本段压缩摘要作为一条 L2 情景写入记忆队列。"""
        text = (summary or "").strip()
        limit = max(200, int(self.config.episode_max_bytes))
        encoded = text.encode("utf-8")
        if len(encoded) > limit:
            text = encoded[:limit].decode("utf-8", errors="ignore").rstrip()
        return self.remember_episode(
            _summary_description(text),
            text,
            emotion="平静",
            at=at,
        )

    @staticmethod
    def _l1_chars(summary: str, lines: list[str]) -> int:
        return len(summary) + sum(len(line) + 1 for line in lines)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def record_turn(
        self,
        speaker: str | None,
        user_text: str | None,
        alice_text: str | None,
        *,
        at: datetime | None = None,
    ) -> None:
        at = at or self.now_fn()
        stamp = f"{at:%H:%M}"
        with self._buffer_lock:
            if user_text and user_text.strip():
                who = speaker or "对方"
                self._recent_turns.append(f"[{stamp}] {who}: {user_text.strip()}")
            if alice_text and alice_text.strip():
                self._recent_turns.append(f"[{stamp}] Alice: {alice_text.strip()}")
            self._trim_hard_limit_locked()
            overflow = (
                self._l1_chars(self._l1_summary, self._recent_turns)
                > self.config.l1_context_max_chars
            )
        self._save_l1()
        if overflow and self.compressor is not None:
            self.compressor.notify()

    def _trim_hard_limit_locked(self) -> None:
        """压缩长期失败时的最后防线：限制 L1 行数，避免无限增长。"""
        limit = max(1, int(self.config.l1_hard_max_lines))
        excess = len(self._recent_turns) - limit
        if excess <= 0:
            return
        del self._recent_turns[:excess]
        logger.warning("L1 压缩持续失败，丢弃最早的 %s 行对话", excess)

    def remember(
        self,
        remember: Remember,
        *,
        speaker: str | None = None,
        conversation: str | None = None,
        at: datetime | None = None,
    ) -> str:
        at = at or self.now_fn()
        metadata = MemoryMetadata(
            speaker=speaker,
            conversation=conversation,
            time=to_iso(at),
            valence=max(-1.0, min(1.0, float(remember.valence))),
            emotion=remember.emotion or "平静",
            arousal=max(0.0, min(1.0, float(remember.arousal))),
        )
        op = MemoryWriteOp(
            op=MemoryWriteType.CREATE_EPISODE,
            target="",
            content=remember.text.strip(),
            metadata=metadata,
        )
        return self.worker.submit(op)

    def remember_episode(
        self,
        description: str,
        content: str,
        *,
        speaker: str | None = None,
        conversation: str | None = None,
        valence: float = 0.0,
        emotion: str = "平静",
        arousal: float = 0.0,
        at: datetime | None = None,
    ) -> str:
        metadata = MemoryMetadata(
            speaker=speaker,
            conversation=conversation,
            time=to_iso(at or self.now_fn()),
            valence=max(-1.0, min(1.0, float(valence))),
            emotion=emotion or "平静",
            arousal=max(0.0, min(1.0, float(arousal))),
        )
        return self.worker.submit(
            MemoryWriteOp(
                op=MemoryWriteType.CREATE_EPISODE,
                target="",
                content=(content or description).strip(),
                metadata=metadata,
                description=description.strip(),
            )
        )

    def write_semantic(
        self,
        target: str,
        content: str,
        *,
        valence: float = 0.0,
        arousal: float = 0.0,
        emotion: str = "平静",
    ) -> str:
        return self.worker.submit(
            MemoryWriteOp(
                op=MemoryWriteType.UPDATE_SEMANTIC,
                target=target,
                content=content,
                metadata=MemoryMetadata(
                    valence=valence, arousal=arousal, emotion=emotion
                ),
            )
        )

    def update_index(
        self, target: str, description: str, *, valence=None, arousal=None
    ) -> str:
        return self.worker.submit(
            MemoryWriteOp(
                op=MemoryWriteType.UPDATE_INDEX,
                target=target,
                content=description,
                metadata=MemoryMetadata(
                    valence=valence if valence is not None else 0.0,
                    arousal=arousal if arousal is not None else 0.0,
                ),
            )
        )

    def archive(self, target: str) -> str:
        return self.worker.submit(
            MemoryWriteOp(op=MemoryWriteType.ARCHIVE, target=target)
        )

    def submit(self, op: MemoryWriteOp) -> str:
        return self.worker.submit(op)

    # ------------------------------------------------------------------
    def ensure_friend(self, friend_id: str) -> str:
        """第一次见到某个好友时留一个空档案；之后每次见面刷新 last_seen。"""
        return self.store.touch_friend(friend_id, self.now_fn())

    def episode_refs(self, episodes: Iterable[Episode]) -> list[str]:
        return [episode.path for episode in episodes if episode.path]


def _summary_description(summary: str) -> str:
    """取摘要第一句作为索引描述，保证 MEMORY.md 里能一眼认出。"""
    for line in (summary or "").splitlines():
        text = line.strip().lstrip("#-* ").strip()
        if text:
            text = text[:80]
            return f"较早对话摘要：{text}"
    return "较早对话摘要"
