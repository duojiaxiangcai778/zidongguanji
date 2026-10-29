"""
自动关机工具 — 新版图形界面（重写版）

设计原则（针对旧版问题）：
1. 【不卡】不用 customtkinter（每个控件都是 Canvas，重绘开销大），
   改用 tkinter 原生控件 + 轻量自绘，启动与渲染都更快。
2. 【输入不丢】所有输入框绑定 StringVar，程序**永不写入**，
   唯一的刷新循环只更新"倒计时"这一个 Label —— 这是旧版文字消失的根因。
3. 【好看】深色卡片式布局，无系统灰边框，主按钮高对比。
4. 【好用】三种定时模式同屏切换、8 种操作可点选、参数按需出现。

运行：python gui.py
"""

import os
import sys
import time
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.timer_engine import TimerEngine
from core import actions
from core.config import Config

# ---------------- 主题 ----------------

C_BG = "#14161a"
C_CARD = "#1c1f26"
C_CARD2 = "#22262f"
C_LINE = "#2b303a"
C_TEXT = "#e9edf5"
C_DIM = "#8b94a3"
C_ACCENT = "#3b82f6"
C_ACCENT_DIM = "#1e3a5f"
C_DANGER = "#ef4444"
C_OK = "#22c55e"
C_WARN = "#f59e0b"

FONT = "Microsoft YaHei UI"

ACTIONS = [
    ("shutdown", "关闭电脑", "执行 shutdown /s"),
    ("restart", "重启电脑", "执行 shutdown /r"),
    ("logoff", "注销电脑", "注销当前用户"),
    ("sleep", "睡眠模式", "进入系统睡眠"),
    ("monitor_off", "关闭显示器", "仅关屏，不锁系统"),
    ("run_program", "运行程序", "启动指定 exe / 文件"),
    ("play_sound", "播放声音", "播放指定音频文件"),
    ("message", "弹出消息", "显示一个提示对话框"),
]

WEEK_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class RoundedCard(tk.Frame):
    """带细边框的卡片容器（比 Canvas 圆角轻，性能好）"""

    def __init__(self, master, **kw):
        super().__init__(master, bg=C_CARD, highlightbackground=C_LINE,
                         highlightthickness=1, bd=0, **kw)


class SegmentButton(tk.Label):
    """分段选择按钮（替代 ttk.Notebook，轻量且好看）"""

    def __init__(self, master, text, command, var_getter, value):
        super().__init__(master, text=text, font=(FONT, 10), padx=14, pady=7,
                         cursor="hand2", bd=0)
        self.command = command
        self.var_getter = var_getter
        self.value = value
        self.bind("<Button-1>", self._click)
        self.refresh()

    def _click(self, _e):
        self.command(self.value)

    def refresh(self):
        active = self.var_getter() == self.value
        self.configure(
            bg=C_ACCENT if active else C_CARD2,
            fg="#ffffff" if active else C_DIM,
        )


