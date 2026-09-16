# -*- coding: utf-8 -*-
"""核心逻辑回归测试（纯离线，不发任何真实请求）。

运行：
    python -m unittest discover -s tests -v
    python tests/test_fafu.py

覆盖的是历史上真实出过问题的三处：
  1. 刷新失败不落冷却 → 守护进程失败风暴触发 WAF 限流（TokenFlowTest）
  2. 陈旧内存快照全量覆盖 → 抹掉别的进程轮换后的 refresh_token（SaveStateTest）
  3. 响应判定用子串匹配 → 被格式差异击穿（PureFunctionTest.test_has_*）
"""
import os, sys, time, base64, shutil, tempfile, datetime, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import fafu_config, fafu_lib, daemon
from fafu_config import TZ


class StateIsolatedTest(unittest.TestCase):
    """把 state.json 重定向到临时目录，避免测试污染真实会话态"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-test-")
        self._orig_state_path = fafu_config._STATE_PATH
        fafu_config._STATE_PATH = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        fafu_config._STATE_PATH = self._orig_state_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_cfg(self):
        return fafu_config.Config()


class TokenFlowTest(StateIsolatedTest):
    """ensure_token 的冷却/退避行为。

    回归背景：失败路径原先不写 next_refresh_after，daemon 在签到窗口内每分钟
    重打整条刷新链（3 个 auth 请求）；实测 ~20 分钟 30 次即触发 WAF 端点限流
    15–20 分钟——正好覆盖 21:30 签到窗口，直接导致漏签。
    """

    def setUp(self):
        super().setUp()
        self.cfg = self.new_cfg()
        self.cfg.save_state(refresh_token="RT1")
        self._orig_cfg = fafu_lib.CFG
        fafu_lib.CFG = self.cfg
        # 隔离网络：整条刷新链换成可编程假实现
        self.refresh_calls = 0
        self._orig = {n: getattr(fafu_lib, n) for n in
                      ("api", "refresh_we_link", "fetch_authcode", "exchange_fafu_token")}
        fafu_lib.api = lambda *a, **k: (401, '{"error":"token expired"}')
        fafu_lib.refresh_we_link = self._refresh_fail
        fafu_lib.fetch_authcode = lambda wt: (None, 500, "")
        fafu_lib.exchange_fafu_token = lambda ac: (None, 500, "")

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(fafu_lib, name, fn)
        fafu_lib.CFG = self._orig_cfg
        super().tearDown()

    def _refresh_fail(self, rt):
        self.refresh_calls += 1
        return None, None, 429, "rate limited"

    def _expire_backoff(self):
        """把退避时间拨到过去，模拟退避到期"""
        self.cfg.save_state(next_refresh_after=time.time() - 1)

    def test_failure_sets_backoff_instead_of_retrying_every_call(self):
        """失败后必须退避：连续 5 次调用只应打 1 次刷新链"""
        for _ in range(5):
            self.assertIsNone(fafu_lib.ensure_token(cooldown=1800))
        self.assertEqual(self.refresh_calls, 1, "失败未退避，窗口内会形成请求风暴")
        self.assertEqual(self.cfg.get_state("refresh_fail"), 1)
        self.assertGreater(self.cfg.get_state("next_refresh_after"), time.time())

    def test_backoff_grows_then_recovers(self):
        """退避按 1→5→30→120 分钟递增，到期后允许再次尝试"""
        fafu_lib.ensure_token()
        first_wait = self.cfg.get_state("next_refresh_after") - time.time()
        self._expire_backoff()
        fafu_lib.ensure_token()
        self.assertEqual(self.refresh_calls, 2)
        self.assertEqual(self.cfg.get_state("refresh_fail"), 2)
        second_wait = self.cfg.get_state("next_refresh_after") - time.time()
        self.assertGreater(second_wait, first_wait, "退避应逐次变长")
        self.assertAlmostEqual(first_wait, 60, delta=5)      # 第 1 次：1 分钟
        self.assertAlmostEqual(second_wait, 300, delta=5)    # 第 2 次：5 分钟

    def test_success_sets_cooldown_and_clears_failures(self):
        """刷新成功后写入冷却并清零失败计数"""
        self.cfg.save_state(refresh_fail=3)
        fafu_lib.refresh_we_link = lambda rt: ("WT", "RT2", 200, "{}")
        fafu_lib.fetch_authcode = lambda wt: ("AC", 200, "{}")
        fafu_lib.exchange_fafu_token = lambda ac: ("TOK", 200, "{}")
        self.assertEqual(fafu_lib.ensure_token(cooldown=1800), "TOK")
        self.assertEqual(self.cfg.get_state("refresh_fail"), 0)
        self.assertEqual(self.cfg.refresh_token, "RT2", "轮换后的 refresh_token 未落盘")
        self.assertGreater(fafu_lib.refresh_wait(), 1700)

    def test_valid_cached_token_bypasses_cooldown(self):
        """本地 token 仍有效时应直接复用，不受冷却影响"""
        self.cfg.save_state(fafu_token="CACHED")
        fafu_lib.api = lambda *a, **k: (200, '{"records":[{"id":1}]}')
        self.assertEqual(fafu_lib.ensure_token(), "CACHED")
        self.assertEqual(self.refresh_calls, 0)
        self.assertEqual(self.cfg.get_state("next_refresh_after", 0), 0)


class SaveStateTest(StateIsolatedTest):
    """state.json 的跨进程合并写。

    回归背景：save_state 原先按内存快照全量覆盖，daemon 这类长驻进程会抹掉
    其他进程刚写入的键；refresh_token 每次刷新都轮换，被抹掉就得重新短信登录。
    """

    def test_stale_snapshot_does_not_clobber_newer_key(self):
        """陈旧进程写自己的增量时，不得覆盖别的进程刚轮换的 refresh_token"""
        a = self.new_cfg()
        a.save_state(refresh_token="OLD")
        stale = self.new_cfg()                    # 长驻进程：加载后不再重读磁盘
        a.save_state(refresh_token="NEW")         # 另一进程完成轮换
        stale.save_state(fafu_token="TOK")        # 陈旧进程只写自己的增量
        final = self.new_cfg()
        self.assertEqual(final.refresh_token, "NEW", "陈旧快照覆盖了轮换后的 refresh_token")
        self.assertEqual(final.fafu_token, "TOK")

    def test_no_tmp_or_lock_residue(self):
        """写完不留 tmp / lock 残留文件"""
        cfg = self.new_cfg()
        cfg.save_state(fafu_token="X")
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith((".tmp", ".lock"))]
        self.assertEqual(leftovers, [], f"残留临时文件：{leftovers}")

    def test_corrupt_state_file_is_tolerated(self):
        """state.json 损坏时应降级为空态并可正常覆写，而不是抛异常"""
        with open(fafu_config._STATE_PATH, "w", encoding="utf-8") as f:
            f.write("{ 这不是合法 JSON")
        cfg = self.new_cfg()
        self.assertIsNone(cfg.get_state("fafu_token"))
        cfg.save_state(fafu_token="OK")
        self.assertEqual(self.new_cfg().fafu_token, "OK")

    def test_stale_lock_is_reclaimed(self):
        """持有者被强杀留下的残留锁应能自动回收，不永久阻塞"""
        lock = fafu_config._STATE_PATH + ".lock"
        open(lock, "w").close()
        old = time.time() - fafu_config._LOCK_STALE - 10
        os.utime(lock, (old, old))
        self.new_cfg().save_state(fafu_token="OK")          # 不应超时抛错
        self.assertFalse(os.path.exists(lock))


class PureFunctionTest(unittest.TestCase):

    def test_mk_auth_layout(self):
        """签名格式 base64(ts:nonce:md5:token)，与参考仓库一致"""
        raw = base64.b64decode(fafu_lib.mk_auth("http://x/y", "TOK")).decode()
        ts, nonce, digest, token = raw.split(":")
        self.assertTrue(ts.isdigit() and len(nonce) == 16 and len(digest) == 32)
        self.assertEqual(token, "TOK")

    def test_json_never_raises(self):
        """WAF 返回 HTML 时应回落到默认值，而不是抛异常"""
        self.assertEqual(fafu_lib._json("<html>403 Forbidden</html>", "D"), "D")
        self.assertEqual(fafu_lib._json(None, "D"), "D")

    def test_has_survives_formatting_drift(self):
        """_has 走 JSON 键判定，不再被空格等格式差异击穿；非 JSON 回退子串"""
        self.assertTrue(fafu_lib._has('{"records":[]}', "records"))
        self.assertTrue(fafu_lib._has('{"records" : []}', "records"))   # 子串匹配会漏判
        self.assertFalse(fafu_lib._has('{"msg":"无权限"}', "records"))
        self.assertTrue(fafu_lib._has('<html>"records"</html>', "records"))

    def test_mask_hides_tail(self):
        masked = fafu_config.mask("2_abcdef123456", 8)
        self.assertTrue(masked.startswith("2_abcdef"))
        self.assertNotIn("123456", masked)

    def test_cookie_token_handles_empty_value(self):
        """token 值为空时返回 None —— 原写法会在 .group(1) 处崩栈（真实踩过）"""
        self.assertEqual(fafu_lib.cookie_token(["token=abc; Path=/"]), "abc")
        self.assertEqual(fafu_lib.cookie_token(["route=x", "token=z9; Max-Age=7200"]), "z9")
        self.assertIsNone(fafu_lib.cookie_token(["token=; Path=/; HttpOnly"]))
        self.assertIsNone(fafu_lib.cookie_token([]))
        self.assertIsNone(fafu_lib.cookie_token(None))

    def test_config_rejects_typo(self):
        """拼错的会话态键应明确报错，而非静默返回空串"""
        with self.assertRaises(AttributeError):
            fafu_config.Config().not_a_real_key


class PidFileTest(unittest.TestCase):
    """daemon 的 PID 文件生命周期（run.sh / run.bat 靠它防止重复启动）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-test-")
        self._orig_pid_path = daemon._PID_PATH
        daemon._PID_PATH = os.path.join(self.tmp, "daemon.pid")

    def tearDown(self):
        daemon._PID_PATH = self._orig_pid_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_then_clear(self):
        """退出后必须删掉 PID 文件：Windows 上文件要先关闭才能删（曾因此静默失败）"""
        daemon._write_pid()
        self.assertTrue(os.path.exists(daemon._PID_PATH))
        daemon._clear_pid()
        self.assertFalse(os.path.exists(daemon._PID_PATH), "PID 文件未清理")

    def test_clear_leaves_foreign_pid_file(self):
        """PID 文件属于别的进程时不得删除，避免误删后继实例的"""
        with open(daemon._PID_PATH, "w", encoding="utf-8") as f:
            f.write("999999")
        daemon._clear_pid()
        self.assertTrue(os.path.exists(daemon._PID_PATH))


class DaemonWindowTest(unittest.TestCase):
    """签到窗口边界：21:30–22:30 主窗口，22:30–23:00 补签窗口（22:30 归主窗口）"""

    @staticmethod
    def _t(h, m, s=0):
        return datetime.datetime(2026, 9, 15, h, m, s, tzinfo=TZ)

    def test_main_window_bounds(self):
        self.assertFalse(daemon.in_main_window(self._t(21, 29, 59)))
        self.assertTrue(daemon.in_main_window(self._t(21, 30)))
        self.assertTrue(daemon.in_main_window(self._t(22, 30)))

    def test_supplement_window_bounds(self):
        self.assertFalse(daemon.in_supp_window(self._t(22, 30)))
        self.assertTrue(daemon.in_supp_window(self._t(22, 31)))
        self.assertTrue(daemon.in_supp_window(self._t(23, 0)))
        self.assertFalse(daemon.in_supp_window(self._t(23, 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
