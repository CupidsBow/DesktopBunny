"""持久化 worker：串行写 JSON/文本，临时文件 + 原子替换。"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_SENTINEL = object()


class PersistenceWorker:
    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="persistence-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(_SENTINEL)
        if self._thread:
            self._thread.join(timeout)

    def submit_json(self, path: str, data: Any) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        self._queue.put((path, text))

    def submit_text(self, path: str, text: str) -> None:
        self._queue.put((path, text))

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                path, text = item
                self._write(path, text)
            finally:
                self._queue.task_done()

    @staticmethod
    def _write(path: str, text: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            temp = f"{path}.tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temp, path)
        except OSError as exc:
            logger.warning("持久化写入失败 %s：%s", path, exc)


__all__ = ["PersistenceWorker"]
