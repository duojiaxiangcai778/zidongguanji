# core/timer_engine.py
# 倒计时引擎模块 - 支持倒计时、日期定时、周期循环三种模式

import threading
import time
from datetime import datetime


class TimerEngine:
    """倒计时引擎，支持三种定时模式（线程安全）"""

    MODE_COUNTDOWN = "countdown"   # 倒计时
    MODE_AT_TIME   = "at_time"     # 日期定时
    MODE_PERIODIC  = "periodic"    # 周期循环

    def __init__(self):
        self._thread = None
        # Bug 3 修复: 使用 threading.Event 替代普通布尔值，确保线程安全
        self._pause_event = threading.Event()  # set=暂停, clear=继续
        self._cancel_event = threading.Event()  # set=取消
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
        self._total = max(1, seconds)
        self._remaining = self._total
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
            'weekdays': weekdays,
            'hour': hour,
            'minute': minute,
            'second': second,
        }
        self._on_tick = on_tick
        self._on_complete = on_complete
        # 计算到下一次触发的时间
        seconds = self._calc_next_periodic_seconds(weekdays, hour, minute, second)
        self._total = max(1, seconds)
        self._remaining = self._total
        self._start_thread(self._periodic_loop)

    def _start_thread(self, target):
        """启动后台线程，禁止旧任务与新任务并行运行。"""
        if self.is_running:
            raise RuntimeError("已有定时任务正在运行，请先停止当前任务")
        self._thread = threading.Thread(target=target, daemon=True, name="shutdown-timer")
        self._thread.start()

    # ---- 内部循环 ----

    def _countdown_loop(self):
        """基本倒计时循环（线程安全）"""
        while self._remaining > 0 and not self._cancel_event.is_set():
            if not self._pause_event.is_set():
                time.sleep(1)
                # 再次检查暂停/取消状态（避免 time.sleep 期间被暂停的竞态条件）
                if not self._cancel_event.is_set() and not self._pause_event.is_set():
                    self._remaining -= 1
                    self._safe_tick()
            else:
                # 暂停期间等待，同时检查取消信号
                self._pause_event.wait(timeout=0.1)

        if not self._cancel_event.is_set():
            self._safe_complete()

    def _periodic_loop(self):
        """周期循环 — 每次触发后重新计算下一次"""
        while not self._cancel_event.is_set():
            # 倒计时到触发点
            while self._remaining > 0 and not self._cancel_event.is_set():
                if not self._pause_event.is_set():
                    time.sleep(1)
                    if not self._cancel_event.is_set():
                        self._remaining -= 1
                        self._safe_tick()
                else:
                    self._pause_event.wait(timeout=0.1)

            if self._cancel_event.is_set():
                break

            # 触发完成回调
            self._safe_complete()

            # 如果不是取消状态，重新计算下一次
            if not self._cancel_event.is_set() and self._periodic_config:
                seconds = self._calc_next_periodic_seconds(
                    self._periodic_config['weekdays'],
                    self._periodic_config['hour'],
                    self._periodic_config['minute'],
                    self._periodic_config['second'],
                )
                # Bug W 修复: 确保周期模式至少有 60 秒间隔，防止异常循环
                self._total = max(60, seconds)
                self._remaining = self._total

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
        计算从当前时间到下一个匹配时间点的秒数
        :param weekdays: 星期集合 {0..6}
        :return: 秒数
        """
        now = datetime.now()
        current_weekday = now.weekday()  # 0=周一
        current_total_sec = now.hour * 3600 + now.minute * 60 + now.second
        target_total_sec = hour * 3600 + minute * 60 + second

        # 如果今天匹配且时间还没过，今天触发
        if current_weekday in weekdays and current_total_sec < target_total_sec:
            return target_total_sec - current_total_sec

        # 找下一个匹配的天
        for days_ahead in range(1, 8):
            next_weekday = (current_weekday + days_ahead) % 7
            if next_weekday in weekdays:
                return days_ahead * 86400 + target_total_sec - current_total_sec

        # 兜底：7 天后（理论上不会走到这里）
        return 7 * 86400 + target_total_sec - current_total_sec

    # ---- 控制方法 ----

    def pause(self):
        """暂停倒计时"""
        self._pause_event.set()

    def resume(self):
        """恢复倒计时"""
        self._pause_event.clear()

    def stop(self):
        """停止倒计时，并唤醒暂停状态下的线程。"""
        self._cancel_event.set()
        self._pause_event.clear()

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
