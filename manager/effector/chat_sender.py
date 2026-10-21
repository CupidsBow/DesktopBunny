"""聊天发送：拆分、被动/主动降级、有限重试与投递状态。"""

from __future__ import annotations

import logging
import time
from typing import Any

from manager.effector.message_splitter import split_message
from manager.shared.config import BrainConfig
from manager.shared.types import ConversationType, DeliveryStatus, Target

logger = logging.getLogger(__name__)


class ChatSender:
    def __init__(
        self,
        config: BrainConfig,
        transport: Any = None,
        *,
        sleep_fn=time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport
        self.sleep_fn = sleep_fn

    def send(
        self,
        target: Target,
        text: str,
        *,
        active: bool = False,
    ) -> DeliveryStatus:
        parts = split_message(
            text,
            max_chars=self.config.message_split_max_chars,
            max_parts=self.config.message_split_max_parts,
        )
        if not parts:
            return DeliveryStatus(ok=False, parts_sent=0, detail="消息内容为空")
        if self.transport is None:
            return DeliveryStatus(ok=False, parts_sent=0, detail="没有可用的发送通道")

        msg_type = 2 if _needs_markdown(text) else 0
        sent = 0
        for index, part in enumerate(parts):
            msg_id = None if active else (target.msg_id if index == 0 else None)
            ok = self._deliver(target, part, msg_id=msg_id, msg_type=msg_type)
            if not ok and msg_id:
                logger.warning("被动回复失败，降级为主动消息")
                ok = self._deliver(target, part, msg_id=None, msg_type=msg_type)
            if not ok:
                return DeliveryStatus(
                    ok=False,
                    parts_sent=sent,
                    detail=f"第 {index + 1}/{len(parts)} 条发送失败",
                )
            sent += 1
            if index < len(parts) - 1 and self.config.message_send_interval > 0:
                self.sleep_fn(self.config.message_send_interval)
        return DeliveryStatus(ok=True, parts_sent=sent)

    def reach_out(self, target: Target, text: str) -> DeliveryStatus:
        return self.send(target, text, active=True)

    # ------------------------------------------------------------------
    def _deliver(
        self,
        target: Target,
        text: str,
        *,
        msg_id: str | None,
        msg_type: int,
    ) -> bool:
        attempts = max(1, int(self.config.send_retry_limit) + 1)
        for attempt in range(attempts):
            try:
                return self._call_transport(target, text, msg_id, msg_type)
            except Exception as exc:
                logger.warning(
                    "发送消息异常（第 %s/%s 次）：%s", attempt + 1, attempts, exc
                )
                if attempt < attempts - 1:
                    self.sleep_fn(0.3 * (attempt + 1))
        return False

    def _call_transport(
        self,
        target: Target,
        text: str,
        msg_id: str | None,
        msg_type: int,
    ) -> bool:
        conversation_id = target.conversation_id or target.friend_id
        if not conversation_id:
            raise ValueError("target 缺少 conversation_id")
        conversation_type = getattr(
            target.conversation_type, "value", target.conversation_type
        )
        if conversation_type == "group":
            method = getattr(self.transport, "send_group_message", None)
        else:
            method = getattr(self.transport, "send_c2c_message", None)
        if method is None:
            raise ValueError("发送通道不支持该会话类型")
        result = method(conversation_id, msg_id, text, msg_type)
        # 旧实现返回 None 表示成功；新实现返回 bool
        return result is not False


def _needs_markdown(text: str) -> bool:
    return "```" in text or "\n|" in text


__all__ = ["ChatSender"]
