# -*- coding: utf-8 -*-
"""离线端到端演练：只把最底层的网络出口（fafu_lib._request）换成假实现，
其余全部走真实代码路径——签名、JSON 解析、会话保障、刷新退避、签到判定都是真的。

两个 seam 各守各的职责：
    FlowDrillTest  会话保障链路：refresh_token → WeLink token → authCode → FAFU token，
                   外加 daemon.try_sign 那层「取 token + 记账」的薄包装
    SignOnceTest   一轮签到：拿到 token 之后怎么判、怎么交

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


class DrillBase(unittest.TestCase):
    """共享的假网络出口与任务构造：两类演练都从这里起步。

    只放 setUp 与 helper，**不要在这里加 test_ 方法**——本类会被两个子类
    各继承一次，测试会跟着跑两遍。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fafu-flow-")
        self._orig = (fafu_config._STATE_PATH, fafu_lib.MIN_GAP, fafu_lib._request)
        fafu_config._STATE_PATH = os.path.join(self.tmp, "state.json")
        fafu_lib.MIN_GAP = 0                    # 免去请求节流的等待

        self.calls = []                         # 记录所有外发请求，供断言
        self.refresh_ok = True
        self.refresh_omits_token = False        # 模拟服务端未回传新 refresh_token
        self.records_ok = True                  # 查询是否返回任务记录
        self.sign_ok = True
        self.sign_state = 0
        self.task_shift_ms = 0
        self.task_extra = {}                    # 覆盖/置空任务字段，构造坏记录
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
            d = {"expires_in": 7200}
            if not self.refresh_omits_token:
                d["refresh_token"] = "RT_ROTATED"
            return 200, json.dumps(d), ["token=WT_NEW; Max-Age=7200"]
        if "sso/auth/v2/code" in url:
            return 200, json.dumps({"code": "AUTHCODE_1"}), None
        if "third_party/welink/login" in url:
            return 200, json.dumps({"isRegister": 1, "userLoginResp": {"token": "FAFU_TOK_NEW"}}), None
        if "sign_in/student/my/page" in url:
            if self._token_of(headers) == "FAFU_TOK_EXPIRED":
                return 401, '{"error":"token expired"}', None
            return 200, json.dumps({"records": [self._task()] if self.records_ok else []}), None
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
        t = {"id": 306764, "name": "晚查寝签到",
             "beginTime": n - 3600_000, "endTime": n + 3600_000,
             "supplementEndTime": n + 7200_000,
             "signInStudent": {"signState": self.sign_state}}
        t.update(self.task_extra)
        return t

    def _count(self, keyword):
        return sum(1 for u in self.calls if keyword in u)

    def _seed(self, **session):
        """写入初始会话态（daemon 与 fafu_lib 共用同一个 CFG 单例）"""
        fafu_config.CFG._state = {}
        fafu_config.CFG.save_state(**session)

    def _state(self):
        with open(fafu_config._STATE_PATH, encoding="utf-8") as f:
            return json.load(f)


