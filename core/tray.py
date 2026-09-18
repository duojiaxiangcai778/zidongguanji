# core/tray.py
# 系统托盘图标 + Win32 气泡通知（纯 ctypes，零第三方依赖）
#
# v3.0 新增:
#   - TrayIcon: 常驻托盘图标（左键显示主窗口，右键菜单：显示主窗口/退出程序）
#   - show_balloon(): 独立气泡通知，替代 v2 的 PowerShell WinRT 方案
#     （PowerShell 方案每次冷启动 1~2 秒，且借用 AUMID 时 toast 会被系统静默丢弃）
#
# 全部 Win32 调用都在专用 daemon 线程的消息循环里完成，绝不阻塞 Tk 主线程。

import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import threading
import time

# ---------- Win32 常量 ----------
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x1, 0x2, 0x3
WM_APP_TRAY = 0x8000 + 7          # 托盘回调消息
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_TIMER, WM_QUIT, WM_NULL = 0x0113, 0x0012, 0x0000
IMAGE_ICON = 1
IDI_APPLICATION = 32512
LR_DEFAULTSIZE, LR_SHARED = 0x40, 0x8000
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
MENU_SHOW, MENU_EXIT = 1, 2

LRESULT = wt.LPARAM  # LONG_PTR，与 LRESULT 同宽

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32


class _NID(ctypes.Structure):
    """NOTIFYICONDATAW（不含完整 guidItem 字段对齐差异，按官方布局声明）"""
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HANDLE),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
    ]


_WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND), ("message", wt.UINT),
        ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
        ("time", wt.DWORD), ("pt", wt.POINT),
    ]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

_class_seq = 0
_class_lock = threading.Lock()


def _load_app_icon():
    """优先取 exe 自带图标，失败回退系统默认应用图标"""
    try:
        large = wt.HANDLE()
        small = wt.HANDLE()
        path = sys.executable
        if shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1) > 0:
            return (small.value or large.value) or None
    except Exception:
        pass
    try:
        return user32.LoadIconW(None, IDI_APPLICATION)
    except Exception:
        return None


def _register_window_class(name, wndproc, hicon):
    """注册隐藏消息窗口类，返回 (atom, hinstance, wndproc_cb)

    注意: 必须把返回的 wndproc_cb 保存在与窗口同生命周期的引用里，
    否则 ctypes 回调对象被 GC 后，第一条窗口消息就会调用悬空指针硬崩。
    """
    hinstance = kernel32.GetModuleHandleW(None)
    wndproc_cb = _WNDPROC(wndproc)  # 持久引用，随返回值交给调用方持有
    wc = _WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = wndproc_cb
    wc.hInstance = hinstance
    wc.hIcon = hicon
    wc.lpszClassName = name
    atom = user32.RegisterClassW(ctypes.byref(wc))
    return atom, hinstance, wndproc_cb


def _pump_messages(msg):
    """标准消息循环；收到 WM_QUIT 或错误时返回"""
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            return
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


class TrayIcon:
    """常驻系统托盘图标。

    :param tooltip: 悬停提示
    :param commands: queue.Queue，用户操作入队（'show' / 'exit'），由 UI 主循环轮询
    """

    def __init__(self, tooltip="自动关机工具", commands=None):
        self.tooltip = tooltip
        self.commands = commands if commands is not None else queue.Queue()
        self._thread = None
        self._hwnd = None
        self._nid = None
        self._started = threading.Event()
        self._stop_requested = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="tray-icon")
        self._thread.start()
        self._started.wait(timeout=3)

    def stop(self):
        """停止托盘线程（退出前调用）"""
        self._stop_requested.set()
        if self._thread and self._thread.is_alive():
            try:
                user32.PostThreadMessageW(self._thread.ident, WM_QUIT, 0, 0)
            except Exception:
                pass
            self._thread.join(timeout=2)

    @property
    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def notify(self, title, text, kind="info"):
        """通过当前托盘图标弹出气泡通知（不新增图标）"""
        if not self._nid:
            return False
        niif = {"info": NIIF_INFO, "warning": NIIF_WARNING, "error": NIIF_ERROR}.get(kind, NIIF_INFO)
        nid = self._nid
        nid.uFlags |= NIF_INFO
        nid.szInfo = (text or "")[:255]
        nid.szInfoTitle = (title or self.tooltip)[:63]
        nid.dwInfoFlags = niif
        return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    # ---- 内部：托盘线程 ----

    def _run(self):
        global _class_seq
        hicon = _load_app_icon()

        with _class_lock:
            _class_seq += 1
            class_name = f"ASD_Tray_{_class_seq}_{id(self):x}"

        tray = self  # 闭包引用

        def dispatch(hwnd, msg, wparam, lparam):
            if msg == WM_APP_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    tray.commands.put("show")
                elif event == WM_RBUTTONUP:
                    tray._popup_menu(hwnd)
            elif msg == WM_TIMER and wparam == 1:
                user32.PostQuitMessage(0)

        def wndproc(hwnd, m, w, l):
            try:
                dispatch(hwnd, m, w, l)
            except Exception:
                pass
            return user32.DefWindowProcW(hwnd, m, w, l)

        atom, hinstance, wndproc_cb = _register_window_class(class_name, wndproc, hicon)
        if not atom:
            return
        self._wndproc_cb = wndproc_cb  # 与窗口同生命周期，防止回调被 GC
        hwnd = user32.CreateWindowExW(
            0, class_name, "ASD_Tray_Hidden", 0x80000000,  # WS_POPUP，不可见
            0, 0, 0, 0, None, None, hinstance, None,
        )
        if not hwnd:
            return
        self._hwnd = hwnd

        nid = _NID()
        nid.cbSize = ctypes.sizeof(_NID)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid
        self._started.set()

        msg = _MSG()
        _pump_messages(msg)  # WM_QUIT（stop()）时返回

        # 清理
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, hinstance)
        self._hwnd = None
        self._nid = None

    def _popup_menu(self, hwnd):
        """右键弹出菜单（TrackPopupMenu 阻塞在本托盘线程，安全）"""
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, MF_STRING, MENU_SHOW, "显示主窗口")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, MENU_EXIT, "退出程序")
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # TrackPopupMenu 前必须 SetForegroundWindow，否则点击菜单外无法关闭
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenuEx(
            menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
            pt.x, pt.y, hwnd, None,
        )
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == MENU_SHOW:
            self.commands.put("show")
        elif cmd == MENU_EXIT:
            self.commands.put("exit")


