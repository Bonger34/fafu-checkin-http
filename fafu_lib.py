# -*- coding: utf-8 -*-
"""公共库：签名、HTTP、登录链、限流退避。凭据全部来自 fafu_config。"""
import json, time, random, string, base64, hashlib, re, urllib.request, urllib.parse, urllib.error
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
import os as _os
_PUBKEY = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "pubkey.txt"),
               encoding="utf-8").read().strip()

def _load_pubkey():
    """解析 pubkey.txt 为 RSA 公钥。

    兼容 PEM 与裸 base64(DER)；对带非 base64 前缀的文件（历史格式）自动跳过。
    """
    raw = _PUBKEY
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

def refresh_we_link(refresh_token):
    """refresh_token → 新 (we_link_token, refresh_token)。refresh_token 每次轮换。"""
    st, body, sc = _request(MAG_BASE + "/v7/refresh/LoginReg",
        data={"refresh_token": refresh_token, "thirdAuthType": "3", "tenantid": _rsa_tenant()})
    if st != 200:
        return None, None, st, body
    d = _json(body, {})
    tok = next((re.match(r"token=([^;]+)", c).group(1) for c in (sc or []) if c.startswith("token=")), None)
    return tok, (d.get("refresh_token") if isinstance(d, dict) else None), st, body


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


def query_task(token, rows=1):
    """查询打卡任务；返回首条记录或 None"""
    st, b = api("sign_in/student/my/page", f"rows={rows}&pageNum=1", token)
    d = _json(b, {})
    if not isinstance(d, dict):
        return None
    recs = d.get("records") or []
    return recs[0] if recs else None