class FlowDrillTest(DrillBase):
    """会话保障：token 从哪来、过期了怎么续、续不上怎么退避。

    daemon.try_sign 收敛后只剩「取 token + 调用 sign_once + 记账 + 措辞」，
    这一组同时守着那层薄包装。
    """

    # ---- 链路 1：token 有效，复用而不刷新 ----
    def test_valid_token_signs_in_without_refresh(self):
        """链路1：本地 token 有效 → 直接查询并签到，不触碰 auth 端点"""
        self._seed(fafu_token="FAFU_TOK_OK")
        self.assertTrue(daemon.try_sign())
        self.assertEqual(self._count("/student/sign"), 1)
        self.assertEqual(self._count("refresh/LoginReg"), 0, "token 有效却触发了刷新")

    # ---- 薄层记账：已了结的一轮要报成 True ----
    def test_settled_day_is_reported_as_completed(self):
        """已签到 / 请假 → try_sign 返回 True，loop 才会把今天记成已完成

        收敛后 try_sign 只剩记账职责，这条守的就是那个映射。
        """
        self._seed(fafu_token="FAFU_TOK_OK")
        self.sign_state = 2                                   # 请假
        self.assertTrue(daemon.try_sign())
        self.assertEqual(self._count("/student/sign"), 0, "已请假却提交了签到")

    # ---- daemon 的日志：保守提交要留痕 ----
    def test_uncertain_state_is_warned_in_daemon_log(self):
        """daemon 要把「判定依据不确定」写进日志

        这条 warning 是发现服务端字段新增取值的信号；没有测试守的话，
        很容易在某次措辞调整里被顺手删掉。
        """
        self._seed(fafu_token="FAFU_TOK_OK")
        self.sign_state = 3
        with self.assertLogs("fafu", level="WARNING") as cm:
            daemon.try_sign()
        self.assertTrue(any("不是已知取值" in line for line in cm.output),
                        "保守提交没有留下日志")

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

    # ---- 链路 7：响应缺失新 refresh_token 时不得抹掉旧值 ----
    def test_refresh_without_rotated_token_keeps_previous_one(self):
        """链路7：刷新成功但响应未回传 refresh_token → 必须沿用旧值

        回归背景：服务端不保证每次都回传该字段（滑动续期常常只延长有效期、
        不换发新值）。若把 None 直接写回 state，唯一的重登凭证被抹成 null，
        此后 ensure_token 会因「没有 refresh_token」永久拒绝刷新，
        只能重新走一次短信登录。整条链路的其余部分应当照常成功。
        """
        self._seed(fafu_token="FAFU_TOK_EXPIRED", refresh_token="RT_OLD")
        self.refresh_omits_token = True
        self.assertTrue(daemon.try_sign(), "缺 refresh_token 不应影响本次刷新与签到")
        state = self._state()
        self.assertEqual(state["refresh_token"], "RT_OLD", "唯一的 refresh_token 被抹掉了")
        self.assertEqual(state["fafu_token"], "FAFU_TOK_NEW")
        self.assertEqual(state["refresh_fail"], 0)


