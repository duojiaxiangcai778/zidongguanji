#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动关机工具 · MCP 服务（stdio / JSON-RPC 2.0，按行分隔）

把本项目的 8 种操作 + 定时能力包装成 MCP 工具，供 DSH / Claude / Cursor 等
任何 MCP 客户端直接调用：

    关机 / 重启 / 注销 / 睡眠 / 关屏 / 运行程序 / 播放声音 / 弹气泡
    定时（延迟秒数 或 指定时刻 或 每周循环）
    查任务 / 取消任务 / 取消已排队的系统关机

设计要点
--------
1.  **进程内直调**：import 本项目的 `core.actions`，与 GUI 走同一份代码、
    同一份审计日志（logs/app.log），不经过 shell，不受命令词黑白名单影响。
2.  **自己的调度线程**：不依赖 GUI/Tk 的 timer_engine，MCP 进程一旦被宿主拉起
    就能独立守时；任务落盘到 `mcp/tasks.json`，宿主重启后自动恢复。
3.  **不阻塞 RPC**：所有定时都交给调度线程，工具调用立即返回任务 ID。
4.  **零依赖**：只用标准库。宿主断开 stdin 时进程自行退出，不留孤儿。

手动调试（不需要宿主）
--------------------
    .venv\\Scripts\\python.exe mcp\\server.py --selftest
    .venv\\Scripts\\python.exe mcp\\server.py            # 从 stdin 读 JSON-RPC

作者留痕：工作记录/2026-11-21-MCP服务.md
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import actions  # noqa: E402
from core.logger import setup_logging, get_logger, audit  # noqa: E402

SERVER_NAME = "auto-power"
SERVER_VERSION = "1.0.0"
TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.json")
NO_WINDOW = 0x08000000

setup_logging(os.environ.get("ASD_LOG_LEVEL", "INFO"))
log = get_logger("mcp")

# 操作名 -> (中文名, 执行函数)。与 CLI 的 X/R/L//sm//som 一一对应。
OPERATIONS = {
    "shutdown": ("关闭电脑", actions.shutdown),
    "restart": ("重启电脑", actions.restart),
    "logoff": ("注销电脑", actions.logoff),
    "sleep": ("进入睡眠模式", actions.sleep),
    "monitor_off": ("关闭显示器", actions.monitor_off),
}


# ======================================================================
# 任务存储 + 调度线程
# ======================================================================

