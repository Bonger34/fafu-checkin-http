#!/bin/sh
# 守护进程管理：run.sh start|stop|status|log
cd "$(dirname "$0")"
PIDF="daemon.pid"; LOGF="daemon.log"
case "$1" in
  start)
    if [ -f "$PIDF" ] && kill -0 "$(cat $PIDF)" 2>/dev/null; then echo "已在运行 (PID $(cat $PIDF))"; exit 0; fi
    nohup python3 daemon.py >> "$LOGF" 2>&1 &
    echo $! > "$PIDF"; echo "✅ 已启动 (PID $!)" ;;
  stop)
    # 分别判断「停止结果」与「清理 PID 文件」，避免 rm 失败被误报成「未运行」
    if [ -f "$PIDF" ] && kill "$(cat $PIDF)" 2>/dev/null; then echo "已停止"; else echo "未运行"; fi
    rm -f "$PIDF" ;;
  status)
    if [ -f "$PIDF" ] && kill -0 "$(cat $PIDF)" 2>/dev/null; then echo "🟢 运行中 (PID $(cat $PIDF))"; else echo "⏸ 未运行"; fi
    python3 rootless_checkin.py status ;;
  log) tail -30 "$LOGF" ;;
  *) echo "用法: $0 start|stop|status|log" ;;
esac
