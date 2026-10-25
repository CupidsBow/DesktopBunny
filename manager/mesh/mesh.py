"""本地兔子网格：文件会合点上的 presence 心跳与 inbox 私聊。

- 每只兔子往 ``presence/<slug>.json`` 周期性写心跳 → 存在感知。
- 出站往对方 ``inbox/<slug>/`` 写消息文件 → 点对点投递。
- 入站轮询自己的 ``inbox/<slug>/`` → 由 brain 转成 MESSAGE 事件。

本模块不做 LLM 判断，只做「发现谁在线 + 收发文件」；与 QQ 通道一样尽力而为。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Callable

from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import Peer, PeerMessage

logger = logging.getLogger(__name__)

_PRESENCE = "presence"
_INBOX = "inbox"
#: 「入队后、删文件前」崩溃导致的重复投递去重表上限，防止内存无限增长。
_MAX_RECENT_IDS = 1024


class MeshService:
    """本地兔子通道：既是 presence 发布者，也是 ``peer:`` 前缀的 transport。"""

    def __init__(
        self,
        peers_root: str,
        *,
        slug: str,
        name: str | None = None,
        pid: int | None = None,
        heartbeat_seconds: float = 5.0,
        heartbeat_timeout: float = 15.0,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.peers_root = peers_root
        self.slug = slug or "bunny"
        self.name = name or self.slug
        self.pid = int(pid if pid is not None else os.getpid())
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.heartbeat_timeout = float(heartbeat_timeout)
        self._now_fn = now_fn
        self.started_at = to_iso(now_fn())

        self._presence_dir = os.path.join(peers_root, _PRESENCE)
        self._inbox_dir = os.path.join(peers_root, _INBOX, self.slug)
        self._last_heartbeat: datetime | None = None
        self._recent_ids: set[str] = set()
        os.makedirs(self._presence_dir, exist_ok=True)
        os.makedirs(self._inbox_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # presence
    # ------------------------------------------------------------------
    def heartbeat(self) -> None:
        """刷新自己的心跳文件；按 heartbeat_seconds 节流，调用方可放心高频调。"""
        now = self._now_fn()
        if self._last_heartbeat is not None and (
            now - self._last_heartbeat
        ).total_seconds() < self.heartbeat_seconds:
            return
        self._last_heartbeat = now
        payload = {
            "name": self.name,
            "slug": self.slug,
            "pid": self.pid,
            "started_at": self.started_at,
            "heartbeat_at": to_iso(now),
        }
        self._atomic_write(
            os.path.join(self._presence_dir, f"{self.slug}.json"),
            json.dumps(payload, ensure_ascii=False),
        )

    def discover(self) -> list[Peer]:
        """列出在线的本地兔子（不含自己）。心跳新鲜度是唯一权威判据。"""
        now = self._now_fn()
        peers: list[Peer] = []
        try:
            names = os.listdir(self._presence_dir)
        except OSError:
            return peers
        for filename in names:
            if not filename.endswith(".json"):
                continue
            slug = filename[: -len(".json")]
            if slug == self.slug:
                continue
            data = self._read_json(os.path.join(self._presence_dir, filename))
            if data is None:
                continue
            peer = Peer.from_dict(data)
            if not peer.slug:
                peer.slug = slug
            if self._is_online(peer, now):
                peers.append(peer)
        return peers

    # ------------------------------------------------------------------
    # transport（供 ChatSender 路由调用）
    # ------------------------------------------------------------------
    def send_message(self, friend_id: str, msg_id: str | None, text: str) -> bool:
        """把消息写进对方 inbox。``friend_id`` 形如 ``peer:<slug>``。"""
        slug = friend_id[5:] if friend_id.startswith("peer:") else friend_id
        if not slug:
            return False
        message = {
            "msg_id": msg_id or f"m_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            "from_slug": self.slug,
            "from_name": self.name,
            "text": text,
            "sent_at": to_iso(self._now_fn()),
        }
        target_dir = os.path.join(self.peers_root, _INBOX, slug)
        try:
            os.makedirs(target_dir, exist_ok=True)
            filename = f"m_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.json"
            self._atomic_write(
                os.path.join(target_dir, filename),
                json.dumps(message, ensure_ascii=False),
            )
            return True
        except OSError:
            logger.exception("写给本地兔子 %s 失败", slug)
            return False

    # ------------------------------------------------------------------
    # 入站
    # ------------------------------------------------------------------
    def poll_inbox(self) -> list[PeerMessage]:
        """读走并删除自己收件箱里的消息（单消费者，读完即删）。"""
        messages: list[PeerMessage] = []
        try:
            names = sorted(os.listdir(self._inbox_dir))
        except OSError:
            return messages
        for filename in names:
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self._inbox_dir, filename)
            data = self._read_json(path)
            if data is None:
                self._remove(path)
                continue
            message = PeerMessage.from_dict(data)
            if not message.msg_id:
                self._remove(path)
                continue
            if message.msg_id in self._recent_ids:
                self._remove(path)
                continue
            self._remember_id(message.msg_id)
            messages.append(message)
            self._remove(path)
        return messages

    # ------------------------------------------------------------------
    def _is_online(self, peer: Peer, now: datetime) -> bool:
        try:
            heartbeat_at = datetime.fromisoformat(peer.heartbeat_at)
            return (now - heartbeat_at).total_seconds() <= self.heartbeat_timeout
        except (TypeError, ValueError, OverflowError):
            return False

    def _remember_id(self, msg_id: str) -> None:
        self._recent_ids.add(msg_id)
        if len(self._recent_ids) > _MAX_RECENT_IDS:
            self._recent_ids = set(
                sorted(self._recent_ids)[-_MAX_RECENT_IDS // 2 :]
            )

    @staticmethod
    def _read_json(path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)


__all__ = ["MeshService"]