class SignOnceTest(DrillBase):
    """一轮签到：喂一个 token 进去，看它怎么判、怎么交。

    不涉及 token 从哪来——那是调用方的事，也正是这个 seam 存在的意义。
    """

    # ---- 链路 4：已签到不重复提交 ----
    def test_already_signed_skips_submit(self):
        """链路4：服务端显示已签到 → 不重复提交，且算今日了结"""
        self.sign_state = 1
        out = fafu_lib.sign_once("TOK")
        self.assertTrue(out.completed, "已签到应算今日了结")
        self.assertEqual(out.status, fafu_lib.SignStatus.ALREADY_SIGNED)
        self.assertEqual(self._count("/student/sign"), 0, "已签到却重复提交")

    def test_leave_counts_as_settled(self):
        """signState=2（请假）同样算今日了结 —— 语义由真实记录比对确证"""
        self.sign_state = 2
        out = fafu_lib.sign_once("TOK")
        self.assertTrue(out.completed, "请假被当成未签，会白跑一整晚")
        self.assertEqual(out.status, fafu_lib.SignStatus.ALREADY_SIGNED)
        self.assertEqual(self._count("/student/sign"), 0)
        self.assertIn("请假", out.detail, "措辞不能把请假说成已签到")

    # ---- 链路 5：不在签到时段 ----
    def test_outside_window_skips_submit(self):
        """链路5：任务不在签到时段 → 不提交，且**不**算今日了结"""
        self.task_shift_ms = -24 * 3600_000       # 任务已在一天前结束
        out = fafu_lib.sign_once("TOK")
        self.assertFalse(out.completed, "不在时段不该被记成今日已完成")
        self.assertEqual(out.status, fafu_lib.SignStatus.NOT_IN_WINDOW)
        self.assertEqual(self._count("/student/sign"), 0)

    # ---- 链路 6：业务失败不得误判为成功 ----
    def test_sign_failure_not_misread_as_success(self):
        """链路6：签到接口返回业务失败 → 不得误判成功（否则窗口内不再重试）"""
        self.sign_ok = False
        out = fafu_lib.sign_once("TOK")
        self.assertFalse(out.completed, "业务失败被误判为签到成功")
        self.assertEqual(out.status, fafu_lib.SignStatus.SUBMIT_FAILED)
        self.assertEqual(self._count("/student/sign"), 1)

    # ---- 正常提交 ----
    def test_submits_and_completes(self):
        """未签到且在时段内 → 提交一次，结果带上被处理的那条任务"""
        out = fafu_lib.sign_once("TOK")
        self.assertTrue(out.completed)
        self.assertEqual(out.status, fafu_lib.SignStatus.SIGNED)
        self.assertEqual(self._count("/student/sign"), 1)
        self.assertEqual(out.task.name, "晚查寝签到", "结果里要能拿到任务，供调用方展示")
        self.assertEqual(out.task.id, 306764)

    # ---- 不变量 4：状态不确定时保守签到 ----
    def test_unknown_state_still_attempts(self):
        """signState 缺失 → 视为未知并尝试签到（不变量 4：宁可多签不可漏签）"""
        self.task_extra = {"signInStudent": None}
        out = fafu_lib.sign_once("TOK")
        self.assertEqual(self._count("/student/sign"), 1, "状态未知时漏签了")
        self.assertEqual(out.status, fafu_lib.SignStatus.SIGNED)
        self.assertIn("保守", out.detail, "保守提交是服务端字段变动的信号，要说出来")

    def test_unseen_state_is_treated_conservatively(self):
        """没见过的取值（如 3）→ 保守提交并说明，不当作已签到

        回归背景：判定原为 `ss is not None and ss != 0`，任何非零值都算已了结，
        而文档写的是「遇到其他值一律保守尝试签到」。这个分歧在服务端新增取值时
        会变成静默漏签——本项目的取舍是宁可多签不可漏签。
        """
        self.sign_state = 3
        out = fafu_lib.sign_once("TOK")
        self.assertEqual(self._count("/student/sign"), 1, "没见过的取值被当成已签到，漏签了")
        self.assertEqual(out.status, fafu_lib.SignStatus.SIGNED)
        self.assertIn("保守", out.detail, "保守提交这件事本身要说出来")
        self.assertIn("3", out.detail, "还要说出是哪个取值，便于察觉服务端变更")

    def test_unseen_state_marks_outcome_uncertain(self):
        """判定依据不是已知取值时，结果要能被调用方识别出来

        调用方（daemon 的日志、CLI 的提示）都要能说出「这一轮是保守决策的」，
        但不该各自再实现一遍取值判断——那正是这次要消灭的漂移形态。
        """
        self.sign_state = 3
        out = fafu_lib.sign_once("TOK")
        self.assertTrue(out.uncertain, "未见过取值没有被标记为不确定")

    def test_missing_state_is_also_uncertain(self):
        """signState 缺失同样属于「判定依据不确定」"""
        self.task_extra = {"signInStudent": None}
        out = fafu_lib.sign_once("TOK")
        self.assertTrue(out.uncertain)

    def test_known_states_are_not_uncertain(self):
        """已知取值不该被标成不确定，否则告警会变成噪音"""
        for state in (0, 1, 2):
            with self.subTest(sign_state=state):
                self.sign_state = state
                self.calls = []
                out = fafu_lib.sign_once("TOK")
                self.assertFalse(out.uncertain, "状态 %s 是已知取值" % state)

    # ---- 坏记录：两份实现曾在此漂移 ----
    def test_missing_end_time_still_signs(self):
        """endTime 缺失但 supplementEndTime 在 → 照常签到

        回归背景：两份实现用不同字段做完整性校验（一份查 dl、一份查 et），
        endTime 缺失时一份照签、一份报错退出。兜底写成
        `dl = supplementEndTime or endTime` 的意图就是「尽量签」，校验应与之一致。
        """
        self.task_extra = {"endTime": None}
        out = fafu_lib.sign_once("TOK")
        self.assertEqual(out.status, fafu_lib.SignStatus.SIGNED)
        self.assertEqual(self._count("/student/sign"), 1)
        self.assertIsNone(out.task.end_ms, "endTime 缺失时 end_ms 就是 None，展示方要能处理")

    def test_missing_all_times_is_skipped_with_reason(self):
        """补签截止与 endTime 都缺 → 跳过并说明，不提交也不崩"""
        self.task_extra = {"endTime": None, "supplementEndTime": None}
        out = fafu_lib.sign_once("TOK")
        self.assertFalse(out.completed)
        self.assertEqual(out.status, fafu_lib.SignStatus.BAD_RECORD)
        self.assertEqual(self._count("/student/sign"), 0)
        self.assertTrue(out.detail, "跳过时必须说明原因，否则用户只看到「什么都没发生」")

    # ---- 查询失败 ----
    def test_query_failure_reports_reason(self):
        """查询不到任务 → 报告原因，交调用方决定记日志还是报错"""
        self.records_ok = False
        out = fafu_lib.sign_once("TOK")
        self.assertFalse(out.completed)
        self.assertEqual(out.status, fafu_lib.SignStatus.QUERY_FAILED)
        self.assertIsNone(out.task, "查询失败时没有任务可展示")
        self.assertTrue(out.detail, "失败时更要有话说")
        self.assertEqual(self._count("/student/sign"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
