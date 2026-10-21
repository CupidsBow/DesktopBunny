"""唤醒理由聚合与批量窗口。"""

from __future__ import annotations

import threading
import time
from typing import Iterable

from manager.shared.config import BrainConfig
from manager.shared.types import WakeReason, WakeReasonKind


class WakeController:
    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._reasons: list[WakeReason] = []
        self._last_message_at = 0.0
        self._stopped = False

    # ------------------------------------------------------------------
    def notify(self, reason: WakeReason) -> None:
        with self._condition:
            if self._stopped:
                return
            self._reasons.append(reason)
            if reason.kind is WakeReasonKind.EVENT and reason.detail.startswith(
                "message"
            ):
                self._last_message_at = time.monotonic()
            self._condition.notify_all()

    def notify_many(self, reasons: Iterable[WakeReason]) -> None:
        for reason in reasons:
            self.notify(reason)

    def take(self) -> list[WakeReason]:
        with self._condition:
            reasons, self._reasons = self._reasons, []
            return reasons

    def has_pending(self) -> bool:
        with self._condition:
            return bool(self._reasons)

    def peek(self) -> list[WakeReason]:
        with self._condition:
            return list(self._reasons)

    def wait(self, timeout: float) -> bool:
        """等待唤醒信号；返回是否有待处理理由。"""
        with self._condition:
            if not self._reasons and not self._stopped:
                self._condition.wait(timeout=max(0.0, timeout))
            return bool(self._reasons)

    def settle_remaining(self) -> float:
        """连珠炮消息的剩余稳定窗口；非消息唤醒返回 0。"""
        with self._condition:
            if not any(
                reason.kind is WakeReasonKind.EVENT
                and reason.detail.startswith("message")
                for reason in self._reasons
            ):
                return 0.0
            elapsed = time.monotonic() - self._last_message_at
            return max(0.0, self.config.settle_window - elapsed)

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def reset(self) -> None:
        with self._condition:
            self._stopped = False
            self._reasons.clear()


__all__ = ["WakeController"]
