# core/actions.py
# 系统操作模块 - 实现 8 种可执行操作

import os
import subprocess
import shlex
import threading
import ctypes
import winsound
import time
from tkinter import messagebox, Tk

from core.common import write_log, get_log_path

# Bug O 修复: 模块级变量，控制关机/重启是否强制关闭应用
_force_shutdown = True  # 默认强制关闭（兼容 CLI 行为）

# Bug P0 修复: 全局缓存主 Tk 实例（由 ui/main_window 在构造时注入），供定时线程内的弹窗复用
_main_tk = None


def set_main_tk(root):
    """注入主 Tk 根窗口，供定时线程在已存在的实例上弹窗，避免创建多个 Tk()"""
    global _main_tk
    _main_tk = root


def _safe_msgbox(kind, title, message):
    """在主线程上弹窗。若无主 Tk 则静默写日志（不阻塞定时线程）"""
    try:
        if _main_tk is not None:
            # tkinter 必须在创建它的线程里使用；worker 线程不能直接 .showinfo()
            # 用 after(0, ...) 把调用排到主事件循环
            try:
                _main_tk.after(0, lambda: _do_msgbox(kind, title, message))
                return
            except Exception:
                pass
    except Exception:
        pass
    # 兜底：写日志（避免 worker 线程里 messagebox 阻塞定时器或触发 Tcl 安全错误）
    write_log(f"[{kind}] {title}: {message}", append=True)


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
    _force_shutdown = val


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


