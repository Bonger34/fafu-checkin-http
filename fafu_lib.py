# -*- coding: utf-8 -*-
"""公共库：签名、HTTP、登录链、限流退避。凭据全部来自 fafu_config。"""
import os, json, time, random, string, base64, hashlib, re, enum, urllib.request, urllib.parse, urllib.error
from typing import NamedTuple, Optional
from fafu_config import CFG, CLIENT_SECRET, SCHOOL_NO, API_BASE, MAG_BASE, USER_AGENT
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization

# ---- 限流保护：所有外发请求统一间隔 + 指数退避 ----
_last_req = [0.0]
MIN_GAP = 2.0          # 请求最小间隔（秒）——实测 <1s 连发会触发 WAF
def _throttle():
    dt = time.time() - _last_req[0]
    if dt < MIN_GAP:
        time.sleep(MIN_GAP - dt)
    _last_req[0] = time.time()

def _request(url, data=None, headers=None, method=None, retry=3):
    """带节流与指数退避的请求；返回 (status, body)。401/429 退避后重试。"""
    for attempt in range(retry):
        _throttle()
        body = None
        if data is not None:
            if isinstance(data, bytes):
                body = data
            elif isinstance(data, str):
                body = data.encode()          # 已是序列化的 JSON 字符串
            else:
                body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"))
        req.add_header("User-Agent", USER_AGENT)
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            r = urllib.request.urlopen(req, timeout=20)
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get_all("Set-Cookie")
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 429) and attempt < retry - 1:
                time.sleep(2 ** (attempt + 1))     # 2s → 4s → 8s 指数退避
                continue
            return code, e.read().decode("utf-8", "replace"), None
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2 ** (attempt + 1)); continue
            return -1, str(e)[:150], None
    return -1, "retry exhausted", None

def _json(body, default=None):
    """安全解析 JSON；非 JSON（如 WAF 返回的 HTML）返回 default，不抛异常。"""
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return default

def _has(body, key):
    """判定响应里是否存在字段 key。

    优先按 JSON 解析，避免 '"records"' 这类子串匹配被空格/换行等格式差异击穿；
    非 JSON（WAF 的 HTML 拦截页）回退子串匹配，保持对异常响应的宽容。
    """
    d = _json(body)
    if isinstance(d, dict):
        return key in d
    return f'"{key}"' in (body or "")

# ---- 打卡接口签名（复刻仓库 mk_auth）----
def mk_auth(url, token=""):
    ts = str(int(time.time()))
    nonce = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    h = hashlib.md5((CLIENT_SECRET + url + ts + nonce).encode()).hexdigest()
    return base64.b64encode(f"{ts}:{nonce}:{h}:{token}".encode()).decode()

def api(path, query="", token=""):
    """调用打卡 API（带签名）。path 形如 'sign_in/student/my/page'"""
    url = f"{API_BASE}/{path}" + (f"?{query}" if query else "")
    st, body, _ = _request(url, data=b"", headers={"Authorization": mk_auth(f"{API_BASE}/{path}", token)}, method="POST")
    return st, body

# ---- 登录链各环节 ----
def fetch_authcode(we_link_token):
    """WeLink token → FAFU OAuth authCode"""
    st, body, _ = _request(MAG_BASE + "/ProxyForText/sso/auth/v2/code",
        data=json.dumps({"codeType": "h5", "codeInfo": "stuhealth.fafu.edu.cn"}),
        headers={"Content-Type": "application/json", "x-wlk-Authorization": we_link_token})
    d = _json(body) if st == 200 else None
    return (d.get("code") if isinstance(d, dict) else None), st, body

def exchange_fafu_token(authcode):
    """authCode + deviceId → FAFU 打卡 token"""
    url = f"{API_BASE}/third_party/welink/login"
    st, body, _ = _request(url, data={"schoolNo": SCHOOL_NO, "clientType": 1,
        "code": authcode, "deviceId": CFG.device_id}, headers={"Authorization": mk_auth(url, "")})
    if st != 200:
        return None, st, body
    d = _json(body, {})
    if not isinstance(d, dict):
        return None, st, f"非 JSON 响应: {body[:150]}"
    if d.get("isRegister") != 1:
        return None, st, d.get("message", body[:150])
    ulr = d.get("userLoginResp") or {}
    tok = ulr.get("token")
    if not tok:
        return None, st, f"响应缺少 userLoginResp.token: {body[:150]}"
    return tok, st, body

