import pygame
import sys
import ctypes
import os
from components.bunny import Bunny

# 系统托盘相关常量
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 1
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4

class TrayIcon:
    def __init__(self, world_ref):
        self.world = world_ref
        self.hwnd = None
        self.hicon = None
        
    def create_tray(self, hwnd, icon_path):
        self.hwnd = hwnd
        
        if getattr(sys, "frozen", False):
            icon_path = os.path.join(sys._MEIPASS, icon_path)
        
        self.hicon = ctypes.windll.user32.LoadImageW(
            None, icon_path, 1, 32, 32, 0x00000010
        )
        
        if not self.hicon:
            print(f"加载图标失败，使用默认图标")
            self.hicon = ctypes.windll.user32.LoadIconW(None, 32516)
        
        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ('cbSize', ctypes.c_ulong),
                ('hWnd', ctypes.c_void_p),
                ('uID', ctypes.c_uint),
                ('uFlags', ctypes.c_uint),
                ('uCallbackMessage', ctypes.c_uint),
                ('hIcon', ctypes.c_void_p),
                ('szTip', ctypes.c_wchar * 128),
                ('dwState', ctypes.c_ulong),
                ('dwStateMask', ctypes.c_ulong),
                ('szInfo', ctypes.c_wchar * 256),
                ('uTimeout', ctypes.c_uint),
                ('szInfoTitle', ctypes.c_wchar * 64),
                ('dwInfoFlags', ctypes.c_ulong),
            ]
        
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = "Bunny"
        
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        
    def remove_tray(self):
        if not self.hwnd:
            return
            
        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ('cbSize', ctypes.c_ulong),
                ('hWnd', ctypes.c_void_p),
                ('uID', ctypes.c_uint),
                ('uFlags', ctypes.c_uint),
                ('uCallbackMessage', ctypes.c_uint),
                ('hIcon', ctypes.c_void_p),
                ('szTip', ctypes.c_wchar * 128),
                ('dwState', ctypes.c_ulong),
                ('dwStateMask', ctypes.c_ulong),
                ('szInfo', ctypes.c_wchar * 256),
                ('uTimeout', ctypes.c_uint),
                ('szInfoTitle', ctypes.c_wchar * 64),
                ('dwInfoFlags', ctypes.c_ulong),
            ]
        
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        
        if self.hicon:
            ctypes.windll.user32.DestroyIcon(self.hicon)


class World:
    def __init__(self, fps=60):
        self.TRANSPARENT_COLOR = (255, 0, 255)
        self.ICON_PATH = "assets/icon.png"
        self.ICO_PATH = "assets/icon.ico"

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
        self.tray = None

    def startup(self):
        pygame.init()
        pygame.mixer.init()
        
        self.window_size = self.get_physical_work_area()
        
        self.screen = pygame.display.set_mode(self.window_size, pygame.NOFRAME)
        self.clock = pygame.time.Clock()
        self.last_frame_time = pygame.time.get_ticks()
        
        self.hwnd = pygame.display.get_wm_info()['window']
        ctypes.windll.user32.ShowWindow(self.hwnd, 0)

        self._set_icon()
        self._set_transparent()
        self._hide_from_taskbar()
        self._set_always_on_top()
        self._position_to_work_area()
        
        self.tray = TrayIcon(self)
        self.tray.create_tray(self.hwnd, self.ICO_PATH)
        
        ctypes.windll.user32.ShowWindow(self.hwnd, 5)

        self.running = True

        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1])))
        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1])))
        self.bunnies.append(Bunny(pygame.math.Vector2(self.window_size[0], self.window_size[1])))
    
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
        if os.path.exists(self.ICON_PATH):
            try:
                icon = pygame.image.load(self.ICON_PATH)
                pygame.display.set_icon(icon)
            except Exception as e:
                print(f"set icon failed: {e}")

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
    
    def _handle_tray_message(self):
        menu = ctypes.windll.user32.CreatePopupMenu()
        ctypes.windll.user32.AppendMenuW(menu, 0x00000000, 1, "退出")
        
        class POINT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
        point = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        
        ctypes.windll.user32.SetForegroundWindow(self.hwnd)
        cmd = ctypes.windll.user32.TrackPopupMenu(
            menu, 0x0002 | 0x0100, point.x, point.y, 0, self.hwnd, None
        )
        ctypes.windll.user32.DestroyMenu(menu)
        
        if cmd == 1:
            self.running = False
        
    def update(self, delta: float):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
        
        # 处理托盘消息
        try:
            msg = ctypes.wintypes.MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), self.hwnd, WM_TRAYICON, WM_TRAYICON, 1):
                if msg.message == WM_TRAYICON and msg.lParam == 0x0205:  # WM_RBUTTONUP
                    self._handle_tray_message()
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
        except:
            pass
                    
        for bunny in self.bunnies:
            bunny.update(delta)
                    
    def draw(self, delta: float):
        self.screen.fill(self.TRANSPARENT_COLOR)

        for bunny in self.bunnies:
            bunny.draw(delta, self.screen)
        
        pygame.display.flip()
        
    def shutdown(self):
        if self.tray:
            self.tray.remove_tray()
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

    world = World(fps=60)
    world.run()