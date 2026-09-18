# 自动关机工具 Bug 报告

## 项目信息
- 语言：Python 3.11 + CustomTkinter
- 系统：Windows 10/11
- 文件结构：
  - `ui/main_window.py` — GUI 主窗口
  - `core/actions.py` — 8 种操作执行（关机/重启/注销/睡眠/关显示器/运行程序/播放声音/弹出消息）
  - `core/timer_engine.py` — 倒计时引擎（倒计时/指定时间/每周循环三种模式）

---

## Bug 1（P0 严重）：WinRT Toast 通知静默失败

**现象**：倒计时结束后"弹出消息"操作触发，但 Windows 右下角无任何通知弹出，代码不报错。

**根因**：`CreateToastNotifier` 需要一个在开始菜单注册了快捷方式的合法 AUMID。当前代码伪造的 AUMID 未注册，Windows 静默吞掉通知。

**实测结果**：
- WinRT 类型加载成功
- `$notifier.Id` 返回空字符串（AUMID 未注册）
- WinForms `ShowBalloonTip` 兜底可以工作
- 但 WinRT Toast 被 OS 静默吞掉

**当前代码**（`core/actions.py` show_message 函数）：
```python
def show_message(text):
    """WinRT 原生 Toast 通知，带 AUMID 注册和 balloon 兜底"""
    if not text or not text.strip():
        text = "定时任务已触发！"
    safe_text = text.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    AUMID = "AutoShutdownTool"
    APP_NAME = "自动关机工具"
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$shortcutPath = [System.IO.Path]::Combine([Environment]::GetFolderPath('StartMenu'), 'Programs', '{APP_NAME}.lnk')
if (-not (Test-Path $shortcutPath)) {{
    try {{
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = 'powershell.exe'
        $shortcut.Arguments = '-NoExit -Command "Write-Host {APP_NAME}"'
        $shortcut.Description = '{APP_NAME}'
        $shortcut.Save()
    }} catch {{}}
}}
try {{
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = @"
<toast launch="app-defined-string"><visual><binding template="ToastGeneric">
    <text>{APP_NAME}</text>
    <text>{safe_text}</text>
</binding></visual></toast>
"@
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{AUMID}').Show($toast)
}} catch {{
    Add-Type -AssemblyName System.Windows.Forms
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.Visible = $True
    $notify.ShowBalloonTip(8000, '{APP_NAME}', '{safe_text}', [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Seconds 8
    $notify.Dispose()
}}
"""
    try:
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_script], creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        write_log("show_message 通知发送失败", e, append=True)
```

**要求**：
1. 必须在 Windows 10/11 上 100% 能弹出通知
2. 不能阻塞主线程（不能用 `subprocess.run`，只能用 `subprocess.Popen` 或 `ctypes`）
3. 不能抢占游戏焦点（不能用 `tkinter.messagebox`）
4. 通知要能进入系统通知中心（优先）或至少在右下角显示气泡
5. 这是一个打包成 exe 的桌面应用，不是 UWP/MSIX 应用

---

## Bug 2（P1）：periodic_vars 初始化可读性差

**当前代码**（`ui/main_window.py` __init__）：
```python
self.periodic_vars = [tk.BooleanVar(value=(i < 5)) for i in range(7)]  # 周一至周日
```

**问题**：`(i < 5)` 含义不直观（索引 0-4 = 周一到周五默认勾选）。

**要求**：改用更清晰的写法，保持功能不变。

---

## Bug 3（P2）：_on_close 中 config.save() 异常被吞

**当前代码**（`ui/main_window.py`）：
```python
def _on_close(self):
    try:
        current_geom = self.root.geometry()
        self.config.set('Window', 'geometry', current_geom)
        self.config.save()
    except Exception:
        pass
    self._closing = True
    self.root.destroy()
```

**问题**：`except Exception: pass` 吞掉所有异常，用户无法得知保存失败。

**要求**：异常时记录日志。

---

## Bug 4（P2）：at_time 错误提示不精确

**当前代码**（`ui/main_window.py` _on_start_timer）：
```python
elif mode == "at_time":
    try:
        y = int(self.entry_year.get() or 0)
        mo = int(self.entry_month.get() or 1)
        d = int(self.entry_day.get() or 1)
        h = int(self.entry_hour.get() or 0)
        mi = int(self.entry_min.get() or 0)
        s = int(self.entry_sec.get() or 0)
        target_time = datetime.datetime(y, mo, d, h, mi, s)
    except (ValueError, TypeError):
        messagebox.showwarning("提示", "日期时间格式无效！")
        return
```

**问题**：`int()` 和 `datetime()` 都抛 `ValueError`，无法区分是"非数字"还是"无效日期（如2月30日）"。

**要求**：分开捕获，给出精确错误提示。

---

## Bug 5（P2）：_toggle_inputs 不含星期复选框

**当前代码**（`ui/main_window.py`）：
```python
def _toggle_inputs(self, state="disabled"):
    for w in (self.spin_days, self.spin_hours, self.spin_mins, self.spin_secs,
              self.entry_year, self.entry_month, self.entry_day,
              self.entry_hour, self.entry_min, self.entry_sec,
              self.spin_p_hour, self.spin_p_min, self.spin_p_sec):
        try:
            w.configure(state=state)
        except Exception:
            pass
```

**问题**：运行中用户仍可修改周期模式的星期选择复选框。

**要求**：在 `_toggle_inputs` 中同时禁用/启用周期模式的 7 个 `CTkCheckBox`。注意这些复选框在 `_build_time_config_card` 的 `panel_periodic` 中创建，需要用列表保存引用。

---

## Bug 6（P2）：btn_pause 和 btn_stop 缺少禁用状态管理

**当前代码**（`ui/main_window.py` _build_bottom_controls）：
```python
self.btn_pause = ctk.CTkButton(row_btns, text="暂停", ...)
self.btn_stop = ctk.CTkButton(row_btns, text="停止", ...)
self.btn_now = ctk.CTkButton(row_btns, text="立即执行", ...)
```

**问题**：初始状态下暂停/停止按钮可点击（虽然内部有 `if not self.is_counting: return` 保护）。

**要求**：
- 初始创建时 `btn_pause` 和 `btn_stop` 设 `state="disabled"`
- `_on_start_timer` 中启用：`self.btn_pause.configure(state="normal")`、`self.btn_stop.configure(state="normal")`
- `_on_stop_timer` 中禁用：`self.btn_pause.configure(state="disabled")`、`self.btn_stop.configure(state="disabled")`
- `_do_execute` 中非周期模式也禁用

---

## 补充约束
1. 所有修改必须保持现有控件变量名不变（`entry_prog_path`、`text_msg`、`periodic_vars` 等），否则会破坏 CLI 同步逻辑
2. `show_message` 不能使用 `subprocess.run`（会阻塞主线程导致界面卡死），只能用 `subprocess.Popen` 或 `ctypes`
3. 所有 GUI 更新必须在主线程执行（通过 `root.after()` 调度）
4. 项目会被 PyInstaller 打包成单文件 exe
