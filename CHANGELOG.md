# 自动关机工具 更新日志

## v4.0.1 (2026-10-29) — 紧急修复：双击启动「应用程序没有响应」

### 现象
双击桌面 `定时关机.lnk` 后界面完整渲染出来，但窗口整体假死，Windows 弹出「应用程序没有响应」。

### 根因
`webview.create_window(..., js_api=api)` **仍然传了 `js_api`**。

pywebview 在初始化窗口时会调 `util.get_functions(window._js_api)`（`util.py:180-214`）
**递归反射遍历** api 对象的全部公开属性：

```
Api.app(ShutdownApp) → .window(pywebview Window) → .native(.NET WebView2 控件)
→ .AccessibilityObject.Owner.ActiveControl.Font.Style.Bold.Bold.Bold.Bold...
```

`.Bold` 取到的对象自身还有 `.Bold`，属性链无限套娃 → 递归爆栈（39 条
`maximum recursion depth exceeded`），每一跳都要跨一次 COM 且大量发生在非 UI 线程
（`CoreWebView2Controller members can only be accessed from the UI thread`），
GUI 线程被彻底占死。

**关键认知**：改用自建 HTTP 接口后 `js_api` 已经没有任何用途，但只要参数还传着，
pywebview 就一定会去反射它 —— 不是「没用到所以无害」。

### 修复
`app.py` 的 `create_window(**kwargs)` 里删掉 `js_api=api`，并加注释防止以后再传回去。

### 验证
| 场景 | 结果 |
|------|------|
| 源码版启动 | `Responding=True`，本次运行 8 行日志 / 0 ERROR / 0 recursion（修复前 39 条递归错误） |
| 打包版 + 环境变量定位 | `Responding=True`，8 行 / 0 ERROR |
| **打包版真实双击（无任何环境变量）** | `Responding=True`，7 行 / 0 ERROR |
| 倒计时交互 | API 起 45 秒 → UI 上 `00:42` 实时递减、环形进度弧正常、按钮禁用态正确 |

## v4.0 (2026-10-29) — 界面整体重写为液态玻璃（tkinter → pywebview/WebView2）

### 一、UI 重写（全新前端）
- 技术栈换成 **pywebview 6.2.1 + WebView2**，视觉直接照搬「外设电量守护」的液态玻璃：
  毛玻璃 26px、青紫渐变（`#6ee7ff` / `#a78bfa`）、漂浮光斑、`@property --p` 驱动的环形进度
- 布局：左栏「环形倒计时 + 三种触发时间」，右栏「8 操作宫格 + 参数 + 设置」
- 无边框窗口（`frameless` + `easy_drag`），自绘标题栏与窗口按钮，窗口尺寸/位置记忆
- 新增：快捷时长芯片（5 分钟 ~ 2 小时）、暂停/继续、本次目标时间显示、底部状态提示
- **输入框永不被轮询覆盖**：所有 render 只写展示节点，`input.value` 只由用户/快捷芯片/文件对话框写入
  （v3.0 的「输入的文字会消失」由此彻底解决）

### 二、前端 ↔ 后端通道（踩坑重点）
- ❌ 放弃 pywebview 的 `js_api`：它依赖内置 HTTP 服务，且**只有窗口 url 是本地文件路径时**才启动；
  走 remote url 时 `window.pywebview.api` 是空对象，前端报 `api.init is not a function`
- ❌ 放弃 `window.evaluate_js()`：GUI 线程同步调用会与 WebView2 消息泵互锁（窗口卡死），
  其它线程调用会导致进程直接退出
- ✅ 改为自建 `127.0.0.1:随机端口` 服务（`ThreadingTCPServer`）：
  `POST /api/<name>` JSON 进出，白名单 `API_WHITELIST` 限定可调用方法
- ✅ 前端每 500ms `POST /api/get_state` 轮询，完成/错误事件通过响应体 `events` 数组下发
- ✅ `Cache-Control: no-store` + 随机端口 → 彻底解决 WebView2 缓存旧页面（改前端不生效）的问题

