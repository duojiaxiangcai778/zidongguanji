# core/common.py
# 公共工具函数

import os
import sys


def get_log_path():
    """获取日志文件路径（统一计算方式，消除冗余）"""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 开发模式下定位到项目根目录
        exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(exe_dir, "_error.log")


def write_log(context, exc=None, append=False):
    """统一写入错误日志

    :param context: 错误上下文描述
    :param exc: 异常对象或字符串，None 则自动使用 traceback
    :param append: True=追加模式, False=覆盖模式
    """
    import traceback
    from datetime import datetime

    if exc is None:
        current = traceback.format_exc()
        exc_info = current if current.strip() != "NoneType: None" else "事件已记录，无异常"
    elif isinstance(exc, BaseException):
        exc_info = f"{type(exc).__name__}: {exc}\n{traceback.format_exc() if hasattr(exc, '__traceback__') and exc.__traceback__ else ''}"
    else:
        exc_info = str(exc)

    msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{context}]\n{exc_info}\n---\n"
    log_path = get_log_path()
    try:
        mode = 'a' if append else 'w'
        with open(log_path, mode, encoding='utf-8') as f:
            f.write(msg)
    except Exception:
        pass


def get_exe_dir():
    """获取可执行文件所在目录（冻结模式）或项目根目录（开发模式）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def strip_quotes(path):
    """去除路径前后的引号（单引号或双引号）"""
    if not path:
        return path
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        return path[1:-1]
    return path
