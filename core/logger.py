# core/logger.py
# 统一日志系统 v3.0
#
# 功能:
#   - RotatingFileHandler 轮转日志: logs/app.log (1MB × 5 个备份)
#   - 级别: DEBUG / INFO / WARNING / ERROR / CRITICAL，可运行时调整
#   - audit(): 结构化运行审计（定时启停 / 操作执行 / 配置变更），一行一事件
#   - clear_all(): 清空全部日志（设置页调用）
#
# 用法:
#   from core.logger import setup_logging, get_logger, audit
#   setup_logging("INFO")
#   log = get_logger("ui")
#   log.info("窗口已创建")
#   audit("定时启动", 模式="倒计时", 操作="关闭电脑", 总时长="01:00:00")

import logging
import logging.handlers
import os
import threading

from core.common import get_exe_dir, format_seconds

LOG_DIR = os.path.join(get_exe_dir(), "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
MAX_BYTES = 1024 * 1024   # 单文件 1MB
BACKUP_COUNT = 5          # 保留 5 个历史

_FMT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_lock = threading.Lock()
_configured = False


def _parse_level(level):
    """'INFO'/'WARNING'/... 或整数 → logging 级别（无效值回退 INFO）"""
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def setup_logging(level="INFO"):
    """初始化根日志器。重复调用只更新级别。返回日志文件路径。"""
    global _configured
    with _lock:
        root = logging.getLogger()
        if not _configured:
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
            except OSError:
                pass
            fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)

            file_handler = logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                encoding="utf-8", delay=True,
            )
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)

            # 开发模式下附带控制台输出；打包 exe（无控制台）时自动无效
            console = logging.StreamHandler()
            console.setFormatter(fmt)
            console.setLevel(logging.WARNING)
            root.addHandler(console)

            root.setLevel(_parse_level(level))
            _configured = True
        else:
            root.setLevel(_parse_level(level))
    return LOG_FILE


def set_level(level):
    """运行时调整日志级别"""
    logging.getLogger().setLevel(_parse_level(level))


def get_logger(name):
    """获取命名子日志器"""
    return logging.getLogger(name)


def audit(event, **fields):
    """记录一条结构化审计事件: audit("定时启动", 模式="倒计时")"""
    parts = [event]
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    get_logger("audit").info(" | ".join(parts))


def log_exception(log, context, exc=None):
    """带堆栈记录异常的便捷方法"""
    import traceback
    if isinstance(exc, BaseException):
        log.error("%s: %s\n%s", context, exc,
                  traceback.format_exc() if exc.__traceback__ else "")
    else:
        log.error("%s\n%s", context, traceback.format_exc())


def clear_all():
    """清空当前日志与全部轮转备份。返回删除的文件数。"""
    removed = 0
    for name in os.listdir(LOG_DIR) if os.path.isdir(LOG_DIR) else []:
        if name == "app.log" or name.startswith("app.log."):
            try:
                os.unlink(os.path.join(LOG_DIR, name))
                removed += 1
            except OSError:
                pass
    get_logger("system").info("日志已清空（清除 %d 个文件）", removed)
    return removed


__all__ = [
    "LOG_DIR", "LOG_FILE", "setup_logging", "set_level",
    "get_logger", "audit", "log_exception", "clear_all",
    "format_seconds",
]
