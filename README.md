# 定时关机 · 液态玻璃版

Windows 定时任务工具（关机 / 重启 / 注销 / 睡眠 / 关屏 / 运行程序 / 播放声音 / 弹消息），
界面是 pywebview + WebView2 渲染的 **液态玻璃** 风格，与「外设电量守护」同源配色。

- **v4.0.1**（2026-10-29）界面整体重写，从 tkinter 迁移到 HTML/CSS/JS；
  v4.0.1 修掉了「双击启动卡死（未响应）」—— 原因见下方「为什么不用 pywebview 的 js_api」
- 保留完整 CLI 通道，行为与 v3.0 完全一致，可用于脚本 / 自动化调用
- 自带 **MCP 服务**（stdio），DSH / Claude / Cursor 等 MCP 客户端可直接调关机、定时任务
- 开源仓库：<https://github.com/duojiaxiangcai778/zidongguanji>

## 功能

### 8 种可执行操作

| 操作 | CLI 参数 | 说明 |
|------|----------|------|
| 关闭电脑 | `X` 或 `S` | 立即或定时关机 |
| 重启电脑 | `R` | 立即或定时重启 |
| 注销电脑 | `L` | 立即或定时注销 |
| 进入睡眠 | `/sm` | 立即或定时睡眠 |
| 关闭显示器 | `/som` | 只关屏，不锁系统 |
| 运行程序 | `/rp=路径` | 定时启动 exe / 文档 |
| 播放声音 | `/ra=路径` | 定时播放音频 |
| 弹出消息 | `/m=文本` | 托盘气泡 / 消息框 |

### 3 种定时模式

- **倒计时** — 时 / 分 / 秒，最长 30 天，带快捷时长（5 分钟 ~ 2 小时）
- **指定时间** — 年 / 月 / 日 / 时 / 分 / 秒，已过去的时间会被拒绝
- **每周循环** — 勾选星期几，到点循环执行

## 截图

运行时窗口（1140×780，无边框 + 可拖拽）：

```
┌─────────────────────────────┬──────────────────────────┐
│  ◜ 环形倒计时 09:58 ◝        │  执行操作（8 宫格）        │
│  目标 10-29 15:39:54         │  ⏻关机  ↻重启  ⎋注销  ☾睡眠 │
│  [开始计时][暂停][停止]      │  ▣关屏  ▶程序  ♪声音  ✦消息 │
├─────────────────────────────┤                          │
│  触发时间 / 倒计时 / 指定时间 │  设置                     │
│  时 分 秒 + 快捷时长         │  托盘 / 强制关闭 / 提示音  │
└─────────────────────────────┴──────────────────────────┘
```

## MCP 服务（给 AI / 自动化调用）

`mcp/server.py` 把本工具的 8 种操作 + 定时能力包成一个标准 MCP 服务（stdio、JSON-RPC 2.0、
纯标准库、零依赖），供 DSH / Claude Desktop / Cursor 等任何 MCP 客户端直接调用。

**关键设计：进程内 `import core.actions` 直调**，不是 spawn `自动关机工具.exe`。
所以和 GUI 用同一份代码、同一个 `logs\app.log`、同一套「先安全关闭 WPS/Word/Excel」逻辑，
毫秒级返回；定时由服务自己的调度线程守，不占 RPC。

### 10 个工具

| 工具 | 说明 |
|---|---|
| `power` | 关机 / 重启 / 注销 / 睡眠 / 关屏。`delay_seconds=0` 或不传 `at` 立即执行，否则建定时任务 |
| `schedule` | 定时任务：一次 / 每天 / 每周任意星期；kind = `power` / `notify` / `run_program` / `play_sound` |
| `list_tasks` | 列待触发任务（倒计时、循环规则、上次执行结果） |
| `cancel_task` | 取消单个（`task_id`）或全部（`all_pending`） |
| `cancel_shutdown` | `shutdown /a` 撤销已排队的系统关机 + 清掉待执行的关机/重启任务（1116 视为正常） |
| `notify` | 立刻弹气泡，不碰电源——自检用这个 |
| `status` | 服务状态、下一个任务、GUI 是否在跑、日志路径。只读 |
| `run_program` / `play_sound` | ShellExecuteW 打开程序 / 播放音频 |
| `open_gui` | 拉起 `定时关机.exe` 图形界面 |

