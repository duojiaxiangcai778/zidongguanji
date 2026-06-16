#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动关机工具 v1.0 — 入口 + CLI 参数解析

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
import re
import time
import threading

# 确保项目根目录在 sys.path 中（打包后不需要）
if not getattr(sys, 'frozen', False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

from core.actions import (
    ACTION_MAP, execute_action, shutdown, restart, logoff,
    sleep, monitor_off, run_program, play_sound, show_message
)
from core.config import Config
from core.timer_engine import TimerEngine


def parse_args(argv=None):
    """
    解析命令行参数
    返回: (action_index, params_dict)
    params_dict 包含:
        - action: 操作索引或 None
        - t: 倒计时秒数 (int, 默认 0)
        - w: 不自动开始倒计时 (bool)
        - min: 启动最小化 (bool)
        - rp_path: 运行程序路径
        - ra_path: 播放声音路径
        - m_text: 消息文本
    """
    if argv is None:
        argv = sys.argv[1:]

    result = {
        'action': None,     # 操作索引或 None
        'action_func': None, # 直接可调用的函数
        't': 0,             # 倒计时秒数
        'w': False,         # 不自动开始
        'min': False,       # 最小化启动
        'rp_path': '',      # 运行程序路径
        'rp_args': '',      # 运行程序参数
        'ra_path': '',      # 播放声音路径
        'm_text': '',       # 消息文本
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

        # 处理 /t=秒
        if arg.startswith('/t='):
            try:
                result['t'] = int(arg[3:])
            except ValueError:
                result['t'] = 0
        # 处理 /w
        elif arg == '/w':
            result['w'] = True
        # 处理 /min
        elif arg == '/min':
            result['min'] = True
        # 处理 /sm (睡眠)
        elif arg == '/sm':
            result['action'] = 3
            result['action_func'] = sleep
        # 处理 /som (关闭显示器)
        elif arg == '/som':
            result['action'] = 4
            result['action_func'] = monitor_off
        # 处理 /rp=path (运行程序)
        elif arg.startswith('/rp='):
            path = arg[4:]
            result['action'] = 5
            result['rp_path'] = path
            result['action_func'] = lambda: run_program(result['rp_path'], result['rp_args'])
        # 处理 /ra=path (播放声音)
        elif arg.startswith('/ra='):
            path = arg[4:]
            result['action'] = 6
            result['ra_path'] = path
            result['action_func'] = lambda: play_sound(result['ra_path'])
        # 处理 /m=text (弹出消息)
        elif arg.startswith('/m='):
            text = arg[3:]
            result['action'] = 7
            result['m_text'] = text
            result['action_func'] = lambda: show_message(result['m_text'])
        # 处理 /rp 或 /ra 或 /m 带参数在下一个参数（老风格）
        elif arg == '/rp' and i + 1 < len(argv):
            i += 1
            result['action'] = 5
            result['rp_path'] = argv[i]
            result['action_func'] = lambda: run_program(result['rp_path'], result['rp_args'])
        elif arg == '/ra' and i + 1 < len(argv):
            i += 1
            result['action'] = 6
            result['ra_path'] = argv[i]
            result['action_func'] = lambda: play_sound(result['ra_path'])
        elif arg == '/m' and i + 1 < len(argv):
            i += 1
            result['action'] = 7
            result['m_text'] = argv[i]
            result['action_func'] = lambda: show_message(result['m_text'])
        # 处理 X, S, R, L
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
    """
    CLI 模式执行
    如果有操作且没有倒计时，立即执行
    如果有操作且有倒计时，启动倒计时后执行
    """
    action_idx = params['action']
    t_seconds = params['t']
    no_auto = params['w']

    if action_idx is None:
        print("错误: 未指定操作", file=sys.stderr)
        print("用法: 自动关机工具.exe <操作> [/t=秒] [/w] [/min]", file=sys.stderr)
        print("操作: X/S=关机 R=重启 L=注销 /sm=睡眠 /som=关显示器 /rp=运行 /ra=播放 /m=消息", file=sys.stderr)
        sys.exit(1)

    action_func = params['action_func']
    if action_func is None:
        action_func = lambda: execute_action(action_idx)

    if t_seconds > 0:
        # 有倒计时
        print(f"操作: {ACTION_MAP.get(action_idx, ('未知',))[0]}")
        print(f"倒计时: {t_seconds} 秒")

        if no_auto:
            print("提示: /w 参数已设置，倒计时不会自动开始（GUI 模式才有效）")
            print("CLI 模式将自动开始倒计时...")

        # 显示倒计时
        for remaining in range(t_seconds, 0, -1):
            mins, secs = divmod(remaining, 60)
            hours, mins = divmod(mins, 60)
            days, hours = divmod(hours, 24)
            if days > 0:
                time_str = f"{days}天 {hours:02d}:{mins:02d}:{secs:02d}"
            elif hours > 0:
                time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
            else:
                time_str = f"{mins:02d}:{secs:02d}"
            print(f"\r剩余 {time_str}  ", end="", flush=True)
            time.sleep(1)

        print("\n正在执行操作...")
        if action_func:
            action_func()
        else:
            execute_action(action_idx)
        print("操作完成")
    else:
        # 立即执行
        print(f"正在执行: {ACTION_MAP.get(action_idx, ('未知',))[0]}")
        if action_func:
            action_func()
        else:
            execute_action(action_idx)
        print("操作完成")


def main():
    """主入口"""
    # 解析命令行参数
    params = parse_args()

    if params['action'] is not None:
        # CLI 模式
        cli_mode(params)
    else:
        # GUI 模式
        try:
            from ui.main_window import MainWindow
            app = MainWindow()

            # 如果指定了 /min，启动后最小化
            if params['min']:
                app.root.after(200, app._minimize_to_tray)

            # 如果指定了 /t，预设倒计时
            if params['t'] > 0:
                # 解析秒数为天/时/分/秒并在界面中设置
                t = params['t']
                days = t // 86400
                t %= 86400
                hours = t // 3600
                t %= 3600
                mins = t // 60
                secs = t % 60
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

                # 除非指定了 /w，否则自动开始倒计时
                if not params['w'] and params['action'] is not None:
                    # 设置操作
                    app.action_var.set(params['action'])
                    app._on_action_changed()
                    # 自动开始
                    app.root.after(500, app._on_start_timer)

            app.run()
        except Exception as e:
            import traceback
            err_msg = f"启动GUI异常: {e}\n\n{traceback.format_exc()}"
            exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(exe_dir, "_error.log")
            try:
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.write(err_msg)
            except Exception:
                pass
            print(f"错误: {e}", file=sys.stderr)
            print(f"日志: {log_path}", file=sys.stderr)
            # 回退到 CLI 模式
            if hasattr(e, 'message'):
                print(f"无法启动图形界面: {e.message}", file=sys.stderr)
            else:
                print(f"无法启动图形界面: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
