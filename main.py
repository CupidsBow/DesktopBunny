from components.bunny_platform import Platform
import pygame
import sys
import ctypes
import os
import threading
import time
from components.bunny import Bunny
from PIL import Image
import pystray
from constants import constants
from tools.platform_detector import PlatformDetector
import random
import functools
from manager.save_manager import SaveManager
from tools.tool_executor import ToolExecutor
from tools.get_local_time import get_local_time
import sympy
import logging
from logging.handlers import TimedRotatingFileHandler


class World:
    def __init__(self, fps=constants.GLOBAL_FPS):
        self.TRANSPARENT_COLOR = (255, 0, 255)
        self.ICON_PATH = constants.BUNNY_ICON

        self.logger = logging.getLogger(__name__)

        self.fps = fps
        
        self.running = False
        self.screen = None
        self.hwnd = None
        self.window = None
        self.clock = None
        self.window_size = (0, 0)
        self.last_frame_time = 0
        self.delta = 0.0
        self.bunnies = []
        self.tray_icon = None
        self.tray_thread = None
        self.detect_platforms_enabled = True

        self.detector = PlatformDetector()
        self.tool_executor = ToolExecutor()
        self.save_manager = SaveManager()
        self.register_tools()

    def startup(self):
        pygame.init()
        pygame.mixer.init()
        
        self.window_size = self.get_physical_work_area()
        self.INIT_BOTTOM_PLATFORM = Platform(
            pygame.math.Vector2(0, self.window_size[1]),
            pygame.math.Vector2(self.window_size[0], constants.PLATFORM_HEIGHT)
        )
        
        self.screen = pygame.display.set_mode(self.window_size, pygame.NOFRAME)
        pygame.event.set_allowed([pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN, pygame.DROPFILE])
        self.clock = pygame.time.Clock()
        self.last_frame_time = pygame.time.get_ticks()
        
        self.hwnd = pygame.display.get_wm_info()['window']
        ctypes.windll.user32.ShowWindow(self.hwnd, 0)

        self._set_icon()
        self._set_transparent()
        self._hide_from_taskbar()
        self._set_always_on_top()
        self._position_to_work_area()
        
        ctypes.windll.user32.ShowWindow(self.hwnd, 5)

        self.running = True

        bunny_data = self.save_manager.load()
        if bunny_data == {}:
            self.bunnies.append(self._create_bunny("Alice"))
        else:
            for name, data in bunny_data.items():
                self.bunnies.append(self._create_bunny(name, data.get("satiety")))
        
        self._start_tray()
        self.platform_detect_thread = threading.Thread(target=self._update_platforms_loop, daemon=True)
        self.platform_detect_thread.start()
        self.refresh_tray_menu_thread = threading.Thread(target=self._refresh_tray_menu_loop, daemon=True)
        self.refresh_tray_menu_thread.start()
        self.auto_save_thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self.auto_save_thread.start()
        self.screen_change_thread = threading.Thread(target=self._watch_screen_change_loop, daemon=True)
        self.screen_change_thread.start()

    def register_tools(self):
        if self.tool_executor is None:
            self.tool_executor = ToolExecutor()
        
        self.tool_executor.registerTool(
            "get_local_time",
            "一个获取当前时间的工具，返回格式为 %Y-%m-%d %H:%M:%S",
            get_local_time
        )
        self.tool_executor.registerTool(
            "calc",
            "计算单个表达式的值并返回结果",
            sympy.sympify
        )

        self.logger.info(f"可用工具: {self.tool_executor.getAvailableTools()}")
    
    def _start_tray(self):
        def _run_tray():
            true_icon_path = self.ICON_PATH
            if getattr(sys, "frozen", False):
                true_icon_path = os.path.join(sys._MEIPASS, true_icon_path)
            if os.path.exists(true_icon_path):
                image = Image.open(true_icon_path)
            else:
                image = Image.new('RGB', (64, 64), (255, 0, 0))

            # 用新的方法生成初始菜单
            menu = self._build_menu()
            
            self.tray_icon = pystray.Icon(
                "bunny",
                image,
                "Bunny",
                menu
            )
            
            self.tray_icon.run()
        
        self.tray_thread = threading.Thread(target=_run_tray, daemon=True)
        self.tray_thread.start()
    
    def _on_tray_interact_bunny(self, icon, item, idx):
        if 0 <= idx < len(self.bunnies):
            self.bunnies[idx].set_comment(f"主人，{self.bunnies[idx].name}在这哦~")

    def _refresh_tray_menu(self):
        if not self.tray_icon:
            return
        self.tray_icon.menu = self._build_menu()
        self.tray_icon.update_menu()

    def _build_menu(self):
        # ① 每只兔子的按钮
        bunny_items = []
        for i, bunny in enumerate(self.bunnies):
            # 用 partial 固定 idx，生成 (icon, item, idx=i) 的回调
            action = functools.partial(self._on_tray_interact_bunny, idx=i)
            bunny_items.append(
                pystray.MenuItem(
                    # 文本仍然用 lambda 动态显示饱食度（这个可以继续用 lambda）
                    lambda item, idx=i, name = bunny.name: f"🐰{name}: 饱食度 {int(self.bunnies[idx].satiety)}",
                    action              # ← 这里换成 partial
                )
            )

        # ② 固定菜单项（不变）
        control_items = [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("添加一只兔兔", self._add_bunny),
            pystray.MenuItem("移除一只兔兔", self._delete_bunny),
            pystray.MenuItem(
                "平台检测",
                self._on_tray_toggle_platform_detection,
                checked=lambda item: self.detect_platforms_enabled
            ),
            pystray.MenuItem("退出", self._on_tray_exit)
        ]

        return pystray.Menu(*(bunny_items + control_items))

    def _create_bunny(self, name: str, satiety=None) -> Bunny:
        """创建一只兔兔并让它进入 Idle 状态。

        startup() 不能省：它才会播 Idle 动画并把 idle_timer 设成随机的 5~15 秒。
        少了它兔子会定格在第 0 帧，而且 idle_timer 还是 0，开局第一帧就起跳。
        """
        bunny = Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1]), name)
        if satiety is not None:
            bunny.satiety = satiety
        bunny.startup()
        return bunny

    def _generate_bunny_name(self):
        index = random.randint(0, len(constants.BUNNY_NAME_LIST) - 1)
        return constants.BUNNY_NAME_LIST[index]

    def _add_bunny(self):
        if len(self.bunnies) >= constants.BUNNY_MAX_NUM:
            return
        self.bunnies.append(self._create_bunny(self._generate_bunny_name()))
        self._refresh_tray_menu()
    
    def _delete_bunny(self):
        if len(self.bunnies) <= 1:
            return
        self.bunnies.pop()
        self._refresh_tray_menu()

    def _on_tray_toggle_platform_detection(self, *args):
        self.detect_platforms_enabled = not self.detect_platforms_enabled
        if not self.detect_platforms_enabled:
            for bunny in self.bunnies:
                bunny.set_platforms([self.INIT_BOTTOM_PLATFORM])

    def _on_tray_exit(self):
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()
    
    def _calculate_delta(self):
        current_time = pygame.time.get_ticks()
        self.delta = (current_time - self.last_frame_time) / 1000.0
        self.last_frame_time = current_time
        return self.delta
    
    def get_physical_work_area(self):
        class RECT(ctypes.Structure):
            _fields_ = [
                ('left', ctypes.c_long),
                ('top', ctypes.c_long),
                ('right', ctypes.c_long),
                ('bottom', ctypes.c_long),
            ]
        rect = RECT()
        SPI_GETWORKAREA = 48
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            return (width, height)
        else:
            self.logger.error("获取工作区域失败")
            return (800, 600)
    
    def _position_to_work_area(self):
        class RECT(ctypes.Structure):
            _fields_ = [
                ('left', ctypes.c_long),
                ('top', ctypes.c_long),
                ('right', ctypes.c_long),
                ('bottom', ctypes.c_long)
            ]
        
        SPI_GETWORKAREA = 48
        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        
        ctypes.windll.user32.MoveWindow(
            self.hwnd,
            rect.left, rect.top,
            self.window_size[0], self.window_size[1],
            True
        )
    
    def _set_icon(self):
        icon_path = self.ICON_PATH
        if getattr(sys, "frozen", False):
            icon_path = os.path.join(sys._MEIPASS, self.ICON_PATH)
        if os.path.exists(icon_path):
            try:
                icon = pygame.image.load(icon_path)
                pygame.display.set_icon(icon)
            except Exception as e:
                self.logger.error(f"设置图标失败: {e}")

        ico_path = constants.BUNNY_ICON_ICO
        if getattr(sys, "frozen", False):
            ico_path = os.path.join(sys._MEIPASS, constants.BUNNY_ICON_ICO)
        self._set_win32_icon(ico_path)

    def _set_win32_icon(self, icon_path: str):
        if not os.path.exists(icon_path):
            return

        try:
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            hicon = ctypes.windll.user32.LoadImageW(
                None,
                icon_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE
            )
            if hicon:
                ctypes.windll.user32.SendMessageW(self.hwnd, WM_SETICON, ICON_SMALL, hicon)
                ctypes.windll.user32.SendMessageW(self.hwnd, WM_SETICON, ICON_BIG, hicon)
        except Exception as e:
            self.logger.error(f"设置 Win32 图标失败: {e}")

    def _set_transparent(self):
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x80000
        LWA_COLORKEY = 0x00000001
        
        style = ctypes.windll.user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        ctypes.windll.user32.SetLayeredWindowAttributes(self.hwnd, 0xFF00FF, 0, LWA_COLORKEY)
    
    def _hide_from_taskbar(self):
        try:
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
        except Exception as e:
            self.logger.error(f"从任务栏隐藏窗口失败: {e}")

    def _set_always_on_top(self):
        try:
            import pywinctl as pwc
            self.window = pwc.Window(self.hwnd)
            self.window.alwaysOnTop(True)
        except ImportError:
            self.logger.error("没有安装 pywinctl 库")
    
    def _update_platforms_loop(self):
        while self.running:
            if self.detect_platforms_enabled:
                platforms = self.detector.get_platforms_for_bunny(top_n=constants.PLATFORM_MAX_NUM)
                for bunny in self.bunnies:
                    bunny.set_platforms([self.INIT_BOTTOM_PLATFORM] + platforms)
            time.sleep(constants.PLATFORM_DETECT_TIME_INTERVAL_SECONDS)

    def _refresh_tray_menu_loop(self):
        while self.running:
            self._refresh_tray_menu()
            time.sleep(3)

    def _auto_save_loop(self):
        while self.running:
            self.save_bunny_info()
            time.sleep(60)

    def _watch_screen_change_loop(self):
        """轮询工作区尺寸，变化则按正常流程退出（分辨率/任务栏变化后需重启应用）"""
        while self.running:
            if self.get_physical_work_area() != self.window_size:
                self.logger.info("检测到工作区尺寸变化，按正常流程退出")
                self._on_tray_exit()
                return
            time.sleep(constants.SCREEN_CHANGE_POLL_INTERVAL_SECONDS)

    def save_bunny_info(self):
        data = {}
        for bunny in self.bunnies:
            data[bunny.name] = {
                "satiety": bunny.satiety
            }
        self.save_manager.save(data)

    def update(self, delta: float):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    for i in range(len(self.bunnies)-1, -1, -1):
                        if self.bunnies[i].handle_click(event.pos):
                            break
            elif event.type == pygame.DROPFILE:
                file_path = event.file
                mouse_pos = pygame.mouse.get_pos()
                for bunny in self.bunnies:
                    if bunny.is_position_inside_bunny(mouse_pos):
                        bunny.eat_carrot(file_path)
                        break
        
        for bunny in self.bunnies:
            bunny.update(delta)
                    
    def draw(self, delta: float):
        self.screen.fill(self.TRANSPARENT_COLOR)

        for bunny in self.bunnies:
            bunny.draw(delta, self.screen)
        
        pygame.display.flip()
        
    def shutdown(self):
        self.save_bunny_info()
        if self.tray_icon:
            self.tray_icon.stop()
        self.screen.fill(self.TRANSPARENT_COLOR)
        pygame.quit()
        sys.exit()
        
    def run(self):
        self.startup()
        while self.running:
            delta = self._calculate_delta()
            self.update(delta)
            self.draw(delta)
            self.clock.tick(self.fps)
        self.shutdown()

if __name__ == "__main__":
    # 这行代码必须在创建任何窗口或调用其他 GUI 相关API之前执行
    try:
        # 让当前进程对DPI感知，系统将不再对其进行缩放
        ctypes.windll.shcore.SetProcessDPIAware()
    except AttributeError:
        # 兼容非常古老的Windows版本
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except AttributeError:
            pass # 如果都不支持，则跳过

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(constants.DEFAULT_SAVE_DIR, "app.log"),
        when="midnight",   # 每天凌晨 0 点切分
        interval=1,        # 每 1 天
        backupCount=30,    # 最多保留 30 天日志
        encoding="utf-8",
        delay=True
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,  # 只输出INFO及以上级别日志
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8",
        handlers=[file_handler, console_handler]
    )

    world = World(fps=constants.GLOBAL_FPS)
    world.run()