class TaskStore:
    """极简持久化：整个列表读改写，量级只有个位数，不存在性能问题。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.tasks = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, list):
                return [t for t in data if isinstance(t, dict)]
        except FileNotFoundError:
            pass
        except Exception as exc:  # 坏文件不能让服务起不来
            log.warning("任务文件损坏，已忽略: %s", exc)
        return []

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(self.tasks, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as exc:
            log.error("任务落盘失败: %s", exc)

    def all(self):
        with self.lock:
            return [dict(t) for t in self.tasks]

    def add(self, task):
        with self.lock:
            self.tasks.append(task)
            self._save()
        return task

    def remove(self, task_id):
        with self.lock:
            hit = [t for t in self.tasks if t.get("id") == task_id]
            if not hit:
                return None
            self.tasks = [t for t in self.tasks if t.get("id") != task_id]
            self._save()
            return hit[0]

    def clear(self, done_only=True):
        with self.lock:
            removed = [t for t in self.tasks if (t.get("status") == "done") if done_only] or []
            if done_only:
                self.tasks = [t for t in self.tasks if t.get("status") != "done"]
                self._save()
                return removed
            removed = list(self.tasks)
            self.tasks = []
            self._save()
            return removed

    def mark(self, task, **fields):
        with self.lock:
            for t in self.tasks:
                if t.get("id") == task.get("id"):
                    t.update(fields)
                    self._save()
                    return t
        return task


STORE = TaskStore(TASKS_PATH)


def _next_run_after(task, now):
    """算出下一次应触发的绝对时间；没有再触发就返回 None。"""
    repeat = task.get("repeat") or {}
    mode = repeat.get("mode") or "once"
    base = datetime.fromtimestamp(task["due_ts"])

    if mode == "once":
        return None

    if mode == "daily":
        while base <= now:
            base += timedelta(days=1)
        return base

    if mode == "weekly":
        weekdays = set(int(d) for d in repeat.get("weekdays") or [])
        if not weekdays:
            return None
        candidate = base + timedelta(days=1)
        for _ in range(14):
            if candidate.weekday() in weekdays and candidate > now:
                return candidate
            candidate += timedelta(days=1)
        return None

    return None


def _execute_task(task):
    """真正执行：调用 core.actions 里同一个函数，审计日志与 GUI 一致。"""
    kind = task.get("kind")
    action = task.get("action") or ""

    try:
        if kind == "power":
            name, func = OPERATIONS[action]
            audit("MCP 定时触发", 操作=name, 任务=task.get("id"), 名称=task.get("label") or "")
            func()
            result = {"ok": True, "operation": name}
        elif kind == "notify":
            actions.show_message(task.get("text") or "定时任务已触发！")
            audit("MCP 定时触发", 操作="弹出消息", 任务=task.get("id"))
            result = {"ok": True, "operation": "弹出消息"}
        elif kind == "run_program":
            actions.run_program(task.get("path") or "", task.get("args") or "")
            audit("MCP 定时触发", 操作="运行程序", 任务=task.get("id"), 路径=task.get("path"))
            result = {"ok": True, "operation": "运行程序"}
        elif kind == "play_sound":
            actions.play_sound(task.get("path") or "")
            audit("MCP 定时触发", 操作="播放声音", 任务=task.get("id"), 路径=task.get("path"))
            result = {"ok": True, "operation": "播放声音"}
        else:
            raise ValueError(f"未知任务类型: {kind}")
    except Exception as exc:
        log.exception("任务执行失败 %s", task.get("id"))
        return {"ok": False, "error": str(exc)}

    return result


class Scheduler(threading.Thread):
    """0.5 秒轮询；到点执行；循环任务顺延下一次。"""

    def __init__(self, store, tick=0.5):
        super().__init__(daemon=True, name="mcp-scheduler")
        self.store = store
        self.tick = tick
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        log.info("调度线程启动，待处理任务 %d 个", len(self.store.all()))
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("调度轮询异常")
            self._stop.wait(self.tick)

    def _tick(self):
        now_ts = time.time()
        now = datetime.now()
        for task in self.store.all():
            if task.get("status") != "pending":
                continue
            if task.get("due_ts", 0) > now_ts:
                continue

            log.info("触发任务 %s (%s)", task.get("id"), task.get("kind"))
            result = _execute_task(task)
            nxt = _next_run_after(task, now)

            if nxt is not None:
                self.store.mark(
                    task,
                    due_ts=nxt.timestamp(),
                    due_text=nxt.strftime("%Y-%m-%d %H:%M:%S"),
                    status="pending",
                    last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                    last_result=result,
                    runs=int(task.get("runs") or 0) + 1,
                )
            else:
                self.store.mark(
                    task,
                    status="done",
                    last_run=now.strftime("%Y-%m-%d %H:%M:%S"),
                    last_result=result,
                    runs=int(task.get("runs") or 0) + 1,
                )


SCHEDULER = Scheduler(STORE)
SCHEDULER.start()


# ======================================================================
# 具体能力
# ======================================================================

def _parse_due(delay_seconds=0, at=None):
    """把 delay_seconds / at 归一化成 (datetime, 来源说明)。"""
    if at:
        text = str(at).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                if fmt.startswith("%H"):
                    today = datetime.now()
                    parsed = today.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second)
                    if parsed <= today:
                        parsed += timedelta(days=1)
                return parsed
            except ValueError:
                continue
        raise ValueError(f"无法解析时间: {at}（支持 2026-11-21 22:30:00 / 22:30）")
    seconds = int(delay_seconds or 0)
    if seconds < 0:
        raise ValueError("delay_seconds 不能为负")
    return datetime.now() + timedelta(seconds=seconds)


def _schedule(kind, action=None, delay_seconds=0, at=None, label="", repeat=None,
              text="", path="", args=""):
    due = _parse_due(delay_seconds, at)
    task = {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "action": action,
        "label": label or "",
        "text": text,
        "path": path,
        "args": args,
        "due_ts": due.timestamp(),
        "due_text": due.strftime("%Y-%m-%d %H:%M:%S"),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "repeat": repeat or {"mode": "once"},
        "status": "pending",
        "runs": 0,
    }
    STORE.add(task)
    audit("MCP 新建任务", 类型=kind, 操作=action or kind,
          触发时间=task["due_text"], 任务=task["id"], 名称=task["label"])
    return task


def tool_power(action, delay_seconds=0, at=None, label=""):
    if action not in OPERATIONS:
        raise ValueError(f"未知操作 {action}，可选: {', '.join(OPERATIONS)}")

    name = OPERATIONS[action][0]
    if int(delay_seconds or 0) == 0 and not at:
        # 立即执行
        audit("MCP 立即执行", 操作=name)
        OPERATIONS[action][1]()
        return {"ok": True, "mode": "immediate", "operation": name,
                "note": "已发起，系统日志见 logs/app.log"}

    task = _schedule("power", action=action, delay_seconds=delay_seconds, at=at, label=label or name)
    return {"ok": True, "mode": "scheduled", "operation": name, "task": _public(task)}


def tool_schedule(kind, action=None, delay_seconds=0, at=None, label="",
                  text="", path="", args="", weekdays=None, daily=False):
    if kind not in ("power", "notify", "run_program", "play_sound"):
        raise ValueError("kind 只能是 power / notify / run_program / play_sound")
    if kind == "power" and action not in OPERATIONS:
        raise ValueError(f"power 需要 action ∈ {', '.join(OPERATIONS)}")
    if kind in ("run_program", "play_sound") and not path:
        raise ValueError("run_program / play_sound 需要 path")

    repeat = {"mode": "once"}
    if weekdays:
        repeat = {"mode": "weekly", "weekdays": [int(d) for d in weekdays]}
    elif daily:
        repeat = {"mode": "daily"}

    task = _schedule(kind, action=action, delay_seconds=delay_seconds, at=at, label=label,
                     repeat=repeat, text=text, path=path, args=args)
    return {"ok": True, "task": _public(task)}


def _public(task):
    remaining = max(0, int(task["due_ts"] - time.time()))
    return {
        "id": task["id"],
        "kind": task["kind"],
        "action": task.get("action"),
        "label": task.get("label"),
        "due": task.get("due_text"),
        "remaining_seconds": remaining,
        "remaining_human": _human(remaining),
        "repeat": task.get("repeat"),
        "status": task.get("status"),
        "runs": task.get("runs", 0),
        "last_result": task.get("last_result"),
    }


def _human(seconds):
    if seconds <= 0:
        return "0 秒"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if mins:
        parts.append(f"{mins} 分")
    if secs and not days:
        parts.append(f"{secs} 秒")
    return " ".join(parts) or "0 秒"


def tool_list_tasks(include_done=False):
    tasks = [_public(t) for t in STORE.all()]
    if not include_done:
        tasks = [t for t in tasks if t["status"] == "pending"]
    tasks.sort(key=lambda t: t["remaining_seconds"])
    return {"count": len(tasks), "tasks": tasks}


def tool_cancel_task(task_id=None, all_pending=False):
    if all_pending:
        removed = STORE.clear(done_only=False)
        audit("MCP 取消任务", 数量=len(removed), 范围="全部")
        return {"ok": True, "removed": [t.get("id") for t in removed]}
    if not task_id:
        raise ValueError("需要 task_id，或 all_pending=true")
    hit = STORE.remove(task_id)
    if not hit:
        return {"ok": False, "error": f"没有任务 {task_id}"}
    audit("MCP 取消任务", 任务=task_id)
    return {"ok": True, "removed": [task_id]}


def tool_cancel_shutdown():
    """撤销已排入系统的关机（shutdown /a），同时清掉待执行的关机任务。"""
    try:
        proc = subprocess.run(["shutdown.exe", "/a"], creationflags=NO_WINDOW, check=False,
                              capture_output=True, text=True, timeout=15)
        code = proc.returncode
        detail = (proc.stderr or proc.stdout or "").strip()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    canceled = [t for t in STORE.all() if t.get("kind") == "power" and t.get("action") in ("shutdown", "restart")]
    for task in canceled:
        STORE.remove(task["id"])

    audit("MCP 取消系统关机", 退出码=code, 同时取消任务=len(canceled))
    # 0 = 成功撤销；1116 = 本来就没有进行中的关机，属正常（不是失败）
    ok = code in (0, 1116)
    return {
        "ok": ok,
        "exit_code": code,
        "detail": detail or ("已取消系统关机" if code == 0 else "当前没有进行中的关机（1116）"),
        "canceled_tasks": [t["id"] for t in canceled],
    }


def tool_notify(text):
    if not text:
        raise ValueError("text 不能为空")
    actions.show_message(text)
    audit("MCP 弹出消息", 内容=text)
    return {"ok": True}


def tool_run_program(path, args=""):
    if not path:
        raise ValueError("path 不能为空")
    actions.run_program(path, args)
    return {"ok": True, "path": path}


def tool_play_sound(path):
    if not path:
        raise ValueError("path 不能为空")
    actions.play_sound(path)
    return {"ok": True, "path": path}


def tool_status():
    pending = [t for t in STORE.all() if t.get("status") == "pending"]

    gui = subprocess.run(["tasklist.exe", "/fi", "IMAGENAME eq 定时关机.exe"],
                         creationflags=NO_WINDOW, capture_output=True, text=True, timeout=15)
    gui_running = "定时关机.exe" in (gui.stdout or "")

    return {
        "server": f"{SERVER_NAME} {SERVER_VERSION}",
        "base_dir": BASE_DIR,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pending_count": len(pending),
        "next": _public(min(pending, key=lambda t: t["due_ts"])) if pending else None,
        # 故意不在这里跑 shutdown /a：那条命令本身就会撤销用户的排队关机，
        # 只读状态不能有副作用。要探测/撤销请用 cancel_shutdown。
        "system_shutdown_pending": "未探测（探测需 shutdown /a，会撤销排队；请用 cancel_shutdown）",
        "gui_running": gui_running,
        "log": os.path.join(BASE_DIR, "logs", "app.log"),
        "operations": {k: v[0] for k, v in OPERATIONS.items()},
    }


def tool_open_gui():
    exe = os.path.join(BASE_DIR, "定时关机.exe")
    if not os.path.isfile(exe):
        raise FileNotFoundError(f"未找到 {exe}")
    subprocess.Popen([exe], cwd=BASE_DIR, creationflags=NO_WINDOW, close_fds=True)
    return {"ok": True, "started": exe}


# ======================================================================
# MCP 工具表
# ======================================================================

TOOLS = [
    {
        "name": "power",
        "description": "立即或按延迟执行电源操作：关机/重启/注销/睡眠/关屏。delay_seconds=0 或不传 at 时立即执行；否则排入本服务的定时表（返回 task.id）。执行前会自动安全关闭 WPS/Word/Excel/Outlook 等窗口（与 GUI 同一套逻辑），写 logs/app.log。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(OPERATIONS), "description": "shutdown=关机, restart=重启, logoff=注销, sleep=睡眠, monitor_off=关显示器"},
                "delay_seconds": {"type": "integer", "description": "延迟多少秒执行，默认 0=立即", "default": 0},
                "at": {"type": "string", "description": "指定触发时刻，如 2026-11-21 23:30:00，或只写 23:30 表示今天/明天该时刻"},
                "label": {"type": "string", "description": "任务备注，便于 list_tasks 辨认"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "schedule",
        "description": "创建定时任务（一次 / 每天 / 每周任意星期）。kind=power 定时电源操作，notify 定时弹气泡，run_program 定时启动程序，play_sound 定时播放音频。任务存在本服务进程里并落盘 mcp/tasks.json，宿主重启自动恢复。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["power", "notify", "run_program", "play_sound"]},
                "action": {"type": "string", "enum": list(OPERATIONS), "description": "kind=power 时必填"},
                "delay_seconds": {"type": "integer", "description": "延迟秒数；与 at 二选一", "default": 0},
                "at": {"type": "string", "description": "触发时刻：2026-11-21 23:30:00 / 2026-11-21 / 23:30"},
                "label": {"type": "string"},
                "text": {"type": "string", "description": "kind=notify 时的气泡文本"},
                "path": {"type": "string", "description": "kind=run_program / play_sound 时的文件路径"},
                "args": {"type": "string", "description": "kind=run_program 的附加参数"},
                "weekdays": {"type": "array", "items": {"type": "integer"}, "description": "每周循环，0=周一 … 6=周日"},
                "daily": {"type": "boolean", "description": "每天同一时刻重复", "default": False},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "list_tasks",
        "description": "列出本服务里待触发的定时任务（含倒计时、循环规则、上次执行结果）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_done": {"type": "boolean", "description": "是否包含已执行完的一次性任务", "default": False},
            },
        },
    },
    {
        "name": "cancel_task",
        "description": "取消一个定时任务（传 task_id）或全部待执行任务（all_pending=true）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "all_pending": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "cancel_shutdown",
        "description": "撤销已经排入 Windows 的关机/重启（shutdown /a），并同时清掉本服务里待执行的关机、重启任务。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "notify",
        "description": "立刻在通知区域弹一条气泡消息（不改变电源状态，适合做自检或提醒）。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "气泡内容"}},
            "required": ["text"],
        },
    },
    {
        "name": "run_program",
        "description": "用 ShellExecuteW 打开程序/文档/网址快捷方式（等价资源管理器双击）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "args": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "play_sound",
        "description": "播放音频文件（WAV 走 winsound，MP3/MID 走 MCI）。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "status",
        "description": "查看服务状态：下一个任务、待执行数量、GUI 是否在跑、日志路径。只读，不会撤销任何已排队的关机（要撤销请用 cancel_shutdown）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "open_gui",
        "description": "打开「定时关机」图形界面（F:\\\\【AI项目】\\定时关机.exe）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

HANDLERS = {
    "power": lambda a: tool_power(a.get("action"), a.get("delay_seconds", 0), a.get("at"), a.get("label", "")),
    "schedule": lambda a: tool_schedule(
        a.get("kind"), a.get("action"), a.get("delay_seconds", 0), a.get("at"),
        a.get("label", ""), a.get("text", ""), a.get("path", ""), a.get("args", ""),
        a.get("weekdays"), bool(a.get("daily")),
    ),
    "list_tasks": lambda a: tool_list_tasks(bool(a.get("include_done"))),
    "cancel_task": lambda a: tool_cancel_task(a.get("task_id"), bool(a.get("all_pending"))),
    "cancel_shutdown": lambda a: tool_cancel_shutdown(),
    "notify": lambda a: tool_notify(a.get("text", "")),
    "run_program": lambda a: tool_run_program(a.get("path", ""), a.get("args", "")),
    "play_sound": lambda a: tool_play_sound(a.get("path", "")),
    "status": lambda a: tool_status(),
    "open_gui": lambda a: tool_open_gui(),
}


# ======================================================================
# JSON-RPC 循环
# ======================================================================

def _write(message):
    """强制 UTF-8 字节输出。

    Windows 上管道里的 sys.stdout 默认走 ANSI 代码页（cp936），
    中文 JSON 会被编成 GBK，宿主按 UTF-8 解码就是乱码/非法字节，
    tools/list 会整批读取失败。所以直接写 buffer。
    """
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _ok(mid, result):
    _write({"jsonrpc": "2.0", "id": mid, "result": result})


def _err(mid, code, message):
    _write({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def handle(message):
    mid = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    # 版本协商：新版客户端先问 server/discover，回一个非 modern 的 result 让它走 legacy initialize
    if method == "server/discover":
        _ok(mid, {})
        return

    if method == "initialize":
        _ok(mid, {
            "protocolVersion": params.get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "这是「自动关机工具」的 MCP 服务。power 执行关机/重启/注销/睡眠/关屏；"
                "schedule 建定时任务；cancel_shutdown 撤销已排队的关机；notify 弹气泡（可自检）。"
                "关机前务必先把结果告知用户，会话会随关机终止。"
            ),
        })
        return

    if method in ("notifications/initialized", "initialized"):
        return

    if method == "ping":
        _ok(mid, {})
        return

    if method == "tools/list":
        _ok(mid, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            _err(mid, -32602, f"未知工具: {name}")
            return
        try:
            payload = handler(arguments)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            is_error = isinstance(payload, dict) and payload.get("ok") is False
            _ok(mid, {
                "content": [{"type": "text", "text": text}],
                "isError": bool(is_error),
            })
        except Exception as exc:
            log.exception("工具 %s 调用失败", name)
            _ok(mid, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return

    if mid is not None:
        _err(mid, -32601, f"不支持的方法: {method}")


def serve():
    log.info("MCP 服务就绪（stdio），BASE_DIR=%s", BASE_DIR)
    stdin = sys.stdin.buffer
    while True:
        raw = stdin.readline()
        if not raw:
            break
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            try:
                line = raw.decode("gbk").strip()
            except UnicodeDecodeError:
                log.warning("收到无法解码的输入，已跳过 %d 字节", len(raw))
                continue
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log.warning("收到非 JSON 输入: %s", line[:200])
            continue
        if isinstance(message, list):
            for item in message:
                handle(item)
        else:
            handle(message)
    log.info("stdin 关闭，MCP 服务退出")
    SCHEDULER.stop()


def selftest():
    print("== tools/list ==")
    print(json.dumps({"tools": [t["name"] for t in TOOLS]}, ensure_ascii=False))
    print("== status ==")
    print(json.dumps(tool_status(), ensure_ascii=False, indent=2))
    print("== schedule 60s notify ==")
    task = tool_schedule("notify", delay_seconds=60, text="selftest 定时气泡")
    print(json.dumps(task, ensure_ascii=False, indent=2))
    print("== list_tasks ==")
    print(json.dumps(tool_list_tasks(), ensure_ascii=False, indent=2))
    print("== cancel_task ==")
    print(json.dumps(tool_cancel_task(task["task"]["id"]), ensure_ascii=False, indent=2))
    print("== notify ==")
    print(json.dumps(tool_notify("自动关机工具 MCP 自检，不会关机"), ensure_ascii=False))
    SCHEDULER.stop()
    print("SELFTEST OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        try:
            serve()
        finally:
            SCHEDULER.stop()