### 三、功能对齐 v3.0
- 8 种操作（关机/重启/注销/睡眠/关屏/运行程序/播放声音/弹消息）× 3 种模式（倒计时/指定时间/每周循环）
- 参数校验前移到后端：倒计时 > 0、时间未过去、路径存在、消息非空、每周至少一天
- 托盘（`core/tray` 原样复用）、关闭窗口最小化到托盘、开机自启（HKCU Run）
- 原生 `GetOpenFileNameW` 文件选择（零 tkinter 依赖）
- 原生 `MessageBoxW` 替换 `core.actions` 里的 tkinter 弹窗
- CLI 通道 100% 保持不变，`app.py` 直接复用 `main.parse_args` / `main.cli_mode`

### 四、清理与打包
- 旧 tkinter 版 GUI 归档到 `archive/gui_v3_tkinter.py`、`archive/ui_v3/`、`archive/_ui_smoke_v3.py`（源码保留）
- 删除冗余：重复的 `自动关机工具-cli.exe`（与 `自动关机工具.exe` 哈希一致，12.4 MB）、
  `build/`、`dist/`、10 张调试截图、`_pv_smoke.*`、`_test_api.py`、`_error.log`、全部 `__pycache__`
- **项目体积 159.0 MB → 84.4 MB，省 74.6 MB**
- 打包参数：`--onefile --windowed --add-data "web;web" --collect-all webview --hidden-import main`
  → `定时关机.exe` 16.5 MB，覆盖到项目根目录

### 五、验证
- 源码运行：窗口渲染、8 宫格、设置开关、倒计时递减（07→06→05）、暂停/继续/停止 全部实测通过
- 打包运行：`定时关机.exe` 启动 → 前端连接 → 界面完整，日志 `前端已连接，返回初始化数据（v4.0）`
- CLI 回归：`定时关机.exe /rp=C:\Windows\System32\notepad.exe` 成功拉起记事本且不弹 GUI
- 留痕：`工作记录\2026-10-29-GUI全量重写.md`

## v3.0.2 (2026-10-21) — DSH 侧放行系统 shutdown（无代码改动）

- 根因：`dsh-permission-rules` 内置基线对 `shutdown/reboot/halt/poweroff` 判 `action: ask`，而本会话 `approval: never` → ask 退化为拒绝
- 修复：工作区规则 `default-workspace\.dsh\rules.yaml` 加 `action: allow`（`argv.command: ["shutdown*","reboot","halt","poweroff"]`），用户规则先于内置基线、首匹配生效
- 验证：`shutdown /?` 可执行；`dd of=/dev/sda` 仍被拒（基线未拆）
- 开关机仍建议走本工具 CLI（多一层安全关闭 WPS/Office 与轮转日志）
- 记录：`工作记录\2026-10-21-DSH助手关机通道.md`「补充」一节

## v3.0.1 (2026-10-21) — 助手调用通道确认（无代码改动）

- DSH 助手侧 `shutdown` 被内置高危规则拦成 `ask` → 审批禁用即拒绝；改为调用本工具 exe CLI，免审批、实测 `exit=0`
- README 新增「助手 / 自动化调用」与「当前状态」两节
- DSH 侧新增技能 `host-power`（`%APPDATA%\dsh-desktop\harness\skills\host-power\SKILL.md`），规定关机走本工具
- 记录：`工作记录\2026-10-21-DSH助手关机通道.md`

## v3.0 (2026-09-19) — 日志系统 + 深色控制台 UI 重做

### 一、日志系统（全新）
- `core/logger.py`：基于 `RotatingFileHandler` 的轮转日志
  - 位置：`logs/app.log`，单文件 1MB × 5 个备份
  - 格式：`时间 [级别] [模块] 消息`
  - `audit()` 结构化审计事件：定时启动/暂停/恢复/停止/完成、操作执行、通知发送、配置变更、程序启停，一行一事件
  - 运行时级别调整：DEBUG / INFO / WARNING（设置页可切换，即时生效）
  - 旧接口 `write_log` / `get_log_path` 保留签名转发到新系统

