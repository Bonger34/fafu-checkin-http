#!/bin/sh
# 守护进程管理：run.sh start|stop|status|log
cd "$(dirname "$0")"
PIDF="daemon.pid"; LOGF="daemon.log"

# 判断该 PID 是否真的是本项目在跑的 daemon。
# 只看 kill -0 会被 PID 复用骗到：daemon 被 kill -9 后 PID 文件残留，
# 该 PID 被系统分给别的进程，于是 start 误判「已在运行」拒绝启动，
# 更糟的是 stop 会去杀一个完全无关的进程。
# 取不到命令行时（部分精简版 ps）退回仅看存在性，避免功能失效。
is_daemon() {
    [ -n "$1" ] || return 1
    kill -0 "$1" 2>/dev/null || return 1
    cmd=$(ps -p "$1" -o args= 2>/dev/null) || return 0
    [ -z "$cmd" ] && return 0
    printf '%s' "$cmd" | grep -q "daemon\.py"
}

case "$1" in
  start)
    if [ -f "$PIDF" ] && is_daemon "$(cat "$PIDF")"; then
      echo "已在运行 (PID $(cat "$PIDF"))"; exit 0
    fi
    nohup python3 daemon.py >> "$LOGF" 2>&1 &
    echo $! > "$PIDF"; echo "✅ 已启动 (PID $!)" ;;
  stop)
    # 先确认这个 PID 确实是我们的 daemon 再动手，避免误杀
    if [ -f "$PIDF" ] && is_daemon "$(cat "$PIDF")" && kill "$(cat "$PIDF")" 2>/dev/null; then
      rm -f "$PIDF"; echo "已停止"
    else
      echo "未在运行（PID 文件如存在则已保留，便于排查）"
    fi ;;
  status)
    if [ -f "$PIDF" ] && is_daemon "$(cat "$PIDF")"; then
      echo "🟢 运行中 (PID $(cat "$PIDF"))"
    else
      echo "⏸ 未运行"
    fi
    python3 rootless_checkin.py status ;;
  log)
    if [ -f "$LOGF" ]; then tail -30 "$LOGF"; else echo "无日志（$LOGF 不存在）"; fi ;;
  *) echo "用法: $0 start|stop|status|log" ;;
esac