### 手跑 / 调试

```bat
.venv\Scripts\python.exe mcp\server.py --selftest   :: 不需要宿主，自检一遍
.venv\Scripts\python.exe mcp\server.py              :: 从 stdin 读 JSON-RPC
```

### 接入 DSH

用 `dsh-mcp-connector` 插件配置（不要手改 `cordis.patch.yml`）：

```json
{ "name": "自动关机工具", "serverName": "auto-power", "transport": "stdio",
  "command": "E:\\自动关机工具\\.venv\\Scripts\\python.exe",
  "args": ["E:\\自动关机工具\\mcp\\server.py"],
  "cwd": "E:\\自动关机工具", "scope": "global" }
```

落库在 `<DSH_HOME>\storages\mcp_connector.json`，`injectionMode: always`。工具名 `mcp__auto-power__*`。

### 已知限制

- MCP 进程由宿主拉起，**宿主退出后定时不再触发**；再次启动会补跑已过期的任务（不丢）。
  要「关掉 DSH 也照样准时关机」，用 GUI 定时或 Windows 任务计划。
- MCP 与 GUI 可同时运行，两边各执行一次同条关机指令不会互相阻塞（第二次通常是 1190 噪音）。
- 坑：Windows 管道默认 GBK，中文 JSON 必须用 `sys.stdout.buffer.write(...encode("utf-8"))`，
  否则宿主 `tools/list` 整批失败、看起来像「目标服务拒绝连接」。

详细留痕：`工作记录\2026-11-21-MCP服务.md`。

## 技术栈

| 层 | 选型 |
|----|------|
| 窗口 / 渲染 | pywebview 6.2.1 + WebView2（Edge Chromium） |
| 前端 | 原生 HTML / CSS / JS，无框架、无构建步骤 |
| 视觉 | 液态玻璃：毛玻璃 26px + 青紫渐变 + 漂浮光斑 |
| 业务逻辑 | 复用 `core/`（timer_engine / actions / config / logger / tray） |
| 前端 ↔ 后端 | 本地 HTTP 接口（`POST /api/<name>`，JSON 进出） |
| 打包 | PyInstaller onefile（16.5 MB） |

### 为什么不用 pywebview 的 js_api

**第一条（会直接卡死程序）**：`create_window(js_api=api)` 这个参数一传，pywebview 就会调
`util.get_functions(js_api)` **递归反射遍历整个对象图**：

```
Api.app → .window → .native(.NET WebView2 控件) → .AccessibilityObject...Font.Style.Bold.Bold.Bold...
```

`.Bold` 取到的对象自身还有 `.Bold`，属性链无限套娃 → 递归爆栈（实测刷了 39 条
`maximum recursion depth exceeded`），每一跳跨一次 COM 且大量发生在非 UI 线程，
GUI 线程被彻底占死 → Windows 报「应用程序没有响应」。

**结论：不用 js_api 就绝对不要传这个参数**，不是「没用到所以无害」。

**第二条（注入了也是空对象）**：pywebview 6.x 的 `js_api` 依赖它内置的 HTTP 服务，
而**只有窗口 url 是本地文件路径时**才会启动这个服务。一旦 `create_window(url="http://...")`，
`window.pywebview.api` 就是个空对象 —— 前端报 `api.init is not a function`。

**第三条**：在 GUI 线程里同步调用 `window.evaluate_js()` 会和 WebView2 的消息泵互锁，
窗口直接卡死；从其它线程调用则可能让进程崩溃。

所以本项目改成：自己在 `127.0.0.1:随机端口` 起一套 `静态文件 + /api/*` 服务，
前端 `fetch('/api/get_state')` 轮询取状态。好处是：

- 完全绕开 pywebview 的注入时机问题
- 接口可以直接用 `curl` / PowerShell 调试
- `Cache-Control: no-store` 彻底解决 WebView2 缓存旧页面的问题
- 前端每 500 ms 轮询，延迟无感

## 目录结构

