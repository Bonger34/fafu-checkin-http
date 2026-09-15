# -*- coding: utf-8 -*-
"""
守护进程：自带调度，无需 cron。
  · 07:00–21:25  保活（每 ~20 分钟 ping 一次，续 token）
  · 21:30–22:30  主签到窗口（每分钟检查，未签则提交）
  · 22:30–23:00  补签兜底（继续重试）
  · token 失效时用 refresh_token 自动刷新（含冷却）

用法：
    python3 daemon.py            # 前台运行
    python3 daemon.py --once     # 只跑一轮检查（调试）
    nohup python3 daemon.py &    # 后台运行
"""
import sys, os, time, random, atexit, datetime, logging
from fafu_config import CFG, TZ, mask
from fafu_lib import api, ensure_token, query_task, _has

# ---- 调度参数（可调）----
KEEPALIVE_SEC   = 20 * 60      # 保活间隔（±20% 抖动）
REFRESH_COOLDOWN= 30 * 60      # token 刷新冷却
MAIN_BEGIN      = (21, 30)     # 主窗口开始
MAIN_END        = (22, 30)     # 主窗口结束
SUPP_END        = (23, 0)      # 补签截止
SIGN_INTERVAL   = 60           # 窗口内检查间隔（秒）
WAKE_MARGIN     = 30           # 提前 30 秒唤醒（避免错过边界）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%m-%d %H:%M:%S")
log = logging.getLogger("fafu")

# ---- PID 文件：run.sh / run.bat 据此防止重复启动（多实例会并发空打刷新链）----
_PID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon.pid")

def _clear_pid():
    """退出时清理 PID 文件；仅当文件仍属于本进程才删，避免误删后继实例的"""
    try:
        with open(_PID_PATH, encoding="utf-8") as f:
            pid = f.read().strip()
        # 必须在 with 之外删除：Windows 不允许删除仍处于打开状态的文件
        if pid == str(os.getpid()):
            os.remove(_PID_PATH)
    except OSError:
        pass

def _write_pid():
    with open(_PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    atexit.register(_clear_pid)

def now(): return datetime.datetime.now(TZ)
def hm(): return now().strftime("%H:%M:%S")

def _check_tz_warning():
    """系统时区若非 UTC+8，提示（daemon 已用固定 UTC+8，不受影响）"""
    local_off = -time.timezone // 3600
    if local_off != 8:
        log.warning("系统时区为 UTC%+d，签到窗口按北京时间(UTC+8)计算；"
                    "如需本地时间一致请设置 TZ=Asia/Shanghai", local_off)


def in_main_window(t):
    b = t.replace(hour=MAIN_BEGIN[0], minute=MAIN_BEGIN[1], second=0, microsecond=0)
    e = t.replace(hour=MAIN_END[0],   minute=MAIN_END[1],   second=0, microsecond=0)
    return b <= t <= e

def in_supp_window(t):
    b = t.replace(hour=MAIN_END[0], minute=MAIN_END[1], second=0, microsecond=0)
    e = t.replace(hour=SUPP_END[0], minute=SUPP_END[1], second=0, microsecond=0)
    return b < t <= e

def try_sign():
    """窗口内检查并签到；返回 True 表示已签到（含服务端已签的情况）"""
    tok = ensure_token(cooldown=REFRESH_COOLDOWN)
    if not tok:
        log.error("无可用 token，跳过本轮"); return False
    r0 = query_task(tok)
    if not r0:
        log.warning("查询任务失败"); return False
    ss = (r0.get("signInStudent") or {}).get("signState")
    name = r0.get("name", "签到")
    rid, bt = r0.get("id"), r0.get("beginTime")
    dl = r0.get("supplementEndTime") or r0.get("endTime")   # 补签截止，缺失时用 endTime 兜底
    if rid is None or bt is None or dl is None:
        log.warning("任务记录缺少 id/时间字段，跳过：%s", list(r0)[:6]); return False
    if ss is not None and ss != 0:                          # 明确已签到（state=1/2）
        log.info("[%s] 已签到(状态%s)，无需操作", name, ss); return True
    if ss is None:
        log.warning("[%s] 签到状态未知(signInStudent 缺失)，保守尝试签到", name)
    t = now(); n = int(t.timestamp() * 1000)
    if not (bt <= n <= dl):
        log.info("[%s] 不在签到时段", name); return False
    st, r = api(f"sign_in/{rid}/student/sign", "lng=119.243462&lat=26.088417", tok)
    if _has(r, "timestamp"):
        kind = "补签" if in_supp_window(t) else "签到"
        log.info("✅ %s成功 [%s] %s", kind, name, hm()); return True
    log.warning("签到失败(%s): %s", st, r[:100]); return False

def seconds_until_next(signed_today=False):
    """计算下次唤醒间隔：区分保活期 / 窗口期 / 窗口边界"""
    t = now()
    if in_main_window(t) or in_supp_window(t):
        if signed_today:
            # 今日已签到：睡到补签结束，避免窗口内每分钟空转
            end = t.replace(hour=SUPP_END[0], minute=SUPP_END[1], second=0, microsecond=0)
            return max(5, (end - t).total_seconds())
        return SIGN_INTERVAL
    # 距离下个窗口开始还有多久？
    nxt = t.replace(hour=MAIN_BEGIN[0], minute=MAIN_BEGIN[1], second=0, microsecond=0)
    if t >= nxt: nxt += datetime.timedelta(days=1)
    secs_to_win = (nxt - t).total_seconds()
    if secs_to_win <= KEEPALIVE_SEC + WAKE_MARGIN:
        return max(5, secs_to_win - WAKE_MARGIN)   # 提前唤醒，别错过窗口
    if 7 <= t.hour < 21:                            # 白天保活
        return KEEPALIVE_SEC * random.uniform(0.8, 1.2)
    return min(secs_to_win - WAKE_MARGIN, 3600)     # 夜间低频检查

def loop(once=False):
    log.info("守护启动 | 账号 %s | 设备 %s", mask(CFG.username,3), mask(CFG.device_id,4))
    _check_tz_warning()
    signed_date = None                       # 已签到日期，避免窗口内重复检查
    while True:
        try:
            t = now()
            today = t.strftime("%Y-%m-%d")
            if in_main_window(t) or in_supp_window(t):
                if signed_date == today:
                    pass                         # 今天已签，跳过剩余窗口
                elif try_sign():
                    signed_date = today
                    log.info("今日任务完成，跳过后续窗口检查")
            elif 7 <= t.hour < 21:
                tok = ensure_token(cooldown=REFRESH_COOLDOWN)
                if tok:
                    r0 = query_task(tok)
                    log.info("保活 ping %s | token=%s", "✅" if r0 else "❌", mask(tok, 8))
            if once: break
            s = seconds_until_next(signed_today=(signed_date == today))
            log.info("下次唤醒：%.0f 秒后", s)
            time.sleep(max(5, s))
        except KeyboardInterrupt:
            log.info("收到中断，退出"); break
        except Exception as e:
            log.error("循环异常：%s", e); time.sleep(60)

if __name__ == "__main__":
    once = "--once" in sys.argv
    if not once:
        _write_pid()                 # --once 是调试模式，不占用 PID 文件
    loop(once=once)