### 二、UI 全面重做（iOS 浅色 → 深色任务控制台）
- 结构：左侧固定侧边栏导航（定时任务 / 运行日志 / 系统设置），替代原纵向卡片堆叠
- 定时任务页：状态英雄区（状态徽章 + 46px 大号倒计时 + 百分比 + 预计执行时间 + 上次事件），
  下方两列布局（触发时间 | 执行操作+动态参数），底部控制条（开始/暂停/停止/立即执行）
- 运行日志页：级别过滤（全部/INFO/WARNING/ERROR）+ 自动滚动开关 + 刷新 + 打开目录，激活时每 2 秒自动刷新
- 系统设置页：8 个功能开关全部接线（见下），日志级别切换，清空日志，关于
- 配色：`#0E1320` 深蓝黑背景 / `#151C2C` 卡片 / `#3E7BFA` 强调蓝 / `#30D158` 绿 / `#FF5A5F` 红
- 所有 v2 控件变量名保留，业务逻辑与 CLI 注入逻辑（main.py /t 预填）完全兼容

### 三、系统托盘（全新）
- `core/tray.py`：纯 ctypes 实现常驻托盘图标（零第三方依赖）
  - 左键/双击恢复主窗口，右键菜单：显示主窗口 / 退出程序
  - 气泡通知：`TrayIcon.notify()` 复用常驻图标发送
- 关闭窗口 → 最小化到托盘（可配置），Esc 最小化（可配置），首次隐藏有气泡提示
- 修复 ctypes WNDPROC 回调对象被 GC 后调用悬空指针导致的进程硬崩（回调与窗口同生命周期）

### 四、通知重写
- `show_message` 弃用 v2 的 PowerShell WinRT 方案（每次冷启动 1~2 秒、借用 AUMID 时 toast 会被系统静默丢弃），
  改用 Win32 `Shell_NotifyIcon` 气泡：毫秒级、100% 进入通知区域
- GUI 下复用常驻托盘图标，CLI 下自动创建临时图标（`show_balloon` 独立线程，CLI 退出前等待气泡展示完毕）

### 五、安全与稳定性优化
- `run_program`：`subprocess.Popen` → `ShellExecuteW`（资源管理器双击同款 API，结构化传参，天然支持 .exe/.bat/关联文档）
- MCI 播放：`mciSendStringW` 命令字符串 → `mciSendCommandW` 结构化接口（MCI_OPEN_PARMSW 字段直传路径，消除命令字符串注入面）
- `_run_shutdown`：记录 shutdown.exe 退出码（1190/1116 等正常噪音记 WARNING）
- 播放声音前验证文件存在
- 修复 `/min` 启动参数引用不存在的 `_minimize_to_tray` 方法导致的崩溃
- 修复 settings.ini 中 8 个配置项全部无接线的问题——现已全部生效：
  | 配置项 | 行为 |
  |---|---|
  | ask_before_execute | 手动「立即执行」前确认 |
  | ask_critical_only | 仅对关机/重启/注销确认 |
  | ask_before_close | 退出程序前确认（未启用托盘时） |
  | minimize_to_tray | 关闭窗口隐藏到托盘 |
  | esc_minimize | Esc 最小化 |
  | play_sound_last_10s | 最后 10 秒提示音 |
  | force_close | 关机/重启附加 /f（运行时同步给 actions） |
  | start_minimized（新增） | 启动即最小化到托盘 |
- 定时引擎接入日志：启动（含模式/时长/下次触发时间）、暂停/恢复/停止、周期任务每次触发

### 六、验证
- 冒烟测试 `_ui_smoke.py`：26/26 通过（模块导入、日志系统、CLI 解析、三页构建、页面切换、模式/操作联动、快捷预设）
- 托盘专项验证：图标存活、气泡发送、线程正常启停
- PyInstaller 重新打包并部署到项目根目录；冻结 exe 启动日志链验证通过

### 七、兼容性说明
- CLI 参数（X/S/R/L /sm /som /rp /ra /m /t /w /min）行为与 v2 一致
- 日志位置从 `_error.log` 变为 `logs/app.log`（旧文件保留未删）
