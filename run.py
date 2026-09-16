#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字FAFU 自动签到 —— 管理脚本（start | stop | status | log）

唯一的启动入口，Windows / macOS / Linux 三端同一份代码：

  python run.py start    启动守护
  ./run.py start         POSIX 下直接执行（已设可执行位）
  run.bat / run.sh       已删除——转发壳只会制造行为漂移，见 DEVELOPMENT.md

Windows 下 start 会开一个可见窗口，日志实时打在里面，**关掉窗口即停止签到**。
POSIX 下没有「窗口」概念，静默后台运行，用 `run.py log -f` 看实时日志。

平台差异只集中在两处：_spawn_daemon（拉起子进程）与 _stop_proc（停止进程）。

设计要点：
  · 日志由 daemon.py 自己双写（UTF-8 文件 + 标准输出），不依赖 shell 重定向。
    这样中文 Windows 下日志文件编码是确定的，也省掉 chcp / PYTHONUTF8 那对 hack。
  · PID 文件记录「pid + 启动时刻」，用于防止 PID 复用导致的误判/误杀。
    psutil 可用时用进程真实创建时间比对；不可用时降级为「仅确认 PID 存在」，
    并在 status 里明确标注、stop 时拒绝执行。
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIDF = HERE / "daemon.pid"
LOGF = HERE / "daemon.log"
ENTRY = HERE / "daemon.py"
TITLE = "fafu-checkin"
PY = sys.executable          # 用当前解释器，天然修正 venv / python vs python3 的差异

try:
    import psutil
except ImportError:          # 允许缺省：降级为弱判据，但会明确提示
    psutil = None


