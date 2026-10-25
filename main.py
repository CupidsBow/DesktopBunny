from components.bunny_platform import Platform
import pygame
import sys
import ctypes
import os
import queue
import threading
import time
from components.bunny import Bunny
from components.single_instance import SingleInstance
from PIL import Image
import pystray
from constants import constants
from tools.platform_detector import PlatformDetector
from bot import bridge
from bot.qq_client import QQClient
from manager.brain import Brain
from manager.mesh.mesh import MeshService
from manager.save_manager import SaveManager
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
        self.bunny = None
        self.brain = None
        self.qq_client = None
        self.mesh = None
        # 认知线程请求进食 → 主线程消费，饱食度仍由 bunny 权威维护
        self._eat_requests: queue.Queue[str] = queue.Queue()
        self.tray_icon = None
        self.tray_thread = None
        self.detect_platforms_enabled = True
        # 单实例闸门：存活到进程结束，防止出现第二个 Bunny（含 LLM 借 sandbox 自启动）
        self._instance_guard: SingleInstance | None = None

        self.detector = PlatformDetector()
        self.save_manager = SaveManager()

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

        self.bunny = self._create_bunny(self._load_satiety())

        self._start_tray()
        self.platform_detect_thread = threading.Thread(target=self._update_platforms_loop, daemon=True)
        self.platform_detect_thread.start()
        self.refresh_tray_menu_thread = threading.Thread(target=self._refresh_tray_menu_loop, daemon=True)
        self.refresh_tray_menu_thread.start()
        self.auto_save_thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self.auto_save_thread.start()
        self.screen_change_thread = threading.Thread(target=self._watch_screen_change_loop, daemon=True)
        self.screen_change_thread.start()

        bridge.set_bunny_snapshot({"satiety": self.bunny.satiety})

        self.qq_client = QQClient()
        self.mesh = MeshService(
            constants.PEERS_DIR,
            slug=constants.BUNNY_NAME_SLUG,
            name=constants.BUNNY_NAME,
        )
        self.brain = Brain(
            transport=self.qq_client,
            snapshot_getter=bridge.get_bunny_snapshot,
            eat_handler=self._request_eat,
            name=constants.BUNNY_NAME,
            bunny_friends=[f"peer:{slug}" for slug in constants.BUNNY_FRIENDS],
            mesh=self.mesh,
        )
        self.brain.start()
        self.qq_client.set_brain(self.brain)
        self.qq_client.start()

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
    
    def _on_tray_interact_bunny(self, icon, item):
        if self.bunny:
            self.bunny.set_comment(f"主人，{constants.BUNNY_NAME}在这哦~")

    def _refresh_tray_menu(self):
        if not self.tray_icon:
            return
        self.tray_icon.menu = self._build_menu()
        self.tray_icon.update_menu()

    def _build_menu(self):
        bunny_items = [
            pystray.MenuItem(
                lambda item: f"🐰{constants.BUNNY_NAME}: 饱食度 {int(self.bunny.satiety)}",
                self._on_tray_interact_bunny,
            )
        ]

        control_items = [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "平台检测",
                self._on_tray_toggle_platform_detection,
                checked=lambda item: self.detect_platforms_enabled
            ),
            pystray.MenuItem("退出", self._on_tray_exit)
        ]

        return pystray.Menu(*(bunny_items + control_items))

    def _load_satiety(self):
        """读取 Alice 的饱食度，兼容旧的 {name: {"satiety": ...}} 存档。"""
        data = self.save_manager.load()
        if not isinstance(data, dict) or not data:
            return None
        if "satiety" in data:
            return data.get("satiety")
        first = next(iter(data.values()), None)
        if isinstance(first, dict):
            return first.get("satiety")
        return None

    def _create_bunny(self, satiety=None) -> Bunny:
        """创建 Alice 的身体并让它进入 Idle 状态。

        startup() 不能省：它才会播 Idle 动画并把 idle_timer 设成随机的 5~15 秒。
        少了它兔子会定格在第 0 帧，而且 idle_timer 还是 0，开局第一帧就起跳。
        """
        bunny = Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1]))
        if satiety is not None:
            bunny.satiety = satiety
        bunny.startup()
        return bunny

    def _on_tray_toggle_platform_detection(self, *args):
        self.detect_platforms_enabled = not self.detect_platforms_enabled
        if not self.detect_platforms_enabled and self.bunny:
            self.bunny.set_platforms([self.INIT_BOTTOM_PLATFORM])

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
            if self.detect_platforms_enabled and self.bunny:
                platforms = self.detector.get_platforms_for_bunny(top_n=constants.PLATFORM_MAX_NUM)
                self.bunny.set_platforms([self.INIT_BOTTOM_PLATFORM] + platforms)
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
        if self.bunny:
            self.save_manager.save({"satiety": self.bunny.satiety})

    def _request_eat(self, food: str = "") -> bool:
        """能力线程调用：把进食请求排队，等主线程下一帧执行。"""
        if not self.bunny or not self.running:
            return False
        self._eat_requests.put(food or "胡萝卜")
        return True

    def _apply_eat_requests(self):
        while True:
            try:
                food = self._eat_requests.get_nowait()
            except queue.Empty:
                return
            self.bunny.eat(food)

    def update(self, delta: float):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and self.bunny:
                    self.bunny.handle_click(event.pos)
            elif event.type == pygame.DROPFILE:
                file_path = event.file
                mouse_pos = pygame.mouse.get_pos()
                if self.bunny and self.bunny.is_position_inside_bunny(mouse_pos):
                    self.bunny.eat_carrot(file_path)

        if self.bunny:
            self._apply_eat_requests()
            self.bunny.update(delta)
            bridge.set_bunny_snapshot({"satiety": self.bunny.satiety})
        if self.brain:
            self.brain.tick(delta)

    def draw(self, delta: float):
        self.screen.fill(self.TRANSPARENT_COLOR)

        if self.bunny:
            self.bunny.draw(delta, self.screen)

        pygame.display.flip()
        
    def shutdown(self):
        self.save_bunny_info()
        if self.qq_client:
            self.qq_client.stop()
        if self.brain:
            self.brain.stop("桌面关闭")
        if self.tray_icon:
            self.tray_icon.stop()
        self.screen.fill(self.TRANSPARENT_COLOR)
        pygame.quit()
        sys.exit()
        
    def _acquire_single_instance(self) -> bool:
        """拿到单实例权才继续；已有实例时本进程立即退出。"""
        guard = SingleInstance()
        if not guard.acquire():
            self.logger.warning("已有 Bunny 进程在运行，本实例退出")
            return False
        self._instance_guard = guard
        return True

    def run(self):
        if not self._acquire_single_instance():
            return
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
    os.makedirs(constants.BUNNY_DATA_DIR, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(constants.BUNNY_DATA_DIR, "app.log"),
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
