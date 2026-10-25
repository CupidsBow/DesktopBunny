"""单实例守护：同一时间同一名字只允许一个 Bunny 进程运行。

Bunny 的 LLM 能通过 sandbox 工具运行任意命令，存在把自己再启动一份的风险。
这里用一把进程级闸门挡住：第一个进程创建并持有它，直到进程退出才释放；
之后任何进程（手动双击，或 sandbox 里 ``python main.py`` / 启动 exe）拿到同一把闸，
发现已被持有就立即退出，不会出现同名的第二个 Bunny。

闸门按兔子名字隔离：不同名字（BUNNY_NAME）的 exe 各拿各的闸，可以同时共存。

对象必须存活到进程结束——它持有的句柄是闸门的唯一凭证，被回收就等于放行。
"""

from __future__ import annotations

import logging
import os
import tempfile

from constants import constants

logger = logging.getLogger(__name__)

#: GetLastError 返回值：对象已存在，说明已有进程持有该互斥量。
_ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """进程级单实例锁。

    用法：:

        guard = SingleInstance()
        if not guard.acquire():
            # 已有实例在运行，本进程立即退出
            return
        # guard 必须存活到进程结束
    """

    def __init__(self, bunny_name: str | None = None) -> None:
        slug = (bunny_name or constants.BUNNY_NAME_SLUG).strip() or "bunny"
        self.name = f"DesktopBunny_{slug}_SingleInstance"
        self._lock_filename = f"desktop_bunny_{slug}_single_instance.lock"
        self._handle = None
        self._lock_file = None

    def acquire(self) -> bool:
        """申请单实例权；``True`` 表示本进程是唯一实例。"""
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_lockfile()

    def release(self) -> None:
        """主动释放闸门（主要用于测试；进程退出时系统也会自动回收）。"""
        if self._handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    # ------------------------------------------------------------------
    def _acquire_windows(self) -> bool:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            logger.error("创建单实例互斥量失败，按已有实例处理")
            return False
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            logger.warning("检测到已有 Bunny 进程在运行（互斥量 %s）", self.name)
            return False
        self._handle = handle
        return True

    def _acquire_lockfile(self) -> bool:
        import fcntl

        path = os.path.join(tempfile.gettempdir(), self._lock_filename)
        handle = open(path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            logger.warning("检测到已有 Bunny 进程在运行（锁文件 %s）", path)
            return False
        self._lock_file = handle
        return True


__all__ = ["SingleInstance"]
