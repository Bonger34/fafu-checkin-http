# -*- coding: utf-8 -*-
"""run.py 管理脚本的离线测试：PID 文件解析、身份判定、子命令分派。

不发任何真实请求，不拉起真实进程（start/stop 只测「不该动的时候不动」）。

    test_fafu.py   单个函数/组件的边界行为
    test_flow.py   完整业务链路
    test_run.py    管理脚本（本文件）

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import json
import time
import shutil
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import run as runmod

try:
    import psutil
except ImportError:
    psutil = None


def _free_pid():
    """找一个当前不存在的 PID，用于模拟「残留 PID 文件」。"""
    if psutil is not None:
        for cand in range(400000, 400200):
            if not psutil.pid_exists(cand):
                return cand
    return 4194303


class PidFileTest(unittest.TestCase):
    """read_pid 要能吃下新旧两种格式，并对垃圾内容保持沉默。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-run-")
        self._orig = runmod.PIDF
        runmod.PIDF = __import__("pathlib").Path(self.tmp) / "daemon.pid"

    def tearDown(self):
        runmod.PIDF = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        with open(runmod.PIDF, "w", encoding="utf-8") as f:
            f.write(text)

    def test_missing_file_is_none(self):
        self.assertIsNone(runmod.read_pid())

    def test_json_format(self):
        self._write(json.dumps({"pid": 4321, "started": 1700000000.5}))
        info = runmod.read_pid()
        self.assertEqual(info["pid"], 4321)
        self.assertAlmostEqual(info["started"], 1700000000.5)

    def test_legacy_plain_int(self):
        """旧版 daemon 只写一行数字，升级后不能因此判成「未运行」。"""
        self._write("4321")
        info = runmod.read_pid()
        self.assertEqual(info["pid"], 4321)
        self.assertEqual(info["started"], 0.0)      # 无启动时刻 -> 判据降级

    def test_empty_and_garbage_are_none(self):
        for bad in ("", "   ", "not-a-pid", "{broken", "{}"):
            self._write(bad)
            self.assertIsNone(runmod.read_pid(), f"输入 {bad!r} 应返回 None")

    def test_json_without_started(self):
        self._write(json.dumps({"pid": 7}))
        self.assertEqual(runmod.read_pid()["started"], 0.0)


class ProbeTest(unittest.TestCase):
    """probe 是防 PID 复用的核心：绝不能把别的进程认成我们的 daemon。"""

    def setUp(self):
        if psutil is None:
            self.skipTest("需要 psutil")
        self.me = os.getpid()
        self.my_start = psutil.Process(self.me).create_time()

    def test_none_info(self):
        self.assertEqual(runmod.probe(None)[0], "stale")

    def test_dead_pid_is_stale(self):
        state, detail = runmod.probe({"pid": _free_pid(), "started": time.time()})
        self.assertEqual(state, "stale")
        self.assertIn("不存在", detail)

    def test_matching_start_time_is_running(self):
        state, _ = runmod.probe({"pid": self.me, "started": self.my_start})
        self.assertEqual(state, "running")

    def test_pid_reuse_is_detected(self):
        """PID 存在、也是 python，但创建时间对不上 —— 正是 PID 复用的情形。

        旧实现只比对进程名（-like 'python*'），这里会被骗到；比对启动时刻才挡得住。
        """
        state, detail = runmod.probe({"pid": self.me, "started": self.my_start - 9999})
        self.assertEqual(state, "stale")
        self.assertIn("复用", detail)

    def test_legacy_without_started_degrades(self):
        """旧格式没有启动时刻：本进程是 python，只能给「存疑」而不是「运行中」。"""
        state, detail = runmod.probe({"pid": self.me, "started": 0})
        self.assertEqual(state, "unverified")
        self.assertIn("降级", detail)