def _console():
    """本脚本自己也要打印 emoji；输出被重定向时避免 GBK 崩栈。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------- PID 文件

def read_pid():
    """读 PID 文件，返回 dict(pid, started) 或 None。

    兼容两种格式：新版 JSON {"pid":..,"started":..}，以及旧版纯数字一行。
    旧格式没有启动时刻，只能做弱校验。
    """
    try:
        raw = PIDF.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    if raw.startswith("{"):
        import json
        try:
            d = json.loads(raw)
            return {"pid": int(d["pid"]), "started": float(d.get("started") or 0)}
        except (ValueError, KeyError, TypeError):
            return None
    if raw.isdigit():
        return {"pid": int(raw), "started": 0.0}
    return None


def probe(info):
    """判断 PID 是否确实是我们的 daemon。

    返回 (state, detail)，state ∈ {"running", "stale", "unverified"}：
      running    —— 存活且身份确认
      stale      —— PID 不存在，或存在但不是我们的进程（PID 被复用）
      unverified —— 存活但判据不足以确认（缺 psutil，或旧格式无启动时刻）
    """
    if not info:
        return "stale", "无 PID 记录"
    pid = info["pid"]
    if psutil is None:
        return ("unverified", "缺 psutil，仅确认 PID 存在") if _pid_exists(pid) \
            else ("stale", "PID 不存在")
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return "stale", "PID 不存在"
    except psutil.Error as e:
        return "unverified", f"查询进程失败: {e}"

    # 启动时刻比对：比只看进程名可靠得多——同机常年跑着别的 python 进程，
    # 一旦 PID 被复用给它们，按名字判断就会误判成「我们的 daemon 还在跑」。
    started = info.get("started") or 0
    if started:
        try:
            drift = abs(p.create_time() - started)
        except psutil.Error:
            drift = None
        if drift is not None:
            if drift <= 5:
                return "running", f"{p.name()}"
            return "stale", f"PID 被复用（创建时间差 {drift:.0f}s）"
    # 旧格式或取不到创建时间：退回名字判断
    try:
        name = (p.name() or "").lower()
    except psutil.Error:
        return "unverified", "取不到进程名"
    return ("unverified", f"{name}（无启动时刻，判据降级）") if name.startswith("python") \
        else ("stale", f"PID 被非 python 进程占用（{name}）")


def _pid_exists(pid):
    """不依赖 psutil 的存在性检查。"""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- 子命令

def _spawn_daemon():
    """拉起守护进程。

    Windows：新开一个可见控制台窗口。日志同时写到窗口和 daemon.log；
             关闭窗口 = 停止守护（用户要的就是这个直觉）。
    POSIX  ：没有「窗口」概念，用 nohup 式脱离；要看实时日志用 run.py log -f。
    """
    kwargs = {"cwd": str(HERE), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kwargs["start_new_session"] = True
        # 日志由 daemon.py 自己写文件，stdout 丢弃即可（避免与 FileHandler 重复）
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.Popen([PY, str(ENTRY)], **kwargs)


def cmd_start(_):
    info = read_pid()
    state, detail = probe(info)
    if state == "running":
        print(f"已在运行 (PID {info['pid']}, {detail})")
        return 0
    if info:
        print(f"检测到残留 PID 文件（PID {info['pid']}：{detail}），继续启动")

    try:
        child = _spawn_daemon()
    except OSError as e:
        print(f"❌ 启动失败: {e}")
        return 1

    # 等 daemon 自己写回 PID 文件，确认它真的起来了（而不是窗口一闪就崩）
    for _ in range(20):
        time.sleep(0.25)
        info2 = read_pid()
        if info2 and info2["pid"] == child.pid:
            break
    else:
        if child.poll() is not None:
            print(f"❌ 守护进程启动后立即退出（退出码 {child.returncode}），请查看 {LOGF.name}")
            return 1
        print("⚠ 守护进程已启动，但尚未写回 PID 文件")

    if os.name == "nt":
        print(f"✅ 已启动 (PID {child.pid})，日志见弹出的窗口与 {LOGF.name}")
        print("   关闭那个窗口即停止签到")
    else:
        print(f"✅ 已启动 (PID {child.pid})，日志见 {LOGF.name}")
        print(f"   实时查看： python {Path(__file__).name} log -f")
    return 0


def _stop_proc(pid):
    """结束进程及其子进程。返回 True 表示已确认结束。"""
    if psutil is not None:
        try:
            p = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        victims = p.children(recursive=True) + [p]
        for c in victims:
            try:
                c.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(victims, timeout=8)
        for c in alive:
            try:
                c.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
        return not psutil.pid_exists(pid)

    # 无 psutil：退回系统命令。继承 stdio，不使用管道。
    if os.name == "nt":
        rc = subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal as _sig
        try:
            os.killpg(os.getpgid(pid), _sig.SIGTERM)
        except OSError:
            try:
                os.kill(pid, _sig.SIGTERM)
            except OSError:
                return True
        rc = 0
    time.sleep(1)
    return rc == 0 or not _pid_exists(pid)


def cmd_stop(_):
    info = read_pid()
    if not info:
        print("未在运行（没有 daemon.pid）")
        return 0
    state, detail = probe(info)
    if state == "stale":
        print(f"未在运行（PID {info['pid']}：{detail}）")
        print(f"  残留的 {PIDF.name} 已保留，确认无用可手动删除")
        return 0
    if state == "unverified":
        print(f"⚠ 无法确认 PID {info['pid']} 就是本项目的守护进程（{detail}）")
        print("  为免误杀无关进程，已中止。请安装 psutil 后重试： pip install psutil")
        return 1

    ok = _stop_proc(info["pid"])
    if ok:
        try:
            PIDF.unlink()
        except OSError:
            pass
        print(f"✅ 已停止 (PID {info['pid']})")
        return 0
    print(f"未能结束进程 {info['pid']}（PID 文件已保留，如确认在运行请手动结束）")
    return 1


def cmd_status(_):
    info = read_pid()
    state, detail = probe(info)
    label = {"running": "🟢 运行中", "unverified": "🟡 状态存疑", "stale": "⏸ 未运行"}[state]
    print(f"守护进程: {label}" + (f" (PID {info['pid']}, {detail})" if info else ""))
    if psutil is None:
        print("  ⚠ 未安装 psutil，身份判据已降级（pip install psutil 可恢复）")
    print()
    # 必须先 flush：子进程直接写控制台，而本进程的 stdout 还是缓冲的，
    # 不 flush 会让子进程的输出插到前面，读者看到的是颠倒的顺序。
    sys.stdout.flush()
    # 直接跑子命令并继承 stdio：不用管道，避免受限环境下失败
    return subprocess.call([PY, str(HERE / "rootless_checkin.py"), "status"], cwd=str(HERE))


def cmd_log(args):
    if not LOGF.exists():
        print(f"无日志（{LOGF.name} 不存在）")
        return 0
    with open(LOGF, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    tail = lines[-args.n:]
    sys.stdout.write("".join(tail))
    if not args.follow:
        return 0

    print(f"--- 跟随中（Ctrl+C 退出）；关闭本窗口不影响签到 ---", flush=True)
    with open(LOGF, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                chunk = f.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n已退出查看")
    return 0


def main():
    _console()
    ap = argparse.ArgumentParser(prog="run.py", description="数字FAFU 自动签到管理")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("start", help="启动守护进程")
    sub.add_parser("stop", help="停止守护进程")
    sub.add_parser("status", help="查看守护与会话状态")
    p_log = sub.add_parser("log", help="查看日志")
    p_log.add_argument("-f", "--follow", action="store_true", help="持续跟随输出")
    p_log.add_argument("-n", type=int, default=30, help="显示末尾 N 行（默认 30）")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    return {"start": cmd_start, "stop": cmd_stop,
            "status": cmd_status, "log": cmd_log}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
