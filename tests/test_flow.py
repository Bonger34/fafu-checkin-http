# -*- coding: utf-8 -*-
"""离线端到端演练：只把最底层的网络出口（fafu_lib._request）换成假实现，
其余全部走真实代码路径——签名、JSON 解析、会话保障、刷新退避、签到决策都是真的。

与 test_fafu.py 的分工：
    test_fafu.py   验证单个函数/组件的边界行为
    test_flow.py   验证「一条完整业务链路能否走通」

不发任何真实请求，不需要账号与凭据。

运行：python -m unittest discover -s tests -v
"""
import os, sys, json, time, base64, shutil, tempfile, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import fafu_config, fafu_lib, daemon


def _now_ms():
    return int(time.time() * 1000)


class FlowDrillTest(unittest.TestCase):
    """链路：refresh_token → WeLink token → authCode → FAFU token → 查询任务 → 签到"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-flow-")
        self._orig = (fafu_config._STATE_PATH, fafu_lib.MIN_GAP, fafu_lib._request)
        fafu_config._STATE_PATH = os.path.join(self.tmp, "state.json")
        fafu_lib.MIN_GAP = 0                    # 免去请求节流的等待

        self.calls = []                         # 记录所有外发请求，供断言
        self.refresh_ok = True
        self.sign_ok = True
        self.sign_state = 0
        self.task_shift_ms = 0
        fafu_lib._request = self._fake_request

    def tearDown(self):
        fafu_config._STATE_PATH, fafu_lib.MIN_GAP, fafu_lib._request = self._orig
        fafu_config.CFG._state = {}
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 假网络出口：按端点返回构造好的响应 ----
    def _fake_request(self, url, data=None, headers=None, method=None, retry=3):
        self.calls.append(url)
        if "refresh/LoginReg" in url:
            if not self.refresh_ok:
                return 429, '{"error":"rate limited"}', None
            return (200, json.dumps({"refresh_token": "RT_ROTATED", "expires_in": 7200}),
                    ["token=WT_NEW; Max-Age=7200"])
        if "sso/auth/v2/code" in url:
            return 200, json.dumps({"code": "AUTHCODE_1"}), None
        if "third_party/welink/login" in url:
            return 200, json.dumps({"isRegister": 1, "userLoginResp": {"token": "FAFU_TOK_NEW"}}), None
        if "sign_in/student/my/page" in url:
            if self._token_of(headers) == "FAFU_TOK_EXPIRED":
                return 401, '{"error":"token expired"}', None
            return 200, json.dumps({"records": [self._task()]}), None
        if "/student/sign" in url:
            if not self.sign_ok:
                return 200, json.dumps({"code": 500, "message": "不在签到时段"}), None
            return 200, json.dumps({"code": 200, "timestamp": _now_ms()}), None
        return 404, "{}", None

    @staticmethod
    def _token_of(headers):
        """从签名头还原调用方实际使用的 token：base64(ts:nonce:md5:token)"""
        try:
            raw = base64.b64decode((headers or {}).get("Authorization", "")).decode()
            return raw.split(":")[-1]
        except Exception:
            return ""

    def _task(self):
        n = _now_ms() + self.task_shift_ms
        return {"id": 306764, "name": "晚查寝签到",
                "beginTime": n - 3600_000, "endTime": n + 3600_000,
                "supplementEndTime": n + 7200_000,
                "signInStudent": {"signState": self.sign_state}}

    def _count(self, keyword):
        return sum(1 for u in self.calls if keyword in u)

    def _seed(self, **session):
        """写入初始会话态（daemon 与 fafu_lib 共用同一个 CFG 单例）"""
        fafu_config.CFG._state = {}
        fafu_config.CFG.save_state(**session)

    def _state(self):
        with open(fafu_config._STATE_PATH, encoding="utf-8") as f:
            return json.load(f)

    # ---- 链路 1：token 有效，直连签到 ----
    def test_valid_token_signs_in_without_refresh(self):
        """链路1：本地 token 有效 → 直接查询并签到，不触碰 auth 端点"""
        self._seed(fafu_token="FAFU_TOK_OK")
        self.assertTrue(daemon.try_sign())
        self.assertEqual(self._count("/student/sign"), 1)
        self.assertEqual(self._count("refresh/LoginReg"), 0, "token 有效却触发了刷新")

    # ---- 链路 2：token 失效，走完整刷新链 ----
    def test_expired_token_runs_full_refresh_chain(self):
        """链路2：token 失效 → 刷新链三跳全过 → 用新 token 签到 → refresh_token 轮换落盘"""
        self._seed(fafu_token="FAFU_TOK_EXPIRED", refresh_token="RT_OLD")
        self.assertTrue(daemon.try_sign())

        self.assertEqual(self._count("refresh/LoginReg"), 1, "未刷新 WeLink token")
        self.assertEqual(self._count("sso/auth/v2/code"), 1, "未换 authCode")
        self.assertEqual(self._count("third_party/welink/login"), 1, "未换 FAFU token")
        self.assertEqual(self._count("/student/sign"), 1, "未提交签到")

        state = self._state()
        self.assertEqual(state["refresh_token"], "RT_ROTATED", "轮换后的 refresh_token 未落盘")
        self.assertEqual(state["fafu_token"], "FAFU_TOK_NEW")
        self.assertEqual(state["we_link_token"], "WT_NEW")
        self.assertEqual(state["refresh_fail"], 0)
        self.assertGreater(state["next_refresh_after"], time.time(), "成功后应写入冷却")

    # ---- 链路 3：刷新失败必须退避 ----
    def test_refresh_failure_backs_off_within_window(self):
        """链路3：刷新失败 → 窗口内反复调用不再重打刷新链（防 WAF 限流导致漏签）"""
        self._seed(fafu_token="FAFU_TOK_EXPIRED", refresh_token="RT_OLD")
        self.refresh_ok = False

        self.assertFalse(daemon.try_sign())
        for _ in range(3):
            self.assertFalse(daemon.try_sign())

        self.assertEqual(self._count("refresh/LoginReg"), 1, "退避期内重复刷新会招来限流")
        self.assertEqual(self._state()["refresh_fail"], 1)
        self.assertEqual(self._count("/student/sign"), 0)

    # ---- 链路 4：已签到不重复提交 ----
    def test_already_signed_skips_submit(self):
        """链路4：服务端显示已签到 → 不重复提交"""
        self._seed(fafu_token="FAFU_TOK_OK")
        self.sign_state = 1
        self.assertTrue(daemon.try_sign())
        self.assertEqual(self._count("/student/sign"), 0, "已签到却重复提交")

    # ---- 链路 5：不在签到时段 ----
    def test_outside_window_skips_submit(self):
        """链路5：任务不在签到时段 → 不提交"""
        self._seed(fafu_token="FAFU_TOK_OK")
        self.task_shift_ms = -24 * 3600_000       # 任务已在一天前结束
        self.assertFalse(daemon.try_sign())
        self.assertEqual(self._count("/student/sign"), 0)

    # ---- 链路 6：业务失败不得误判为成功 ----
    def test_sign_failure_not_misread_as_success(self):
        """链路6：签到接口返回业务失败 → 不得误判成功（否则窗口内不再重试）"""
        self._seed(fafu_token="FAFU_TOK_OK")
        self.sign_ok = False
        self.assertFalse(daemon.try_sign(), "业务失败被误判为签到成功")
        self.assertEqual(self._count("/student/sign"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