# ---------- 独立气泡通知（无托盘时使用，如 CLI 模式） ----------

_balloon_threads = []
_balloon_lock = threading.Lock()


def show_balloon(title, text, timeout=10, kind="info"):
    """弹出一次性系统气泡通知。

    在独立 daemon 线程里创建隐藏窗口+临时托盘图标并显示气泡，
    10 秒后自动清理。全程异步，调用方立即返回。
    """
    t = threading.Thread(
        target=_balloon_thread, args=(title, text, timeout, kind),
        daemon=True, name="balloon-notify",
    )
    with _balloon_lock:
        _balloon_threads.append(t)
    t.start()
    return t


def wait_balloons(timeout=12):
    """等待全部在飞气泡通知结束（CLI 模式退出前调用，防止通知被杀）"""
    deadline = time.time() + timeout
    for t in list(_balloon_threads):
        remain = max(0, deadline - time.time())
        t.join(timeout=remain)


def _balloon_thread(title, text, timeout, kind):
    global _class_seq
    niif = {"info": NIIF_INFO, "warning": NIIF_WARNING, "error": NIIF_ERROR}.get(kind, NIIF_INFO)
    hicon = _load_app_icon()

    with _class_lock:
        _class_seq += 1
        class_name = f"ASD_Balloon_{_class_seq}_{threading.get_ident():x}"

    def wndproc(hwnd, m, w, l):
        if m == WM_TIMER and w == 1:
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, m, w, l)

    atom, hinstance, wndproc_cb = _register_window_class(class_name, wndproc, hicon)
    if not atom:
        return
    # wndproc_cb 与本线程同生命周期（函数运行期间一直被局部变量引用）
    hwnd = user32.CreateWindowExW(
        0, class_name, "ASD_Balloon_Hidden", 0x80000000,
        0, 0, 0, 0, None, None, hinstance, None,
    )
    if not hwnd:
        return

    nid = _NID()
    nid.cbSize = ctypes.sizeof(_NID)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_INFO | NIF_SHOWTIP
    nid.uCallbackMessage = WM_APP_TRAY
    nid.hIcon = hicon
    nid.szInfo = (text or "")[:255]
    nid.szInfoTitle = (title or "")[:63]
    nid.dwInfoFlags = niif
    ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    if ok:
        # NIM_ADD + NIF_INFO 直接显示气泡；定时器控制图标存续时间
        user32.SetTimer(hwnd, 1, int(timeout * 1000), None)
        msg = _MSG()
        _pump_messages(msg)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    user32.DestroyWindow(hwnd)
    user32.UnregisterClassW(class_name, hinstance)

    with _balloon_lock:
        try:
            _balloon_threads.remove(threading.current_thread())
        except ValueError:
            pass


# ---------- 全局默认托盘（供 actions.show_message 复用） ----------

_default_tray = None
_default_tray_lock = threading.Lock()


def set_default_tray(tray):
    """注册 UI 的常驻托盘实例；show_message 优先通过它发通知"""
    global _default_tray
    with _default_tray_lock:
        _default_tray = tray


def get_default_tray():
    with _default_tray_lock:
        if _default_tray and _default_tray.is_alive:
            return _default_tray
    return None