# ---- WeLink refresh_token 刷新（无需短信）----
_PUBKEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pubkey.txt")

def _load_pubkey():
    """解析 pubkey.txt 为 RSA 公钥。

    惰性读取：原先在模块顶层 open，文件缺失时 `import fafu_lib` 直接抛
    FileNotFoundError，连一句可读的错误都没有。兼容 PEM 与裸 base64(DER)；
    对带非 base64 前缀的文件（历史格式）自动跳过。
    """
    try:
        with open(_PUBKEY_PATH, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        raise RuntimeError(
            f"无法读取 {_PUBKEY_PATH}：{e}\n"
            "  该文件是 WeLink 公钥（RSA-OAEP 加密租户 ID 用），应随仓库完整获取"
        ) from e
    if "BEGIN" in raw:                                   # PEM 格式
        return serialization.load_pem_public_key(raw.encode())
    for skip in range(0, 8):                             # 裸 base64，尝试跳过 0~7 个前缀字符
        cand = raw[skip:]
        cand += "=" * ((4 - len(cand) % 4) % 4)
        try:
            return serialization.load_der_public_key(base64.b64decode(cand))
        except Exception:
            continue
    raise ValueError("pubkey.txt 无法解析为 RSA 公钥（格式异常）")

_PUBKEY_OBJ = None                                       # 惰性解析：仅在需要刷新时加载

def _rsa_tenant():
    global _PUBKEY_OBJ
    if _PUBKEY_OBJ is None:
        _PUBKEY_OBJ = _load_pubkey()
    return base64.b64encode(_PUBKEY_OBJ.encrypt(CFG.tenant_id.encode(),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode()

def cookie_token(set_cookies):
    """从 Set-Cookie 列表里取 token 的值，取不到返回 None。

    Set-Cookie 可能是 "token=; Path=/..."（值为空），此时 re.match 返回 None；
    原先 next(... if c.startswith("token=")) 的写法在那种情况下会直接 .group(1)
    崩栈，把整条登录链打断。
    """
    for c in (set_cookies or []):
        m = re.match(r"token=([^;]+)", c)
        if m:
            return m.group(1)
    return None

def refresh_we_link(refresh_token):
    """refresh_token → (we_link_token, refresh_token)。

    服务端不保证回传新的 refresh_token：滑动续期往往只延长有效期、不换发新值
    （实测同一次刷新前后取到的是同一个值）。此时沿用传入值——它是唯一的重登
    凭证，返回 None 会被调用方写回 state 抹成 null，此后只能重新短信登录。
    """
    st, body, sc = _request(MAG_BASE + "/v7/refresh/LoginReg",
        data={"refresh_token": refresh_token, "thirdAuthType": "3", "tenantid": _rsa_tenant()})
    if st != 200:
        return None, None, st, body
    d = _json(body, {})
    new_rt = d.get("refresh_token") if isinstance(d, dict) else None
    return cookie_token(sc), (new_rt or refresh_token), st, body


# ---- 会话保障：优先用本地 token，失效则刷新（成功冷却 / 失败退避）----
_BACKOFF = (60, 300, 1800, 7200)     # 刷新失败退避：1→5→30→120 分钟（与 reverse-notes 硬规则一致）

def refresh_wait():
    """距下次允许刷新还剩几秒；<=0 表示当前可以刷新"""
    return CFG.get_state("next_refresh_after", 0) - time.time()

def _refresh_state(ok, cooldown):
    """返回本次刷新结果对应的会话态增量。

    失败也必须写入 next_refresh_after：否则调用方在签到窗口内每分钟重打整条
    刷新链（3 个 auth 请求），而实测 ~20 分钟 30 次就会触发 WAF 端点限流
    15–20 分钟——正好覆盖 21:30 签到窗口，直接导致漏签。
    """
    now = time.time()
    if ok:
        return {"refresh_fail": 0, "last_refresh_ts": now, "next_refresh_after": now + cooldown}
    n = min(CFG.get_state("refresh_fail", 0) + 1, len(_BACKOFF))
    return {"refresh_fail": n, "next_refresh_after": now + _BACKOFF[n - 1]}

def ensure_token(force=False, cooldown=1800):
    """返回可用的打卡 token；不可用时返回 None。

    - 优先复用 state 中的 fafu_token，仅发 1 次请求验证
    - 失效则用 refresh_token 刷新：成功后冷却 cooldown，失败按 _BACKOFF 递增退避
    - 冷却/退避期内直接返回 None，不再空打刷新链（失败风暴会招来 WAF 限流）
    """
    if not force:
        tok = CFG.fafu_token
        if tok:
            st, b = api("sign_in/student/my/page", "rows=1&pageNum=1", tok)
            if _has(b, "records"):
                return tok
        if refresh_wait() > 0:
            return None
    rt = CFG.refresh_token
    if not rt:
        return None
    wt, rt2, st, body = refresh_we_link(rt)
    if not wt:
        CFG.save_state(**_refresh_state(False, cooldown)); return None
    ac, st, _ = fetch_authcode(wt)
    if not ac:
        CFG.save_state(**_refresh_state(False, cooldown)); return None
    tok, st, msg = exchange_fafu_token(ac)
    if not tok:
        CFG.save_state(**_refresh_state(False, cooldown)); return None
    CFG.save_state(we_link_token=wt, refresh_token=rt2, fafu_token=tok,
                   **_refresh_state(True, cooldown))
    return tok


def query_tasks(token, rows=5):
    """查询打卡任务，返回记录列表（服务端按时间倒序）"""
    st, b = api("sign_in/student/my/page", f"rows={rows}&pageNum=1", token)
    d = _json(b, {})
    if not isinstance(d, dict):
        return []
    return d.get("records") or []

def query_task(token, rows=5):
    """查询打卡任务；返回「当前该处理」的那条，没有则 None。

    多任务时优先挑未签到的：服务端按时间倒序返回，原实现恒取 recs[0]，
    在「较新的任务已签、旧任务还没签」的场景下会误判成已完成而漏签。
    """
    recs = query_tasks(token, rows)
    if not recs:
        return None
    for r in recs:
        ss = (r.get("signInStudent") or {}).get("signState")
        if ss is None or ss == 0:            # 未签到（含状态未知）优先
            return r
    return recs[0]


# ---- 一轮签到：判定 + 提交（daemon 与 rootless_checkin 共用同一个 interface）----

class SignStatus(str, enum.Enum):
    """一轮签到的结局。

    用 Enum 而非裸字符串：调用方要按结局分日志级别与输出，拼错一个字符串
    就是静默失效（与「CFG 属性拼错抛 AttributeError」同一条约定）。
    取 str 子类是为了打进日志时仍是可读的字符串。
    """
    SIGNED = "signed"                  # 本次提交成功
    ALREADY_SIGNED = "already_signed"  # 服务端已签，或已请假
    NOT_IN_WINDOW = "not_in_window"    # 不在签到时段
    BAD_RECORD = "bad_record"          # 任务记录缺少判定所需字段
    QUERY_FAILED = "query_failed"      # 查不到任务
    SUBMIT_FAILED = "submit_failed"    # 提交被服务端拒绝


class TaskInfo(NamedTuple):
    """被处理的那条任务，供调用方展示。

    只带展示与判定需要的字段：把原始响应 dict 透出去，等于让每个调用方
    各自再实现一遍「哪个字段缺失时用哪个兜底」——那正是本次收敛要消灭的缝隙。
    """
    name: str
    id: int
    sign_state: object          # 0 未签 / 1 已签到 / 2 请假 / None 未知
    begin_ms: int
    end_ms: Optional[int]       # endTime 可缺：校验放宽后仍能凭补签截止签到
    deadline_ms: int            # 补签截止（supplementEndTime，缺失时已回退 endTime）


class SignOutcome(NamedTuple):
    """一轮签到的结果。

    completed —— 今日任务是否已了结。daemon 靠它维护「今天签过了」的记账；
                 「不在时段」「提交失败」都不算了结，窗口内还要再试。
    status    —— 可靠的分支依据（日志级别、措辞都按它选）。
    detail    —— 结论句成品，不含任务名前缀（那在 task.name 里），
                 调用方直接 log/print 即可。
    task      —— 查询失败时为 None。
    uncertain —— 判定依据的 `signState` 不是已知取值（缺失或没见过），本轮按
                 保守策略处理。调用方想知道「为什么这轮在保守提交」就读它，
                 不要各自再实现一遍取值判断——那正是这次要消灭的漂移形态。
    """
    completed: bool
    status: SignStatus
    detail: str
    task: Optional[TaskInfo] = None
    uncertain: bool = False


def sign_once(token):
    """跑一轮签到：查询任务 → 判定 → 需要时提交。返回 SignOutcome。

    token 由调用方提供——这个 module 不管会话从哪来（那是 ensure_token），
    也不管输出到哪去（daemon 记日志、CLI 打终端）。

    判定依据逐条都有实测或回归支撑：
      · `signState` 为 `1`（已签到）或 `2`（请假）→ 今日了结，不重复提交
      · `signState` 为 `0` → 未签，照常走窗口判定并提交
      · `signState` 缺失或**没见过的取值** → 判定依据不确定，一律保守提交
        （不变量 4：宁可多签不可漏签），并在 `uncertain` 里标出来
      · 当前时间不在 [beginTime, 补签截止] 内 → 跳过，且不算今日了结
      · 完整性校验看的是**回退之后**的补签截止，与
        `dl = supplementEndTime or endTime` 的兜底意图保持一致
      · 提交成败以响应里是否有 timestamp 判定，不做子串猜测
    """
    r0 = query_task(token, 5)
    if not r0:
        return SignOutcome(False, SignStatus.QUERY_FAILED, "查询任务失败（token 或网络问题）")

    ss = (r0.get("signInStudent") or {}).get("signState")
    name = r0.get("name") or "签到"
    rid, bt = r0.get("id"), r0.get("beginTime")
    et = r0.get("endTime")
    dl = r0.get("supplementEndTime") or et          # 补签截止，缺失时用 endTime 兜底
    if rid is None or bt is None or dl is None:
        return SignOutcome(False, SignStatus.BAD_RECORD,
                           "任务记录缺少 id/时间字段，跳过：%s" % list(r0)[:6])

    task = TaskInfo(name, rid, ss, bt, et, dl)

    # 已知取值只有三种：0 未签、1 已签到、2 请假。其余一律视为判定依据不确定，
    # 走保守路径（不变量 4）。曾经判的是「非 0 即已签」，那样服务端一旦新增
    # 一个表示未签的取值，就会静默漏签。
    uncertain = ss not in (0, 1, 2)

    if ss in (1, 2):                                # 明确已签到(1) / 已请假(2)
        return SignOutcome(True, SignStatus.ALREADY_SIGNED,
                           "%s，无需操作" % ("请假" if ss == 2 else "已签到"), task, uncertain)

    if ss is None:
        note = "签到状态未知，已保守提交："
    elif uncertain:
        note = "签到状态为未见过取值 %s，已保守提交：" % ss
    else:
        note = ""

    n = int(time.time() * 1000)
    if not (bt <= n <= dl):
        return SignOutcome(False, SignStatus.NOT_IN_WINDOW, "不在签到时段", task, uncertain)

    st, r = api("sign_in/%s/student/sign" % rid, "lng=%s&lat=%s" % (CFG.lng, CFG.lat), token)
    if _has(r, "timestamp"):
        return SignOutcome(True, SignStatus.SIGNED, note + "签到成功", task, uncertain)
    return SignOutcome(False, SignStatus.SUBMIT_FAILED,
                       note + "签到失败(%s): %s" % (st, r[:100]), task, uncertain)