class ActionRow(tk.Frame):
    """可点击的操作行（替代 Radiobutton，深色下更好看、更好点）"""

    def __init__(self, master, key, name, desc, on_select, var_getter):
        super().__init__(master, bg=C_CARD2, cursor="hand2", bd=0,
                         highlightthickness=1, highlightbackground=C_CARD2)
        self.key = key
        self.on_select = on_select
        self.var_getter = var_getter

        self.bar = tk.Frame(self, bg=C_CARD2, width=4)
        self.bar.pack(side="left", fill="y")

        self.body = tk.Frame(self, bg=C_CARD2)
        self.body.pack(side="left", fill="both", expand=True, padx=(8, 6), pady=4)

        self.lbl = tk.Label(self.body, text=name, font=(FONT, 10, "bold"),
                            bg=C_CARD2, fg=C_TEXT, anchor="w")
        self.lbl.pack(fill="x")

        self.sub = tk.Label(self.body, text=desc, font=(FONT, 8),
                            bg=C_CARD2, fg=C_DIM, anchor="w")
        self.sub.pack(fill="x")

        for w in (self, self.bar, self.body, self.lbl, self.sub):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

        self.refresh()

    def _click(self, _e):
        self.on_select(self.key)

    def _enter(self, _e):
        if self.var_getter() != self.key:
            self._paint(C_CARD, C_TEXT)

    def _leave(self, _e):
        self.refresh()

    def _paint(self, bg, fg):
        for w in (self, self.bar, self.body):
            w.configure(bg=bg)
        self.lbl.configure(bg=bg, fg=fg)
        self.sub.configure(bg=bg)

    def refresh(self):
        active = self.var_getter() == self.key
        bg = C_ACCENT_DIM if active else C_CARD2
        self._paint(bg, C_TEXT if active else C_TEXT)
        self.bar.configure(bg=C_ACCENT if active else C_CARD2)
        self.sub.configure(fg=C_ACCENT if active else C_DIM)
        self.configure(highlightbackground=C_ACCENT if active else C_CARD2)


class WeekChip(tk.Label):
    def __init__(self, master, idx, on_toggle, is_on):
        super().__init__(master, text=WEEK_NAMES[idx], font=(FONT, 9), padx=9, pady=5,
                         cursor="hand2")
        self.idx = idx
        self.on_toggle = on_toggle
        self.is_on = is_on
        self.bind("<Button-1>", self._click)
        self.refresh()

    def _click(self, _e):
        self.is_on = not self.is_on
        self.refresh()
        self.on_toggle()

    def refresh(self):
        self.configure(bg=C_ACCENT if self.is_on else C_CARD2,
                       fg="#fff" if self.is_on else C_DIM)


