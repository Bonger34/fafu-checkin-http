# -*- coding: utf-8 -*-
"""CAS 登录流程的离线回归测试（不发任何真实请求）。

对照 reverse/cas/login.js 的三个关键点：
  1. checkNeedCaptcha.htl 是提交前必经一步，缺了它后续验证必然失败
  2. toSliderCaptcha.htl 负责把会话切到滑块模式
  3. tracks 的 a/b/c 都是「累计值」，c 必须单调不减
外加 login_once 的收尾守卫：拿不到 refresh_token 时必须中止，
而不是写下一份无法续期的会话态、却照样报告「登录成功」。

未安装 numpy/opencv（fafu_login 的依赖）时整组跳过。
"""
import os, sys, json, random, shutil, tempfile, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")          # 模块在 src/，测试仍在 tests/
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import fafu_config

fafu_login = None
_IMPORT_ERR = ""
try:
    import fafu_login
except ImportError as exc:
    _IMPORT_ERR = str(exc)

_NEED_DEPS = unittest.skipIf(fafu_login is None, f"需要 numpy/opencv：{_IMPORT_ERR}")


@_NEED_DEPS
class CaptchaGateTest(unittest.TestCase):
    """checkNeedCaptcha 的解析"""

    def setUp(self):
        self._orig = fafu_login._cas
        self._stub('{"isNeed":true}')

    def tearDown(self):
        fafu_login._cas = self._orig

    def _stub(self, body, status=200):
        fafu_login._cas = lambda *a, **k: (status, body, "http://cas/x", None)

    def test_isneed_true(self):
        self.assertTrue(fafu_login._need_captcha("0250000000"))

    def test_isneed_false(self):
        self._stub('{"isNeed":false}')
        self.assertFalse(fafu_login._need_captcha("0250000000"))

    def test_non_json_is_not_treated_as_need(self):
        """WAF 拦截页不能被误判成「需要验证码」"""
        self._stub("<html>403 Forbidden</html>")
        self.assertFalse(fafu_login._need_captcha("0250000000"))


@_NEED_DEPS
class SliderSwitchTest(unittest.TestCase):
    """toSliderCaptcha 的判定"""

    def setUp(self):
        self._orig = fafu_login._cas

    def tearDown(self):
        fafu_login._cas = self._orig

    def test_accepts_slider_markup(self):
        fafu_login._cas = lambda *a, **k: (200, '<div id="sliderDiv" style="width: 280px"></div>', "", None)
        self.assertTrue(fafu_login._to_slider())

    def test_rejects_unrelated_page(self):
        fafu_login._cas = lambda *a, **k: (200, "<html>登录页</html>", "", None)
        self.assertFalse(fafu_login._to_slider())

    def test_rejects_error_status(self):
        fafu_login._cas = lambda *a, **k: (500, '<div id="sliderDiv"></div>', "", None)
        self.assertFalse(fafu_login._to_slider())


@_NEED_DEPS
class TracksTest(unittest.TestCase):
    """滑动轨迹必须是累计值——原实现把 c 写成每步随机 20~50（时间非单调）"""

    def setUp(self):
        random.seed(20260916)
        self.tr = fafu_login._tracks(140)

    def test_starts_at_origin(self):
        self.assertEqual(self.tr[0], {"a": 0, "b": 0, "c": 0})

    def test_c_is_monotonic(self):
        cs = [p["c"] for p in self.tr]
        self.assertEqual(cs, sorted(cs), "c 是距滑动开始的时间，必须单调不减")
        self.assertGreater(cs[-1], 0)

    def test_a_is_cumulative_and_lands_on_target(self):
        a = [p["a"] for p in self.tr]
        self.assertEqual(a, sorted(a))
        self.assertEqual(a[-1], 140, "末点位移必须等于 moveLength")

    def test_b_drifts_slowly(self):
        """b 是累计 Y 位移：单步变化不超过 1，整体贴近 0"""
        bs = [p["b"] for p in self.tr]
        for prev, cur in zip(bs, bs[1:]):
            self.assertLessEqual(abs(cur - prev), 1)
        self.assertLessEqual(max(abs(b) for b in bs), 8)


@_NEED_DEPS
class CasBinaryTest(unittest.TestCase):
    """_cas 必须能返回二进制——验证码图片是 JPEG，按 UTF-8 解码会抛异常"""

    def setUp(self):
        import urllib.request
        self._urlopen = urllib.request.urlopen

        class _Resp:
            status = 200
            url = "http://cas/x"
            headers = {}
            def read(self):
                return b"\xff\xd8\xff\xe0JFIF\x00\x01"

        urllib.request.urlopen = lambda *a, **k: _Resp()

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._urlopen

    def test_binary_returns_raw_bytes(self):
        st, body, _, _ = fafu_login._cas("http://cas/getCaptcha.htl", binary=True)
        self.assertEqual(st, 200)
        self.assertIsInstance(body, bytes)
        self.assertTrue(body.startswith(b"\xff\xd8"), "JPEG 魔数应完整保留")

    def test_default_mode_returns_str(self):
        st, body, _, _ = fafu_login._cas("http://cas/login")
        self.assertIsInstance(body, str)


