"""聊天发送：按 friend_id 路由通道，被动/主动降级、有限重试与投递状态。

通道差异只在这一层的路由：``peer:`` 前缀走本地朋友网格，其余走 QQ。
transport 接口统一为 ``send_message(friend_id, msg_id, text)``；QQ 专属的
``msg_type``（markdown 判断）下沉进 QQ transport，这里不再关心。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from manager.shared.config import BrainConfig
from manager.shared.types import DeliveryStatus, Target

logger = logging.getLogger(__name__)


class ChatSender:
    def __init__(
        self,
        config: BrainConfig,
        transport: Any = None,
        *,
        mesh: Any = None,
        sleep_fn=time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport
        self.mesh = mesh
        self.sleep_fn = sleep_fn

    def send(
        self,
        target: Target,
        text: str,
        *,
        active: bool = False,
    ) -> DeliveryStatus:
        text = (text or "").strip()
        if not text:
            return DeliveryStatus(ok=False, parts_sent=0, detail="消息内容为空")

        try:
            friend_id = self.normalize(target.friend_id)
        except ValueError as exc:
            logger.warning("地址不合法，拒绝发送：%s", exc)
            return DeliveryStatus(ok=False, parts_sent=0, detail=str(exc))
        target = Target(friend_id=friend_id, msg_id=target.msg_id)

        msg_id = None if active else target.msg_id
        ok = self._deliver(target, text, msg_id=msg_id)
        if not ok and msg_id:
            logger.warning("被动回复失败，降级为主动消息")
            ok = self._deliver(target, text, msg_id=None)
        if not ok:
            return DeliveryStatus(ok=False, parts_sent=0, detail="发送失败")
        return DeliveryStatus(ok=True, parts_sent=1)

    def reach_out(self, target: Target, text: str) -> DeliveryStatus:
        return self.send(target, text, active=True)

    @staticmethod
    def normalize(friend_id: str) -> str:
        """把地址改对；明显坏的地址直接拒绝。

        已知的错法：本地朋友写成 ``peertestbot``（少冒号）。这种地址如果放进
        QQ 通道，会打到 ``/v2/users/peertestbot/messages`` 换来一个 400，
        错误还藏在 HTTP 层。所以在发之前就补上冒号。

        QQ openid 是 32 位十六进制；其他形式按原样放行——本地测试与将来的
        通道都可能用别的地址，只在字符集明显非法（空白、括号、引号）时拒绝。

        Raises:
            ValueError: 地址为空或含不可能出现在任何地址里的字符。
        """
        text = (friend_id or "").strip()
        if not text:
            raise ValueError("target 缺少 friend_id")
        if text.startswith("peer:"):
            if len(text) <= len("peer:"):
                raise ValueError("peer: 后面缺少 slug")
            return text
        if text.startswith("peer") and len(text) > len("peer"):
            return "peer:" + text[len("peer") :]
        if not re.fullmatch(r"[\w:.\-]{1,64}", text):
            raise ValueError(f"地址含非法字符：{text!r}")
        return text

    # ------------------------------------------------------------------
    def _deliver(
        self,
        target: Target,
        text: str,
        *,
        msg_id: str | None,
    ) -> bool:
        attempts = max(1, int(self.config.send_retry_limit) + 1)
        for attempt in range(attempts):
            try:
                return self._call_transport(target, text, msg_id)
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
    ) -> bool:
        friend_id = target.friend_id
        if not friend_id:
            raise ValueError("target 缺少 friend_id")
        transport = self._transport_for(friend_id)
        if transport is None:
            raise ValueError(f"没有可用的发送通道：{friend_id}")
        method = getattr(transport, "send_message", None)
        if method is None:
            raise ValueError("发送通道不支持发送消息")
        result = method(friend_id, msg_id, text)
        # 旧实现返回 None 表示成功；新实现返回 bool
        return result is not False

    def _transport_for(self, friend_id: str) -> Any:
        if friend_id.startswith("peer:"):
            return self.mesh
        return self.transport


__all__ = ["ChatSender"]
