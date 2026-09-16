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
import sys, os, time, random, atexit, json, datetime, logging
from fafu_config import CFG, TZ, mask, setup_console
from fafu_lib import api, ensure_token, query_task, _has

setup_console()          # 中文 Windows 控制台默认 GBK，不处理会在打印 ✅ 时崩栈

_HERE = os.path.dirname(os.path.abspath(__file__))
_START_TS = time.time()  # 进程启动时刻，写进 PID 文件供 run.py 识破 PID 复用

# ---- 调度参数（可调）----
KEEPALIVE_SEC   = 20 * 60      # 保活间隔（±20% 抖动）
REFRESH_COOLDOWN= 30 * 60      # token 刷新冷却
MAIN_BEGIN      = (21, 30)     # 主窗口开始
MAIN_END        = (22, 30)     # 主窗口结束
SUPP_END        = (23, 0)      # 补签截止
SIGN_INTERVAL   = 60           # 窗口内检查间隔（秒）
WAKE_MARGIN     = 30           # 提前 30 秒唤醒（避免错过边界）

# 不在模块层配 basicConfig：它会给 root 加 StreamHandler，而 "fafu" 的 handler
# 又会向 root 传播，日志会被打印两遍。真正的 handler 由 _setup_logging() 装，
# 且只在作为脚本运行时装；被 import（例如单测）时保持安静。
log = logging.getLogger("fafu")
log.addHandler(logging.NullHandler())

_PID_PATH = os.path.join(_HERE, "daemon.pid")
_LOG_PATH = os.path.join(_HERE, "daemon.log")

def _setup_logging():
    """日志双写：文件固定 UTF-8 + 标准输出。

    由 daemon 自己写文件（而不是让启动脚本 `>>` 重定向）是因为 shell 重定向在
    中文 Windows 下按控制台代码页写，日志会变成 GBK；读的时候又是另一个坑。

    stdout 始终挂 StreamHandler，由调用方决定它通向哪里：
      · run.py start（Windows）→ 新控制台窗口，于是窗口里能实时看到
      · run.py start（POSIX） → /dev/null，只有文件日志
      · systemd              → journald，保持和以前一样的可观测性
    所以**手工启动时不要把 stdout 重定向到 daemon.log**，否则会双写。
    """
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()                      # 去掉 import 期占位的 NullHandler，保证幂等
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)

def _prep_console():
    """Windows 控制台两件事，都只在「窗口模式」下才有意义：

    1. **关掉 QuickEdit**。用户在窗口里拖选文本会让控制台进入标记模式，此后
       任何写 stdout 的进程都会阻塞在 WriteConsole 上。daemon 一卡可能就是
       几小时，正好错过签到窗口——这是把日志放进可见窗口必须付的代价。
    2. 设置窗口标题，便于一眼认出是哪个脚本。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetConsoleTitleW("fafu-checkin 自动签到 —— 关闭本窗口即停止")
        h = k32.GetStdHandle(-11)                     # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            k32.SetConsoleMode(h, (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT)
    except Exception:
        pass

_ctrl_ref = None

def _install_close_handler():
    """关窗口 / 注销 / 关机时清掉 PID 文件。

    atexit 在这些情况下不会执行（进程是被系统直接终止的），不处理就会一直
    留下残留 PID 文件，下次 start 都要先报一句「检测到残留」。
    """
    global _ctrl_ref
    if os.name != "nt":
        import signal
        # POSIX：run.py stop 发的是 SIGTERM，转成正常退出以便 atexit 清理
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        return
    try:
        import ctypes
        HANDLER = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        def _on_event(evt):
            if evt in (2, 5, 6):                      # CLOSE / LOGOFF / SHUTDOWN
                try:
                    os.remove(_PID_PATH)
                except OSError:
                    pass
            return False                              # 交回默认处理，正常退出

        _ctrl_ref = HANDLER(_on_event)                # 必须持引用，否则会被 GC 回收
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_ref, True)
    except Exception:
        pass

def _clear_pid():
    """退出时清理 PID 文件；仅当文件仍属于本进程才删，避免误删后继实例的"""
    try:
        with open(_PID_PATH, encoding="utf-8") as f:
            pid = json.loads(f.read()).get("pid")
        # 必须在 with 之外删除：Windows 不允许删除仍处于打开状态的文件
        if pid == os.getpid():
            os.remove(_PID_PATH)
    except (OSError, ValueError, AttributeError):
        pass

def _write_pid():
    """写 pid + 启动时刻。启动时刻让 run.py 能比对进程真实创建时间，
    从而识破 PID 复用（daemon 被强杀后 PID 被分给别的 python 进程）。"""
    with open(_PID_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started": _START_TS}, f)
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
    st, r = api(f"sign_in/{rid}/student/sign", f"lng={CFG.lng}&lat={CFG.lat}", tok)
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
    _prep_console()                  # 关 QuickEdit + 设窗口标题（仅 Windows 窗口模式有意义）
    _install_close_handler()         # 关窗口时也能清掉 PID 文件
    _setup_logging()
    once = "--once" in sys.argv
    try:
        if not once:
            _write_pid()             # --once 是调试模式，不占用 PID 文件
        loop(once=once)
    except SystemExit:
        raise
    except BaseException:
        log.exception("守护进程异常退出")
        # 窗口模式下如果直接退出，窗口一闪就没了，用户完全不知道发生了什么。
        if sys.stdout is not None and sys.stdout.isatty():
            try:
                input("\n发生错误（详见上面日志），按回车关闭窗口...")
            except Exception:
                pass
        raise
