@echo off
setlocal enabledelayedexpansion
REM 数字FAFU 自动签到 —— Windows 管理脚本
REM 用法: run.bat start | stop | status | log
cd /d "%~dp0"
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
    if exist daemon.pid del daemon.pid
    if "!KILLED!"=="0" (echo ✅ 已停止) else (echo 未运行)
    goto :eof
)
if "%1"=="status" ( %PY% rootless_checkin.py status & goto :eof )
if "%1"=="log"    ( if exist daemon.log (type daemon.log) else (echo 无日志) & goto :eof )
echo 用法: run.bat start ^| stop ^| status ^| log
