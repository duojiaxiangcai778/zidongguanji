# 定时关机 · 液态玻璃版

Windows 定时任务工具（关机 / 重启 / 注销 / 睡眠 / 关屏 / 运行程序 / 播放声音 / 弹消息），
界面是 pywebview + WebView2 渲染的 **液态玻璃** 风格，与「外设电量守护」同源配色。

- **v4.0.1**（2026-10-29）界面整体重写，从 tkinter 迁移到 HTML/CSS/JS；
  v4.0.1 修掉了「双击启动卡死（未响应）」—— 原因见下方「为什么不用 pywebview 的 js_api」
- 保留完整 CLI 通道，行为与 v3.0 完全一致，可用于脚本 / 自动化调用
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
├─ web/                   # 前端源码（会被打进 exe）
│  ├─ index.html          # 两栏布局
│  ├─ style.css           # 液态玻璃（调色板与「外设电量守护」同源）
│  └─ app.js              # 轮询 + 事件绑定（严格不覆盖用户输入）
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

## 当前状态（2026-10-29）

**已完成**

- ✅ 液态玻璃界面重写，功能与 v3.0 完全对齐（8 操作 × 3 模式）
- ✅ 前端 ↔ 后端接口联调通过（init / start / stop / pause / resume / state 全绿）
- ✅ PyInstaller 打包成功，单文件 16.5 MB，实测启动 → 渲染 → 交互正常
- ✅ CLI 通道回归验证（`/rp=` 启动 notepad 成功，且不弹 GUI）
- ✅ 清理冗余：删除重复 exe、build/dist、10 张调试截图、旧 `_ui_smoke` 等，省 74.6 MB
- ✅ 旧 tkinter 版归档到 `archive/`，源码保留
- ✅ **v4.0.1 修复双击启动卡死**：删掉 `create_window(js_api=...)`，
  源码版 / 打包版 / 真实双击三种场景均 `Responding=True`、日志 0 ERROR

**已知问题**

- pywebview 会在日志里刷 WebView2 的 COM 噪音（`CanGoForward` / `CoreWebView2` 无限递归），
  不影响功能，已在 `main()` 里把 `pywebview` logger 降到 CRITICAL
- 打包版首次启动约 3–6 秒（onefile 解压），之后由系统缓存加速
- `actions.py` 顶层 `from tkinter import messagebox` 会把整个 tkinter 拖进包里（约 10 MB 冗余），
  v4.0 已用原生 `MessageBoxW` 替换调用点，但顶层 import 尚未去掉

**下一步**

- 把 `actions.py` 的 tkinter 导入改成延迟导入，预计包体降到 ~14 MB
- 接入自定义图标（当前用 PyInstaller 默认图标）
- 增加「本次倒计时结束后自动记住上次时长」

## License

MIT
