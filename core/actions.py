# core/actions.py
# 系统操作模块 - 实现 8 种可执行操作（v3.0：全操作接入统一日志）

import os
import subprocess
import ctypes
import ctypes.wintypes as wt
import winsound
import time
from tkinter import messagebox

from core.common import strip_quotes, get_log_path
from core.logger import get_logger, audit

log = get_logger("actions")

# 控制关机/重启是否强制关闭应用（CLI 默认强制；GUI 启动时按配置覆盖）
_force_shutdown = True

# 全局缓存主 Tk 实例（由 ui/main_window 在构造时注入），供定时线程内的弹窗复用
_main_tk = None


def set_main_tk(root):
    """注入主 Tk 根窗口，供定时线程在已存在的实例上弹窗，避免创建多个 Tk()"""
    global _main_tk
    _main_tk = root


def _safe_msgbox(kind, title, message):
    """在主线程上弹窗。若无主 Tk 则静默写日志（不阻塞定时线程）"""
    try:
        if _main_tk is not None:
            try:
                _main_tk.after(0, lambda: _do_msgbox(kind, title, message))
                return
            except Exception:
                pass
    except Exception:
        pass
    # 兜底：写日志（避免 worker 线程里 messagebox 阻塞定时器或触发 Tcl 安全错误）
    log.error("[弹窗兜底] %s: %s", title, message)


def _do_msgbox(kind, title, message):
    """必须在主线程调用的真实弹窗"""
    try:
        if kind == "error":
            messagebox.showerror(title, message)
        else:
            messagebox.showinfo(title, message)
    except Exception:
        pass


def set_force_close(val):
    """设置是否强制关闭阻止的应用程序（在 GUI 加载配置后调用）"""
    global _force_shutdown
    _force_shutdown = bool(val)
    log.info("强制关闭应用程序: %s", "开启" if val else "关闭")


# 安全关闭列表：需要先保存再退出的程序（窗口标题关键词）
SAFE_CLOSE_PROGRAMS = ['Hermes', '爱马仕', 'Word', 'Excel', 'PowerPoint',
                       'Outlook', 'WPS文字', 'WPS表格', 'WPS演示',
                       'WPS Office', 'LibreOffice']

# 不需要关闭的程序
SKIP_CLOSE_PROGRAMS = ['QQ', '微信', 'WeChat', 'TIM']


NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW，防止关机时闪黑窗


def _bind_user32():
    """为关键 ctypes 调用绑定签名，避免 64 位指针被截断导致崩溃/静默失败"""
    try:
        user32 = ctypes.windll.user32
        # EnumWindows
        user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.EnumWindows.restype = ctypes.c_bool
        # GetWindowTextW / GetWindowTextLengthW
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
        # SendMessageTimeoutW（带超时，避免窗口卡死时永久阻塞定时线程）
        user32.SendMessageTimeoutW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_void_p
        # 系统电源
        ctypes.windll.powrprof.SetSuspendState.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
        ctypes.windll.powrprof.SetSuspendState.restype = ctypes.c_bool
    except Exception:
        pass


def log_exception_local(context, exc):
    """模块内异常记录（带堆栈）"""
    import traceback
    if isinstance(exc, BaseException) and exc.__traceback__:
        log.error("%s: %s\n%s", context, exc, traceback.format_exc())
    else:
        log.error("%s: %s", context, exc)


