# 自动关机工具 更新日志

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