@_NEED_DEPS
class PickCaptchaTest(unittest.TestCase):
    """图形验证码的长度过滤：固定 4 位，长度不符应换图而不是提交"""

    def test_accepts_matching_length(self):
        calls = []

        def fetch():
            calls.append(1)
            return b"img"

        self.assertEqual(fafu_login._pick_captcha(fetch, lambda i: "a1b2"), "a1b2")
        self.assertEqual(len(calls), 1, "合格结果不应重复取图")

    def test_retries_until_length_matches(self):
        seq = iter(["abc", "a1b2c3", "  x9Y7  "])
        self.assertEqual(fafu_login._pick_captcha(lambda: b"img", lambda i: next(seq)), "x9Y7")

    def test_gives_up_and_returns_none(self):
        """始终不合格时应返回 None，由调用方决定重试或回退"""
        self.assertIsNone(fafu_login._pick_captcha(lambda: b"img", lambda i: "abc", retry=3))

    def test_skips_empty_image(self):
        seq = iter([None, b"img"])
        self.assertEqual(fafu_login._pick_captcha(lambda: next(seq), lambda i: "a1b2"), "a1b2")

    def test_recognizer_exception_does_not_abort(self):
        """识别抛异常应被吞掉并继续重试，而不是把登录流程整个打断"""
        def boom(img):
            raise ValueError("model error")

        self.assertIsNone(fafu_login._pick_captcha(lambda: b"img", boom, retry=2))


@_NEED_DEPS
class LoginOnceGuardTest(unittest.TestCase):
    """login_once 的收尾守卫：拿不到 refresh_token 时必须中止。

    回归背景：oauth_to_welink 在响应缺 refresh_token 时返回 None，而 main()
    只检查了 we_link_token。于是脚本会写下一份无法续期的会话态（refresh_token
    落成 null），却照样打印「✅ 登录成功！refresh_token 已保存」——用户要等
    token 过期才会发现，中途没有任何提示。

    登录链的三个网络协作者全部替换，只验证 main() 的收尾判定。
    """

    ENV = {"FAFU_USERNAME": "u", "FAFU_PASSWORD": "p",
           "FAFU_DEVICE_ID": "d", "FAFU_TENANT_ID": "t"}

    def setUp(self):
        import login_once
        self.mod = login_once
        self.tmp = tempfile.mkdtemp(prefix="fafu-login-")
        self._orig_state = fafu_config._STATE_PATH
        fafu_config._STATE_PATH = os.path.join(self.tmp, "state.json")
        # 用环境变量喂凭据：既不读 config.ini 里的真实配置，也不依赖该文件存在
        self._env = {k: os.environ.get(k) for k in self.ENV}
        os.environ.update(self.ENV)

        self._orig = {n: getattr(login_once, n) for n in
                      ("CFG", "login", "submit_mfa", "oauth_to_welink")}
        login_once.CFG = fafu_config.Config()
        # 三个替身都用 *a/**k：不写死参数名与个数，签名变化时不会静默失效
        login_once.login = lambda *a, **k: ("SVC", "COOKIE")
        login_once.submit_mfa = lambda *a, **k: ("OAUTH", "info")
        self._argv = sys.argv
        sys.argv = ["login_once.py", "123456"]       # 传验证码，绕开 input()

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(self.mod, name, fn)
        fafu_config._STATE_PATH = self._orig_state
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.argv = self._argv
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        with open(fafu_config._STATE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_missing_refresh_token_aborts_before_writing(self):
        """响应缺 refresh_token → 中止，且不得落盘任何会话态"""
        self.mod.oauth_to_welink = lambda *a, **k: ("WT", None, 200, "{}")
        with self.assertRaises(SystemExit) as cm:
            self.mod.main()
        self.assertIn("refresh_token", str(cm.exception))
        self.assertFalse(os.path.exists(fafu_config._STATE_PATH),
                         "写入了无法续期的会话态")

    def test_normal_path_saves_both_tokens(self):
        """正常路径不受影响：两个 token 都落盘"""
        self.mod.oauth_to_welink = lambda *a, **k: ("WT", "RT", 200, "{}")
        self.mod.main()
        st = self._state()
        self.assertEqual(st["refresh_token"], "RT")
        self.assertEqual(st["we_link_token"], "WT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