class CommandTest(unittest.TestCase):
    """子命令在「不该动作」时必须什么都不做。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-run-")
        self._orig = (runmod.PIDF, runmod.LOGF)
        import pathlib
        runmod.PIDF = pathlib.Path(self.tmp) / "daemon.pid"
        runmod.LOGF = pathlib.Path(self.tmp) / "daemon.log"

    def tearDown(self):
        runmod.PIDF, runmod.LOGF = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _silent(fn, *a):
        """跑子命令并吞掉它的 stdout —— 这些命令是给人看的，测试只关心返回值，
        不吞的话测试输出里会混进一堆状态文字，真失败反而被淹没。"""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(*a)
        return rc, buf.getvalue()

    def test_stop_without_pidfile_is_noop(self):
        rc, out = self._silent(runmod.cmd_stop, None)
        self.assertEqual(rc, 0)
        self.assertIn("没有 daemon.pid", out)

    def test_stop_refuses_on_stale_pid(self):
        """PID 不是我们的进程时必须拒绝，绝不能误杀。"""
        runmod.PIDF.write_text(json.dumps({"pid": _free_pid(), "started": time.time()}),
                               encoding="utf-8")
        rc, _ = self._silent(runmod.cmd_stop, None)
        self.assertEqual(rc, 0)
        self.assertTrue(runmod.PIDF.exists(), "存疑/失效的 PID 文件应保留，便于排查")

    def test_start_refuses_when_already_running(self):
        """已在运行时不重复拉起 —— 多实例会并发空打刷新链。"""
        if psutil is None:
            self.skipTest("需要 psutil")
        me = os.getpid()
        runmod.PIDF.write_text(
            json.dumps({"pid": me, "started": psutil.Process(me).create_time()}),
            encoding="utf-8")
        called = []
        orig = runmod._spawn_daemon
        runmod._spawn_daemon = lambda: called.append(1)
        try:
            rc, out = self._silent(runmod.cmd_start, None)
        finally:
            runmod._spawn_daemon = orig
        self.assertEqual(rc, 0)
        self.assertIn("已在运行", out)
        self.assertEqual(called, [], "已在运行时不应再拉起新进程")

    def test_log_without_file(self):
        rc, out = self._silent(runmod.cmd_log,
                               type("A", (), {"n": 10, "follow": False})())
        self.assertEqual(rc, 0)
        self.assertIn("无日志", out)

    def test_log_tails_n_lines(self):
        runmod.LOGF.write_text("".join(f"line{i}\n" for i in range(100)), encoding="utf-8")
        _, out = self._silent(runmod.cmd_log,
                              type("A", (), {"n": 3, "follow": False})())
        self.assertIn("line99", out)
        self.assertIn("line97", out)
        self.assertNotIn("line96", out)

    def test_status_reports_daemon_state_without_touching_network(self):
        """status 会转调 rootless_checkin.py（真跑会打接口），这里打桩。"""
        calls = []
        orig = runmod.subprocess.call
        runmod.subprocess.call = lambda *a, **k: calls.append(a) or 0
        try:
            rc, out = self._silent(runmod.cmd_status, None)
        finally:
            runmod.subprocess.call = orig
        self.assertEqual(rc, 0)
        self.assertIn("未运行", out)
        self.assertEqual(len(calls), 1, "应转调一次 rootless_checkin.py status")


class CliTest(unittest.TestCase):
    """命令行分派：无参数给帮助，未知子命令由 argparse 拒绝。"""

    def test_no_args_prints_help(self):
        import io
        from contextlib import redirect_stdout
        orig = sys.argv
        sys.argv = ["run.py"]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = runmod.main()
        finally:
            sys.argv = orig
        self.assertEqual(rc, 0)
        self.assertIn("start", buf.getvalue())

    def test_unknown_subcommand_exits(self):
        import io
        from contextlib import redirect_stderr
        orig = sys.argv
        sys.argv = ["run.py", "bogus"]
        err = io.StringIO()
        try:
            with redirect_stderr(err), self.assertRaises(SystemExit):
                runmod.main()
        finally:
            sys.argv = orig
        self.assertIn("invalid choice", err.getvalue())


if __name__ == "__main__":
    unittest.main()
