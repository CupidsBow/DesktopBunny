"""电脑前是否有人：只读取最后一次输入时间，不看输入内容。"""

from __future__ import annotations

import ctypes
import os


class _LastInputInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def get_idle_seconds() -> float | None:
    """返回距最后一次键鼠输入的秒数；无法读取时返回 None。"""
    if os.name != "nt":
        return None
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        user32 = ctypes.windll.user32
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick_count = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        last = info.dwTime & 0xFFFFFFFF
        elapsed_ms = (tick_count - last) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except (AttributeError, OSError, ValueError):
        return None
