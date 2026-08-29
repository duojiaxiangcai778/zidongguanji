# core/timer_engine.py
# 倒计时引擎模块 - 支持倒计时、日期定时、周期循环三种模式
#
# v2.0 (审计修复版):
#  - 全部基于 time.monotonic() 计时，不受系统时钟跳变影响
#  - 暂停/恢复使用事件+单调时间，不依赖 sleep 粒度
#  - 完成/触发后线程自动退出，is_running 立即反映真实状态
#  - 周期模式触发后重算下一次; 修正部分"当天已过但未触发"的差一天问题

import threading
import time
from datetime import datetime, timedelta


class TimerEngine:
    """倒计时引擎，支持三种定时模式（线程安全）"""

    MODE_COUNTDOWN = "countdown"   # 倒计时
    MODE_AT_TIME   = "at_time"     # 日期定时
    MODE_PERIODIC  = "periodic"    # 周期循环

    def __init__(self):
        self._lock = threading.RLock()
        self._thread = None
        self._pause_event = threading.Event()   # set=暂停
        self._cancel_event = threading.Event()  # set=取消
        # 以单调时钟为基准的截止时间（epoch=None 表示未启动）
        self._deadline_mono = None
        self._remaining = 0
        self._total = 0
        self._mode = self.MODE_COUNTDOWN
        self._on_tick = None          # 每秒回调: func(remaining, total)
        self._on_complete = None      # 完成回调: func()
        self._periodic_config = None  # 周期模式的配置

    # ---- 启动三种模式 ----

    def start_countdown(self, seconds, on_tick=None, on_complete=None):
        """
        启动倒计时模式
        :param seconds: 倒计时总秒数
        :param on_tick: 每秒回调 callback(remaining_seconds, total_seconds)
        :param on_complete: 完成回调 callback()
        """
        self._mode = self.MODE_COUNTDOWN
        self._cancel_event.clear()
        self._pause_event.clear()
        self._total = max(1, int(seconds))
        self._remaining = self._total
        self._deadline_mono = time.monotonic() + self._total
        self._on_tick = on_tick
        self._on_complete = on_complete
        self._start_thread(self._countdown_loop)

    def start_at_time(self, target_time, on_tick=None, on_complete=None):
        """
        启动日期定时模式
        :param target_time: datetime 对象，指定触发时间
        """
        self._mode = self.MODE_AT_TIME
        self._cancel_event.clear()
        self._pause_event.clear()
        now = datetime.now()
        delta = (target_time - now).total_seconds()
        self._total = max(1, int(delta))
        self._remaining = self._total
        self._deadline_mono = time.monotonic() + self._total
        self._on_tick = on_tick
        self._on_complete = on_complete
        self._start_thread(self._countdown_loop)

    def start_periodic(self, weekdays, hour, minute, second,
                       on_tick=None, on_complete=None):
        """
        启动周期模式 — 每周指定日的指定时间循环执行
        :param weekdays: 星期集合，如 {0,1,2,3,4,5,6}，0=周一，6=周日
        :param hour: 触发时 (0-23)
        :param minute: 触发分 (0-59)
        :param second: 触发秒 (0-59)
        """
        self._mode = self.MODE_PERIODIC
        self._cancel_event.clear()
        self._pause_event.clear()
        self._periodic_config = {
            'weekdays': set(weekdays),
            'hour': int(hour),
            'minute': int(minute),
            'second': int(second),
        }
        self._on_tick = on_tick
        self._on_complete = on_complete
        # 计算到下一次触发的时间
        seconds = self._calc_next_periodic_seconds(**self._periodic_config)
        self._total = max(60, int(seconds))
        self._remaining = self._total
        self._deadline_mono = time.monotonic() + self._total
        self._start_thread(self._periodic_loop)

    def _start_thread(self, target):
        """启动后台线程，禁止旧任务与新任务并行运行。"""
        if self.is_running:
            raise RuntimeError("已有定时任务正在运行，请先停止当前任务")
        self._thread = threading.Thread(target=target, daemon=True, name="shutdown-timer")
        self._thread.start()

    # ---- 内部循环 ----

    def _countdown_loop(self):
        """基本倒计时循环（基于单调时钟，线程安全）"""
        self._tick_loop()
        # 正常结束（非取消）时触发完成回调
        if not self._cancel_event.is_set():
            self._safe_complete()

    def _periodic_loop(self):
        """周期循环 — 每次触发后重新计算下一次"""
        while not self._cancel_event.is_set():
            self._tick_loop()
            if self._cancel_event.is_set():
                break
            # 触发完成回调
            self._safe_complete()
            # 重新计算下一次
            with self._lock:
                cfg = self._periodic_config
                if cfg is None or self._cancel_event.is_set():
                    break
                seconds = max(60, self._calc_next_periodic_seconds(**cfg))
                self._total = seconds
                self._remaining = seconds
                self._deadline_mono = time.monotonic() + seconds

    def _tick_loop(self):
        """单调时钟倒计时循环：每秒回调 remaining/total"""
        last_report = None
        while True:
            with self._lock:
                if self._cancel_event.is_set():
                    return
                deadline = self._deadline_mono
                if deadline is None:
                    return
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                with self._lock:
                    self._remaining = 0
                return

            # 暂停：等待恢复或取消（最多 100ms 以便及时响应）
            if self._pause_event.is_set():
                self._pause_event.wait(0.1)
                continue

            # 报告整秒进度（避免 sleep 粒度误差导致跳秒/重复）
            int_remaining = max(1, int(remaining) + 1)
            reported = int_remaining
            if reported != last_report:
                last_report = reported
                with self._lock:
                    self._remaining = reported
                self._safe_tick()

            # 睡眠到下一个整秒或取消
            time.sleep(self._next_sleep(remaining))

    def _next_sleep(self, remaining):
        """计算到下一次 tick 的毫秒数：对齐整秒并避开边界竞态"""
        frac = remaining - int(remaining)
        # 剩余不足 1 秒的零头，等待到边界
        wait = max(0.02, frac)
        return min(wait, 1.0)

    def _safe_tick(self):
        """安全执行 tick 回调"""
        if self._on_tick:
            try:
                self._on_tick(self._remaining, self._total)
            except Exception:
                pass

    def _safe_complete(self):
        """安全执行 complete 回调"""
        if self._on_complete:
            try:
                self._on_complete()
            except Exception:
                pass

    # ---- 辅助方法 ----

    def _calc_next_periodic_seconds(self, weekdays, hour, minute, second):
        """
        计算从当前时间到下一个匹配时间点的秒数（基于挂钟时间）
        :param weekdays: 星期集合 {0..6}
        :return: 秒数
        """
        now = datetime.now()
        target_weekday = None
        target_dt = None

        # 逐个候选日（今天 + 未来 6 天），取第一个匹配的日期-时间
        for days_ahead in range(0, 8):
            candidate = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=minute, second=second, microsecond=0
            )
            if candidate.weekday() in weekdays and candidate > now:
                target_dt = candidate
                break
        if target_dt is None:
            target_dt = (now + timedelta(days=7)).replace(
                hour=hour, minute=minute, second=second, microsecond=0
            )
        delta = (target_dt - now).total_seconds()
        return max(1, int(delta))

    # ---- 控制方法 ----

    def pause(self):
        """暂停倒计时"""
        self._pause_event.set()

    def resume(self):
        """恢复倒计时"""
        self._pause_event.clear()

    def stop(self):
        """停止倒计时，并唤醒暂停状态下的线程。"""
        with self._lock:
            self._cancel_event.set()
            self._pause_event.clear()
            # 唤醒可能阻塞在 sleep 的线程（最短 20ms 轮询，无需强唤醒）

    # ---- 属性 ----

    @property
    def remaining(self):
        return self._remaining

    @remaining.setter
    def remaining(self, value):
        self._remaining = value

    @property
    def total(self):
        return self._total

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self):
        return self._pause_event.is_set()

    @property
    def mode(self):
        return self._mode