"""QQ 协议事件 -> Brain 事件。

只做归一化：不判断重要性、不过滤、不合并内容。
QQ 官方机器人只保留私聊（C2C）通道。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
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

    def message(
        self,
        user_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event:
        at = at or self.now_fn()
        return Event(
            id="",
            kind=EventKind.MESSAGE,
            at=to_iso(at),
            source=EventSource(friend_id=user_openid),
            payload={
                "text": text,
                "msg_id": msg_id,
                "at": to_iso(at),
            },
        )

    def submit_message(
        self,
        user_openid: str,
        msg_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
    ) -> Event | None:
        if self.brain is None:
            logger.warning("QQ 消息到达时 Brain 尚未就绪，已丢弃")
            return None
        return self.brain.submit_event(
            self.message(user_openid, msg_id, text, at=at)
        )


__all__ = ["QQAdapter"]
