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
    # 仅在确认停止后才删 PID 文件：kill 失败还删的话，旧实例仍在跑却没了 PID，
    # 下次 start 检测不到进程就会再拉起一个实例
    if [ -f "$PIDF" ] && kill "$(cat $PIDF)" 2>/dev/null; then
      rm -f "$PIDF"; echo "已停止"
    else
      echo "未能结束守护进程（PID 文件如存在则已保留，如确认在运行请手动结束）"
    fi ;;
  status)
    if [ -f "$PIDF" ] && kill -0 "$(cat $PIDF)" 2>/dev/null; then echo "🟢 运行中 (PID $(cat $PIDF))"; else echo "⏸ 未运行"; fi
    python3 rootless_checkin.py status ;;
  log) tail -30 "$LOGF" ;;
  *) echo "用法: $0 start|stop|status|log" ;;
esac