def safe_close_windows(timeout_ms=3000):
    """发送 WM_CLOSE 给需要安全关闭的程序窗口，等待它们保存后退出。

    返回 True 如果找到了需要安全关闭的窗口（调用方应避免使用 /f）。
    """
    _bind_user32()
    found_windows = []
    try:
        WM_CLOSE = 0x0010
        SMTO_ABORTIFHUNG = 0x0002

        enum_windows = ctypes.windll.user32.EnumWindows
        get_window_text = ctypes.windll.user32.GetWindowTextW
        get_window_text_length = ctypes.windll.user32.GetWindowTextLengthW
        is_window_visible = ctypes.windll.user32.IsWindowVisible
        send_timeout = ctypes.windll.user32.SendMessageTimeoutW

        def enum_proc(hwnd, lparam):
            try:
                if is_window_visible(hwnd):
                    length = get_window_text_length(hwnd) + 1
                    buffer = ctypes.create_unicode_buffer(length)
                    get_window_text(hwnd, buffer, length)
                    title = buffer.value.lower()

                    for prog in SAFE_CLOSE_PROGRAMS:
                        if prog.lower() in title:
                            skip = any(s.lower() in title for s in SKIP_CLOSE_PROGRAMS)
                            if not skip:
                                found_windows.append(hwnd)
                                log.info("安全关闭目标窗口: %s", buffer.value)
                            break
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        enum_windows(WNDENUMPROC(enum_proc), 0)

        if not found_windows:
            log.debug("未检测到需要安全关闭的窗口")
            return False

        # 发送 WM_CLOSE 给每个找到的窗口（带超时，防止窗口无响应时卡死定时线程）
        for hwnd in found_windows:
            try:
                result = ctypes.c_size_t()
                send_timeout(hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result))
            except Exception:
                pass

        log.info("已发送 WM_CLOSE 给 %d 个窗口，等待 %.1f 秒保存...",
                 len(found_windows), timeout_ms / 1000.0)
        time.sleep(timeout_ms / 1000.0)

    except Exception as e:
        log_exception_local("safe_close_windows 出错", e)
    return len(found_windows) > 0


def _run_shutdown(args):
    """通过 subprocess 调用 shutdown.exe（固定程序名 + 列表参数，无 shell 拼接）"""
    log.info("调用 shutdown.exe，参数: %r", args)
    try:
        proc = subprocess.run(["shutdown.exe", *args], creationflags=NO_WINDOW, check=False)
        if proc.returncode != 0:
            # 1190=已有关机在进行 1116=无可结束的会话，属正常噪音，记 WARNING
            log.warning("shutdown.exe 退出码 %s，参数: %r", proc.returncode, args)
        else:
            log.info("shutdown.exe 执行成功")
    except Exception as e:
        log_exception_local(f"shutdown 调用失败，参数: {args!r}", e)


def shutdown():
    """安全关闭程序后关闭电脑。
    如果检测到需要安全关闭的窗口，不使用 /f，让 Windows 拦截界面接管。
    """
    audit("开始执行操作", 操作="关闭电脑")
    had_safe_windows = safe_close_windows()
    cmd_args = ["/s", "/t", "0"]
    if _force_shutdown and not had_safe_windows:
        cmd_args.append("/f")
    _run_shutdown(cmd_args)


def restart():
    """安全关闭程序后重启电脑。
    如果检测到需要安全关闭的窗口，不使用 /f，让 Windows 拦截界面接管。
    """
    audit("开始执行操作", 操作="重启电脑")
    had_safe_windows = safe_close_windows()
    cmd_args = ["/r", "/t", "0"]
    if _force_shutdown and not had_safe_windows:
        cmd_args.append("/f")
    _run_shutdown(cmd_args)


def logoff():
    """注销电脑 — 异步执行，避免等待确认对话框"""
    audit("开始执行操作", 操作="注销电脑")
    # shutdown /l 不支持 /f 参数
    _run_shutdown(["/l"])


def sleep():
    """进入睡眠模式。

    SetSuspendState 需要先设置 SE_SHUTDOWN_PRIVILEGE，
    否则启用休眠时调用可能静默失败。
    """
    audit("开始执行操作", 操作="睡眠模式")
    try:
        # 开启关机权限
        ctypes.windll.ntdll.RtlAdjustPrivilege(19, 1, 0, ctypes.byref(ctypes.c_bool()))
    except Exception:
        pass
    try:
        result = ctypes.windll.powrprof.SetSuspendState(False, True, False)
        if not result:
            error_code = ctypes.GetLastError()
            log.warning("SetSuspendState 返回失败，错误码: %s", error_code)
        else:
            log.info("已进入睡眠")
    except Exception as e:
        log_exception_local("进入睡眠失败", e)


