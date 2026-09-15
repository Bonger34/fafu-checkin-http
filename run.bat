@echo off
REM 数字FAFU 自动签到 —— Windows 管理脚本
REM 用法: run.bat start | stop | status | log
cd /d "%~dp0"
set PY=python
set TITLE=fafu-checkin

if "%1"=="start" (
    if exist daemon.pid (
        set /p PID=<daemon.pid
        tasklist /FI "PID eq !PID!" 2>NUL | find "!PID!" >NUL && (echo 已在运行 ^(PID !PID!^) & exit /b 0)
    )
    echo 正在启动守护进程...
    REM 用 cmd /c 包装，确保日志重定向在新窗口中生效
    start "%TITLE%" /min cmd /c "%PY% daemon.py >> daemon.log 2>&1"
    echo ✅ 已启动（后台最小化窗口，日志见 daemon.log）
    goto :eof
)
if "%1"=="stop" (
    taskkill /FI "WINDOWTITLE eq %TITLE%" >NUL 2>&1
    if errorlevel 1 (echo 未运行) else (echo ✅ 已停止)
    goto :eof
)
if "%1"=="status" ( %PY% rootless_checkin.py status & goto :eof )
if "%1"=="log"    ( if exist daemon.log (type daemon.log) else (echo 无日志) & goto :eof )
echo 用法: run.bat start ^| stop ^| status ^| log
