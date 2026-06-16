# core/actions.py
# 系统操作模块 - 实现 8 种可执行操作

import os
import sys
import subprocess
import ctypes
import winsound
from tkinter import messagebox, Tk


def shutdown():
    """关闭电脑"""
    os.system("shutdown /s /t 0 /f")


def restart():
    """重启电脑"""
    os.system("shutdown /r /t 0 /f")


def logoff():
    """注销电脑"""
    os.system("shutdown /l /f")


def sleep():
    """进入睡眠模式"""
    ctypes.windll.powrprof.SetSuspendState(False, True, False)


def monitor_off():
    """关闭显示器"""
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def run_program(path, params=""):
    """运行指定程序"""
    if not path or not path.strip():
        return
    try:
        if params and params.strip():
            subprocess.Popen([path.strip()] + params.strip().split(), shell=False)
        else:
            subprocess.Popen(path.strip(), shell=False)
    except Exception as e:
        messagebox.showerror("运行程序失败", f"无法运行程序:\n{path}\n\n错误: {e}")


def play_sound(path):
    """播放指定声音文件"""
    if not path or not path.strip():
        return
    try:
        winsound.PlaySound(path.strip(), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        messagebox.showerror("播放声音失败", f"无法播放声音文件:\n{path}\n\n错误: {e}")


def show_message(text):
    """弹出消息提示框"""
    if not text or not text.strip():
        text = "定时任务已触发！"
    # 确保有 tkinter 根窗口
    root = Tk()
    root.withdraw()
    try:
        messagebox.showinfo("自动关机工具", text.strip())
    finally:
        root.destroy()


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
        import traceback
        err_msg = f"执行操作失败 [{name}]: {e}\n\n{traceback.format_exc()}"
        exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(exe_dir, "_error.log")
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(err_msg)
        except Exception:
            pass
        messagebox.showerror("执行失败", f"操作出错: {e}\n日志: {log_path}")
        return False


def get_action_name(index):
    """获取操作的中文名称"""
    return ACTION_MAP.get(index, ("未知操作", None))[0]
