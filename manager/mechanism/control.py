"""运行标志、停止与急停。"""

from __future__ import annotations

import threading
import time


class Control:
    def __init__(self) -> None:
        self._running = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._emergency = False

    def start(self) -> None:
        with self._lock:
            self._stopped.clear()
            self._reason = ""
            self._emergency = False
            self._running.set()

    def stop(self, reason: str = "") -> None:
        with self._lock:
            self._reason = reason
            self._running.clear()
            self._stopped.set()

    def emergency_stop(self, reason: str = "用户急停") -> None:
        with self._lock:
            self._emergency = True
        self.stop(reason)

    @property
    def running(self) -> bool:
        return self._running.is_set() and not self._stopped.is_set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def emergency(self) -> bool:
        return self._emergency

    @property
    def reason(self) -> str:
        return self._reason

    def sleep(self, seconds: float) -> bool:
        """可被停止打断的等待；返回是否仍然在运行。"""
        deadline = time.monotonic() + max(0.0, seconds)
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._stopped.wait(min(0.1, remaining))
        return self.running


__all__ = ["Control"]