def monitor_off():
    """关闭显示器（使用异步 PostMessageW 避免被无响应窗口阻塞）"""
    audit("开始执行操作", 操作="关闭显示器")
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    try:
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.PostMessageW.restype = ctypes.c_bool
        result = user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        if not result:
            error_code = ctypes.GetLastError()
            log.warning("PostMessageW 关闭显示器失败，错误码: %s", error_code)
        else:
            log.info("已广播关闭显示器消息")
    except Exception as e:
        log_exception_local("关闭显示器失败", e)


def run_program(path, params=""):
    """运行指定程序/文件（ShellExecuteW，与资源管理器双击同款 API）

    - 执行前验证文件存在
    - 通过结构化参数传递路径与附加参数，不经过任何命令行/shell 拼接
    - 天然支持 .exe/.bat/.cmd/.vbs 及系统关联文档
    """
    if not path or not path.strip():
        raise ValueError("程序路径不能为空")
    path = strip_quotes(path.strip())
    if not os.path.isfile(path):
        raise FileNotFoundError(f"程序不存在: {path}")
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_int
        shell32.ShellExecuteW.argtypes = [
            wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int,
        ]
        # ShellExecuteW 返回值 >32 表示成功
        ret = shell32.ShellExecuteW(None, "open", path, params or None, None, 1)  # SW_SHOWNORMAL
        if ret <= 32:
            raise OSError(f"ShellExecuteW 返回 {ret}")
        log.info("已启动程序: %s 附加参数: %r", path, params or "")
    except Exception as e:
        log_exception_local("运行程序失败", e)
        _safe_msgbox("error", "运行程序失败", f"无法运行程序:\n{path}\n\n错误: {e}")


def _play_sound_wav(path):
    """播放 WAV 文件（使用 winsound）"""
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)


# ---- MCI 结构化播放（mciSendCommandW，非命令字符串，无注入面） ----
_MCI_OPEN = 0x0804
_MCI_PLAY = 0x0806
_MCI_CLOSE = 0x0808
_MCI_OPEN_TYPE = 0x2000
_MCI_OPEN_ELEMENT = 0x2001
_MCI_FROM = 0x00000004

_mci_device_id = 0  # 当前打开的 MCI 设备（单设备复用，防资源泄漏）


class _MCI_OPEN_PARMSW(ctypes.Structure):
    _fields_ = [
        ("dwCallback", wt.HWND),
        ("wDeviceID", wt.UINT),
        ("lpstrDeviceType", wt.LPCWSTR),
        ("lpstrElementName", wt.LPCWSTR),
        ("lpstrAlias", wt.LPCWSTR),
    ]


class _MCI_PLAY_PARMS(ctypes.Structure):
    _fields_ = [
        ("dwCallback", wt.HWND),
        ("dwFrom", wt.DWORD),
        ("dwTo", wt.DWORD),
    ]


def _mci_close_current():
    """关闭已打开的 MCI 设备"""
    global _mci_device_id
    if _mci_device_id:
        ctypes.windll.winmm.mciSendCommandW(
            _mci_device_id, _MCI_CLOSE, 0, 0
        )
        _mci_device_id = 0


def _play_sound_mci(path):
    """使用 Windows MCI 结构化接口播放任意格式音频（MP3/MID/WAV 等）

    通过 mciSendCommandW + MCI_OPEN_PARMSW 的字段直接传递路径，
    不经过 MCI 命令字符串解析，路径中的任何字符都不会改变播放语义。
    """
    global _mci_device_id
    winmm = ctypes.windll.winmm

    _mci_close_current()  # 先关闭上一次的设备，防止资源泄漏

    open_parms = _MCI_OPEN_PARMSW()
    open_parms.lpstrDeviceType = "mpegvideo"
    open_parms.lpstrElementName = path
    err = winmm.mciSendCommandW(
        0, _MCI_OPEN, _MCI_OPEN_TYPE | _MCI_OPEN_ELEMENT,
        ctypes.byref(open_parms),
    )
    if err != 0:
        raise OSError(f"MCI 打开设备失败 (错误码 {err})")

    _mci_device_id = open_parms.wDeviceID
    play_parms = _MCI_PLAY_PARMS()
    play_parms.dwFrom = 0
    err = winmm.mciSendCommandW(
        _mci_device_id, _MCI_PLAY, _MCI_FROM, ctypes.byref(play_parms)
    )
    if err != 0:
        raise OSError(f"MCI 播放失败 (错误码 {err})")


