"""前台窗口标题：只读当前活跃窗口标题，不保存历史，也不做分类。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ForegroundInfo:
    title: str | None
    dwell: float = 0.0


class ForegroundTracker:
    """跟踪当前前台窗口标题及其持续时长；标题只在内存里短暂使用。"""

    def __init__(self, dwell_seconds: float = 30.0) -> None:
        self.dwell_seconds = float(dwell_seconds)
        self._current: str | None = None
        self._since: datetime | None = None
        self._readable = True

    def _read_title(self) -> str | None:
        if not self._readable:
            return None
        try:
            import pywinctl as pwc

            window = pwc.getActiveWindow()
            if window is None:
                return None
            return window.title or None
        except ImportError:
            self._readable = False
            logger.info("未安装 pywinctl，前台标题降级为 None")
            return None
        except Exception as exc:  # 窗口随时可能消失，不阻塞状态时钟
            logger.debug("读取前台窗口失败：%s", exc)
            return None

    def tick(self, now: datetime) -> ForegroundInfo:
        title = self._read_title()
        if title != self._current:
            self._current = title
            self._since = now
        dwell = 0.0 if self._since is None else max(0.0, (now - self._since).total_seconds())
        return ForegroundInfo(title=self._current, dwell=dwell)