def safe_close_windows(timeout_ms=3000):
    """发送 WM_CLOSE 给需要安全关闭的程序窗口，等待它们保存后退出"""
    _bind_user32()
    try:
        WS_CONST = 0
        WM_CLOSE = 0x0010
        SMTO_ABORTIFHUNG = 0x0002

        enum_windows = ctypes.windll.user32.EnumWindows
        get_window_text = ctypes.windll.user32.GetWindowTextW
        get_window_text_length = ctypes.windll.user32.GetWindowTextLengthW
        is_window_visible = ctypes.windll.user32.IsWindowVisible
        send_timeout = ctypes.windll.user32.SendMessageTimeoutW

        found_windows = []

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
                                write_log(f"安全关闭: {buffer.value}", append=True)
                            break
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        enum_windows(WNDENUMPROC(enum_proc), 0)

        if not found_windows:
            write_log("未检测到需要安全关闭的窗口", append=True)
            return

        # 发送 WM_CLOSE 给每个找到的窗口（带超时，防止窗口无响应时卡死定时线程）
        for hwnd in found_windows:
            try:
                result = ctypes.c_uint()
                send_timeout(hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result))
            except Exception:
                pass

        write_log(f"已发送 WM_CLOSE 给 {len(found_windows)} 个窗口，等待 {timeout_ms//1000} 秒保存...", append=True)
        time.sleep(min(timeout_ms // 1000, 3))

    except Exception as e:
        write_log("safe_close_windows 出错", e, append=True)


def _run_shutdown(args):
    """通过 subprocess 调用 shutdown.exe，避免 os.system 闪黑窗"""
    try:
        subprocess.run(
            ["shutdown.exe"] + args,
            creationflags=NO_WINDOW,  # 抑制控制台窗口
            check=False,
        )
    except Exception as e:
        write_log(f"shutdown 调用失败: {' '.join(args)}", e, append=True)


def shutdown():
    """安全关闭程序后关闭电脑"""
    safe_close_windows()
    cmd = ["/s", "/t", "0"]
    if _force_shutdown:
        cmd.append("/f")
    _run_shutdown(cmd)


def restart():
    """安全关闭程序后重启电脑"""
    safe_close_windows()
    cmd = ["/r", "/t", "0"]
    if _force_shutdown:
        cmd.append("/f")
    _run_shutdown(cmd)


def logoff():
    """注销电脑 — 异步执行，避免等待确认对话框"""
    # Bug N 修复: shutdown /l 不支持 /f 参数
    _run_shutdown(["/l"])


def sleep():
    """进入睡眠模式。

    Bug P0 修复: SetSuspendState 需要先设置 SE_SHUTDOWN_PRIVILEGE，
    否则启用休眠时调用可能静默失败。
    """
    try:
        # 开启关机权限
        ctypes.windll.ntdll.RtlAdjustPrivilege(19, 1, 0, ctypes.byref(ctypes.c_bool()))
    except Exception:
        pass
    try:
        ctypes.windll.powrprof.SetSuspendState(False, True, False)
    except Exception as e:
        write_log("进入睡眠失败", e, append=True)


def monitor_off():
    """关闭显示器"""
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    try:
        user32 = ctypes.windll.user32
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
    except Exception as e:
        write_log("关闭显示器失败", e, append=True)


def run_program(path, params=""):
    """运行指定程序，并在执行前验证文件存在。"""
    if not path or not path.strip():
        raise ValueError("程序路径不能为空")
    path = strip_quotes(path.strip())
    if not os.path.isfile(path):
        raise FileNotFoundError(f"程序不存在: {path}")
    try:
        if params and params.strip():
            # Bug 5 修复: 使用 shlex.split() 正确处理带引号的参数
            args = [path.strip()] + shlex.split(params.strip())
        else:
            # Bug H 修复: 始终使用列表形式，避免空格路径被当成多个参数
            args = [path.strip()]
        subprocess.Popen(args, shell=False)
    except Exception as e:
        write_log("运行程序失败", e)
        _safe_msgbox("error", "运行程序失败", f"无法运行程序:\n{path}\n\n错误: {e}")


def _play_sound_wav(path):
    """播放 WAV 文件（使用 winsound）"""
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)


def _play_sound_mci(path):
    """使用 Windows MCI 播放任意格式音频（MP3/MID/WAV 等）"""
    try:
        # Bug I 修复: 先关闭之前的 MCI 设备，防止资源泄漏
        ctypes.windll.winmm.mciSendStringW('close sound_auto', None, 0, 0)
        ctypes.windll.winmm.mciSendStringW(
            f'open "{path}" type mpegvideo alias sound_auto',
            None, 0, 0
        )
        ctypes.windll.winmm.mciSendStringW(
            'play sound_auto from 0 notify',
            None, 0, 0
        )
    except Exception:
        # MCI 失败时回退到 winsound
        _play_sound_wav(path)


def play_sound(path):
    """播放指定声音文件（支持 WAV/MP3/MID 等格式）

    Bug 6 修复: WAV 用 winsound，其他格式（MP3/MID）用 Windows MCI API
    """
    if not path or not path.strip():
        return
    try:
        path = path.strip()
        lower = path.lower()
        if lower.endswith('.wav'):
            _play_sound_wav(path)
        else:
            _play_sound_mci(path)
    except Exception as e:
        write_log("播放声音失败", e)
        _safe_msgbox("error", "播放声音失败", f"无法播放声音文件:\n{path}\n\n错误: {e}")


def show_message(text):
    """弹出消息提示框
    Bug J/P0 修复: 若存在主 Tk，用 after 在主线程弹窗；否则才创建临时 Tk。
    """
    if not text or not text.strip():
        text = "定时任务已触发！"
    text = text.strip()
    _safe_msgbox("info", "自动关机工具", text)


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
        # Bug 8 修复: 统一使用追加模式写日志
        write_log(f"执行操作失败 [{name}]", e, append=True)
        log_path = get_log_path()
        _safe_msgbox("error", "执行失败", f"操作出错: {e}\n日志: {log_path}")
        return False


def get_action_name(index):
    """获取操作的中文名称"""
    return ACTION_MAP.get(index, ("未知操作", None))[0]


def clean_sound_resources():
    """清理 MCI 播放资源（在程序退出时调用）"""
    try:
        ctypes.windll.winmm.mciSendStringW('close sound_auto', None, 0, 0)
    except Exception:
        pass