def play_sound(path):
    """播放指定声音文件（支持 WAV/MP3/MID 等格式）

    WAV 用 winsound，其他格式用 Windows MCI 结构化接口
    """
    if not path or not path.strip():
        return
    path = strip_quotes(path.strip())
    if not os.path.isfile(path):
        log.warning("播放声音失败：文件不存在 %s", path)
        _safe_msgbox("error", "播放声音失败", f"音频文件不存在:\n{path}")
        return
    try:
        if path.lower().endswith('.wav'):
            _play_sound_wav(path)
        else:
            _play_sound_mci(path)
        log.info("播放声音: %s", path)
    except Exception as e:
        # MCI 失败时回退到 winsound（仅对 WAV 有意义，其他格式记录失败）
        try:
            if path.lower().endswith('.wav'):
                _play_sound_wav(path)
                log.warning("MCI 失败，已回退 winsound: %s", e)
                return
        except Exception:
            pass
        log_exception_local("播放声音失败", e)
        _safe_msgbox("error", "播放声音失败", f"无法播放声音文件:\n{path}\n\n错误: {e}")


def show_message(text):
    """系统气泡通知（Win32 Shell_NotifyIcon，原生进入通知区域）

    优先复用 GUI 的常驻托盘图标发送；CLI 模式自动创建独立临时图标。
    气泡通知是系统核心 API，不依赖外部进程，发送结果记录到日志。
    """
    if not text or not text.strip():
        text = "定时任务已触发！"
    text = text.strip()
    APP_NAME = "自动关机工具"

    try:
        from core.tray import get_default_tray, show_balloon
        tray = get_default_tray()
        if tray is not None and tray.notify(APP_NAME, text):
            audit("发送通知", 方式="托盘气泡", 内容=text)
            return
        show_balloon(APP_NAME, text)
        audit("发送通知", 方式="独立气泡", 内容=text)
    except Exception as e:
        log_exception_local("show_message 通知发送失败", e)


# 操作映射表: index -> (中文名称, 执行函数)
ACTION_MAP = {
    0: ("关闭电脑", shutdown),
    1: ("重启电脑", restart),
    2: ("注销电脑", logoff),
    3: ("进入睡眠模式", sleep),
    4: ("关闭显示器", monitor_off),
    5: ("运行程序", run_program),
    6: ("播放声音", play_sound),
    7: ("弹出消息提示框", show_message),
}

# 关键操作（需要额外确认的）
CRITICAL_ACTIONS = {0, 1, 2}  # 关机、重启、注销


def execute_action(action_index, params=None):
    """
    执行指定操作
    :param action_index: 操作索引 (0-7)
    :param params: 操作参数（运行程序传 dict, 播放声音传路径, 弹消息传文本）
    :return: 是否成功执行
    """
    if action_index not in ACTION_MAP:
        log.error("执行了未知操作索引: %s", action_index)
        return False
    name, func = ACTION_MAP[action_index]

    try:
        if action_index == 5:  # 运行程序
            path = params.get('path', '') if isinstance(params, dict) else ''
            args = params.get('args', '') if isinstance(params, dict) else ''
            run_program(path, args)
        elif action_index == 6:  # 播放声音
            play_sound(params if isinstance(params, str) else '')
        elif action_index == 7:  # 弹出消息
            show_message(params if isinstance(params, str) else '')
        else:
            func()
        return True
    except Exception as e:
        log_exception_local(f"执行操作失败 [{name}]", e)
        _safe_msgbox("error", "执行失败", f"操作出错: {e}\n日志: {get_log_path()}")
        return False


def get_action_name(index):
    """获取操作的中文名称"""
    return ACTION_MAP.get(index, ("未知操作", None))[0]


def clean_sound_resources():
    """清理 MCI 播放资源（在程序退出时调用）"""
    try:
        _mci_close_current()
    except Exception:
        pass
