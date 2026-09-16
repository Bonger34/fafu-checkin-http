@echo off
setlocal enabledelayedexpansion
REM 数字FAFU 自动签到 —— Windows 管理脚本
REM 用法: run.bat start | stop | status | log
cd /d "%~dp0"
REM 中文 Windows 控制台默认 GBK，脚本输出的 ✅ 等字符会抛 UnicodeEncodeError
REM 直接崩在 status 上；切到 UTF-8 代码页并让 Python 也用 UTF-8
chcp 65001 >nul
set PYTHONUTF8=1
set PY=python
set TITLE=fafu-checkin

if "%1"=="start" (
    if exist daemon.pid (
        set /p PID=<daemon.pid
        REM PID 由 daemon.py 自行写入；!PID! 需 enabledelayedexpansion 才能取到刚读入的值
        REM 用 Get-Process 而非 tasklist：受限会话（非交互式 / 组策略）下 tasklist 会
        REM 报 Access denied，被误判成「进程不存在」就会重复启动第二个实例
        set STATE=dead
        for /f %%i in ('powershell -NoProfile -Command "if (Get-Process -Id !PID! -ErrorAction SilentlyContinue) { Write-Output alive }"') do set STATE=%%i
        if "!STATE!"=="alive" (echo 已在运行 ^(PID !PID!^) & exit /b 0)
        echo 检测到残留 PID 文件（进程 !PID! 不存在），继续启动
    )
    echo 正在启动守护进程...
    REM 用 cmd /c 包装，确保日志重定向在新窗口中生效
    start "%TITLE%" /min cmd /c "%PY% daemon.py >> daemon.log 2>&1"
    echo ✅ 已启动（后台最小化窗口，日志见 daemon.log）
    goto :eof
)
if "%1"=="stop" (
    taskkill /FI "WINDOWTITLE eq %TITLE%" >NUL 2>&1
    set KILLED=!errorlevel!
    REM 仅在确认停止后才删 PID 文件：失败还删的话，旧实例仍在跑却没了 PID，
    REM 下次 start 检测不到进程就会再拉起一个实例
    if "!KILLED!"=="0" (
        if exist daemon.pid del daemon.pid
        echo ✅ 已停止
    ) else (
        echo 未能结束守护进程（PID 文件已保留，如确认在运行请手动结束）
    )
    goto :eof
)
if "%1"=="status" (
    %PY% rootless_checkin.py status
    goto :eof
)
if "%1"=="log" (
    REM 写成多行块：单行括号里的 goto :eof 不可靠，会继续落到下面的用法提示
    if exist daemon.log (type daemon.log) else (echo 无日志)
    goto :eof
)
echo 用法: run.bat start ^| stop ^| status ^| log
