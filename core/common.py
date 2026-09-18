# core/common.py
# 公共工具函数 + 旧日志接口兼容层
#
# v3.0: write_log/get_log_path 保留签名，内部转发到统一日志系统
#       （core/logger.py 初始化后写入 logs/app.log，未初始化时退回 stderr）

import logging
import os
import sys


def get_exe_dir():
    """获取可执行文件所在目录（冻结模式）或项目根目录（开发模式）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_log_dir():
    """日志目录: <程序目录>/logs"""
    return os.path.join(get_exe_dir(), "logs")


def get_log_path():
    """获取日志文件路径（兼容旧接口，统一指向 logs/app.log）"""
    return os.path.join(get_log_dir(), "app.log")


def format_seconds(sec):
    """把秒数格式化为 'MM:SS' / 'HH:MM:SS' / 'N天 HH:MM:SS'"""
    sec = max(0, int(sec))
    mins, secs = divmod(sec, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    if days > 0:
        return f"{days}天 {hours:02d}:{mins:02d}:{secs:02d}"
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def write_log(context, exc=None, append=False):
    """统一写入错误日志（兼容旧接口，转发到 logging）

    :param context: 错误上下文描述
    :param exc: 异常对象或字符串
    :param append: 保留参数（新日志系统始终追加），仅为签名兼容
    """
    import traceback
    log = logging.getLogger("legacy")
    if isinstance(exc, BaseException):
        tb = traceback.format_exc() if exc.__traceback__ else ""
        log.error("%s: %s %s", context, exc, tb.strip())
    elif exc is not None:
        log.error("%s: %s", context, exc)
    else:
        current = traceback.format_exc()
        if current.strip() != "NoneType: None":
            log.error("%s\n%s", context, current)
        else:
            log.error("%s", context)


def strip_quotes(path):
    """去除路径前后的引号（单引号或双引号）"""
    if not path:
        return path
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        return path[1:-1]
    return path
