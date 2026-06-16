# ui/main_window.py
# 主窗口 GUI — 自动关机工具的主界面

import os
import sys
import threading
import ctypes
import winsound
from datetime import datetime
from tkinter import (
    Tk, Toplevel, Frame, LabelFrame, Label, Radiobutton, Checkbutton,
    Button, Spinbox, Entry, Text, messagebox, filedialog, StringVar,
    IntVar, BooleanVar, Menu, ttk, PhotoImage
)

from core.config import Config
from core.actions import ACTION_MAP, CRITICAL_ACTIONS, get_action_name, execute_action
from core.timer_engine import TimerEngine

# ============================================================
# 系统托盘实现（Windows API, ctypes）
# ============================================================

# Windows 常量
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 100
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
GWL_WNDPROC = -4


class NOTIFYICONDATAW(ctypes.Structure):
    """Windows NOTIFYICONDATAW 结构体 (Vista+)"""
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_ulong),
        ("dwStateMask", ctypes.c_ulong),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_ulong),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


WNDPROC_CB = ctypes.CFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
    ctypes.c_void_p, ctypes.c_void_p
)


class SystemTray:
    """Windows 系统托盘实现 (通过 WndProc 子类化)"""

    def __init__(self, root, on_show=None, on_exit=None):
        self.root = root
        self.on_show = on_show
        self.on_exit = on_exit
        self.hwnd = None
        self._old_proc = None       # 原窗口过程地址
        self._new_proc_cb = None    # 新窗口过程（保持引用防GC）
        self._icon_added = False
        self._nid = None

    def get_hwnd(self):
        """获取 tkinter 窗口的 HWND"""
        self.root.update_idletasks()
        hwnd = self.root.winfo_id()
        # 有时需要取父窗口（Tk 内部）
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        return hwnd

    def install(self):
        """安装系统托盘"""
        try:
            self.hwnd = self.get_hwnd()
            self._subclass_window()
            self._add_icon()
        except Exception as e:
            print(f"系统托盘安装失败: {e}", file=sys.stderr)

    def _subclass_window(self):
        """子类化窗口过程以接收托盘消息"""
        @WNDPROC_CB
        def tray_wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                self._on_tray_message(lparam)
                return 0
            return ctypes.windll.user32.CallWindowProcW(
                self._old_proc, hwnd, msg, wparam, lparam
            )

        self._new_proc_cb = tray_wndproc  # 保持引用
        self._old_proc = ctypes.windll.user32.SetWindowLongPtrW(
            self.hwnd, GWL_WNDPROC, self._new_proc_cb
        )

    def _add_icon(self):
        """添加托盘图标"""
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.szTip = "自动关机工具 v1.0"

        # 加载默认应用图标
        nid.hIcon = ctypes.windll.user32.LoadIconW(0, 32512)

        ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._icon_added = True
        self._nid = nid

    def remove(self):
        """移除托盘图标"""
        if self._icon_added and self._nid:
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(
                    NIM_DELETE, ctypes.byref(self._nid)
                )
            except Exception:
                pass
            self._icon_added = False

    def _on_tray_message(self, lparam):
        """处理托盘消息"""
        if lparam == WM_LBUTTONUP or lparam == WM_LBUTTONDBLCLK:
            # 左键点击：显示窗口
            if self.on_show:
                self.root.after(0, self.on_show)
        elif lparam == WM_RBUTTONUP:
            # 右键点击：弹出菜单
            self.root.after(0, self._show_popup_menu)

    def _show_popup_menu(self):
        """显示右键弹出菜单"""
        menu = Menu(self.root, tearoff=False)
        menu.add_command(label="显示", command=self.on_show or self._default_show)
        menu.add_separator()
        menu.add_command(label="退出", command=self.on_exit or self._default_exit)
        # 在鼠标位置弹出
        try:
            x = self.root.winfo_pointerx()
            y = self.root.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _default_show(self):
        """默认显示窗口"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _default_exit(self):
        """默认退出"""
        self.remove()
        self.root.destroy()

    def update_tooltip(self, text):
        """更新托盘图标的提示文本"""
        if self._nid:
            self._nid.szTip = text[:127]
            ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_MODIFY, ctypes.byref(self._nid)
            )

    def __del__(self):
        self.remove()


# ============================================================
# 主窗口类
# ============================================================

class MainWindow:
    """自动关机工具主窗口"""

    # 星期名称（周一=0）
    WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    def __init__(self):
        self.root = Tk()
        self.config = Config()
        self.timer = TimerEngine()
        self.tray = None

        # ---- 状态变量 ----
        self.action_var = IntVar(value=0)          # 当前选中的操作 (0-7)
        self.timer_mode_var = StringVar(value="countdown")  # countdown / at_time / periodic
        self.is_counting = False    # 定时器是否正在运行
        self.is_paused = False      # 定时器是否暂停
        self._closing = False       # 正在关闭标志

        # 字体变量（必须用实例变量）
        self.f_sec = ("微软雅黑", 10)
        self.f_title = ("微软雅黑", 12, "bold")
        self.f_normal = ("微软雅黑", 9)
        self.f_small = ("微软雅黑", 8)
        self.f_timer = ("Consolas", 24, "bold")

        # ---- 窗口基本设置 ----
        self.root.title("自动关机工具 v1.0")
        self.root.resizable(False, False)
        self.root.minsize(640, 480)

        # ---- 构建界面 ----
        self._build_menu()
        self._build_ui()

        # ---- 恢复窗口位置 ----
        self._restore_geometry()

        # ---- 系统托盘 ----
        self._setup_tray()

        # ---- 绑定事件 ----
        self._bind_events()

        # 协议：关闭窗口
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ============================================================
    # 构建菜单
    # ============================================================

    def _build_menu(self):
        """构建菜单栏"""
        menubar = Menu(self.root)
        self.root.config(menu=menubar)

        # 设置菜单
        settings_menu = Menu(menubar, tearoff=False)
        settings_menu.add_command(label="设置...", command=self._show_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="退出", command=self._on_closing)
        menubar.add_cascade(label="选项", menu=settings_menu)

        # 帮助菜单
        help_menu = Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

    # ============================================================
    # 构建主界面
    # ============================================================

    def _build_ui(self):
        """构建主界面所有控件"""
        main_frame = Frame(self.root, padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)

        # 上部：操作选择 + 定时模式（左右分栏）
        top_frame = Frame(main_frame)
        top_frame.pack(fill="x", pady=(0, 10))

        # ---- 左：操作选择 ----
        action_frame = LabelFrame(top_frame, text=" 可以执行的操作 ",
                                  font=self.f_normal, padx=8, pady=5)
        action_frame.pack(side="left", fill="y", padx=(0, 10))

        action_names = [get_action_name(i) for i in range(8)]
        for i, name in enumerate(action_names):
            Radiobutton(
                action_frame, text=name, variable=self.action_var,
                value=i, font=self.f_normal, anchor="w",
                command=self._on_action_changed
            ).pack(anchor="w", pady=1)

        # ---- 右：定时模式 ----
        timer_frame = LabelFrame(top_frame, text=" 定时模式 ",
                                 font=self.f_normal, padx=8, pady=5)
        timer_frame.pack(side="left", fill="both", expand=True)

        # 模式选择收音钮行
        mode_row = Frame(timer_frame)
        mode_row.pack(fill="x", pady=(0, 8))
        Radiobutton(mode_row, text="倒计时", variable=self.timer_mode_var,
                    value="countdown", font=self.f_normal,
                    command=self._on_timer_mode_changed).pack(side="left", padx=(0, 15))
        Radiobutton(mode_row, text="日期", variable=self.timer_mode_var,
                    value="at_time", font=self.f_normal,
                    command=self._on_timer_mode_changed).pack(side="left", padx=(0, 15))
        Radiobutton(mode_row, text="时间段", variable=self.timer_mode_var,
                    value="periodic", font=self.f_normal,
                    command=self._on_timer_mode_changed).pack(side="left")

        # ---- 倒计时输入区域 ----
        self.countdown_frame = Frame(timer_frame)
        self.countdown_frame.pack(fill="x")
        Label(self.countdown_frame, text="天:", font=self.f_normal).pack(side="left")
        self.spin_days = Spinbox(self.countdown_frame, from_=0, to=365, width=3,
                                 font=self.f_normal, justify="center")
        self.spin_days.pack(side="left", padx=(0, 10))
        Label(self.countdown_frame, text="时:", font=self.f_normal).pack(side="left")
        self.spin_hours = Spinbox(self.countdown_frame, from_=0, to=23, width=3,
                                  font=self.f_normal, justify="center")
        self.spin_hours.pack(side="left", padx=(0, 10))
        Label(self.countdown_frame, text="分:", font=self.f_normal).pack(side="left")
        self.spin_mins = Spinbox(self.countdown_frame, from_=0, to=59, width=3,
                                 font=self.f_normal, justify="center")
        self.spin_mins.pack(side="left", padx=(0, 10))
        Label(self.countdown_frame, text="秒:", font=self.f_normal).pack(side="left")
        self.spin_secs = Spinbox(self.countdown_frame, from_=0, to=59, width=3,
                                 font=self.f_normal, justify="center")
        self.spin_secs.pack(side="left")

        # ---- 日期输入区域 ----
        self.at_time_frame = Frame(timer_frame)
        # 初始隐藏
        Label(self.at_time_frame, text="日期时间 (YYYY-MM-DD HH:MM:SS):",
              font=self.f_normal).pack(anchor="w")
        entry_row = Frame(self.at_time_frame)
        entry_row.pack(fill="x")
        self.entry_datetime = Entry(entry_row, font=self.f_normal, width=22)
        self.entry_datetime.pack(side="left", padx=(0, 5))
        # 用默认时间
        self.entry_datetime.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        Button(entry_row, text="现在", font=self.f_small,
               command=self._set_datetime_now).pack(side="left")

        # ---- 时间段输入区域 ----
        self.periodic_frame = Frame(timer_frame)
        Label(self.periodic_frame, text="选择日期:",
              font=self.f_normal).pack(anchor="w")
        day_row = Frame(self.periodic_frame)
        day_row.pack(fill="x")
        self.periodic_vars = {}
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = BooleanVar(value=True)  # 默认全选
            self.periodic_vars[i] = var
            Checkbutton(day_row, text=name, variable=var,
                        font=self.f_small).pack(side="left")

        time_row = Frame(self.periodic_frame)
        time_row.pack(fill="x", pady=(5, 0))
        Label(time_row, text="时:", font=self.f_normal).pack(side="left")
        self.spin_p_hour = Spinbox(time_row, from_=0, to=23, width=3,
                                   font=self.f_normal, justify="center")
        self.spin_p_hour.pack(side="left", padx=(0, 10))
        Label(time_row, text="分:", font=self.f_normal).pack(side="left")
        self.spin_p_min = Spinbox(time_row, from_=0, to=59, width=3,
                                  font=self.f_normal, justify="center")
        self.spin_p_min.pack(side="left", padx=(0, 10))
        Label(time_row, text="秒:", font=self.f_normal).pack(side="left")
        self.spin_p_sec = Spinbox(time_row, from_=0, to=59, width=3,
                                  font=self.f_normal, justify="center")
        self.spin_p_sec.pack(side="left")

        # 隐藏非倒计时面板
        self.at_time_frame.forget()
        self.periodic_frame.forget()

        # ---- 中间：操作参数区域 ----
        self.param_frame = LabelFrame(main_frame, text=" 操作参数 ",
                                      font=self.f_normal, padx=8, pady=5)
        self.param_frame.pack(fill="x", pady=(0, 10))

        # 运行程序参数
        self.run_param_frame = Frame(self.param_frame)
        Label(self.run_param_frame, text="程序路径:", font=self.f_normal).grid(row=0, column=0, sticky="w")
        self.entry_prog_path = Entry(self.run_param_frame, font=self.f_normal, width=40)
        self.entry_prog_path.grid(row=0, column=1, padx=5)
        Button(self.run_param_frame, text="浏览...", font=self.f_small,
               command=self._browse_program).grid(row=0, column=2)
        Label(self.run_param_frame, text="参数:", font=self.f_normal).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.entry_prog_args = Entry(self.run_param_frame, font=self.f_normal, width=40)
        self.entry_prog_args.grid(row=1, column=1, padx=5, pady=(5, 0), columnspan=2, sticky="w")

        # 播放声音参数
        self.sound_param_frame = Frame(self.param_frame)
        Label(self.sound_param_frame, text="声音文件:", font=self.f_normal).pack(side="left")
        self.entry_sound_path = Entry(self.sound_param_frame, font=self.f_normal, width=40)
        self.entry_sound_path.pack(side="left", padx=5)
        Button(self.sound_param_frame, text="浏览...", font=self.f_small,
               command=self._browse_sound).pack(side="left")

        # 消息文本参数
        self.msg_param_frame = Frame(self.param_frame)
        Label(self.msg_param_frame, text="消息内容:", font=self.f_normal).pack(anchor="w")
        self.text_msg = Text(self.msg_param_frame, width=50, height=3, font=self.f_normal)
        self.text_msg.pack(fill="x", pady=(3, 0))
        self.text_msg.insert("1.0", "定时任务已触发！")

        # 默认隐藏所有参数面板
        self.run_param_frame.pack_forget()
        self.sound_param_frame.pack_forget()
        self.msg_param_frame.pack_forget()
        # 根据初始操作（0=关机）不需要参数面板
        self.param_frame.pack_forget()

        # ---- 按钮行 ----
        btn_frame = Frame(main_frame)
        btn_frame.pack(fill="x", pady=(0, 10))

        self.btn_start = Button(btn_frame, text="开始定时器", font=self.f_normal,
                                width=16, command=self._on_start_timer)
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_pause = Button(btn_frame, text="暂停定时器", font=self.f_normal,
                                width=16, command=self._on_pause_timer,
                                state="disabled")
        self.btn_pause.pack(side="left", padx=(0, 8))

        self.btn_now = Button(btn_frame, text="立即执行选中的操作", font=self.f_normal,
                              width=22, command=self._on_execute_now)
        self.btn_now.pack(side="left", padx=(0, 8))

        self.btn_stop = Button(btn_frame, text="停止定时器", font=self.f_normal,
                               width=12, command=self._on_stop_timer,
                               state="disabled")
        self.btn_stop.pack(side="left")

        # ---- 倒计时显示 + 进度条 ----
        progress_frame = Frame(main_frame, relief="groove", bd=1, padx=8, pady=5)
        progress_frame.pack(fill="x")

        self.label_time_display = Label(progress_frame, text="准备就绪",
                                        font=self.f_timer, fg="#333333")
        self.label_time_display.pack(pady=(5, 5))

        # 进度条
        self.progress_var = IntVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                            length=600, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 5))

        self.label_progress_info = Label(progress_frame, text="",
                                         font=self.f_small, fg="#666666")
        self.label_progress_info.pack()

        # ---- 状态栏 ----
        self.status_bar = Label(main_frame, text="就绪", font=self.f_small,
                                bd=1, relief="sunken", anchor="w")
        self.status_bar.pack(fill="x", pady=(5, 0))

    # ============================================================
    # 事件处理
    # ============================================================

    def _on_action_changed(self):
        """操作选择改变时的处理"""
        action = self.action_var.get()
        # 隐藏所有参数面板
        self.run_param_frame.pack_forget()
        self.sound_param_frame.pack_forget()
        self.msg_param_frame.pack_forget()

        if action == 5:  # 运行程序
            self.param_frame.pack(fill="x", pady=(0, 10))
            self.run_param_frame.pack(fill="x")
        elif action == 6:  # 播放声音
            self.param_frame.pack(fill="x", pady=(0, 10))
            self.sound_param_frame.pack(fill="x")
        elif action == 7:  # 弹出消息
            self.param_frame.pack(fill="x", pady=(0, 10))
            self.msg_param_frame.pack(fill="x")
        else:
            self.param_frame.pack_forget()

    def _on_timer_mode_changed(self):
        """定时模式切换"""
        mode = self.timer_mode_var.get()
        self.countdown_frame.pack_forget()
        self.at_time_frame.pack_forget()
        self.periodic_frame.pack_forget()

        if mode == "countdown":
            self.countdown_frame.pack(fill="x")
        elif mode == "at_time":
            self.at_time_frame.pack(fill="x")
        elif mode == "periodic":
            self.periodic_frame.pack(fill="x")

    def _set_datetime_now(self):
        """设置日期时间为当前时间"""
        self.entry_datetime.delete(0, "end")
        self.entry_datetime.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _browse_program(self):
        """浏览选择可执行程序"""
        path = filedialog.askopenfilename(
            title="选择程序",
            filetypes=[("可执行文件", "*.exe;*.bat;*.cmd;*.com"),
                       ("所有文件", "*.*")]
        )
        if path:
            self.entry_prog_path.delete(0, "end")
            self.entry_prog_path.insert(0, path)

    def _browse_sound(self):
        """浏览选择声音文件"""
        path = filedialog.askopenfilename(
            title="选择声音文件",
            filetypes=[("音频文件", "*.wav;*.mp3;*.mid"),
                       ("所有文件", "*.*")]
        )
        if path:
            self.entry_sound_path.delete(0, "end")
            self.entry_sound_path.insert(0, path)

    # ============================================================
    # 定时器控制
    # ============================================================

    def _on_start_timer(self):
        """开始定时器"""
        try:
            if self.is_counting:
                messagebox.showinfo("提示", "定时器已在运行中")
                return

            mode = self.timer_mode_var.get()
            total_seconds = 0

            if mode == "countdown":
                # 读取倒计时时间
                days = int(self.spin_days.get())
                hours = int(self.spin_hours.get())
                mins = int(self.spin_mins.get())
                secs = int(self.spin_secs.get())
                total_seconds = days * 86400 + hours * 3600 + mins * 60 + secs
                if total_seconds <= 0:
                    messagebox.showwarning("提示", "请设置大于 0 的倒计时时间")
                    return

                self.timer.start_countdown(
                    total_seconds,
                    on_tick=self._on_timer_tick,
                    on_complete=self._on_timer_complete
                )

            elif mode == "at_time":
                # 解析日期时间
                dt_str = self.entry_datetime.get().strip()
                try:
                    target_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    messagebox.showwarning("提示", "日期时间格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式")
                    return
                if target_time <= datetime.now():
                    messagebox.showwarning("提示", "设定的时间已过，请设置未来时间")
                    return
                total_seconds = int((target_time - datetime.now()).total_seconds())

                self.timer.start_at_time(
                    target_time,
                    on_tick=self._on_timer_tick,
                    on_complete=self._on_timer_complete
                )

            elif mode == "periodic":
                # 读取时间段配置
                selected_days = {i for i, var in self.periodic_vars.items() if var.get()}
                if not selected_days:
                    messagebox.showwarning("提示", "请至少选择一天")
                    return
                hour = int(self.spin_p_hour.get())
                minute = int(self.spin_p_min.get())
                second = int(self.spin_p_sec.get())

                self.timer.start_periodic(
                    selected_days, hour, minute, second,
                    on_tick=self._on_timer_tick,
                    on_complete=self._on_timer_complete
                )
                total_seconds = self.timer.total

            # 更新界面状态
            self.is_counting = True
            self.is_paused = False
            self.btn_start.config(state="disabled")
            self.btn_pause.config(state="normal", text="暂停定时器")
            self.btn_stop.config(state="normal")
            self._set_time_display(self._format_time(total_seconds))
            self.label_progress_info.config(text="")
            self.progress_var.set(0)
            self.status_bar.config(text=f"定时器已启动（{self._get_mode_text(mode)}）")

            # 更新托盘提示
            if self.tray:
                self.tray.update_tooltip(f"⏳ {self._format_time(total_seconds)}")

        except Exception as e:
            import traceback
            err_msg = f"启动定时器异常: {e}\n\n{traceback.format_exc()}"
            exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(exe_dir, "_error.log")
            try:
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.write(err_msg)
            except Exception:
                pass
            messagebox.showerror("启动失败", f"启动定时器出错: {e}\n日志: {log_path}")

    def _on_pause_timer(self):
        """暂停/恢复定时器"""
        try:
            if not self.is_counting:
                return
            if self.is_paused:
                # 恢复
                self.timer.resume()
                self.is_paused = False
                self.btn_pause.config(text="暂停定时器")
                self.status_bar.config(text="定时器已恢复")
            else:
                # 暂停
                self.timer.pause()
                self.is_paused = True
                self.btn_pause.config(text="继续定时器")
                self.status_bar.config(text="定时器已暂停")
        except Exception as e:
            messagebox.showerror("操作失败", f"暂停/恢复定时器出错: {e}")

    def _on_stop_timer(self):
        """停止定时器"""
        try:
            if not self.is_counting:
                return
            self.timer.stop()
            self.is_counting = False
            self.is_paused = False
            self._reset_timer_ui()
            self.status_bar.config(text="定时器已停止")
            if self.tray:
                self.tray.update_tooltip("自动关机工具 v1.0")
        except Exception as e:
            messagebox.showerror("操作失败", f"停止定时器出错: {e}")

    def _on_timer_tick(self, remaining, total):
        """每秒定时器回调（在后台线程中调用）"""
        try:
            self.root.after(0, self._update_ui_tick, remaining, total)
        except Exception:
            pass

    def _update_ui_tick(self, remaining, total):
        """更新界面（主线程）"""
        try:
            if not self.is_counting and not self._closing:
                return

            # 更新剩余时间显示
            time_str = self._format_time(remaining)
            self._set_time_display(time_str)

            # 更新进度条
            if total > 0:
                pct = int((total - remaining) / total * 100)
                self.progress_var.set(pct)

            # 更新进度信息文字
            mode = self.config.get('General', 'progress_mode', 'percent')
            if mode == 'percent':
                pct = int((total - remaining) / total * 100) if total > 0 else 0
                self.label_progress_info.config(text=f"已完成 {pct}%")
            elif mode == 'timeleft':
                self.label_progress_info.config(text=f"剩余 {time_str}")
            else:
                self.label_progress_info.config(text="")

            # 更新托盘提示
            if self.tray:
                self.tray.update_tooltip(f"⏳ {time_str}")

            # 最后10秒播放音效
            if remaining <= 10 and remaining > 0:
                play_sound = self.config.getboolean('General', 'play_sound_last_10s', True)
                if play_sound:
                    try:
                        winsound.Beep(800, 100)
                    except Exception:
                        pass

        except Exception as e:
            # 不阻塞定时器，静默记录
            import traceback
            err_msg = f"UI更新异常: {e}\n\n{traceback.format_exc()}"
            exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(exe_dir, "_error.log")
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(err_msg + "\n")
            except Exception:
                pass

    def _on_timer_complete(self):
        """定时器完成回调（后台线程）"""
        try:
            self.root.after(0, self._execute_timer_action)
        except Exception:
            pass

    def _execute_timer_action(self):
        """执行定时器触发的操作"""
        try:
            self.is_counting = False
            self.is_paused = False
            self._reset_timer_ui()

            action = self.action_var.get()
            action_name = get_action_name(action)

            self.status_bar.config(text=f"正在执行: {action_name}")

            # 检查是否需要在执行前询问
            ask = self.config.getboolean('General', 'ask_before_execute', True)
            critical_only = self.config.getboolean('General', 'ask_critical_only', False)

            should_ask = False
            if ask:
                if critical_only:
                    should_ask = (action in CRITICAL_ACTIONS)
                else:
                    should_ask = True

            if should_ask:
                result = messagebox.askyesno(
                    "确认操作",
                    f"定时器已触发！\n即将执行以下操作：\n\n【{action_name}】\n\n是否继续？"
                )
                if not result:
                    self.status_bar.config(text="操作已取消")
                    if self.tray:
                        self.tray.update_tooltip("自动关机工具 v1.0")
                    return

            # 收集操作参数
            params = None
            if action == 5:  # 运行程序
                params = {
                    'path': self.entry_prog_path.get(),
                    'args': self.entry_prog_args.get()
                }
            elif action == 6:  # 播放声音
                params = self.entry_sound_path.get()
            elif action == 7:  # 弹出消息
                params = self.text_msg.get("1.0", "end-1c")

            # 执行操作
            success = execute_action(action, params)

            if success:
                self.status_bar.config(text=f"操作完成: {action_name}")
            else:
                self.status_bar.config(text=f"操作失败: {action_name}")

            # 更新托盘
            if self.tray:
                self.tray.update_tooltip("自动关机工具 v1.0")

        except Exception as e:
            import traceback
            err_msg = f"执行定时操作异常: {e}\n\n{traceback.format_exc()}"
            exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(exe_dir, "_error.log")
            try:
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.write(err_msg)
            except Exception:
                pass
            messagebox.showerror("执行失败", f"操作出错: {e}\n日志: {log_path}")
            self.status_bar.config(text="执行失败")

    def _on_execute_now(self):
        """立即执行选中的操作"""
        try:
            action = self.action_var.get()
            action_name = get_action_name(action)

            # 如果有定时器正在运行，先停止
            if self.is_counting:
                self.timer.stop()
                self.is_counting = False
                self.is_paused = False
                self._reset_timer_ui()

            self.status_bar.config(text=f"立即执行: {action_name}")

            # 收集参数
            params = None
            if action == 5:
                params = {
                    'path': self.entry_prog_path.get(),
                    'args': self.entry_prog_args.get()
                }
            elif action == 6:
                params = self.entry_sound_path.get()
            elif action == 7:
                params = self.text_msg.get("1.0", "end-1c")

            success = execute_action(action, params)

            if success:
                self.status_bar.config(text=f"操作已执行: {action_name}")
            else:
                self.status_bar.config(text=f"操作失败: {action_name}")

        except Exception as e:
            import traceback
            err_msg = f"立即执行异常: {e}\n\n{traceback.format_exc()}"
            exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(exe_dir, "_error.log")
            try:
                with open(log_path, 'w', encoding='utf-8') as f:
                    f.write(err_msg)
            except Exception:
                pass
            messagebox.showerror("执行失败", f"操作出错: {e}\n日志: {log_path}")
            self.status_bar.config(text="执行失败")

    # ============================================================
    # 设置对话框
    # ============================================================

    def _show_settings(self):
        """显示设置对话框"""
        dialog = Toplevel(self.root)
        dialog.title("设置")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 加载当前设置
        ask_exec = BooleanVar(value=self.config.getboolean('General', 'ask_before_execute', True))
        ask_crit = BooleanVar(value=self.config.getboolean('General', 'ask_critical_only', False))
        ask_close = BooleanVar(value=self.config.getboolean('General', 'ask_before_close', True))
        force_close = BooleanVar(value=self.config.getboolean('General', 'force_close', False))
        play_sound = BooleanVar(value=self.config.getboolean('General', 'play_sound_last_10s', True))
        min_tray = BooleanVar(value=self.config.getboolean('General', 'minimize_to_tray', True))
        esc_min = BooleanVar(value=self.config.getboolean('General', 'esc_minimize', True))
        prog_mode = StringVar(value=self.config.get('General', 'progress_mode', 'percent'))

        main = Frame(dialog, padx=15, pady=15)
        main.pack(fill="both", expand=True)

        # 通用设置
        Label(main, text="通用设置", font=self.f_title).pack(anchor="w", pady=(0, 5))
        Checkbutton(main, text="执行操作前弹出窗口询问", variable=ask_exec,
                    font=self.f_normal).pack(anchor="w")
        Checkbutton(main, text="仅询问关键操作（关机/重启/注销）", variable=ask_crit,
                    font=self.f_normal).pack(anchor="w", padx=(20, 0))
        Checkbutton(main, text="关闭前询问确认", variable=ask_close,
                    font=self.f_normal).pack(anchor="w")
        Checkbutton(main, text="强制关闭阻止的应用程序（关机/重启时）", variable=force_close,
                    font=self.f_normal).pack(anchor="w")

        Label(main, text="", font=self.f_small).pack()  # 间隔

        # 界面设置
        Label(main, text="界面设置", font=self.f_title).pack(anchor="w", pady=(0, 5))
        Checkbutton(main, text="最小化到系统托盘", variable=min_tray,
                    font=self.f_normal).pack(anchor="w")
        Checkbutton(main, text="按下 ESC 键最小化到托盘", variable=esc_min,
                    font=self.f_normal).pack(anchor="w")

        Label(main, text="进度条显示模式:", font=self.f_normal).pack(anchor="w", pady=(3, 0))
        mode_frame = Frame(main)
        mode_frame.pack(anchor="w", padx=(20, 0))
        Radiobutton(mode_frame, text="不显示", variable=prog_mode, value="none",
                    font=self.f_normal).pack(side="left")
        Radiobutton(mode_frame, text="百分比", variable=prog_mode, value="percent",
                    font=self.f_normal).pack(side="left", padx=10)
        Radiobutton(mode_frame, text="剩余时间", variable=prog_mode, value="timeleft",
                    font=self.f_normal).pack(side="left")

        Label(main, text="", font=self.f_small).pack()

        # 其他设置
        Label(main, text="其他设置", font=self.f_title).pack(anchor="w", pady=(0, 5))
        Checkbutton(main, text="最后 10 秒播放提示音效", variable=play_sound,
                    font=self.f_normal).pack(anchor="w")

        # 按钮
        btn_frame = Frame(main)
        btn_frame.pack(fill="x", pady=(15, 0))

        def _save_settings():
            self.config.setboolean('General', 'ask_before_execute', ask_exec.get())
            self.config.setboolean('General', 'ask_critical_only', ask_crit.get())
            self.config.setboolean('General', 'ask_before_close', ask_close.get())
            self.config.setboolean('General', 'force_close', force_close.get())
            self.config.setboolean('General', 'play_sound_last_10s', play_sound.get())
            self.config.setboolean('General', 'minimize_to_tray', min_tray.get())
            self.config.setboolean('General', 'esc_minimize', esc_min.get())
            self.config.set('General', 'progress_mode', prog_mode.get())
            self.config.save()
            dialog.destroy()
            messagebox.showinfo("设置", "设置已保存")

        Button(btn_frame, text="保存", font=self.f_normal, width=10,
               command=_save_settings).pack(side="right", padx=(5, 0))
        Button(btn_frame, text="取消", font=self.f_normal, width=10,
               command=dialog.destroy).pack(side="right")

        # 居中显示
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def _show_about(self):
        """显示关于对话框"""
        messagebox.showinfo(
            "关于 自动关机工具",
            "自动关机工具 v1.0\n\n"
            "基于 Python + tkinter 复刻\n"
            "原版: PShutDown v1.2.3\n\n"
            "功能:\n"
            "• 8 种可执行操作\n"
            "• 3 种定时模式\n"
            "• 完整的 CLI 命令行支持"
        )

    # ============================================================
    # 系统托盘
    # ============================================================

    def _setup_tray(self):
        """初始化系统托盘"""
        try:
            self.tray = SystemTray(
                self.root,
                on_show=self._tray_on_show,
                on_exit=self._tray_on_exit
            )
            # 首次不安装，等最小化时才安装
        except Exception as e:
            print(f"托盘初始化失败: {e}", file=sys.stderr)
            self.tray = None

    def _tray_on_show(self):
        """托盘菜单：显示窗口"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.state("normal")

    def _tray_on_exit(self):
        """托盘菜单：退出"""
        self._closing = True
        if self.is_counting:
            self.timer.stop()
        if self.tray:
            self.tray.remove()
        # 保存窗口位置
        self._save_geometry()
        self.root.destroy()

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        if self.config.getboolean('General', 'minimize_to_tray', True) and self.tray:
            self.root.withdraw()
            if not self.tray._icon_added:
                self.tray.install()
            # 更新托盘提示
            if self.is_counting and not self.is_paused:
                self.tray.update_tooltip(f"⏳ {self._format_time(self.timer.remaining)}")
            else:
                self.tray.update_tooltip("自动关机工具 v1.0")
        else:
            self.root.iconify()

    # ============================================================
    # 窗口事件
    # ============================================================

    def _bind_events(self):
        """绑定键盘和窗口事件"""
        # ESC 最小化
        self.root.bind("<Escape>", lambda e: self._on_esc())

        # 窗口状态变化
        self.root.bind("<Unmap>", self._on_window_unmap)

        # 窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_esc(self):
        """ESC 键处理"""
        if self.config.getboolean('General', 'esc_minimize', True):
            self._minimize_to_tray()

    def _on_window_unmap(self, event):
        """窗口被隐藏/最小化"""
        # 只有真正的 iconify 才触发最小化到托盘
        if event.widget == self.root:
            try:
                state = self.root.state()
                if state == "iconic":
                    self._minimize_to_tray()
            except Exception:
                pass

    def _on_closing(self):
        """关闭窗口"""
        if self.is_counting:
            # 如果定时器正在运行，先停止
            result = messagebox.askyesno("确认关闭",
                                         "定时器正在运行中，关闭后将停止。\n确定要关闭吗？")
            if not result:
                return
            self.timer.stop()
            self.is_counting = False

        if self.config.getboolean('General', 'ask_before_close', True):
            result = messagebox.askyesno("确认关闭", "确定要退出自动关机工具吗？")
            if not result:
                return

        self._closing = True
        if self.tray:
            self.tray.remove()
        self._save_geometry()
        self.root.destroy()

    # ============================================================
    # 窗口位置持久化
    # ============================================================

    def _restore_geometry(self):
        """恢复上次的窗口位置"""
        try:
            geo = self.config.get('Window', 'geometry', '')
            if geo:
                self.root.geometry(geo)
        except Exception:
            pass

    def _save_geometry(self):
        """保存窗口位置"""
        try:
            geo = self.root.geometry()
            self.config.set('Window', 'geometry', geo)
            self.config.save()
        except Exception:
            pass

    # ============================================================
    # 辅助方法
    # ============================================================

    def _format_time(self, total_seconds):
        """将秒数格式化为 天 时 分 秒 字符串"""
        total_seconds = int(total_seconds)
        if total_seconds < 0:
            total_seconds = 0
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        mins = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if days > 0:
            return f"{days}天 {hours:02d}:{mins:02d}:{secs:02d}"
        elif hours > 0:
            return f"{hours:02d}:{mins:02d}:{secs:02d}"
        else:
            return f"{mins:02d}:{secs:02d}"

    def _set_time_display(self, text):
        """设置时间显示标签"""
        self.label_time_display.config(text=text)

    def _reset_timer_ui(self):
        """重置定时器界面状态"""
        self.btn_start.config(state="normal")
        self.btn_pause.config(state="disabled", text="暂停定时器")
        self.btn_stop.config(state="disabled")
        self._set_time_display("准备就绪")
        self.progress_var.set(0)
        self.label_progress_info.config(text="")

    def _get_mode_text(self, mode):
        """获取定时模式的中文名称"""
        modes = {
            "countdown": "倒计时",
            "at_time": "日期定时",
            "periodic": "时间段"
        }
        return modes.get(mode, mode)

    # ============================================================
    # 启动
    # ============================================================

    def run(self):
        """启动主窗口"""
        self.root.mainloop()
