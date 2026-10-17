"""前台窗口监视：读取窗口标题，供吐槽生成上下文使用。

窗口标题是比截图可靠得多的信息来源——"狐妖小红娘第15集-bilibili"
这种文字，640px 的截图根本读不出来，但标题里是完整精确的。
"""
import ctypes
import logging
from typing import List

user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# 这些标题没有吐槽价值（系统窗口 / 输入法 / 桌面）
IGNORED_TITLES = {
    "",
    "Program Manager",
    "设置",
    "Windows 输入体验",
    "Microsoft Text Input Application",
    "Windows Shell Experience Host",
    "Windows Default Lock Screen",
}


class WindowWatcher:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_foreground_title(self) -> str:
        """返回当前前台窗口的标题，取不到时返回空串"""
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value.strip()
        except Exception as e:
            self.logger.error(f"读取前台窗口标题失败: {e}")
            return ""

    def get_visible_titles(self, exclude: str = "", limit: int = 5) -> List[str]:
        """返回当前可见的顶层窗口标题（去重、去掉 excluded 和噪音标题）"""
        titles: List[str] = []

        def _on_window(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            titles.append(buffer.value.strip())
            return True

        try:
            user32.EnumWindows(_WNDENUMPROC(_on_window), None)
        except Exception as e:
            self.logger.error(f"枚举窗口失败: {e}")
            return []

        result = []
        for title in titles:
            if title in IGNORED_TITLES or title == exclude:
                continue
            if title not in result:
                result.append(title)
            if len(result) >= limit:
                break
        return result

    def is_ignored(self, title: str) -> bool:
        """这个标题是否不值得吐槽"""
        return title.strip() in IGNORED_TITLES