```
自动关机工具/
├─ app.py                 # v4.0 主程序：窗口 + Api + 托盘 + 自建 HTTP 接口
├─ main.py                # v3.0 CLI 解析（app.py 复用其 parse_args / cli_mode）
├─ mcp/                   # MCP 服务（stdio JSON-RPC）
│  ├─ server.py           # 10 个工具 + 自带调度线程 + tasks.json 持久化
│  └─ tasks.json          # 运行时任务表（自动生成，已 gitignore）
├─ web/                   # 前端源码（会被打进 exe）
│  ├─ index.html          # 两栏布局
│  ├─ style.css           # 液态玻璃（调色板与「外设电量守护」同源）
│  └─ app.js              # 轮询 + 事件绑定（严格不覆盖用户输入）
├─ assets/                # 图标资源
│  ├─ icon.ico            # 7 个尺寸（16/24/32/48/64/128/256）
│  ├─ icon-256.png        # 预览
│  ├─ icon-512.png        # 预览
│  └─ preview-*.png       # 16/24/32 放大 8 倍的小尺寸检查图
├─ tools/
│  └─ make_icon.py        # 图标生成脚本（可重复运行）
├─ core/                  # 业务逻辑，与 UI 解耦
│  ├─ timer_engine.py     # 三种模式的定时引擎（线程）
│  ├─ actions.py          # 8 种操作的执行
│  ├─ config.py           # settings.ini 原子读写
│  ├─ logger.py           # 轮转日志 + 审计
│  ├─ tray.py             # 托盘图标（纯 ctypes，零 tkinter）
│  └─ common.py
├─ archive/               # 历史版本（不参与运行）
│  ├─ gui_v3_tkinter.py   # v3.0 的 tkinter GUI
│  └─ ui_v3/              # v3.0 的旧 UI 包
├─ 工作记录/               # 每次改动的留痕
├─ logs/                  # 运行日志
├─ settings.ini           # 配置
├─ 定时关机.exe            # v4.0 GUI（16.5 MB，PyInstaller onefile）
├─ 自动关机工具.exe         # v3.0 CLI 通道（12.2 MB，自动化脚本用）
├─ 启动GUI.bat            # 免装环境启动（venv → pyw → pythonw 三级回退）
└─ *.spec                 # PyInstaller 打包配置
```

## 运行

### 直接用 exe

```bat
定时关机.exe                  :: 启动图形界面
定时关机.exe X /t=300         :: 5 分钟后关机（不弹界面）
定时关机.exe /m="该休息了"     :: 弹出消息
```

### 从源码跑

```bat
:: 首次
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install pywebview pythonnet pyinstaller

:: 启动
.venv\Scripts\python.exe app.py
```

需要系统已装 **WebView2 Runtime**（Win10 1803+ / Win11 通常自带）。

### 重新打包

```bat
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "定时关机" --add-data "web;web" ^
  --hidden-import webview.platforms.edgechromium --hidden-import main ^
  --collect-all webview --paths . app.py
```

产物在 `dist\定时关机.exe`，复制到项目根目录覆盖同名文件即可。

## 图标

`assets/icon.ico` 是程序图标：深色玻璃圆角底 + 青→紫渐变的 C 形环 + 缺口处的圆头竖线，
也就是 UI 里那个环形倒计时的抽象版。

重新生成：

```bat
.venv\Scripts\python.exe tools\make_icon.py
```

脚本会重新画出 7 个尺寸（16 / 24 / 32 / 48 / 64 / 128 / 256）并自己拼 ICO 文件。
三套预设按尺寸切换，因为小尺寸不能直接缩：

| 预设 | 用于 | 环宽 | 缺口 | 光晕/高光 |
|------|------|------|------|-----------|
| `std` | 48 px 以上 | 90 | 21° | 有 |
| `bold` | 32 px | 112 | 27° | 弱 |
| `tiny` | 16 / 24 px | 130 | 40° | 无 |

小尺寸把环加粗、缺口开大、去掉光晕，否则缩到 16 px 会糊成一团。
`assets/preview-*.png` 是 16/24/32 放大 8 倍的检查图，改完直接看这几张就知道小图标长什么样。

托盘图标不用单独做 —— `core/tray.py` 是 `ExtractIconExW(exe, 0, ...)` 从 exe 里取第 0 个图标，
所以只要 exe 有图标，托盘、任务栏、窗口标题栏会自动跟着变。

## 配置

`settings.ini`（程序同目录，首次运行自动生成）：

