# -*- coding: utf-8 -*-
"""
ui/main_window.py — 自动关机工具 v3.0 GUI 主窗口

深色「任务控制台」设计：
  - 左侧固定侧边栏导航：定时任务 / 运行日志 / 系统设置
  - 定时任务页：状态英雄区（大号倒计时 + 进度）+ 触发时间 + 执行操作 + 底部控制条
  - 运行日志页：级别过滤 + 自动滚动 + 打开日志目录（2 秒自动刷新）
  - 系统设置页：全部接线的功能开关（确认/托盘/提示音/强制关闭/日志级别）
  - 常驻系统托盘：左键恢复、右键菜单（显示主窗口/退出程序）
"""

import datetime
import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import winsound

from core.actions import (
    ACTION_MAP, execute_action, get_action_name,
    set_force_close, set_main_tk, clean_sound_resources, CRITICAL_ACTIONS,
)
from core.config import Config
from core.common import get_log_dir, get_log_path, format_seconds
from core.logger import get_logger, audit, set_level as set_log_level, clear_all as clear_logs
from core.timer_engine import TimerEngine
from core.tray import TrayIcon, set_default_tray, get_default_tray

log = get_logger("ui")

APP_VERSION = "v3.0"


class MainWindow:
    """自动关机工具主窗口（深色任务控制台）"""

    # ---- 深色控制台色彩体系 ----
    BG_MAIN = "#0E1320"       # 主背景（深蓝黑）
    BG_SIDEBAR = "#0A0E19"    # 侧边栏（更深）
    CARD_BG = "#151C2C"       # 卡片
    CARD_BG2 = "#1B2440"      # 卡片内嵌面板
    TEXT_MAIN = "#E6EBF7"     # 主文字
    TEXT_MUTED = "#7C87A0"    # 次级文字
    ACCENT = "#3E7BFA"        # 主强调蓝
    ACCENT_HOVER = "#5B8FFF"
    CYAN = "#22D3EE"          # 辅助青
    GREEN = "#30D158"         # 运行/开始
    GREEN_HOVER = "#28B84C"
    RED = "#FF5A5F"           # 停止/危险
    ORANGE = "#FFB020"        # 暂停/警告
    BORDER = "#232C42"        # 分隔

    NAV_ITEMS = [
        ("timer", "⏱  定时任务"),
        ("logs", "☰  运行日志"),
        ("settings", "⚙  系统设置"),
    ]

    def __init__(self, enable_tray=True):
        # 1. DPI 与外观初始化
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk()
        self.root.title(f"自动关机工具 {APP_VERSION}")

        # 先初始化配置，再读取窗口位置（配置对象必须先于一切读取）
        self.config = Config()

        geom = self.config.get('Window', 'geometry')
        if geom:
            self.root.geometry(geom)
        else:
            self.root.geometry("900x640")
        self.root.minsize(760, 560)
        self.root.configure(fg_color=self.BG_MAIN)

        # 2. 引擎与状态
        self.timer = TimerEngine()
        self.is_counting = False
        self._closing = False
        self._saved_action_idx = 0
        self._tray_hint_shown = False
        self._last_beep_sec = 10
        self._current_page = "timer"
        self._log_loop_id = 0
        self._force_close_confirmed = False

        # 状态变量（保留 v2 命名，兼容 main.py CLI 注入逻辑）
        self.timer_mode_var = tk.StringVar(value="countdown")  # countdown / at_time / periodic
        self.action_var = tk.IntVar(value=0)                   # 0~7
        default_days = [True, True, True, True, True, False, False]  # 周一至周五
        self.periodic_vars = [tk.BooleanVar(value=v) for v in default_days]

        # 3. 应用配置到运行时
        set_force_close(self.config.getboolean('General', 'force_close', True))
        try:
            set_log_level(self.config.get('General', 'log_level', 'INFO'))
        except Exception:
            pass

        # 4. 构建界面
        self._build_sidebar()
        self._build_page_timer()
        self._build_page_logs()
        self._build_page_settings()
        self.show_page("timer")

        # 5. 系统托盘
        self.tray_commands = queue.Queue()
        self.tray = None
        if enable_tray:
            try:
                self.tray = TrayIcon(tooltip=f"自动关机工具 {APP_VERSION}", commands=self.tray_commands)
                self.tray.start()
                set_default_tray(self.tray)
            except Exception as e:
                log.warning("托盘初始化失败（不影响主功能）: %s", e)

        # 6. 事件绑定
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", self._on_escape)

        # 7. 托盘命令轮询
        self.root.after(200, self._poll_tray)

        audit("程序启动", 版本=APP_VERSION)

    # ================= 侧边栏 =================

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self.root, width=178, corner_radius=0, fg_color=self.BG_SIDEBAR)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        title = ctk.CTkLabel(self.sidebar, text="自动关机", font=("Microsoft YaHei UI", 20, "bold"), text_color=self.TEXT_MAIN)
        title.pack(anchor="w", padx=18, pady=(20, 0))
        ctk.CTkLabel(self.sidebar, text=f"任务控制台 {APP_VERSION}", font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED).pack(anchor="w", padx=18, pady=(0, 18))

        self._nav_buttons = {}
        for key, label in self.NAV_ITEMS:
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w", height=40,
                corner_radius=8, fg_color="transparent", hover_color=self.CARD_BG2,
                text_color=self.TEXT_MUTED, font=("Microsoft YaHei UI", 13),
                command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x", padx=10, pady=2)
            self._nav_buttons[key] = btn

        # 侧边栏底部：日志文件提示
        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=14, pady=14)
        ctk.CTkLabel(bottom, text="日志文件", font=("Microsoft YaHei UI", 10), text_color=self.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(bottom, text="logs/app.log", font=("Consolas", 10), text_color=self.CYAN).pack(anchor="w")

    def _mark_nav(self, key):
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=self.CARD_BG2, text_color=self.TEXT_MAIN)
            else:
                btn.configure(fg_color="transparent", text_color=self.TEXT_MUTED)

    # ================= 页面：定时任务 =================

    def _build_page_timer(self):
        self.page_timer = ctk.CTkFrame(self.content_host(), fg_color="transparent")

        # ---- 英雄状态区 ----
        self.card_header = ctk.CTkFrame(self.page_timer, fg_color=self.CARD_BG, corner_radius=14)
        self.card_header.pack(fill="x", padx=18, pady=(16, 8))

        hero = ctk.CTkFrame(self.card_header, fg_color="transparent")
        hero.pack(fill="x", padx=16, pady=(12, 4))

        self._status_dot = ctk.CTkLabel(hero, text="● 准备就绪", font=("Microsoft YaHei UI", 13, "bold"), text_color=self.TEXT_MUTED)
        self._status_dot.pack(side="left")
        self._status_label = self._status_dot

        hero_right = ctk.CTkFrame(hero, fg_color="transparent")
        hero_right.pack(side="right")
        self.label_target = ctk.CTkLabel(hero_right, text="", font=("Microsoft YaHei UI", 11), text_color=self.CYAN)
        self.label_target.pack(anchor="e")
        self.label_last_event = ctk.CTkLabel(hero_right, text="", font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED)
        self.label_last_event.pack(anchor="e")

        self.label_time_display = ctk.CTkLabel(
            self.card_header, text="00:00:00",
            font=("Consolas", 46, "bold"), text_color=self.TEXT_MAIN,
        )
        self.label_time_display.pack(pady=(0, 2))

        self.label_percent = ctk.CTkLabel(self.card_header, text="", font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED)
        self.label_percent.pack(pady=(0, 4))

        self.progress = ctk.CTkProgressBar(self.card_header, height=6, fg_color=self.BORDER, progress_color=self.ACCENT)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=16, pady=(0, 12))

        # ---- 底部控制条（先 pack 占住底部，再让中部区域扩展） ----
        card_bottom = ctk.CTkFrame(self.page_timer, fg_color="transparent")
        card_bottom.pack(fill="x", padx=18, pady=(6, 16), side="bottom")

        self.btn_start = ctk.CTkButton(
            card_bottom, text="开始定时器", fg_color=self.GREEN, hover_color=self.GREEN_HOVER,
            text_color="#06220F", font=("Microsoft YaHei UI", 15, "bold"), height=40, corner_radius=10,
            command=self._on_start_timer,
        )
        self.btn_start.pack(fill="x", pady=(0, 6))

        row_btns = ctk.CTkFrame(card_bottom, fg_color="transparent")
        row_btns.pack(fill="x")

        self.btn_pause = ctk.CTkButton(row_btns, text="暂停", fg_color=self.CARD_BG, hover_color=self.CARD_BG2,
                                       text_color=self.TEXT_MAIN, height=34, state="disabled", command=self._on_pause_timer)
        self.btn_pause.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_stop = ctk.CTkButton(row_btns, text="停止", fg_color=self.CARD_BG, hover_color=self.CARD_BG2,
                                      text_color=self.RED, border_color=self.RED, border_width=1, height=34,
                                      state="disabled", command=self._on_stop_timer)
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=2)

        self.btn_now = ctk.CTkButton(row_btns, text="立即执行", fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
                                     height=34, command=self._on_execute_now)
        self.btn_now.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ---- 中部两列：触发时间 | 执行操作(+参数) ----
        mid = ctk.CTkFrame(self.page_timer, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=18, pady=4)
        mid.grid_columnconfigure(0, weight=1, uniform="cols")
        mid.grid_columnconfigure(1, weight=1, uniform="cols")
        mid.grid_rowconfigure(0, weight=1)

        left_col = ctk.CTkFrame(mid, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        right_col = ctk.CTkFrame(mid, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_time_config_card(into=left_col)
        self._build_action_card(into=right_col)
        self._build_param_card(into=right_col)

    def content_host(self):
        """页面宿主容器（惰性创建）"""
        if not hasattr(self, "_content"):
            self._content = ctk.CTkFrame(self.root, fg_color="transparent")
            self._content.pack(side="left", fill="both", expand=True)
        return self._content

    # ---- 触发时间卡片 ----

    def _build_time_config_card(self, into):
        self.card_time = ctk.CTkFrame(into, fg_color=self.CARD_BG, corner_radius=14)
        self.card_time.pack(fill="both", expand=True)

        top_bar = ctk.CTkFrame(self.card_time, fg_color="transparent")
        top_bar.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(top_bar, text="触发时间", font=("Microsoft YaHei UI", 13, "bold"), text_color=self.TEXT_MAIN).pack(side="left")

        self.mode_segment = ctk.CTkSegmentedButton(
            top_bar, values=["倒计时", "指定时间", "每周循环"],
            command=self._on_segment_changed, height=26, font=("Microsoft YaHei UI", 11),
            selected_color=self.ACCENT, selected_hover_color=self.ACCENT_HOVER,
            unselected_color=self.CARD_BG2, unselected_hover_color=self.BORDER,
            fg_color=self.CARD_BG2, text_color=self.TEXT_MAIN,
        )
        self.mode_segment.set("倒计时")
        self.mode_segment.pack(side="right")

        # 1. 倒计时面板
        self.panel_countdown = ctk.CTkFrame(self.card_time, fg_color="transparent")
        row_inputs = ctk.CTkFrame(self.panel_countdown, fg_color="transparent")
        row_inputs.pack(pady=(6, 2))

        self.spin_days = ctk.CTkEntry(row_inputs, width=52, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_days.insert(0, "0")
        self.spin_hours = ctk.CTkEntry(row_inputs, width=52, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_hours.insert(0, "0")
        self.spin_mins = ctk.CTkEntry(row_inputs, width=52, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_mins.insert(0, "0")
        self.spin_secs = ctk.CTkEntry(row_inputs, width=52, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_secs.insert(0, "0")

        for entry, unit in [(self.spin_days, "天"), (self.spin_hours, "时"), (self.spin_mins, "分"), (self.spin_secs, "秒")]:
            entry.pack(side="left", padx=2)
            ctk.CTkLabel(row_inputs, text=unit, font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED).pack(side="left", padx=(0, 4))

        # 快捷预设芯片（2 行 4 列）
        row_chips = ctk.CTkFrame(self.panel_countdown, fg_color="transparent")
        row_chips.pack(fill="x", padx=10, pady=(10, 14))

        presets = [
            ("10分", 0, 10), ("30分", 0, 30), ("1小时", 1, 0), ("2小时", 2, 0),
            ("3小时", 3, 0), ("4小时", 4, 0), ("6小时", 6, 0), ("8小时", 8, 0),
        ]
        for idx, (label, h, m) in enumerate(presets):
            r, c = divmod(idx, 4)
            btn = ctk.CTkButton(
                row_chips, text=label, height=30,
                fg_color=self.CARD_BG2, hover_color=self.BORDER, text_color=self.TEXT_MAIN,
                font=("Microsoft YaHei UI", 11), corner_radius=8,
                command=lambda h=h, m=m: self._apply_preset(h, m),
            )
            btn.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
            row_chips.grid_columnconfigure(c, weight=1)

        # 2. 指定时间面板
        self.panel_at_time = ctk.CTkFrame(self.card_time, fg_color="transparent")
        row_dt = ctk.CTkFrame(self.panel_at_time, fg_color="transparent")
        row_dt.pack(pady=10)

        now_plus_1h = datetime.datetime.now() + datetime.timedelta(hours=1)
        self.entry_year = ctk.CTkEntry(row_dt, width=58, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_year.insert(0, str(now_plus_1h.year))
        self.entry_month = ctk.CTkEntry(row_dt, width=40, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_month.insert(0, f"{now_plus_1h.month:02d}")
        self.entry_day = ctk.CTkEntry(row_dt, width=40, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_day.insert(0, f"{now_plus_1h.day:02d}")
        self.entry_hour = ctk.CTkEntry(row_dt, width=40, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_hour.insert(0, f"{now_plus_1h.hour:02d}")
        self.entry_min = ctk.CTkEntry(row_dt, width=40, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_min.insert(0, f"{now_plus_1h.minute:02d}")
        self.entry_sec = ctk.CTkEntry(row_dt, width=40, height=32, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_sec.insert(0, f"{now_plus_1h.second:02d}")

        dt_fields = [
            (self.entry_year, "年"), (self.entry_month, "月"), (self.entry_day, "日"),
            (self.entry_hour, "时"), (self.entry_min, "分"), (self.entry_sec, "秒"),
        ]
        for entry, label in dt_fields:
            entry.pack(side="left", padx=2)
            ctk.CTkLabel(row_dt, text=label, font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED).pack(side="left", padx=(0, 5))

        # 3. 每周循环面板
        self.panel_periodic = ctk.CTkFrame(self.card_time, fg_color="transparent")
        row_days = ctk.CTkFrame(self.panel_periodic, fg_color="transparent")
        row_days.pack(fill="x", padx=6, pady=(8, 2))
        day_names = ["一", "二", "三", "四", "五", "六", "日"]
        self.periodic_checkboxes = []
        for idx, name in enumerate(day_names):
            cb = ctk.CTkCheckBox(row_days, text=name, variable=self.periodic_vars[idx], width=34,
                                 font=("Microsoft YaHei UI", 11), fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER)
            cb.pack(side="left", expand=True)
            self.periodic_checkboxes.append(cb)

        row_p_time = ctk.CTkFrame(self.panel_periodic, fg_color="transparent")
        row_p_time.pack(pady=(8, 12))
        self.spin_p_hour = ctk.CTkEntry(row_p_time, width=50, height=30, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_p_hour.insert(0, "23")
        self.spin_p_min = ctk.CTkEntry(row_p_time, width=50, height=30, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_p_min.insert(0, "00")
        self.spin_p_sec = ctk.CTkEntry(row_p_time, width=50, height=30, justify="center", fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.spin_p_sec.insert(0, "00")
        for e, u in [(self.spin_p_hour, "时"), (self.spin_p_min, "分"), (self.spin_p_sec, "秒")]:
            e.pack(side="left", padx=2)
            ctk.CTkLabel(row_p_time, text=u, font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED).pack(side="left", padx=(0, 4))

    # ---- 执行操作卡片 ----

    def _build_action_card(self, into):
        self.card_action = ctk.CTkFrame(into, fg_color=self.CARD_BG, corner_radius=14)
        self.card_action.pack(fill="x")

        lbl = ctk.CTkLabel(self.card_action, text="执行操作", font=("Microsoft YaHei UI", 13, "bold"), text_color=self.TEXT_MAIN)
        lbl.pack(anchor="w", padx=14, pady=(12, 4))

        grid = ctk.CTkFrame(self.card_action, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(0, 10))

        actions = [
            ("⏻ 关闭电脑", 0), ("↻ 重启电脑", 1),
            ("⏏ 注销电脑", 2), ("☾ 睡眠模式", 3),
            ("▱ 关闭显示器", 4), ("▶ 运行程序", 5),
            ("♪ 播放声音", 6), ("✉ 弹出消息", 7),
        ]
        for idx, (name, val) in enumerate(actions):
            r, c = divmod(idx, 2)
            rb = ctk.CTkRadioButton(
                grid, text=name, variable=self.action_var, value=val,
                font=("Microsoft YaHei UI", 12), text_color=self.TEXT_MAIN,
                fg_color=self.ACCENT, hover_color=self.ACCENT_HOVER,
                border_color=self.BORDER, command=self._on_action_changed, height=26,
            )
            rb.grid(row=r, column=c, sticky="w", padx=12, pady=4)

    # ---- 动态参数卡片 ----

    def _build_param_card(self, into):
        self.card_param = ctk.CTkFrame(into, fg_color=self.CARD_BG, corner_radius=14)
        # 默认不 pack，由 _on_action_changed 动态控制

        # 1. 运行程序控件组
        self.frame_prog = ctk.CTkFrame(self.card_param, fg_color="transparent")
        f_p1 = ctk.CTkFrame(self.frame_prog, fg_color="transparent")
        f_p1.pack(fill="x", pady=2)
        self.entry_prog_path = ctk.CTkEntry(f_p1, placeholder_text="可执行文件路径 (.exe / .bat)", height=30, fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_prog_path.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(f_p1, text="浏览...", width=64, height=30, command=self._browse_program).pack(side="right")
        self.entry_prog_args = ctk.CTkEntry(self.frame_prog, placeholder_text="附加运行参数 (可选)", height=30, fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_prog_args.pack(fill="x", pady=(4, 0))

        # 2. 播放声音控件组
        self.frame_sound = ctk.CTkFrame(self.card_param, fg_color="transparent")
        f_s1 = ctk.CTkFrame(self.frame_sound, fg_color="transparent")
        f_s1.pack(fill="x", pady=2)
        self.entry_sound_path = ctk.CTkEntry(f_s1, placeholder_text="音频文件路径 (.wav / .mp3)", height=30, fg_color=self.CARD_BG2, border_color=self.BORDER)
        self.entry_sound_path.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(f_s1, text="浏览...", width=64, height=30, command=self._browse_sound).pack(side="right")

        # 3. 弹窗消息控件组
        self.frame_msg = ctk.CTkFrame(self.card_param, fg_color="transparent")
        self.text_msg = ctk.CTkTextbox(self.frame_msg, height=56, font=("Microsoft YaHei UI", 12), fg_color=self.CARD_BG2, border_color=self.BORDER, border_width=1)
        self.text_msg.pack(fill="x", pady=2)
        self.text_msg.insert("1.0", "定时提醒时间已到！")

    def _on_action_changed(self):
        """切换操作时动态挂载参数输入卡片"""
        act = self.action_var.get()
        self.frame_prog.pack_forget()
        self.frame_sound.pack_forget()
        self.frame_msg.pack_forget()
        self.card_param.pack_forget()

        if act == 5:  # 运行程序
            self.card_param.pack(fill="x", pady=(8, 0), after=self.card_action)
            self.frame_prog.pack(fill="x", padx=14, pady=10)
        elif act == 6:  # 播放声音
            self.card_param.pack(fill="x", pady=(8, 0), after=self.card_action)
            self.frame_sound.pack(fill="x", padx=14, pady=10)
        elif act == 7:  # 弹出消息
            self.card_param.pack(fill="x", pady=(8, 0), after=self.card_action)
            self.frame_msg.pack(fill="x", padx=14, pady=10)

        log.debug("切换执行操作: %s", get_action_name(act))

    def _on_segment_changed(self, value):
        mapping = {"倒计时": "countdown", "指定时间": "at_time", "每周循环": "periodic"}
        self.timer_mode_var.set(mapping.get(value, "countdown"))
        self._on_timer_mode_changed()

    def _on_timer_mode_changed(self):
        mode = self.timer_mode_var.get()
        self.panel_countdown.pack_forget()
        self.panel_at_time.pack_forget()
        self.panel_periodic.pack_forget()

        if mode == "countdown":
            self.panel_countdown.pack(fill="x")
        elif mode == "at_time":
            self.panel_at_time.pack(fill="x")
        elif mode == "periodic":
            self.panel_periodic.pack(fill="x")

    # ================= 页面：运行日志 =================

    def _build_page_logs(self):
        self.page_logs = ctk.CTkFrame(self.content_host(), fg_color="transparent")

        card = ctk.CTkFrame(self.page_logs, fg_color=self.CARD_BG, corner_radius=14)
        card.pack(fill="both", expand=True, padx=18, pady=(16, 16))

        toolbar = ctk.CTkFrame(card, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(toolbar, text="运行日志", font=("Microsoft YaHei UI", 13, "bold"), text_color=self.TEXT_MAIN).pack(side="left")

        self.log_filter_var = tk.StringVar(value="全部")
        ctk.CTkOptionMenu(
            toolbar, values=["全部", "INFO", "WARNING", "ERROR"],
            variable=self.log_filter_var, command=lambda _=None: self._refresh_logs(),
            width=96, height=28, fg_color=self.CARD_BG2, button_color=self.BORDER,
            button_hover_color=self.ACCENT, text_color=self.TEXT_MAIN,
        ).pack(side="right")

        ctk.CTkButton(toolbar, text="打开目录", width=76, height=28, fg_color=self.CARD_BG2, hover_color=self.BORDER, command=self._open_log_dir).pack(side="right", padx=(0, 6))
        ctk.CTkButton(toolbar, text="刷新", width=60, height=28, fg_color=self.CARD_BG2, hover_color=self.BORDER, command=self._refresh_logs).pack(side="right", padx=(0, 6))

        self.log_autoscroll_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(toolbar, text="自动滚动", variable=self.log_autoscroll_var, font=("Microsoft YaHei UI", 11),
                      text_color=self.TEXT_MUTED, progress_color=self.ACCENT, width=70).pack(side="right", padx=(0, 10))

        self.log_textbox = ctk.CTkTextbox(
            card, font=("Consolas", 12), fg_color=self.BG_SIDEBAR,
            text_color="#B8C4DC", wrap="none",
        )
        self.log_textbox.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.log_textbox.configure(state="disabled")

    def _refresh_logs(self):
        """从日志文件读取末尾内容，按级别过滤后显示"""
        try:
            tail_lines = self._read_log_tail(max_lines=500)
            level = self.log_filter_var.get()
            if level != "全部":
                token = f" [{level}] "
                tail_lines = [ln for ln in tail_lines if token in ln]
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", "\n".join(tail_lines) if tail_lines else "（暂无日志）")
            self.log_textbox.configure(state="disabled")
            if self.log_autoscroll_var.get():
                self.log_textbox.yview("end")
        except Exception as e:
            log.debug("读取日志显示失败: %s", e)

    @staticmethod
    def _read_log_tail(max_lines=500):
        """读取当前日志文件末尾 N 行（编码异常容忍）"""
        path = get_log_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))  # 最多回读 512KB
                data = f.read()
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            return lines[-max_lines:]
        except OSError:
            return []

    def _open_log_dir(self):
        try:
            os.makedirs(get_log_dir(), exist_ok=True)
            os.startfile(get_log_dir())  # noqa: S606 — 打开资源管理器定位日志目录
        except Exception as e:
            log.warning("打开日志目录失败: %s", e)

    def _log_autorefresh(self, loop_id=0):
        """日志页激活时每 2 秒自动刷新（代际校验防止多重循环）"""
        if self._closing or self._current_page != "logs" or loop_id != self._log_loop_id:
            return
        self._refresh_logs()
        self.root.after(2000, lambda: self._log_autorefresh(loop_id))

    # ================= 页面：系统设置 =================

    def _build_page_settings(self):
        self.page_settings = ctk.CTkFrame(self.content_host(), fg_color="transparent")

        card = ctk.CTkFrame(self.page_settings, fg_color=self.CARD_BG, corner_radius=14)
        card.pack(fill="both", expand=True, padx=18, pady=(16, 16))

        ctk.CTkLabel(card, text="系统设置", font=("Microsoft YaHei UI", 13, "bold"), text_color=self.TEXT_MAIN).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(card, text="以下开关即时生效并自动保存到 settings.ini", font=("Microsoft YaHei UI", 11), text_color=self.TEXT_MUTED).pack(anchor="w", padx=14, pady=(0, 8))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=14)

        # (key, 显示文本, 说明)
        switches = [
            ("ask_before_execute", "手动执行前需要确认", "点击「立即执行」时弹出确认框"),
            ("ask_critical_only", "仅对关键操作确认", "仅关机/重启/注销需要确认"),
            ("ask_before_close", "退出程序前需要确认", "关闭窗口且未启用托盘时生效"),
            ("minimize_to_tray", "关闭窗口最小化到托盘", "点击 × 隐藏到系统托盘而非退出"),
            ("esc_minimize", "Esc 键最小化到托盘", "按 Esc 隐藏窗口"),
            ("play_sound_last_10s", "最后 10 秒提示音", "倒计时结束前每秒提示"),
            ("force_close", "强制关闭应用程序", "关机/重启时附加 /f 强制关闭"),
            ("start_minimized", "启动时最小化到托盘", "下次启动后直接隐藏到托盘"),
        ]
        self._setting_switches = {}
        for i, (key, text, desc) in enumerate(switches):
            r, c = divmod(i, 2)
            cell = ctk.CTkFrame(grid, fg_color=self.CARD_BG2, corner_radius=10)
            cell.grid(row=r, column=c, sticky="nsew", padx=5, pady=5)
            sw = ctk.CTkSwitch(
                cell, text=text, command=lambda k=key: self._on_setting_changed(k),
                font=("Microsoft YaHei UI", 12), text_color=self.TEXT_MAIN,
                progress_color=self.ACCENT, button_color=self.TEXT_MUTED,
            )
            sw.pack(anchor="w", padx=12, pady=(10, 2))
            sw.set(self.config.getboolean('General', key, True))
            ctk.CTkLabel(cell, text=desc, font=("Microsoft YaHei UI", 10), text_color=self.TEXT_MUTED).pack(anchor="w", padx=12, pady=(0, 10))
            self._setting_switches[key] = sw
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1)

        # 日志级别 + 日志管理
        log_row = ctk.CTkFrame(card, fg_color="transparent")
        log_row.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(log_row, text="日志级别", font=("Microsoft YaHei UI", 12), text_color=self.TEXT_MAIN).pack(side="left")
        self.log_level_var = tk.StringVar(value=self.config.get('General', 'log_level', 'INFO'))
        ctk.CTkOptionMenu(
            log_row, values=["DEBUG", "INFO", "WARNING"], variable=self.log_level_var,
            command=self._on_log_level_changed, width=110, height=28,
            fg_color=self.CARD_BG2, button_color=self.BORDER, button_hover_color=self.ACCENT,
            text_color=self.TEXT_MAIN,
        ).pack(side="left", padx=(8, 0))

        ctk.CTkButton(log_row, text="打开日志目录", height=28, fg_color=self.CARD_BG2, hover_color=self.BORDER, command=self._open_log_dir).pack(side="right", padx=(6, 0))
        ctk.CTkButton(log_row, text="清空全部日志", height=28, fg_color=self.CARD_BG2, hover_color=self.RED, text_color=self.RED, command=self._clear_logs).pack(side="right")

        # 关于
        about_row = ctk.CTkFrame(card, fg_color="transparent")
        about_row.pack(fill="x", padx=14, pady=(8, 14))
        ctk.CTkButton(about_row, text="关于本工具", height=28, width=110, fg_color=self.CARD_BG2, hover_color=self.BORDER, command=self._show_about).pack(side="left")
        ctk.CTkLabel(about_row, text=f"自动关机工具 {APP_VERSION} · 深色任务控制台", font=("Microsoft YaHei UI", 10), text_color=self.TEXT_MUTED).pack(side="right")

    def _on_setting_changed(self, key):
        """设置开关切换：写配置 + 即时生效 + 记日志"""
        value = bool(self._setting_switches[key].get())
        self.config.set('General', key, 'true' if value else 'false')
        self.config.save()
        audit("配置变更", 项=key, 值="开启" if value else "关闭")
        log.info("设置变更: %s = %s", key, value)
        if key == "force_close":
            set_force_close(value)

    def _on_log_level_changed(self, value):
        set_log_level(value)
        self.config.set('General', 'log_level', value)
        self.config.save()
        audit("配置变更", 项="log_level", 值=value)
        log.info("日志级别调整为 %s", value)

    def _clear_logs(self):
        if not messagebox.askyesno("清空日志", "确定删除全部日志文件吗？此操作不可恢复。"):
            return
        removed = clear_logs()
        audit("清空日志", 文件数=removed)
        self._refresh_logs()

    # ================= 页面切换 =================

    def show_page(self, key):
        self._current_page = key
        self._mark_nav(key)
        for page in (self.page_timer, self.page_logs, self.page_settings):
            page.pack_forget()
        target = {"timer": self.page_timer, "logs": self.page_logs, "settings": self.page_settings}[key]
        target.pack(fill="both", expand=True)
        if key == "logs":
            self._log_loop_id = getattr(self, "_log_loop_id", 0) + 1
            self._refresh_logs()
            self.root.after(2000, lambda: self._log_autorefresh(self._log_loop_id))

    # ================= 辅助事件与文件浏览 =================

    def _browse_program(self):
        f = filedialog.askopenfilename(title="选择可执行程序", filetypes=[("程序文件", "*.exe *.bat *.cmd *.vbs"), ("所有文件", "*.*")])
        if f:
            self.entry_prog_path.delete(0, "end")
            self.entry_prog_path.insert(0, f)

    def _browse_sound(self):
        f = filedialog.askopenfilename(title="选择音频文件", filetypes=[("音频文件", "*.wav *.mp3 *.wma"), ("所有文件", "*.*")])
        if f:
            self.entry_sound_path.delete(0, "end")
            self.entry_sound_path.insert(0, f)

    def _apply_preset(self, h, m):
        self.spin_days.delete(0, "end"); self.spin_days.insert(0, "0")
        self.spin_hours.delete(0, "end"); self.spin_hours.insert(0, str(h))
        self.spin_mins.delete(0, "end"); self.spin_mins.insert(0, str(m))
        self.spin_secs.delete(0, "end"); self.spin_secs.insert(0, "0")

    # ================= 定时器控制 =================

    def _on_start_timer(self):
        """核心：读取输入参数，真正启动底层定时器"""
        if self.is_counting:
            return

        action_idx = self.action_var.get()
        mode = self.timer_mode_var.get()

        # 1. 保存特殊操作参数
        if action_idx == 5:
            path = self.entry_prog_path.get().strip()
            if not path:
                messagebox.showwarning("缺失参数", "请选择要运行的程序路径！")
                return
        elif action_idx == 6:
            path = self.entry_sound_path.get().strip()
            if not path:
                messagebox.showwarning("缺失参数", "请选择音频文件！")
                return
        elif action_idx == 7:
            msg = self.text_msg.get("1.0", "end-1c").strip()
            if not msg:
                messagebox.showwarning("缺失参数", "请输入要提示的消息内容！")
                return

        # 2. 解析时间并启动引擎
        try:
            if mode == "countdown":
                d = int(self.spin_days.get() or 0)
                h = int(self.spin_hours.get() or 0)
                m = int(self.spin_mins.get() or 0)
                s = int(self.spin_secs.get() or 0)
                total_sec = d * 86400 + h * 3600 + m * 60 + s
                if total_sec <= 0:
                    messagebox.showwarning("提示", "请输入有效时间！")
                    return
                self._saved_action_idx = action_idx
                self.timer.start_countdown(
                    total_sec,
                    on_tick=lambda r, t: self.root.after(0, self._update_ui_tick, r, t),
                    on_complete=lambda: self.root.after(0, self._on_timer_complete),
                )

            elif mode == "at_time":
                try:
                    y = int(self.entry_year.get() or 0)
                    mo = int(self.entry_month.get() or 1)
                    d = int(self.entry_day.get() or 1)
                    h = int(self.entry_hour.get() or 0)
                    mi = int(self.entry_min.get() or 0)
                    s = int(self.entry_sec.get() or 0)
                except ValueError:
                    messagebox.showwarning("提示", "日期时间必须为有效数字！")
                    return
                try:
                    target_time = datetime.datetime(y, mo, d, h, mi, s)
                except ValueError as e:
                    messagebox.showwarning("提示", f"日期时间无效（{e}）！")
                    return
                if target_time <= datetime.datetime.now():
                    messagebox.showwarning("提示", "设定的时间已过，请设置未来时间！")
                    return
                self._saved_action_idx = action_idx
                self.timer.start_at_time(
                    target_time,
                    on_tick=lambda r, t: self.root.after(0, self._update_ui_tick, r, t),
                    on_complete=lambda: self.root.after(0, self._on_timer_complete),
                )

            elif mode == "periodic":
                days = [i for i, var in enumerate(self.periodic_vars) if var.get()]
                if not days:
                    messagebox.showwarning("提示", "请至少选择一个星期！")
                    return
                h = int(self.spin_p_hour.get() or 0)
                m = int(self.spin_p_min.get() or 0)
                s = int(self.spin_p_sec.get() or 0)
                self._saved_action_idx = action_idx
                self.timer.start_periodic(
                    days, h, m, s,
                    on_tick=lambda r, t: self.root.after(0, self._update_ui_tick, r, t),
                    on_complete=lambda: self.root.after(0, self._on_timer_complete),
                )

        except ValueError as e:
            messagebox.showerror("参数错误", f"时间格式错误: {e}")
            return
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return

        # 3. 更新界面状态
        self.is_counting = True
        self._last_beep_sec = 10
        self._set_status("● 正在运行", self.GREEN)
        self._toggle_inputs(state="disabled")
        self.btn_pause.configure(state="normal", text="暂停")
        self.btn_stop.configure(state="normal")
        audit("定时启动", 操作=get_action_name(action_idx), 模式=mode)

    def _set_status(self, text, color):
        self._status_dot.configure(text=text, text_color=color)

    def _update_ui_tick(self, remaining_sec, total_sec):
        """驱动 UI 倒计时数字与进度（由引擎线程经 root.after 调度回主线程）"""
        if not self.is_counting:
            return

        if remaining_sec <= 0:
            self.label_time_display.configure(text="00:00:00")
            self.progress.set(1.0)
            self.label_percent.configure(text="100%")
            return

        rem = int(remaining_sec)
        self.label_time_display.configure(text=format_seconds(rem))

        if total_sec > 0:
            frac = 1.0 - (remaining_sec / total_sec)
            self.progress.set(frac)
            if self.config.get('General', 'progress_mode', 'percent') == 'percent':
                self.label_percent.configure(text=f"{frac * 100:.0f}%")

        target = datetime.datetime.now() + datetime.timedelta(seconds=remaining_sec)
        self.label_target.configure(text=f"预计执行  {target:%m-%d %H:%M:%S}")

        # 最后 10 秒提示音
        if rem <= 10 and self.config.getboolean('General', 'play_sound_last_10s', False):
            if rem < self._last_beep_sec:
                self._last_beep_sec = rem
                try:
                    winsound.Beep(1000, 150)
                except Exception:
                    pass

    def _on_timer_complete(self):
        """定时器完成回调：执行选定操作"""
        action_idx = getattr(self, '_saved_action_idx', self.action_var.get())
        self.root.after(0, lambda: self._do_execute(action_idx))

    def _do_execute(self, action_idx):
        """在主线程执行操作"""
        name = get_action_name(action_idx)
        # 周期模式：只执行操作，不重置状态（引擎继续运行）
        is_periodic = (self.timer.mode == TimerEngine.MODE_PERIODIC)
        if not is_periodic:
            self.is_counting = False
            self._set_status("● 已完成", self.TEXT_MUTED)
            self.progress.set(1.0)
            self.label_percent.configure(text="100%")
            self._toggle_inputs(state="normal")
            self.btn_pause.configure(state="disabled")
            self.btn_stop.configure(state="disabled")

        # 构建参数
        params = None
        if action_idx == 5:
            params = {
                'path': self.entry_prog_path.get().strip(),
                'args': self.entry_prog_args.get().strip(),
            }
        elif action_idx == 6:
            params = self.entry_sound_path.get().strip()
        elif action_idx == 7:
            params = self.text_msg.get("1.0", "end-1c").strip()

        audit("定时触发执行", 操作=name, 周期=is_periodic)
        execute_action(action_idx, params)
        self.label_last_event.configure(
            text=f"上次执行  {datetime.datetime.now():%H:%M:%S}  {name}"
        )

    def _on_pause_timer(self):
        """暂停/恢复切换"""
        if not self.is_counting:
            return
        if self.timer.is_paused:
            self.timer.resume()
            self._set_status("● 正在运行", self.GREEN)
            self.btn_pause.configure(text="暂停")
        else:
            self.timer.pause()
            self._set_status("● 已暂停", self.ORANGE)
            self.btn_pause.configure(text="恢复")

    def _on_stop_timer(self):
        """停止定时器"""
        self.timer.stop()
        self.is_counting = False
        self._set_status("● 准备就绪", self.TEXT_MUTED)
        self.label_time_display.configure(text="00:00:00")
        self.progress.set(0)
        self.label_percent.configure(text="")
        self.label_target.configure(text="")
        self._toggle_inputs(state="normal")
        self.btn_pause.configure(state="disabled", text="暂停")
        self.btn_stop.configure(state="disabled")

    def _toggle_inputs(self, state="disabled"):
        """禁用/启用时间输入框（运行中禁止修改）"""
        widgets = [
            self.spin_days, self.spin_hours, self.spin_mins, self.spin_secs,
            self.entry_year, self.entry_month, self.entry_day,
            self.entry_hour, self.entry_min, self.entry_sec,
            self.spin_p_hour, self.spin_p_min, self.spin_p_sec,
        ]
        if hasattr(self, 'periodic_checkboxes'):
            widgets.extend(self.periodic_checkboxes)
        for w in widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _on_execute_now(self):
        """立即执行（不启动定时器），按设置决定是否确认"""
        action_idx = self.action_var.get()
        name = get_action_name(action_idx)

        if self.config.getboolean('General', 'ask_before_execute', True):
            critical_only = self.config.getboolean('General', 'ask_critical_only', False)
            if (not critical_only) or (action_idx in CRITICAL_ACTIONS):
                if not messagebox.askyesno("确认执行", f"确定要立即执行「{name}」吗？"):
                    audit("手动执行取消", 操作=name)
                    return

        params = None
        if action_idx == 5:
            params = {
                'path': self.entry_prog_path.get().strip(),
                'args': self.entry_prog_args.get().strip(),
            }
        elif action_idx == 6:
            params = self.entry_sound_path.get().strip()
        elif action_idx == 7:
            params = self.text_msg.get("1.0", "end-1c").strip()

        audit("手动执行", 操作=name)
        execute_action(action_idx, params)
        self.label_last_event.configure(
            text=f"上次执行  {datetime.datetime.now():%H:%M:%S}  {name}"
        )

    # ================= 托盘与窗口管理 =================

    def _poll_tray(self):
        """轮询托盘图标发来的命令"""
        if self._closing:
            return
        try:
            while True:
                cmd = self.tray_commands.get_nowait()
                if cmd == "show":
                    self._restore_from_tray()
                elif cmd == "exit":
                    self._force_close_confirmed = True
                    self._really_quit()
                    return
        except queue.Empty:
            pass
        except Exception as e:
            log.debug("托盘命令处理异常: %s", e)
        self.root.after(200, self._poll_tray)

    def _minimize_to_tray(self):
        """最小化到系统托盘（main.py /min 启动参数同样调用此方法）"""
        try:
            self.root.withdraw()
            tray = get_default_tray()
            if tray is not None and not self._tray_hint_shown:
                self._tray_hint_shown = True
                tray.notify("自动关机工具", "程序已最小化到系统托盘，点击图标可恢复窗口。")
            audit("最小化到托盘")
        except Exception as e:
            log.warning("最小化到托盘失败: %s", e)

    def _restore_from_tray(self):
        """从托盘恢复主窗口"""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _on_escape(self, _event=None):
        if self.config.getboolean('General', 'esc_minimize', True):
            self._minimize_to_tray()

    def _on_close(self):
        """点击窗口 × — 按配置决定托盘隐藏或退出"""
        if (self.config.getboolean('General', 'minimize_to_tray', True)
                and not self._force_close_confirmed):
            self._minimize_to_tray()
            return
        if self.config.getboolean('General', 'ask_before_close', False):
            if not messagebox.askyesno("退出确认", "确定要退出自动关机工具吗？\n退出后未完成的定时任务将被取消。"):
                audit("退出取消")
                return
        self._really_quit()

    def _really_quit(self):
        """真正退出：保存配置、停止引擎与托盘、销毁窗口"""
        try:
            audit("程序退出")
            if self.timer.is_running:
                self.timer.stop()
            try:
                current_geom = self.root.geometry()
                self.config.set('Window', 'geometry', current_geom)
                self.config.save()
            except Exception as e:
                log.warning("退出时保存配置失败: %s", e)
            try:
                if self.tray is not None:
                    self.tray.stop()
                set_default_tray(None)
            except Exception:
                pass
            clean_sound_resources()
        finally:
            self._closing = True
            try:
                self.root.destroy()
            except Exception:
                pass

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            f"自动关机工具 {APP_VERSION}\n\n"
            "深色任务控制台 · 支持倒计时 / 指定时间 / 每周循环三种定时模式\n"
            "8 种执行操作 · 系统托盘 · 轮转日志系统\n\n"
            f"日志目录: {get_log_dir()}",
        )

    def run(self):
        self.root.mainloop()
