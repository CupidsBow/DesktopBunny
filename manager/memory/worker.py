"""所有记忆写入的串行队列：L2/L3 落盘、索引维护、有限重试。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from manager.memory.index import MemoryIndex
from manager.memory.store import MemoryStore, parse_frontmatter, slugify
from manager.shared.types import MemoryIndexEntry, MemoryWriteOp, MemoryWriteType

logger = logging.getLogger(__name__)

_SENTINEL = object()


class MemoryWorker:
    """单线程执行写操作；失败重试，持续失败交给 ``on_error``。"""

    def __init__(
        self,
        store: MemoryStore,
        index: MemoryIndex,
        *,
        max_retries: int = 3,
        on_error: Callable[[MemoryWriteOp, Exception], None] | None = None,
    ) -> None:
        self.store = store
        self.index = index
        self.max_retries = max(1, int(max_retries))
        self.on_error = on_error
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._counter = 0
        self._counter_lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="memory-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._queue.put(_SENTINEL)
        if self._thread:
            self._thread.join(timeout)

    def submit(self, op: MemoryWriteOp) -> str:
        with self._counter_lock:
            self._counter += 1
            op_id = f"mem_{self._counter:06d}"
        self._queue.put((op_id, op))
        return op_id

    def flush(self, timeout: float = 5.0) -> bool:
        """等待队列清空；返回是否在超时前完成。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return self._queue.unfinished_tasks == 0

    @property
    def pending(self) -> int:
        return self._queue.unfinished_tasks

    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                op_id, op = item
                self._execute(op_id, op)
            finally:
                self._queue.task_done()

    def _execute(self, op_id: str, op: MemoryWriteOp) -> None:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                description = self._apply(op)
                logger.debug(
                    "记忆写入完成 %s %s：%s", op_id, op.op, description or op.target
                )
                return
            except Exception as exc:  # 磁盘/格式问题都不应拖垮进程
                last_error = exc
                logger.warning(
                    "记忆写入失败 %s（第 %s 次）：%s", op_id, attempt + 1, exc
                )
                time.sleep(0.1 * (attempt + 1))
        if last_error is not None and self.on_error is not None:
            try:
                self.on_error(op, last_error)
            except Exception:
                logger.exception("记忆失败回调异常")

    def _apply(self, op: MemoryWriteOp) -> str:
        if op.op is MemoryWriteType.CREATE_EPISODE:
            target = self.store.normalize_target(op.target) if op.target else ""
            name_hint = None
            if target:
                name_hint = target.rsplit("/", 1)[-1]
                if name_hint.endswith(".md"):
                    name_hint = name_hint[:-3]
            description = (
                op.description.strip()
                or _first_line(op.content)
                or "一段经历"
            )
            episode = self.store.write_episode(
                description=description,
                content=op.content,
                metadata=op.metadata,
                name_hint=name_hint,
            )
            self.index.add(
                MemoryIndexEntry(
                    name=episode.name,
                    path=episode.path,
                    description=episode.description,
                    valence=episode.metadata.valence,
                    arousal=episode.metadata.arousal,
                )
            )
            return episode.path

        if op.op is MemoryWriteType.UPDATE_SEMANTIC:
            target = self.store.normalize_target(op.target)
            if not target:
                raise ValueError("update_semantic 缺少 target")
            self.store.write_semantic(
                target,
                op.content,
                metadata=op.metadata,
                description=_first_line(op.content),
            )
            data, body = parse_frontmatter(op.content)
            description = str(data.get("description") or _first_line(body))
            name = (
                str(data.get("name"))
                if data.get("name")
                else slugify(target.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            )
            self.index.add(
                MemoryIndexEntry(
                    name=name,
                    path=target,
                    description=description or name,
                    valence=op.metadata.valence,
                    arousal=op.metadata.arousal,
                )
            )
            return target

        if op.op is MemoryWriteType.UPDATE_INDEX:
            target = self.store.normalize_target(op.target)
            if not target:
                raise ValueError("update_index 缺少 target")
            self.index.update_description(
                target,
                _first_line(op.content),
                valence=op.metadata.valence,
                arousal=op.metadata.arousal,
            )
            return target

        if op.op is MemoryWriteType.ARCHIVE:
            target = self.store.normalize_target(op.target)
            if not target:
                raise ValueError("archive 缺少 target")
            self.index.remove(target)
            return target

        raise ValueError(f"未知记忆写操作: {op.op}")


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
