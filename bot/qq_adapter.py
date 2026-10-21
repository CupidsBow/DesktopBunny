"""QQ 协议事件 -> Brain 事件。

只做归一化：不判断重要性、不过滤、不合并内容。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
    ConversationType,
    Event,
    EventKind,
    EventSource,
)

logger = logging.getLogger(__name__)


class QQAdapter:
    def __init__(
        self,
        brain: Any = None,
        *,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.brain = brain
        self.now_fn = now_fn

    def set_brain(self, brain: Any) -> None:
        self.brain = brain

    # ------------------------------------------------------------------
    def c2c(
        self,
        user_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event:
        return self._message(
            friend_id=user_openid,
            conversation_id=user_openid,
            msg_id=msg_id,
            text=text,
            conversation_type=ConversationType.C2C,
            at=at,
        )

    def group(
        self,
        group_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event:
        return self._message(
            friend_id=group_openid,
            conversation_id=group_openid,
            msg_id=msg_id,
            text=text,
            conversation_type=ConversationType.GROUP,
            at=at,
        )

    def submit_c2c(
        self,
        user_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event | None:
        return self._submit(
            self.c2c(user_openid, msg_id, text, at=at)
        )

    def submit_group(
        self,
        group_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event | None:
        return self._submit(
            self.group(group_openid, msg_id, text, at=at)
        )

    # ------------------------------------------------------------------
    def _message(
        self,
        *,
        friend_id: str,
        conversation_id: str,
        msg_id: str | None,
        text: str,
        conversation_type: ConversationType,
        at: datetime | None,
    ) -> Event:
        at = at or self.now_fn()
        return Event(
            id="",
            kind=EventKind.MESSAGE,
            at=to_iso(at),
            source=EventSource(
                friend_id=friend_id,
                conversation_id=conversation_id,
            ),
            payload={
                "text": text,
                "msg_id": msg_id,
                "at": to_iso(at),
                "conversation_type": conversation_type.value,
            },
        )

    def _submit(self, event: Event) -> Event | None:
        if self.brain is None:
            logger.warning("QQ 消息到达时 Brain 尚未就绪，已丢弃")
            return None
        return self.brain.submit_event(event)


__all__ = ["QQAdapter"]