| 段 | 键 | 说明 |
|----|----|------|
| `[General]` | `minimize_to_tray` | 关闭窗口时最小化到托盘（默认开） |
| | `force_close` | 关机/重启前强制关闭程序（默认开） |
| | `play_sound_last_10s` | 最后 10 秒提示音 |
| | `start_minimized` | 启动即最小化 |
| `[Window]` | `geometry` | 窗口尺寸与位置记忆 |
| `[ui]` | `mode` / `action` / `weeks` | 上次选择的模式、操作、星期 |

日志在 `logs\app.log`（5 MB 轮转，保留 3 份）。

## 当前状态（2026-11-27）

**v4.0.2 已打包并实测**（`定时关机.exe`，17,302,173 B，2026-11-27 02:52 构建）。

- ✅ 液态玻璃界面正常：托盘 + GUI 1108×702 + 前端已连接（日志 02:53:00）
- ✅ 8 种操作、3 种定时模式、设置项全部在（这就是完整版，别再另写精简版）
- ✅ 关机/重启前自动预刷卷写缓存（`core/preflush.py`），每卷硬超时 10 秒，
  默认跳过 USB 外置盘（E: TOSHIBA 2TB / F: HSEN 1TB 会休眠，刷它们能卡几十秒）
- ✅ 2026-11-24 E:→F: 搬家后无残留：venv `home = F:\\Python311` 有效、python 3.11.9 可跑
- ✅ 修好打包脚本 `定时关机.spec` 里写死的旧图标路径（`E:/自动关机工具/...`）
- ✅ MCP 服务 `auto-power 1.0.0` 正常
- ✅ 桌面入口：`定时关机.lnk` → 本目录 `定时关机.exe`
- ⚠️ 未验证：真关机那一次的完整串联（跑就会关机），等下次真关机看 `logs\\app.log`
  里是否出现 `[预刷] ... 预刷完成：成功 N 个卷`
- 📄 详见 `工作记录/2026-11-27-关机前预刷缓存.md`


### 历史记录（截至 2026-11-21）

**已完成**

- ✅ 液态玻璃界面重写，功能与 v3.0 完全对齐（8 操作 × 3 模式）
- ✅ 前端 ↔ 后端接口联调通过（init / start / stop / pause / resume / state 全绿）
- ✅ PyInstaller 打包成功，单文件 16.5 MB，实测启动 → 渲染 → 交互正常
- ✅ CLI 通道回归验证（`/rp=` 启动 notepad 成功，且不弹 GUI）
- ✅ 清理冗余：删除重复 exe、build/dist、10 张调试截图、旧 `_ui_smoke` 等，省 74.6 MB
- ✅ 旧 tkinter 版归档到 `archive/`，源码保留
- ✅ **v4.0.1 修复双击启动卡死**：删掉 `create_window(js_api=...)`，
  源码版 / 打包版 / 真实双击三种场景均 `Responding=True`、日志 0 ERROR
- ✅ **v4.0.2 自定义图标**（2026-10-29）
- ✅ **2026-11-21 新增 MCP 服务**：`mcp/server.py` 10 个工具 + 自带调度线程 + `tasks.json`
  持久化；DSH 侧以 `auto-power` 接入并实测 12/12 探针 + 真机调用（关屏/定时/撤销全通，
  全程未真关机）。**版本号未变**——MCP 是新增旁路，不动 `定时关机.exe` 与 `app.py`。

**已知问题**

- pywebview 会在日志里刷 WebView2 的 COM 噪音（`CanGoForward` / `CoreWebView2` 无限递归），
  不影响功能，已在 `main()` 里把 `pywebview` logger 降到 CRITICAL
- 打包版首次启动约 3–6 秒（onefile 解压），之后由系统缓存加速
- `actions.py` 顶层 `from tkinter import messagebox` 会把整个 tkinter 拖进包里（约 10 MB 冗余），
  v4.0 已用原生 `MessageBoxW` 替换调用点，但顶层 import 尚未去掉
- DSH 的 `mcp_connector_reconnect` 对 stdio 连接误报「目标服务拒绝连接」，工具实际可用

**下一步**

- 把 `actions.py` 的 tkinter 导入改成延迟导入，预计包体降到 ~14 MB
- 增加「本次倒计时结束后自动记住上次时长」
- MCP：考虑加 `log_tail` 工具（读最近 N 行 `logs/app.log`），方便 AI 自查执行结果

## License

MIT
