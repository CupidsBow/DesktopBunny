"""前台应用的粗分类。

只把窗口归入 code / browser / video / game / office / chat / other，
不保存窗口标题，也不推断用户在做什么。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "code",
        (
            "visual studio",
            "vscode",
            "code.exe",
            "pycharm",
            "idea",
            "jetbrains",
            "devenv",
            "sublime",
            "terminal",
            "powershell",
            "windowsterminal",
            "cmd.exe",
            "goland",
            "clion",
            "webstorm",
            "jupyter",
        ),
    ),
    (
        "browser",
        (
            "chrome",
            "edge",
            "firefox",
            "opera",
            "brave",
            "browser",
        ),
    ),
    (
        "video",
        (
            "youtube",
            "bilibili",
            "netflix",
            "potplayer",
            "vlc",
            "mpv",
            "iqiyi",
            "youku",
        ),
    ),
    (
        "game",
        (
            "steam",
            "epicgames",
            "game",
            "unity",
            "genshin",
            "minecraft",
        ),
    ),
    (
        "office",
        (
            "winword",
            "excel",
            "powerpnt",
            "wps",
            "notion",
            "obsidian",
            "onenote",
            "acrobat",
        ),
    ),
    (
        "chat",
        (
            "wechat",
            "weixin",
            "qq",
            "discord",
            "telegram",
            "slack",
            "teams",
        ),
    ),
)


def classify_window(title: str, class_name: str = "") -> str:
    """把窗口标题/类名映射到粗分类；无法识别时返回 ``unknown``。"""
    text = f"{title} {class_name}".lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


@dataclass
class ForegroundInfo:
    app: str | None
    dwell: float = 0.0


class ForegroundTracker:
    """跟踪前台分类及其持续时长；标题只在内存里短暂使用。"""

    def __init__(self, dwell_seconds: float = 30.0) -> None:
        self.dwell_seconds = float(dwell_seconds)
        self._current: str | None = None
        self._since: datetime | None = None
        self._readable = True

    def _read_category(self) -> str | None:
        if not self._readable:
            return None
        try:
            import pywinctl as pwc

            window = pwc.getActiveWindow()
            if window is None:
                return None
            title = window.title or ""
            try:
                class_name = window.getClassName() or ""
            except Exception:
                class_name = ""
            return classify_window(title, class_name)
        except ImportError:
            self._readable = False
            logger.info("未安装 pywinctl，前台分类降级为 unknown")
            return None
        except Exception as exc:  # 窗口随时可能消失，不阻塞状态时钟
            logger.debug("读取前台窗口失败：%s", exc)
            return None

    def tick(self, now: datetime) -> ForegroundInfo:
        category = self._read_category()
        if category is None:
            category = "unknown"
        if category != self._current:
            self._current = category
            self._since = now
        dwell = 0.0 if self._since is None else max(0.0, (now - self._since).total_seconds())
        return ForegroundInfo(app=self._current, dwell=dwell)
