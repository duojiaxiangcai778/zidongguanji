# -*- coding: utf-8 -*-
"""
_ui_smoke.py — 无头安全冒烟测试（v3.0）

验证范围（不启动定时器线程，避免 root.destroy() 挂起）：
  1. 各模块可导入、语法正确
  2. 统一日志系统初始化与审计事件
  3. 主窗口三页构建 + 页面切换
  4. 模式切换 / 操作切换 / 参数卡片联动 / 快捷预设
  5. CLI 参数解析
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL += 1
        print(f"  [失败] {name}")


def main():
    print("== 1. 模块导入 ==")
    from core.common import format_seconds, get_log_path
    from core.logger import setup_logging, audit, get_logger, LOG_FILE
    from core.config import Config
    from core.timer_engine import TimerEngine
    from core import actions
    from core.tray import TrayIcon
    import main as app_main
    check("core 模块导入", True)
    check("format_seconds(3601) == '01:00:01'", format_seconds(3601) == "01:00:01")
    check("format_seconds(90061) 含 '天'", "天" in format_seconds(90061))

    print("== 2. 日志系统 ==")
    log_path = setup_logging("DEBUG")
    audit("冒烟测试事件", 阶段="开始")
    get_logger("smoke").info("日志系统工作正常")
    check("日志路径指向 logs/app.log", log_path.endswith("app.log"))
    check("日志文件已创建", os.path.isfile(log_path))

    print("== 3. CLI 参数解析 ==")
    p = app_main.parse_args(["X", "/t=3600"])
    check("X + /t=3600", p['action'] == 0 and p['t'] == 3600)
    p = app_main.parse_args(["/min", "/w"])
    check("/min /w", p['min'] and p['w'])
    p = app_main.parse_args(["/sm"])
    check("/sm → 睡眠", p['action'] == 3)
    p = app_main.parse_args(["/rp=C:\\test a\\demo.exe"])
    check("/rp=带空格路径", p['action'] == 5 and p['rp_path'] == "C:\\test a\\demo.exe")
    p = app_main.parse_args(["/m=你好 世界"])
    check("/m=中文消息", p['action'] == 7 and p['m_text'] == "你好 世界")
    p = app_main.parse_args([])
    check("无参数 → GUI 模式", p['action'] is None)

    print("== 4. GUI 构建（托盘禁用）==")
    from ui.main_window import MainWindow
    app = MainWindow(enable_tray=False)
    root = app.root
    root.update()

    check("三页均已构建",
          all(hasattr(app, a) for a in ("page_timer", "page_logs", "page_settings")))
    check("关键控件存在",
          all(hasattr(app, a) for a in (
              "spin_days", "spin_hours", "spin_mins", "spin_secs",
              "entry_year", "entry_month", "entry_day", "entry_hour", "entry_min", "entry_sec",
              "spin_p_hour", "spin_p_min", "spin_p_sec",
              "entry_prog_path", "entry_prog_args", "entry_sound_path", "text_msg",
              "btn_start", "btn_pause", "btn_stop", "btn_now",
              "label_time_display", "progress", "mode_segment",
              "_status_label", "_status_dot",
              "periodic_vars", "periodic_checkboxes")))

    print("== 5. 页面切换 ==")
    app.show_page("logs")
    root.update()
    check("日志页切换", app._current_page == "logs")
    app.show_page("settings")
    root.update()
    check("设置页切换", app._current_page == "settings")
    check("设置开关全部创建", len(app._setting_switches) == 8)
    app.show_page("timer")
    root.update()

    print("== 6. 模式与操作联动 ==")
    app._on_segment_changed("指定时间")
    check("指定时间模式", app.timer_mode_var.get() == "at_time")
    app._on_segment_changed("每周循环")
    check("每周循环模式", app.timer_mode_var.get() == "periodic")
    app._on_segment_changed("倒计时")
    check("倒计时模式", app.timer_mode_var.get() == "countdown")

    app.action_var.set(5)
    app._on_action_changed()
    root.update()
    check("运行程序 → 参数卡片显示", app.frame_prog.winfo_manager() != "")
    app.action_var.set(6)
    app._on_action_changed()
    root.update()
    check("播放声音 → 音频卡片显示", app.frame_sound.winfo_manager() != "")
    app.action_var.set(7)
    app._on_action_changed()
    root.update()
    check("弹出消息 → 消息卡片显示", app.frame_msg.winfo_manager() != "")
    app.action_var.set(0)
    app._on_action_changed()
    root.update()
    check("关闭电脑 → 参数卡片隐藏", app.card_param.winfo_manager() == "")

    print("== 7. 快捷预设 ==")
    app._apply_preset(2, 30)
    check("预设 2小时30分",
          app.spin_hours.get() == "2" and app.spin_mins.get() == "30"
          and app.spin_days.get() == "0" and app.spin_secs.get() == "0")

    print("== 8. 引擎状态初始检查 ==")
    check("引擎初始未运行", not app.timer.is_running)
    check("引擎初始非暂停", not app.timer.is_paused)

    root.update()
    root.destroy()

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
