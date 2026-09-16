# -*- coding: utf-8 -*-
"""CAS 登录流程的离线回归测试（不发任何真实请求）。

对照 reverse/cas/login.js 的三个关键点：
  1. checkNeedCaptcha.htl 是提交前必经一步，缺了它后续验证必然失败
  2. toSliderCaptcha.htl 负责把会话切到滑块模式
  3. tracks 的 a/b/c 都是「累计值」，c 必须单调不减

未安装 numpy/opencv（fafu_login 的依赖）时整组跳过。
"""
import os, sys, random, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
