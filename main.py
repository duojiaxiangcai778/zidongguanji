#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动关机工具 v3.0 — 入口 + CLI 参数解析（深色任务控制台 + 轮转日志系统）

CLI 参数:
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
import time
import logging
import ctypes

# 确保项目根目录在 sys.path 中（打包后不需要）
if not getattr(sys, 'frozen', False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

from core.actions import (
    ACTION_MAP, execute_action, shutdown, restart, logoff,
    sleep, monitor_off, run_program, play_sound, show_message,
    set_main_tk,
)
from core.common import get_log_path, strip_quotes, format_seconds
from core.config import Config
from core.logger import setup_logging, get_logger, audit, set_level
from core.timer_engine import TimerEngine

APP_VERSION = "v3.0"
log = get_logger("main")


def _global_exception_handler(exc_type, exc_value, exc_traceback):
    """全局未捕获异常处理器 — 防止程序静默崩溃"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("发生未捕获的异常", exc_info=(exc_type, exc_value, exc_traceback))
    print(f"严重错误: {exc_value}", file=sys.stderr)
    print(f"日志: {get_log_path()}", file=sys.stderr)


def _ensure_single_instance():
    """确保只有一个实例运行（Windows 命名互斥体）"""
    mutex_name = "AutoShutdownTool_v3_Mutex"
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log.error("程序已在运行中，本次启动退出")
            print("程序已在运行中", file=sys.stderr)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


def parse_args(argv=None):
    """
    解析命令行参数
    返回: params_dict，包含 action/action_func/t/w/min/rp_path/ra_path/m_text
    """
    if argv is None:
        argv = sys.argv[1:]

    result = {
        'action': None,      # 操作索引或 None
        'action_func': None,  # 直接可调用的函数
        't': 0,              # 倒计时秒数
        'w': False,          # 不自动开始
        'min': False,        # 最小化启动
        'rp_path': '',       # 运行程序路径
        'rp_args': '',       # 运行程序参数
        'ra_path': '',       # 播放声音路径
        'm_text': '',        # 消息文本
    }

    # CLI 操作映射
    cli_actions = {
        'X': 0,   # 关机
        'S': 0,   # 关机（同 X）
        'R': 1,   # 重启
        'L': 2,   # 注销
    }

    i = 0
    while i < len(argv):
        arg = argv[i]

        if arg.startswith('/t='):
            try:
                result['t'] = int(arg[3:])
            except ValueError:
                result['t'] = 0
        elif arg == '/w':
            result['w'] = True
        elif arg == '/min':
            result['min'] = True
        elif arg == '/sm':
            result['action'] = 3
            result['action_func'] = sleep
        elif arg == '/som':
            result['action'] = 4
            result['action_func'] = monitor_off
        elif arg.startswith('/rp='):
            path = strip_quotes(arg[4:])
            result['action'] = 5
            result['rp_path'] = path
            result['action_func'] = lambda p=path: run_program(p)
        elif arg.startswith('/ra='):
            path = strip_quotes(arg[4:])
            result['action'] = 6
            result['ra_path'] = path
            result['action_func'] = lambda p=path: play_sound(p)
        elif arg.startswith('/m='):
            text = arg[3:]
            result['action'] = 7
            result['m_text'] = text
            result['action_func'] = lambda t=text: show_message(t)
        # /rp /ra /m 带参数在下一个参数（老风格）
        elif arg == '/rp' and i + 1 < len(argv):
            i += 1
            p = argv[i]
            result['action'] = 5
            result['rp_path'] = p
            result['action_func'] = lambda path=p: run_program(path)
        elif arg == '/ra' and i + 1 < len(argv):
            i += 1
            p = argv[i]
            result['action'] = 6
            result['ra_path'] = p
            result['action_func'] = lambda path=p: play_sound(path)
        elif arg == '/m' and i + 1 < len(argv):
            i += 1
            t = argv[i]
            result['action'] = 7
            result['m_text'] = t
            result['action_func'] = lambda text=t: show_message(text)
        elif arg.upper() in cli_actions:
            result['action'] = cli_actions[arg.upper()]
            idx = cli_actions[arg.upper()]
            if idx == 0:
                result['action_func'] = shutdown
            elif idx == 1:
                result['action_func'] = restart
            elif idx == 2:
                result['action_func'] = logoff
        # 其他参数忽略

        i += 1

    return result


def cli_mode(params):
    """CLI 模式执行（全程记录日志）"""
    action_idx = params['action']
    t_seconds = params['t']
    action_name = ACTION_MAP.get(action_idx, ('未知',))[0]

    if action_idx is None:
        print("错误: 未指定操作", file=sys.stderr)
        print("用法: 自动关机工具.exe <操作> [/t=秒] [/w] [/min]", file=sys.stderr)
        print("操作: X/S=关机 R=重启 L=注销 /sm=睡眠 /som=关显示器 /rp=运行 /ra=播放 /m=消息", file=sys.stderr)
        sys.exit(1)

    action_func = params['action_func'] or (lambda: execute_action(action_idx))
    audit("CLI 模式", 操作=action_name, 倒计时=t_seconds)

    if t_seconds > 0:
        print(f"操作: {action_name}")
        print(f"倒计时: {t_seconds} 秒")
        if params['w']:
            print("提示: /w 参数在 CLI 模式下无效，倒计时将自动开始")

        for remaining in range(t_seconds, 0, -1):
            print(f"\r剩余 {format_seconds(remaining)}  ", end="", flush=True)
            time.sleep(1)

        print("\n正在执行操作...")
        audit("CLI 倒计时结束", 操作=action_name)
        action_func()
        print("操作完成")
    else:
        print(f"正在执行: {action_name}")
        action_func()
        print("操作完成")

    # /m 模式的气泡通知在后台线程展示，等待其结束再退出
    if action_idx == 7:
        try:
            from core.tray import wait_balloons
            wait_balloons(timeout=12)
        except Exception:
            pass


def main():
    """主入口：日志系统最先初始化"""
    setup_logging(os.environ.get("ASD_LOG_LEVEL", "INFO"))
    log.info("========== 自动关机工具 %s 启动 ==========", APP_VERSION)
    sys.excepthook = _global_exception_handler

    _ensure_single_instance()

    params = parse_args()
    log.info("命令行参数: %r", sys.argv[1:])

    if params['action'] is not None:
        cli_mode(params)
        return

    # GUI 模式
    try:
        from ui.main_window import MainWindow
        app = MainWindow()
        # 把主 Tk 注入 actions，让定时线程在主线程上弹窗（不创建额外 Tk）
        try:
            set_main_tk(app.root)
        except Exception:
            pass

        # /min 参数或设置项 → 启动后最小化到托盘
        start_minimized = params['min'] or (
            params['action'] is None
            and app.config.getboolean('General', 'start_minimized', False)
        )
        if start_minimized:
            app.root.after(400, app._minimize_to_tray)

        # /t 预设倒计时
        if params['t'] > 0:
            t = params['t']
            days, t = divmod(t, 86400)
            hours, t = divmod(t, 3600)
            mins, secs = divmod(t, 60)
            app.timer_mode_var.set("countdown")
            app._on_timer_mode_changed()
            app.spin_days.delete(0, "end")
            app.spin_days.insert(0, str(days))
            app.spin_hours.delete(0, "end")
            app.spin_hours.insert(0, str(hours))
            app.spin_mins.delete(0, "end")
            app.spin_mins.insert(0, str(mins))
            app.spin_secs.delete(0, "end")
            app.spin_secs.insert(0, str(secs))

            if not params['w'] and params['action'] is not None:
                # CLI 参数同步到 GUI 控件后自动开始
                if params['action'] == 5 and params['rp_path']:
                    app.entry_prog_path.delete(0, "end")
                    app.entry_prog_path.insert(0, params['rp_path'])
                elif params['action'] == 6 and params['ra_path']:
                    app.entry_sound_path.delete(0, "end")
                    app.entry_sound_path.insert(0, params['ra_path'])
                elif params['action'] == 7 and params['m_text']:
                    app.text_msg.delete("1.0", "end")
                    app.text_msg.insert("1.0", params['m_text'])
                app.action_var.set(params['action'])
                app._on_action_changed()
                app.root.after(500, app._on_start_timer)

        app.run()
    except Exception as e:
        import traceback
        log.critical("启动 GUI 异常\n%s", traceback.format_exc())
        print(f"错误: {e}", file=sys.stderr)
        print(f"日志: {get_log_path()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
