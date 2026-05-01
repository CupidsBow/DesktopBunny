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


class World:
    def __init__(self, fps=constants.GLOBAL_FPS):
        self.TRANSPARENT_COLOR = (255, 0, 255)
        self.ICON_PATH = constants.BUNNY_ICON

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
        self.screen_analyze_enabled = True

        self.detector = PlatformDetector()

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

        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1]), "Blossom"))
        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1]), "Bubble"))
        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1]), "Buttercup"))
        self._start_tray()
        self.platform_detect_thread = threading.Thread(target=self._update_platforms_loop, daemon=True)
        self.platform_detect_thread.start()
        self.screen_analyze_thread = threading.Thread(target=self._screen_analyze_loop, daemon=True)
        self.screen_analyze_thread.start()
    
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
            pystray.MenuItem("Add a bunny", self._on_tray_add_bunny),
            pystray.MenuItem("Delete a bunny", self._on_tray_delete_bunny),
            pystray.MenuItem(
                "Detect platforms",
                self._on_tray_toggle_platform_detection,
                checked=lambda item: self.detect_platforms_enabled
            ),
            pystray.MenuItem(
                "Read screen (Ollama needed)",
                self._on_tray_toggle_screen_analysis,
                checked=lambda item: self.screen_analyze_enabled
            ),
            pystray.MenuItem("Quit", self._on_tray_exit)
        ]

        return pystray.Menu(*(bunny_items + control_items))

    def _on_tray_add_bunny(self):
        if self.bunnies and len(self.bunnies) < constants.BUNNY_MAX_NUM:
            self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1])))
            self._refresh_tray_menu()  # 新增这行

    def _on_tray_delete_bunny(self):
        if self.bunnies and len(self.bunnies) > 1:
            self.bunnies.pop()
            self._refresh_tray_menu()  # 新增这行

    def _on_tray_toggle_platform_detection(self, *args):
        self.detect_platforms_enabled = not self.detect_platforms_enabled
        if not self.detect_platforms_enabled:
            for bunny in self.bunnies:
                bunny.set_platforms([self.INIT_BOTTOM_PLATFORM])

    def _on_tray_toggle_screen_analysis(self, *args):
        self.screen_analyze_enabled = not self.screen_analyze_enabled

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
            print("get work area failed")
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
                print(f"set icon failed: {e}")

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
            print(f"set win32 icon failed: {e}")

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
            print(f"hide from taskbar failed: {e}")
        
    def _set_always_on_top(self):
        try:
            import pywinctl as pwc
            self.window = pwc.Window(self.hwnd)
            self.window.alwaysOnTop(True)
        except ImportError:
            print("pywinctl not found.")
    
    def _update_platforms_loop(self):
        while self.running:
            if self.detect_platforms_enabled:
                platforms = self.detector.get_platforms_for_bunny(top_n=constants.PLATFORM_MAX_NUM)
                for bunny in self.bunnies:
                    bunny.set_platforms([self.INIT_BOTTOM_PLATFORM] + platforms)
            time.sleep(constants.PLATFORM_DETECT_TIME_INTERVAL_SECONDS)

    def _screen_analyze_loop(self):
        from tools.screen_analyzer import ScreenAnalyzer
        analyzer = ScreenAnalyzer()
        while self.running:
            try:
                if self.screen_analyze_enabled:
                    comment_bunny = max(self.bunnies, key=lambda b: b.current_position.y)
                    comment = analyzer.analyze(self.detector, comment_bunny)
                    if comment:
                        comment_bunny.set_comment(comment)
            except Exception as e:
                print(f"Screen analyze failed: {e}")
            time.sleep(random.randint(
                constants.SCREEN_ANALYZE_TIME_INTERVAL_MIN_SECONDS, 
                constants.SCREEN_ANALYZE_TIME_INTERVAL_MAX_SECONDS
            ))

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
        if self.tray_icon:
            self.tray_icon.stop()
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

    world = World(fps=constants.GLOBAL_FPS)
    world.run()
