@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 自动关机工具 - CLI 模式测试
echo ================================
echo.
echo ⚠ 警告：以下操作会立即执行！请小心测试：
echo   关闭电脑: main.py X      — 会立刻关机！
echo   重启电脑: main.py R      — 会立刻重启！
echo   注销电脑: main.py L      — 会立刻注销！
echo   睡眠模式: main.py /sm    — 会立刻睡眠！
echo.
echo ✅ 安全测试（可验证 UI 和参数）：
echo   关显示器: main.py /som
echo   弹出消息: main.py /m=测试消息
echo   运行程序: main.py /rp=notepad.exe
echo   播放声音: main.py /ra=sound.wav
echo   倒计时:  main.py /som /t=5    ← 5秒后关显示器（安全）
echo   参数:  /t=秒  /w(不自动执行)  /min(最小化启动)
echo.
echo ================================
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
echo.
echo 命令已执行完成。
pause
