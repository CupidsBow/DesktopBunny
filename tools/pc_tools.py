"""本机观察工具（纯函数，不导入 manager）。"""

from __future__ import annotations

import ctypes
import os
import socket
from typing import Any, Iterable


def observe(what: Iterable[str] | str | None = None) -> dict[str, Any]:
    if what is None:
        items = ["foreground", "presence"]
    elif isinstance(what, str):
        items = [part.strip() for part in what.split(",") if part.strip()]
    else:
        items = [str(part) for part in what if str(part).strip()]
    result: dict[str, Any] = {}
    for item in items:
        handler = _OBSERVERS.get(item)
        result[item] = (
            _guarded(handler) if handler else {"error": f"未知的观察项: {item}"}
        )
    return result


def _guarded(func):
    try:
        return func()
    except Exception as exc:  # 观察失败不应该中断其它项
        return {"error": str(exc)}


def foreground() -> dict[str, Any]:
    return _guarded(_foreground)


def presence() -> dict[str, Any]:
    return _guarded(_presence)


def processes() -> list[dict[str, Any]]:
    try:
        import psutil

        items: list[dict[str, Any]] = []
        for process in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_info"]
        ):
            try:
                info = process.info
                memory_info = info.get("memory_info")
                items.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "cpu_percent": info.get("cpu_percent"),
                        "memory_mb": (
                            round(memory_info.rss / 1024 / 1024, 1)
                            if memory_info
                            else None
                        ),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        items.sort(key=lambda item: item.get("memory_mb") or 0, reverse=True)
        return items[:25]
    except ImportError:
        return _processes_tasklist()


def cpu() -> dict[str, Any]:
    try:
        import psutil

        return {
            "percent": psutil.cpu_percent(interval=0.2),
            "count": psutil.cpu_count(),
            "frequency_mhz": getattr(psutil.cpu_freq(), "current", None),
        }
    except ImportError:
        return {"error": "未安装 psutil"}


def memory() -> dict[str, Any]:
    try:
        import psutil

        mem = psutil.virtual_memory()
        return {
            "total_mb": round(mem.total / 1024 / 1024, 1),
            "available_mb": round(mem.available / 1024 / 1024, 1),
            "percent": mem.percent,
        }
    except ImportError:
        return {"error": "未安装 psutil"}


def network() -> dict[str, Any]:
    data: dict[str, Any] = {"hostname": socket.gethostname()}
    try:
        import psutil

        counters = psutil.net_io_counters()
        data.update(
            {
                "bytes_sent": counters.bytes_sent,
                "bytes_recv": counters.bytes_recv,
            }
        )
    except ImportError:
        data["error"] = "未安装 psutil"
    return data


# ----------------------------------------------------------------------
def _foreground() -> dict[str, Any]:
    try:
        import pywinctl
    except ImportError:
        return {"error": "未安装 pywinctl"}
    window = pywinctl.getActiveWindow()
    if window is None:
        return {"title": "", "app": "", "rect": None}
    rect = getattr(window, "rect", None)
    return {
        "title": getattr(window, "title", "") or "",
        "app": (
            getattr(window, "className", "")
            or getattr(window, "getAppName", lambda: "")()
            or ""
        ),
        "rect": list(rect) if rect else None,
    }


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _presence() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick = ctypes.windll.kernel32.GetTickCount()
    elapsed = (tick - info.dwTime) & 0xFFFFFFFF
    return {"idle_seconds": elapsed / 1000.0}


def _processes_tasklist() -> list[dict[str, Any]]:
    import csv
    import subprocess

    try:
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout
    except Exception as exc:
        return [{"error": str(exc)}]
    items: list[dict[str, Any]] = []
    for row in csv.reader(output.splitlines()):
        if len(row) < 5:
            continue
        memory_text = row[4].replace(" K", "").replace(",", "").strip()
        try:
            memory_mb = round(int(memory_text) / 1024, 1)
        except ValueError:
            memory_mb = None
        items.append(
            {"pid": row[1], "name": row[0], "memory_mb": memory_mb}
        )
    items.sort(key=lambda item: item.get("memory_mb") or 0, reverse=True)
    return items[:25]


_OBSERVERS: dict[str, Any] = {
    "foreground": foreground,
    "presence": presence,
    "processes": processes,
    "cpu": cpu,
    "memory": memory,
    "network": network,
}


__all__ = [
    "cpu",
    "foreground",
    "memory",
    "network",
    "observe",
    "presence",
    "processes",
]
