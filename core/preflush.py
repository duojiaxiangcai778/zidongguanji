# core/preflush.py
# 关机 / 重启前预刷卷写缓存
#
# 为什么要这一步
#   本机 2026-11-05 起「关机关不掉」，2026-11-06 定案：C: 卷文件系统损坏并置脏位，
#   关机走到内核 PopFlushVolumes（刷所有卷）时卡在 Ntfs 上。chkdsk 修好后偶发复现，
#   对策是「在发起 shutdown 之前，主动把各卷写缓存刷干净」，让内核那一步无活可干。
#
# 为什么必须有硬超时
#   本机 E:（TOSHIBA 2TB）与 F:（HSEN 1TB）都是 USB 外置盘，闲置几小时后会休眠。
#   对休眠中的 USB 盘刷写缓存，可以几十秒不返回，表现为「点了关机没反应」。
#   实测记录：2026-11-27 02:24 两次手动运行旧脚本，都死在同一行（C、D 刷完了，E 永远没返回）。
#   所以：每一次刷缓存都跑在独立子进程里，带 timeout，超时就放弃该卷继续往下走，
#   绝不允许任何一块盘拖住整个关机流程。
#
# 默认行为
#   跳过 BusType == USB 的卷（它们不是关机卡死的原因，且最慢）。
#   要连 USB 盘一起刷，把 settings.ini 的 preflush_include_usb 设为 true。

import subprocess
import sys

from core.logger import get_logger

log = get_logger("preflush")

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_PS_PROBE = (
    "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {"
    "$d=$_.DeviceID.Substring(0,1);"
    "$b='Unknown';"
    "try{$b=(Get-Partition -DriveLetter $d -ErrorAction Stop | Get-Disk -ErrorAction Stop).BusType.ToString()}catch{};"
    "\"$d|$b\"}"
)


def _probe_targets():
    """返回 [(盘符, 总线类型)]，C: 排最前，USB 排最后。

    探测失败不是错误：降级成「只刷 C:」—— C: 是系统卷，也是真正会卡死的那一个。
    """
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_PROBE],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=NO_WINDOW,
        )
    except Exception as e:
        log.warning("探测卷总线类型失败，降级为只刷 C: (%s)", e)
        return [("C", "Unknown")]

    rows = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        drive, bus = line.split("|", 1)
        drive = drive.strip().upper()
        if len(drive) == 1 and drive.isalpha():
            rows.append((drive, bus.strip()))

    if not rows:
        return [("C", "Unknown")]

    def sort_key(item):
        drive, bus = item
        if drive == "C":
            return (0, drive)
        if bus.lower() == "usb":
            return (2, drive)
        return (1, drive)

    rows.sort(key=sort_key)
    return rows


def flush_volumes(include_usb=False, timeout_sec=10):
    """预刷各卷写缓存。

    :param include_usb: 是否连 USB 外置盘一起刷（慢，可能等它唤醒）
    :param timeout_sec: 单个卷的硬超时（秒），超时就杀掉子进程并放弃该卷
    :return: (成功数, 跳过数)
    """
    ok = 0
    skipped = 0
    if timeout_sec is None or timeout_sec <= 0:
        timeout_sec = 10

    for drive, bus in _probe_targets():
        if bus.lower() == "usb" and not include_usb:
            log.info("[预刷] %s: 跳过（USB 外置盘，刷它可能卡住）", drive)
            skipped += 1
            continue

        vol = drive + ":"
        try:
            proc = subprocess.run(
                ["fsutil.exe", "volume", "flush", vol],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                creationflags=NO_WINDOW,
            )
            if proc.returncode == 0:
                ok += 1
                log.info("[预刷] %s 已刷完", vol)
            else:
                skipped += 1
                log.warning(
                    "[预刷] %s 返回 %s: %s",
                    vol,
                    proc.returncode,
                    (proc.stderr or proc.stdout or "").strip()[:200],
                )
        except subprocess.TimeoutExpired:
            skipped += 1
            log.warning("[预刷] %s 超时 %ss 已放弃（不影响继续关机）", vol, timeout_sec)
        except Exception as e:
            skipped += 1
            log.warning("[预刷] %s 失败: %s", vol, e)

    return ok, skipped


def run_before_poweroff(operation="关机", timeout_sec=10, include_usb=False, enabled=True):
    """关机 / 重启前的统一入口。任何异常都吞掉，绝不允许挡住关机。"""
    if not enabled:
        log.info("[预刷] %s前预刷已关闭（settings.ini: preflush_cache=false）", operation)
        return
    try:
        ok, skipped = flush_volumes(include_usb=include_usb, timeout_sec=timeout_sec)
        log.info("[预刷] %s前预刷完成：成功 %s 个卷，跳过 %s 个", operation, ok, skipped)
    except Exception as e:
        log.warning("[预刷] 预刷异常，忽略并继续%s: %s", operation, e)