class ShutdownApp:
    def __init__(self, root):
        self.root = root
        self.cfg = Config(os.path.join(BASE_DIR, "settings.ini"))
        self.engine = TimerEngine()
        actions.set_main_tk(root)

        self.mode = tk.StringVar(value="countdown")     # countdown / at_time / periodic
        self.action_key = tk.StringVar(value="shutdown")
        self.week_flags = [False] * 7
        self.running = False
        self.finished = False

        self._build()
        self._load_prefs()
        self.root.after(200, self._tick)

    # ---------------- 布局 ----------------

    def _build(self):
        root = self.root
        root.title("定时关机")
        root.configure(bg=C_BG)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        root.geometry("1100x920+150+15")
        root.minsize(1040, 880)

        # 顶部状态条
        top = tk.Frame(root, bg=C_BG)
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(8, 0))

        self.dot = tk.Canvas(top, width=10, height=10, bg=C_BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=C_DIM, outline="")
        self.dot.pack(side="left", pady=4)

        self.status = tk.Label(top, text="准备就绪", font=(FONT, 10),
                               bg=C_BG, fg=C_DIM, anchor="w")
        self.status.pack(side="left", padx=8)

        self.target_lbl = tk.Label(top, text="", font=(FONT, 9),
                                   bg=C_BG, fg=C_DIM, anchor="e")
        self.target_lbl.pack(side="right")

        # 大倒计时
        clockwrap = tk.Frame(root, bg=C_BG)
        clockwrap.grid(row=1, column=0, sticky="ew")
        self.clock = tk.Label(clockwrap, text="00:00:00", font=(FONT, 30, "bold"),
                              bg=C_BG, fg=C_TEXT)
        self.clock.pack(pady=(2, 2))

        self.progress = tk.Canvas(root, height=4, bg=C_BG, highlightthickness=0)
        self.progress.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))

        # 操作条（放顶部，永远不会被内容挤掉）
        bar = tk.Frame(root, bg=C_BG)
        bar.grid(row=3, column=0, sticky="ew", padx=20, pady=(2, 8))
        self.btn_start = tk.Button(bar, text="开始", font=(FONT, 11, "bold"),
                                   bg=C_ACCENT, fg="#fff", activebackground="#2f6fd8",
                                   activeforeground="#fff", relief="flat", bd=0,
                                   cursor="hand2", command=self.start)
        self.btn_start.pack(side="left", ipadx=34, ipady=7)
        self.btn_stop = tk.Button(bar, text="停止", font=(FONT, 11),
                                  bg=C_CARD2, fg=C_DIM, activebackground=C_CARD,
                                  activeforeground=C_TEXT, relief="flat", bd=0,
                                  cursor="hand2", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=10, ipadx=26, ipady=7)
        self.hint = tk.Label(bar, text="", font=(FONT, 9), bg=C_BG, fg=C_DIM)
        self.hint.pack(side="right")
        self.progress_bar = self.progress.create_rectangle(0, 0, 0, 4, fill=C_ACCENT, outline="")

        # 主体：单列垂直
        body = tk.Frame(root, bg=C_BG)
        body.grid(row=4, column=0, sticky="nsew", padx=20)

        self._build_time_panel(body)
        self._build_action_panel(body)


    def _build_time_panel(self, parent):
        card = RoundedCard(parent)
        card.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(card, bg=C_CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        tk.Label(inner, text="触发时间", font=(FONT, 11, "bold"),
                 bg=C_CARD, fg=C_TEXT, anchor="w").pack(fill="x")

        # 分段按钮
        seg = tk.Frame(inner, bg=C_CARD)
        seg.pack(fill="x", pady=(10, 14))

        self.seg_btns = {}
        for key, text in (("countdown", "倒计时"), ("at_time", "指定时间"), ("periodic", "每周循环")):
            btn = SegmentButton(seg, text, self.set_mode, lambda: self.mode.get(), key)
            btn.pack(side="left", padx=(0, 6))
            self.seg_btns[key] = btn

        # 三种模式的面板容器
        self.mode_host = tk.Frame(inner, bg=C_CARD)
        self.mode_host.pack(fill="both", expand=True)

        # 1) 倒计时
        self.pane_countdown = tk.Frame(self.mode_host, bg=C_CARD)
        row = tk.Frame(self.pane_countdown, bg=C_CARD)
        row.pack(anchor="w")
        self.hh = self._num_field(row, "时", "0")
        self.mm = self._num_field(row, "分", "0")
        self.ss = self._num_field(row, "秒", "30")
        tk.Label(self.pane_countdown, text="到点后执行选定的操作",
                 font=(FONT, 8), bg=C_CARD, fg=C_DIM, anchor="w").pack(fill="x", pady=(8, 0))

        # 2) 指定时间
        self.pane_at = tk.Frame(self.mode_host, bg=C_CARD)
        now = datetime.datetime.now()
        drow = tk.Frame(self.pane_at, bg=C_CARD)
        drow.pack(anchor="w", pady=(0, 8))
        self.dy = self._num_field(drow, "年", str(now.year), width=6, suffix="")
        self.dm = self._num_field(drow, "月", str(now.month), width=4, suffix="")
        self.dd = self._num_field(drow, "日", str(now.day), width=4, suffix="")
        trow = tk.Frame(self.pane_at, bg=C_CARD)
        trow.pack(anchor="w")
        self.th = self._num_field(trow, "时", "23", width=4)
        self.tmin = self._num_field(trow, "分", "00", width=4)
        self.ts = self._num_field(trow, "秒", "00", width=4)
        tk.Label(self.pane_at, text="按电脑本地时间执行",
                 font=(FONT, 8), bg=C_CARD, fg=C_DIM, anchor="w").pack(fill="x", pady=(8, 0))

        # 3) 每周循环
        self.pane_week = tk.Frame(self.mode_host, bg=C_CARD)
        wrow = tk.Frame(self.pane_week, bg=C_CARD)
        wrow.pack(anchor="w")
        self.week_chips = []
        for i in range(7):
            chip = WeekChip(wrow, i, self._on_week_toggle, self.week_flags[i])
            chip.pack(side="left", padx=(0, 4))
            self.week_chips.append(chip)
        tk.Label(self.pane_week, text="选中的星期几，到点循环执行",
                 font=(FONT, 8), bg=C_CARD, fg=C_DIM, anchor="w").pack(fill="x", pady=(8, 0))
        prow = tk.Frame(self.pane_week, bg=C_CARD)
        prow.pack(anchor="w", pady=(6, 0))
        self.ph = self._num_field(prow, "时", "23", width=4)
        self.pmin = self._num_field(prow, "分", "00", width=4)
        self.ps = self._num_field(prow, "秒", "00", width=4)

        self.set_mode("countdown")

    def _num_field(self, parent, label, default, width=4, suffix=None):
        box = tk.Frame(parent, bg=C_CARD)
        box.pack(side="left", padx=(0, 10))

        wrap = tk.Frame(box, bg=C_CARD2, highlightthickness=1,
                        highlightbackground=C_LINE)
        wrap.pack()

        var = tk.StringVar(value=default)
        e = tk.Entry(wrap, textvariable=var, width=width, font=(FONT, 12),
                     bg=C_CARD2, fg=C_TEXT, insertbackground=C_ACCENT,
                     relief="flat", bd=0, justify="center")
        e.pack(padx=8, pady=6)

        tk.Label(box, text=label, font=(FONT, 8), bg=C_CARD, fg=C_DIM).pack()
        # 只保存引用，程序永不 set 这个 var
        box.var = var
        box.entry = e
        return box

    def _build_action_panel(self, parent):
        card = RoundedCard(parent)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=C_CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        tk.Label(inner, text="执行操作", font=(FONT, 11, "bold"),
                 bg=C_CARD, fg=C_TEXT, anchor="w").pack(fill="x", pady=(0, 10))

        grid = tk.Frame(inner, bg=C_CARD)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self.action_rows = {}
        for idx, (key, name, desc) in enumerate(ACTIONS):
            row = ActionRow(grid, key, name, desc, self.set_action,
                            lambda: self.action_key.get())
            row.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 4) if idx % 2 == 0 else (4, 0), pady=3)
            self.action_rows[key] = row

        # 参数区（按操作显示）
        self.param_box = tk.Frame(inner, bg=C_CARD)
        self.param_box.pack(fill="x", pady=(10, 0))

        self.param_label = tk.Label(self.param_box, text="", font=(FONT, 9),
                                    bg=C_CARD, fg=C_DIM, anchor="w")
        prow = tk.Frame(self.param_box, bg=C_CARD)
        self.param_entry_var = tk.StringVar()
        self.param_entry = tk.Entry(prow, textvariable=self.param_entry_var,
                                    font=(FONT, 9), bg=C_CARD2, fg=C_TEXT,
                                    insertbackground=C_ACCENT, relief="flat", bd=0)
        self.param_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        self.btn_browse = tk.Button(prow, text="浏览", font=(FONT, 9),
                                    bg=C_CARD2, fg=C_DIM, relief="flat", bd=0,
                                    cursor="hand2", command=self._browse)
        self.btn_browse.pack(side="right", ipadx=10, ipady=5)
        self.prow = prow
        self.set_action("shutdown")

    # ---------------- 交互 ----------------

    def set_mode(self, mode):
        self.mode.set(mode)
        for k, b in self.seg_btns.items():
            b.refresh()
        for p in (self.pane_countdown, self.pane_at, self.pane_week):
            p.pack_forget()
        {"countdown": self.pane_countdown,
         "at_time": self.pane_at,
         "periodic": self.pane_week}[mode].pack(fill="both", expand=True)

    def _on_week_toggle(self):
        self.week_flags = [c.is_on for c in self.week_chips]

    def set_action(self, key):
        self.action_key.set(key)
        for k, row in self.action_rows.items():
            row.refresh()

        needs = {"run_program": "程序路径", "play_sound": "音频文件", "message": "消息内容"}
        if key in needs:
            self.param_label.configure(text=needs[key])
            self.param_label.pack(fill="x", pady=(0, 4))
            self.prow.pack(fill="x")
            self.btn_browse.configure(state="normal" if key != "message" else "disabled")
        else:
            self.param_label.pack_forget()
            self.prow.pack_forget()

    def _browse(self):
        key = self.action_key.get()
        if key == "run_program":
            p = filedialog.askopenfilename(title="选择要运行的程序",
                                          filetypes=[("程序", "*.exe"), ("所有文件", "*.*")])
        elif key == "play_sound":
            p = filedialog.askopenfilename(title="选择音频文件",
                                          filetypes=[("音频", "*.wav *.mp3 *.wma"), ("所有文件", "*.*")])
        else:
            return
        if p:
            self.param_entry_var.set(p)  # 只在用户点浏览时写入，可接受

    # ---------------- 定时计算 ----------------

    def _read_int(self, box, default=0, lo=0, hi=None):
        try:
            v = int(str(box.var.get()).strip() or default)
        except Exception:
            v = default
        if v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        return v

    def _compute_target(self):
        mode = self.mode.get()
        if mode == "countdown":
            secs = (self._read_int(self.hh, 0, 0, 999) * 3600
                    + self._read_int(self.mm, 0, 0, 59) * 60
                    + self._read_int(self.ss, 0, 0, 59))
            return ("countdown", secs)

        if mode == "at_time":
            y = self._read_int(self.dy, 0, 2000, 2100)
            mo = self._read_int(self.dm, 1, 1, 12)
            d = self._read_int(self.dd, 1, 1, 31)
            h = self._read_int(self.th, 0, 0, 23)
            mi = self._read_int(self.tmin, 0, 0, 59)
            s = self._read_int(self.ts, 0, 0, 59)
            try:
                dt = datetime.datetime(y, mo, d, h, mi, s)
            except ValueError as e:
                raise ValueError("日期或时间不合法：" + str(e))
            return ("at", dt)

        # periodic
        hour = self._read_int(self.ph, 0, 0, 23)
        minute = self._read_int(self.pmin, 0, 0, 59)
        second = self._read_int(self.ps, 0, 0, 59)
        days = [i for i, f in enumerate(self.week_flags) if f]
        if not days:
            raise ValueError("请至少选择一个星期")
        return ("periodic", (days, hour, minute, second))

    # ---------------- 启动 / 停止 ----------------

    def start(self):
        if self.running:
            return
        try:
            kind, payload = self._compute_target()
        except ValueError as e:
            messagebox.showerror("设置有问题", str(e))
            return

        key = self.action_key.get()
        params = None
        if key == "run_program":
            params = self.param_entry_var.get().strip()
            if not params or not os.path.exists(params):
                messagebox.showerror("设置有问题", "请选择一个存在的程序路径")
                return
        elif key == "play_sound":
            params = self.param_entry_var.get().strip()
            if not params or not os.path.exists(params):
                messagebox.showerror("设置有问题", "请选择一个存在的音频文件")
                return
        elif key == "message":
            params = self.param_entry_var.get().strip()
            if not params:
                messagebox.showerror("设置有问题", "请填写要弹出的消息内容")
                return

        self.running = True
        self.finished = False
        self._set_running_ui(True)

        on_done = lambda: self.root.after(0, self._on_complete, key, params)

        if kind == "countdown":
            if payload <= 0:
                messagebox.showerror("设置有问题", "倒计时必须大于 0 秒")
                self._set_running_ui(False)
                self.running = False
                return
            self.engine.start_countdown(payload, on_tick=None, on_complete=on_done)
            self.target_lbl.configure(
                text="目标 " + (datetime.datetime.now() + datetime.timedelta(seconds=payload)).strftime("%m-%d %H:%M:%S"))
        elif kind == "at":
            if payload <= datetime.datetime.now():
                messagebox.showerror("设置有问题", "指定时间已经过去了")
                self._set_running_ui(False)
                self.running = False
                return
            self.engine.start_at_time(payload, on_tick=None, on_complete=on_done)
            self.target_lbl.configure(text="目标 " + payload.strftime("%m-%d %H:%M:%S"))
        else:
            days, h, m, s = payload
            self.engine.start_periodic(days, h, m, s, on_tick=None, on_complete=on_done)
            names = "、".join(WEEK_NAMES[d] for d in days)
            self.target_lbl.configure(text=f"每周 {names} {h:02d}:{m:02d}:{s:02d}")

        self._save_prefs()
        self._flash_status("已开始计时", C_OK)

    def stop(self):
        if not self.running:
            return
        self.engine.stop()
        self.running = False
        self.finished = False
        self._set_running_ui(False)
        self.clock.configure(text="00:00:00", fg=C_TEXT)
        self.progress.coords(self.progress_bar, 0, 0, 0, 4)
        self.target_lbl.configure(text="")
        self._flash_status("已停止", C_WARN)

    def _on_complete(self, key, params):
        self.running = False
        self.finished = True
        self._set_running_ui(False)
        self.clock.configure(text="00:00:00", fg=C_OK)
        self._flash_status("已执行：" + actions.get_action_name(
            [k for k, _, _ in ACTIONS].index(key)), C_OK)
        try:
            actions.execute_action([k for k, _, _ in ACTIONS].index(key), params)
        except Exception as e:
            messagebox.showerror("执行失败", str(e))

    def _set_running_ui(self, running):
        self.btn_start.configure(state="disabled" if running else "normal",
                                 bg=C_ACCENT_DIM if running else C_ACCENT)
        self.btn_stop.configure(state="normal" if running else "disabled",
                                fg=C_TEXT if running else C_DIM)

    def _flash_status(self, text, color):
        self.status.configure(text=text, fg=color)
        self.dot.itemconfigure(self.dot_id, fill=color)

    # ---------------- 刷新循环（只碰倒计时这一个 Label） ----------------

    def _tick(self):
        try:
            if self.running:
                remain = self.engine.remaining() or 0
                total = self.engine.total() or 0
                remain = max(0, int(remain))
                h, r = divmod(remain, 3600)
                m, s = divmod(r, 60)
                self.clock.configure(
                    text=f"{h:02d}:{m:02d}:{s:02d}",
                    fg=C_DANGER if remain <= 10 else C_TEXT,
                )
                w = self.progress.winfo_width()
                if total > 0 and w > 1:
                    done = max(0.0, min(1.0, 1 - remain / total))
                    self.progress.coords(self.progress_bar, 0, 0, int(w * done), 4)
                if self.engine.is_paused():
                    self.status.configure(text="已暂停", fg=C_WARN)
        except Exception:
            pass
        self.root.after(500, self._tick)

    # ---------------- 设置持久化 ----------------

    def _save_prefs(self):
        try:
            self.cfg.set("ui", "mode", self.mode.get())
            self.cfg.set("ui", "action", self.action_key.get())
            self.cfg.set("ui", "weeks", ",".join("1" if f else "0" for f in self.week_flags))
            self.cfg.save()
        except Exception:
            pass

    def _load_prefs(self):
        try:
            mode = self.cfg.get("ui", "mode", "countdown")
            if mode in ("countdown", "at_time", "periodic"):
                self.set_mode(mode)
            key = self.cfg.get("ui", "action", "shutdown")
            if key in [k for k, _, _ in ACTIONS]:
                self.set_action(key)
            weeks = self.cfg.get("ui", "weeks", "")
            if weeks:
                parts = weeks.split(",")
                for i in range(min(7, len(parts))):
                    self.week_flags[i] = parts[i] == "1"
                    self.week_chips[i].is_on = self.week_flags[i]
                    self.week_chips[i].refresh()
        except Exception:
            pass


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.0)
    except Exception:
        pass
    ShutdownApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
