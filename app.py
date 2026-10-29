#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动关机工具 v4.0 — 液态玻璃界面

UI 技术栈：pywebview + WebView2（与「外设电量守护」同款液态玻璃视觉）
业务逻辑：复用 core/ 全部模块（timer_engine / actions / config / logger / tray）

CLI 参数与 v3.0 完全一致（保持 DSH 关机通道可用）：
  X, S            关闭电脑（可带 /t=秒 倒计时）
  R               重启电脑
  L               注销电脑
  /sm             进入睡眠模式
  /som            关闭显示器
  /rp=path        运行指定程序
  /ra=path        播放指定声音文件
  /m=text         弹出消息提示框显示文本
  /w              不要自动开启倒计时（需配合 /t 使用）
  /t=秒           以秒为单位设置倒计时
  /min            启动后最小化到系统托盘
"""

import os
import sys
import json
import queue
import time
import ctypes
import threading
import datetime
import traceback

APP_VERSION = "v4.0"
APP_NAME = "定时关机"

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
WEB_DIR = os.path.join(RES_DIR, "web")

if not FROZEN and BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import actions
from core.common import get_log_path, format_seconds
from core.config import Config
from core.logger import setup_logging, get_logger, audit
from core.timer_engine import TimerEngine

log = get_logger("app")

# 操作顺序（索引与 core.actions.ACTION_MAP 一致）
ACTION_KEYS = [
    "shutdown", "restart", "logoff", "sleep",
    "monitor_off", "run_program", "play_sound", "message",
]
ACTION_NAMES = {
    "shutdown": "关闭电脑", "restart": "重启电脑", "logoff": "注销电脑",
    "sleep": "睡眠模式", "monitor_off": "关闭显示器", "run_program": "运行程序",
    "play_sound": "播放声音", "message": "弹出消息",
}
WEEK_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "定时关机"


# ======================================================================
# 工具
# ======================================================================

def format_clock(sec):
    """秒 -> 展示串。>=1小时用 H:MM:SS，否则 MM:SS"""
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", ctypes.wintypes.DWORD),
        ("hwndOwner", ctypes.wintypes.HWND),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("lpstrFilter", ctypes.wintypes.LPCWSTR),
        ("lpstrCustomFilter", ctypes.wintypes.LPWSTR),
        ("nMaxCustFilter", ctypes.wintypes.DWORD),
        ("nFilterIndex", ctypes.wintypes.DWORD),
        ("lpstrFile", ctypes.wintypes.LPWSTR),
        ("nMaxFile", ctypes.wintypes.DWORD),
        ("lpstrFileTitle", ctypes.wintypes.LPWSTR),
        ("nMaxFileTitle", ctypes.wintypes.DWORD),
        ("lpstrInitialDir", ctypes.wintypes.LPCWSTR),
        ("lpstrTitle", ctypes.wintypes.LPCWSTR),
        ("Flags", ctypes.wintypes.DWORD),
        ("nFileOffset", ctypes.wintypes.WORD),
        ("nFileExtension", ctypes.wintypes.WORD),
        ("lpstrDefExt", ctypes.wintypes.LPCWSTR),
        ("lCustData", ctypes.wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", ctypes.wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", ctypes.wintypes.DWORD),
        ("FlagsEx", ctypes.wintypes.DWORD),
    ]


def pick_file_native(kind="any", owner_hwnd=None, title="选择文件"):
    """Win32 原生文件选择框（不依赖 tkinter）"""
    try:
        import ctypes.wintypes  # noqa: F401
    except Exception:
        pass

    if kind == "run_program":
        pattern = ("程序与文档\0*.exe;*.bat;*.cmd;*.com;*.lnk;*.msi;*.txt;*.pdf\0"
                   "可执行文件\0*.exe;*.bat;*.cmd\0"
                   "所有文件\0*.*\0\0")
    elif kind == "play_sound":
        pattern = ("音频文件\0*.wav;*.mp3;*.wma;*.m4a;*.flac;*.ogg\0"
                   "所有文件\0*.*\0\0")
    else:
        pattern = "所有文件\0*.*\0\0"

    buf = ctypes.create_unicode_buffer(32768)
    filt = ctypes.create_unicode_buffer(pattern, len(pattern))

    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = owner_hwnd
    ofn.lpstrFilter = ctypes.cast(filt, ctypes.wintypes.LPCWSTR)
    ofn.lpstrFile = ctypes.cast(buf, ctypes.wintypes.LPWSTR)
    ofn.nMaxFile = 32768
    ofn.lpstrTitle = title
    # OFN_EXPLORER | OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST | OFN_NOCHANGEDIR
    ofn.Flags = 0x00080000 | 0x00000800 | 0x00001000 | 0x00000008

    ctypes.windll.comdlg32.GetOpenFileNameW.restype = ctypes.c_int
    ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return ""
    return buf.value


def find_window(title):
    try:
        return ctypes.windll.user32.FindWindowW(None, title)
    except Exception:
        return None


API_WHITELIST = {
    "init", "get_state", "start", "stop", "toggle_pause", "save_prefs",
    "apply_force_close", "set_startup", "pick_file", "win", "open_log", "open_config",
}


def _dispatch(api, name, arg):
    """把 /api/<name> 的 JSON body 派发到 Api 方法。
    body 形如 {"_arg": X} 表示单值参数，其余对象整体作为第一个参数。"""
    if api is None or name not in API_WHITELIST:
        return {"ok": False, "error": "unknown api: " + str(name)}
    fn = getattr(api, name, None)
    if fn is None:
        return {"ok": False, "error": "unknown api: " + str(name)}
    try:
        if not arg:
            return fn()
        if isinstance(arg, dict) and set(arg.keys()) == {"_arg"}:
            return fn(arg["_arg"])
        return fn(arg)
    except TypeError:
        # 方法不接受参数（例如 get_state）
        return fn()


def start_frontend_server(directory, api=None):
    """静态文件 + /api/* 一体服务，随机端口并强制禁用缓存。

    为什么不用 pywebview 自带的 js_api：pywebview 6.x 的 js_api 依赖内置
    HTTP 服务，且只有在窗口 url 是本地文件路径时才会启动；一旦走 remote url，
    window.pywebview.api 就是个空对象，所有方法都缺失。自己开一套接口最稳。
    """
    import http.server
    import socketserver
    import functools
    import urllib.parse

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, *args):
            pass

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if not path.startswith("/api/"):
                self.send_error(404)
                return
            name = path[len("/api/"):].strip("/")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n > 0 else b""
                arg = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                arg = {}
            try:
                result = _dispatch(api, name, arg)
            except Exception as e:
                log.error("API %s 失败: %s\n%s", name, e, traceback.format_exc())
                result = {"ok": False, "error": str(e)}
            data = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class ThreadedServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    handler = functools.partial(Handler, directory=directory)
    httpd = ThreadedServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="frontend-http").start()
    log.info("前端服务已启动: http://127.0.0.1:%d/", port)
    return port


def _apply_env_window_pos(win):
    """ASD_WIN_POS=x,y[,w,h] —— 把窗口摆到指定位置（测试/副屏用）"""
    spec = os.environ.get("ASD_WIN_POS")
    if not spec or not win:
        return
    try:
        parts = [int(v) for v in spec.replace(" ", "").split(",") if v != ""]
        if len(parts) < 2:
            return
        px, py = parts[0], parts[1]
        pw, ph = (parts[2], parts[3]) if len(parts) >= 4 else (None, None)
        hwnd = find_window(APP_NAME) or 0
        SWP_NOACTIVATE = 0x0010
        HWND_TOPMOST = -1
        if hwnd:
            ctypes.windll.user32.SetWindowPos(
                hwnd, ctypes.c_void_p(HWND_TOPMOST), px, py,
                pw if pw else int(win.width),
                ph if ph else int(win.height),
                SWP_NOACTIVATE,
            )
            log.info("窗口已移动到 %d,%d 并置顶 (hwnd=%s)", px, py, hwnd)
        else:
            win.move(px, py)
            log.info("窗口已移动到 %d,%d (pywebview move)", px, py)
    except Exception as e:
        log.warning("窗口定位失败: %s", e)


def startup_registry_path():
    """当前应写入开机启动的命令行"""
    if FROZEN:
        return f'"{sys.executable}" --tray'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    return f'"{exe}" "{os.path.join(BASE_DIR, "app.py")}" --tray'


def is_startup_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, RUN_VALUE)
            return bool(val)
    except Exception:
        return False


def set_startup_enabled(enable):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, startup_registry_path())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        return False, str(e)


# ======================================================================
# 应用主体
# ======================================================================

class ShutdownApp:
    def __init__(self, start_minimized=False):
        self.cfg = Config(os.path.join(BASE_DIR, "settings.ini"))
        self.engine = TimerEngine()
        self.events = queue.Queue()      # Python -> 前端
        self.commands = queue.Queue()    # 托盘 -> 主线程
        self.tray = None
        self.window = None
        self.start_minimized = start_minimized

        self._active = False
        self._finished = False
        self._action = "shutdown"
        self._mode = "countdown"
        self._params = None
        self._total = 0
        self._target_text = ""
        self._allow_close = False
        self._stopping = False

        # 恢复运行时设置到 core
        actions.set_force_close(self.cfg.getboolean("General", "force_close", True))
        try:
            actions.set_main_tk(None)   # 明确不使用 tk 弹窗
        except Exception:
            pass
        self._patch_msgbox()

    # ---- 把 core 里的 tkinter 弹窗换成原生 MessageBoxW ----
    def _patch_msgbox(self):
        def _native(kind, title, message):
            try:
                flags = 0x00000010 if kind == "error" else 0x00000040
                threading.Thread(
                    target=lambda: ctypes.windll.user32.MessageBoxW(
                        None, str(message), str(title), flags),
                    daemon=True, name="native-msgbox",
                ).start()
            except Exception:
                log.error("[弹窗失败] %s: %s", title, message)

        actions._safe_msgbox = _native

    # ---- 配置 ----
    def prefs(self):
        weeks_raw = self.cfg.get("ui", "weeks", "0,0,0,0,0,0,0") or ""
        parts = [p.strip() for p in weeks_raw.split(",")]
        weeks = [(i < len(parts) and parts[i] == "1") for i in range(7)]
        return {
            "mode": self.cfg.get("ui", "mode", "countdown"),
            "action": self.cfg.get("ui", "action", "shutdown"),
            "weeks": weeks,
            "minimize_to_tray": self.cfg.getboolean("General", "minimize_to_tray", True),
            "force_close": self.cfg.getboolean("General", "force_close", True),
            "play_sound_last_10s": self.cfg.getboolean("General", "play_sound_last_10s", False),
            "startup": is_startup_enabled(),
        }

    def save_prefs(self, p):
        try:
            self.cfg.set("ui", "mode", p.get("mode", "countdown"))
            self.cfg.set("ui", "action", p.get("action", "shutdown"))
            weeks = p.get("weeks") or [False] * 7
            self.cfg.set("ui", "weeks", ",".join("1" if w else "0" for w in weeks[:7]))
            self.cfg.setboolean("General", "minimize_to_tray", bool(p.get("minimize_to_tray", True)))
            self.cfg.setboolean("General", "force_close", bool(p.get("force_close", True)))
            self.cfg.setboolean("General", "play_sound_last_10s", bool(p.get("play_sound_last_10s", False)))
            self.cfg.save()
        except Exception as e:
            log.warning("保存设置失败: %s", e)

    # ---- 状态 ----
    def state(self):
        remain = 0
        total = self._total or 0
        paused = False
        running = False

        if self._active:
            running = self.engine.is_running
            paused = self.engine.is_paused
            remain = max(0, int(self.engine.remaining or 0))

        if running:
            pct = 0 if total <= 0 else max(0, min(100, int((1 - remain / total) * 100)))
            urgent = remain <= 10
            caption = "已暂停" if paused else "剩余"
            status = "已暂停" if paused else "计时中"
            hint = "点「继续」恢复，或「停止」取消任务" if paused else "关闭窗口会最小化到托盘"
        elif self._finished:
            pct = 100
            urgent = False
            caption = "已执行"
            status = "已执行完毕"
            hint = "任务已触发，可重新设置下一次"
            remain = 0
        else:
            pct = 0
            urgent = False
            caption = "未开始"
            status = "准备就绪"
            hint = "选好时间与操作后点「开始计时」"

        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                break

        return {
            "running": bool(running),
            "paused": bool(paused),
            "clock": format_clock(remain) if (running or self._finished) else "00:00",
            "pct": pct,
            "caption": caption,
            "status": status,
            "urgent": urgent,
            "hint": hint,
            "target": self._target_text,
            "events": events,
        }

    # ---- 定时控制 ----
    def _compute_target(self, p):
        mode = p.get("mode", "countdown")
        now = datetime.datetime.now()

        if mode == "countdown":
            secs = int(p.get("h", 0)) * 3600 + int(p.get("m", 0)) * 60 + int(p.get("s", 0))
            if secs <= 0:
                raise ValueError("倒计时必须大于 0 秒")
            if secs > 30 * 86400:
                raise ValueError("倒计时最长支持 30 天")
            target = now + datetime.timedelta(seconds=secs)
            return "countdown", secs, target

        if mode == "at_time":
            try:
                dt = datetime.datetime(
                    int(p.get("y", 0)), int(p.get("mo", 1)), int(p.get("d", 1)),
                    int(p.get("h", 0)), int(p.get("m", 0)), int(p.get("s", 0)),
                )
            except ValueError as e:
                raise ValueError("日期或时间不合法：" + str(e))
            if dt <= now:
                raise ValueError("指定的时间已经过去了")
            return "at", dt, dt

        weeks = p.get("weeks") or []
        if not weeks:
            raise ValueError("请至少选择一个星期")
        h, m, s = int(p.get("h", 0)), int(p.get("m", 0)), int(p.get("s", 0))
        days = sorted(int(d) for d in weeks)
        return "periodic", (days, h, m, s), None

    def _validate_param(self, action_key, param):
        param = (param or "").strip()
        if action_key == "run_program":
            if not param:
                raise ValueError("请选择要运行的程序")
            if not os.path.isfile(param):
                raise ValueError("程序不存在：" + param)
            return {"path": param}
        if action_key == "play_sound":
            if not param:
                raise ValueError("请选择要播放的音频文件")
            if not os.path.isfile(param):
                raise ValueError("音频文件不存在：" + param)
            return param
        if action_key == "message":
            if not param:
                raise ValueError("请填写要弹出的消息内容")
            return param
        return None

    def start(self, p):
        if self._active and self.engine.is_running:
            return {"ok": False, "error": "已有任务在计时，先停止它"}

        mode = p.get("mode", "countdown")
        action_key = p.get("action", "shutdown")
        if action_key not in ACTION_KEYS:
            return {"ok": False, "error": "未知操作：" + str(action_key)}

        try:
            kind, payload, target_dt = self._compute_target(p)
            params = self._validate_param(action_key, p.get("param"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        # 记录本次选择
        self._action = action_key
        self._mode = mode
        self._params = params
        self._finished = False
        self._stopping = False

        on_done = lambda: self._on_complete()

        try:
            if kind == "countdown":
                self.engine.start_countdown(payload, on_tick=None, on_complete=on_done)
                self._total = payload
                self._target_text = "目标 <b>" + target_dt.strftime("%m-%d %H:%M:%S") + "</b>"
            elif kind == "at":
                self.engine.start_at_time(payload, on_tick=None, on_complete=on_done)
                self._total = max(1, int((target_dt - datetime.datetime.now()).total_seconds()))
                self._target_text = "目标 <b>" + target_dt.strftime("%m-%d %H:%M:%S") + "</b>"
            else:
                days, h, m, s = payload
                self.engine.start_periodic(days, h, m, s, on_tick=None, on_complete=on_done)
                self._total = self.engine.total
                names = "、".join(WEEK_NAMES[d] for d in days)
                self._target_text = (
                    "每周 <b>" + names + "</b> " + f"{h:02d}:{m:02d}:{s:02d}"
                )
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}

        self._active = True
        self.save_prefs({
            "mode": mode, "action": action_key,
            "weeks": p.get("weeks") or [False] * 7,
            "minimize_to_tray": self.cfg.getboolean("General", "minimize_to_tray", True),
            "force_close": self.cfg.getboolean("General", "force_close", True),
            "play_sound_last_10s": self.cfg.getboolean("General", "play_sound_last_10s", False),
        })
        audit("GUI 开始计时", 模式=mode, 操作=ACTION_NAMES.get(action_key, action_key))
        log.info("GUI 开始计时: 模式=%s 操作=%s 目标=%s", mode, action_key, self._target_text)
        return {"ok": True}

    def stop(self):
        if not self._active:
            return {"ok": True}
        self._stopping = True
        self.engine.stop()
        self._active = False
        self._finished = False
        self._total = 0
        self._target_text = ""
        self.events.put({"type": "toast", "text": "已停止计时", "kind": "warn"})
        log.info("GUI 停止计时")
        return {"ok": True}

    def toggle_pause(self):
        if not (self._active and self.engine.is_running):
            return {"ok": False}
        if self.engine.is_paused:
            self.engine.resume()
            self.events.put({"type": "toast", "text": "已继续", "kind": "ok"})
        else:
            self.engine.pause()
            self.events.put({"type": "toast", "text": "已暂停", "kind": "warn"})
        return {"ok": True}

    def _on_complete(self):
        """定时线程回调：先复位 UI 状态，再执行操作"""
        if self._stopping:
            return

        action_key = self._action
        params = self._params
        mode = self._mode
        name = ACTION_NAMES.get(action_key, action_key)

        self._active = False
        if mode == "periodic":
            # 循环任务：执行完继续等下一轮
            self._active = True
            try:
                self._total = self.engine.total
            except Exception:
                pass
        else:
            self._finished = True
            self._total = 0
            self._target_text = ""

        log.info("定时触发: 操作=%s 模式=%s", name, mode)
        audit("定时触发", 操作=name, 模式=mode)
        self.events.put({"type": "toast", "text": "定时到点，正在执行：" + name, "kind": "ok"})

        try:
            idx = ACTION_KEYS.index(action_key)
            actions.execute_action(idx, params)
        except Exception as e:
            log.error("执行操作失败: %s\n%s", e, traceback.format_exc())
            self.events.put({"type": "error", "text": "执行失败：" + str(e)})

        self.events.put({"type": "completed", "text": "已执行：" + name})

    # ---- 托盘 ----
    def start_tray(self):
        try:
            from core.tray import TrayIcon, set_default_tray
            self.tray = TrayIcon(APP_NAME, commands=self.commands)
            self.tray.start()
            set_default_tray(self.tray)
            threading.Thread(target=self._tray_loop, daemon=True, name="tray-cmd").start()
            log.info("托盘图标已启动")
        except Exception as e:
            log.warning("托盘启动失败: %s", e)
            self.tray = None

    def _tray_loop(self):
        while True:
            try:
                cmd = self.commands.get(timeout=0.6)
            except queue.Empty:
                continue
            try:
                if cmd == "show":
                    self._show_window()
                elif cmd == "exit":
                    self._real_exit()
                    return
            except Exception:
                pass

    def _show_window(self):
        try:
            if self.window:
                self.window.show()
                self.window.restore()
        except Exception as e:
            log.warning("显示窗口失败: %s", e)

    def _real_exit(self):
        self._allow_close = True
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass

    # ---- 窗口关闭拦截 ----
    def on_closing(self):
        if self._allow_close:
            self._save_geometry()
            return True

        want_tray = self.cfg.getboolean("General", "minimize_to_tray", True)
        if want_tray and self.tray is not None and self.tray.is_alive:
            try:
                if self.tray:
                    self.tray.notify(APP_NAME, "已最小化到托盘，右键托盘图标可退出")
            except Exception:
                pass
            self.window.hide()
            return False

        self._save_geometry()
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        return True

    def _save_geometry(self):
        try:
            w = self.window
            if not w:
                return
            self.cfg.set("Window", "geometry",
                         f"{int(w.width)}x{int(w.height)}+{int(w.x)}+{int(w.y)}")
            self.cfg.save()
        except Exception:
            pass

    def _load_geometry(self):
        geo = self.cfg.get("Window", "geometry", "") or ""
        try:
            size, x, y = geo.split("+")
            cw, ch = size.split("x")
            cw, ch, x, y = int(cw), int(ch), int(x), int(y)
            if 900 <= cw <= 4000 and 620 <= ch <= 3000 and -3000 < x < 12000 and -3000 < y < 12000:
                return cw, ch, x, y
        except Exception:
            pass
        return 1140, 780, None, None


# ======================================================================
# JS 桥
# ======================================================================

class Api:
    def __init__(self, app):
        self.app = app

    # -- 初始化 --
    def init(self):
        now = datetime.datetime.now()
        log.info("前端已连接，返回初始化数据（%s）", APP_VERSION)
        return {
            "version": APP_VERSION,
            "prefs": self.app.prefs(),
            "now": now.isoformat(),
            "actions": ACTION_KEYS,
            "last_start_seconds": self.app.cfg.getint("ui", "last_seconds", 0),
        }

    # -- 状态轮询 --
    def get_state(self):
        return self.app.state()

    # -- 控制 --
    def start(self, payload):
        try:
            return self.app.start(payload or {})
        except Exception as e:
            log.error("start 异常: %s\n%s", e, traceback.format_exc())
            return {"ok": False, "error": str(e)}

    def stop(self):
        return self.app.stop()

    def toggle_pause(self):
        return self.app.toggle_pause()

    # -- 设置 --
    def save_prefs(self, prefs):
        self.app.save_prefs(prefs or {})
        return {"ok": True}

    def apply_force_close(self, value):
        actions.set_force_close(bool(value))
        return {"ok": True}

    def set_startup(self, enable):
        ok, err = set_startup_enabled(bool(enable))
        return {"ok": ok, "error": err}

    # -- 文件选择 --
    def pick_file(self, action_key):
        hwnd = find_window(APP_NAME)
        title = "选择要运行的程序" if action_key == "run_program" else "选择音频文件"
        return pick_file_native(action_key, hwnd, title)

    # -- 窗口 --
    def win(self, what):
        w = self.app.window
        if not w:
            return {"ok": False}
        try:
            if what == "minimize":
                if self.app.cfg.getboolean("General", "minimize_to_tray", True) and self.app.tray:
                    w.hide()
                else:
                    w.minimize()
            elif what == "maximize":
                w.maximize()
            elif what == "close":
                if self.app.cfg.getboolean("General", "minimize_to_tray", True) and self.app.tray \
                        and self.app.tray.is_alive:
                    w.hide()
                    try:
                        self.app.tray.notify(APP_NAME, "已最小化到托盘，右键托盘图标可退出")
                    except Exception:
                        pass
                else:
                    self.app._real_exit()
            return {"ok": True}
        except Exception as e:
            log.warning("窗口操作失败 %s: %s", what, e)
            return {"ok": False, "error": str(e)}

    # -- 杂项 --
    def open_log(self):
        try:
            d = os.path.dirname(get_log_path())
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_config(self):
        try:
            p = os.path.join(BASE_DIR, "settings.ini")
            if os.path.isfile(p):
                os.startfile(p)
                return {"ok": True}
            return {"ok": False, "error": "配置文件不存在"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ======================================================================
# 单实例
# ======================================================================

def ensure_single_instance():
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "AutoShutdownTool_v4_Mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:
            # 已有实例：把它的窗口叫出来
            hwnd = find_window(APP_NAME)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            return False
    except Exception:
        pass
    return True


# ======================================================================
# 入口
# ======================================================================

def run_cli(argv):
    """CLI 模式：与旧版完全一致的行为（供 DSH 等自动化调用）"""
    from main import parse_args, cli_mode
    params = parse_args(argv)
    if params["action"] is None:
        print("错误: 未指定操作", file=sys.stderr)
        return 1
    cli_mode(params)
    return 0


def main():
    setup_logging(os.environ.get("ASD_LOG_LEVEL", "INFO"))
    # pywebview 会刷 WebView2 的 COM 噪音（CanGoForward / CoreWebView2 无限递归），
    # 与功能无关，直接压到 CRITICAL。
    import logging as _logging
    _logging.getLogger("pywebview").setLevel(_logging.CRITICAL)
    log.info("========== %s %s 启动 ==========", APP_NAME, APP_VERSION)
    log.info("命令行参数: %r", sys.argv[1:])

    argv = sys.argv[1:]
    want_gui_flag = "--gui" in argv
    args = [a for a in argv if a not in ("--gui", "--tray")]

    # 有 CLI 动作参数 -> 走命令行
    from main import parse_args
    parsed = parse_args(args)
    if parsed["action"] is not None:
        return run_cli(args)

    if not ensure_single_instance():
        log.info("已有实例在运行，本次启动退出")
        return 0

    start_minimized = ("--tray" in argv) or parsed["min"] or \
        Config(os.path.join(BASE_DIR, "settings.ini")).getboolean("General", "start_minimized", False)

    import webview

    app = ShutdownApp(start_minimized=start_minimized)
    api = Api(app)

    width, height, x, y = app._load_geometry()
    port = start_frontend_server(WEB_DIR, api)
    kwargs = dict(
        title=APP_NAME,
        url=f"http://127.0.0.1:{port}/index.html",
        js_api=api,
        width=width,
        height=height,
        min_size=(980, 660),
        frameless=True,
        easy_drag=True,
        background_color="#0a0d13",
        text_select=False,
        confirm_close=False,
    )
    if x is not None:
        kwargs["x"] = x
        kwargs["y"] = y

    window = webview.create_window(**kwargs)
    app.window = window
    window.events.closing += app.on_closing

    def _after_start():
        app.start_tray()
        _apply_env_window_pos(window)
        if start_minimized:
            try:
                window.hide()
            except Exception:
                pass
        log.info("GUI 已就绪（%dx%d）", width, height)

    try:
        webview.start(_after_start, gui="edgechromium", debug=False)
    except Exception as e:
        log.critical("GUI 启动失败: %s\n%s", e, traceback.format_exc())
        print(f"错误: {e}", file=sys.stderr)
        print(f"日志: {get_log_path()}", file=sys.stderr)
        return 1
    finally:
        try:
            if app.tray:
                app.tray.stop()
        except Exception:
            pass

    log.info("========== 退出 ==========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
