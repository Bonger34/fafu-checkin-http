# -*- coding: utf-8 -*-
"""CAS 登录（滑块 + 密码 + MFA），产出 WeLink token / refresh_token。凭据来自 fafu_config。"""
import json, time, random, base64, re, urllib.parse, urllib.request, urllib.error
import numpy as np, cv2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fafu_config import CFG, CAS_BASE, MAG_BASE
from fafu_lib import _request, _rsa_tenant

_AC = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
def _rnd(n): return "".join(random.choice(_AC) for _ in range(n))
def _aes_cbc(p, k, iv):
    k = k.strip().encode(); pad = 16 - len(p.encode()) % 16
    e = Cipher(algorithms.AES(k), modes.CBC(iv.encode())).encryptor()
    return base64.b64encode(e.update(p.encode() + bytes([pad]) * pad) + e.finalize()).decode()
def _enc(o, s): return _aes_cbc(_rnd(64) + json.dumps(o, separators=(",", ":")), s, _rnd(16))

def _cas(u, data=None, xhr=False, ref=None, cookie=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(u, data=body, method="POST" if body is not None else "GET")
    r.add_header("User-Agent", "Mozilla/5.0 (Linux; Android 12) Mobile Safari/537.36")
    if cookie: r.add_header("Cookie", cookie)
    if body is not None: r.add_header("Content-Type", "application/x-www-form-urlencoded")
    if xhr: r.add_header("X-Requested-With", "XMLHttpRequest")
    if ref: r.add_header("Referer", ref)
    try:
        rr = urllib.request.urlopen(r, timeout=20)
        return rr.status, rr.read().decode("utf-8", "replace"), rr.url, rr.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location", u), e.headers

def solve_slider():
    """滑块验证：最多重试 5 次"""
    for _ in range(5):
        _, b, _, _ = _cas(CAS_BASE + "/common/openSliderCaptcha.htl", xhr=True)
        try:
            cap = json.loads(b)
            safe = base64.b64decode(cap["smallImage"])[-16:].decode("latin1")
            big = cv2.imdecode(np.frombuffer(base64.b64decode(cap["bigImage"]), np.uint8), cv2.IMREAD_COLOR)
            sm = cv2.imdecode(np.frombuffer(base64.b64decode(cap["smallImage"]), np.uint8), cv2.IMREAD_UNCHANGED)
            ys, xs = np.where(sm[:, :, 3] > 0)
        except (ValueError, KeyError, TypeError, cv2.error):
            time.sleep(3); continue
        crop = sm[ys.min():ys.max()+1, xs.min():xs.max()+1]
        r = cv2.matchTemplate(big, crop[:, :, :3], cv2.TM_CCORR_NORMED, mask=crop[:, :, 3])
        _, _, _, loc = cv2.minMaxLoc(r)
        base_mv = loc[0] * 278 / big.shape[1]
        for mv in range(int(base_mv)+3, int(base_mv)+8):
            tr = [{"a": 0, "b": 0, "c": 0}]
            for i in range(1, 26):
                p = 1 - (1 - i/25)**2.2
                tr.append({"a": int(round(mv*p)), "b": random.randint(-1,1), "c": random.randint(20,50)})
            tr[-1] = {"a": int(mv), "b": 0, "c": random.randint(20,50)}
            _, b2, _, _ = _cas(CAS_BASE + "/common/verifySliderCaptcha.htl",
                {"sign": _enc({"canvasLength": 278, "moveLength": mv, "tracks": tr}, safe)}, xhr=True)
            if '"errorCode":1' in b2: return True
            time.sleep(0.3)
        time.sleep(2)
    return False

def login(username=None, password=None):
    """完整登录：滑块 → 密码 → 返回 (service, cas_cookie)。此时应到达 MFA 页。"""
    username = username or CFG.username; password = password or CFG.password
    state = str(random.randint(10**9, 10**10))
    authz = (CAS_BASE + "/oauth2.0/authorize?client_id=998592105751490560&redirect_uri="
             + urllib.parse.quote("https://api.welink.huaweicloud.com/sso/oauth2/magcallback.html", safe="")
             + "&response_type=code&state=" + state)
    _, h, url, hd = _cas(authz)
    cookies = "; ".join(c.split(";")[0] for c in (hd.get_all("Set-Cookie") or []))
    m_salt = re.search(r'pwdEncryptSalt"[^>]*value="([^"]*)"', h)
    if not m_salt:
        raise SystemExit("❌ 未能从登录页提取 pwdEncryptSalt（页面结构可能已变，或 IP 被拦）")
    salt = m_salt.group(1)
    ex = re.search(r'id="execution"\s+name="execution"\s+value="([^"]*)"', h)
    if not solve_slider(): raise SystemExit("❌ 滑块验证失败")
    st, b, furl, hd2 = _cas(CAS_BASE + "/login",
        {"username": username, "password": _aes_cbc(_rnd(64)+password, salt, _rnd(16)),
         "userPassword": "", "lt": "", "execution": ex.group(1) if ex else "e1s1",
         "_eventId": "submit", "cllt": "userNameLogin", "dllt": "generalLogin",
         "rememberMe": "true", "agreeProtocol": "true"}, ref=url)
    cookies = "; ".join(c.split(";")[0] for c in (hd2.get_all("Set-Cookie") or [])) or cookies
    m = re.search(r'service=([^&"]+)', furl)
    svc = urllib.parse.unquote(m.group(1)) if m else ""
    return svc, cookies

def submit_mfa(service, cas_cookie, code):
    """提交 MFA（skip=false，即'仅本次登录'）。返回 OAuth code。"""
    ref = CAS_BASE + "/reAuthCheck/reAuthLoginView.do?isMultifactor=true&service=" + urllib.parse.quote(service, safe="")
    st, b, _, _ = _cas(CAS_BASE + "/reAuthCheck/reAuthSubmit.do",
        {"service": service, "reAuthType": "3", "isMultifactor": "true", "password": "",
         "dynamicCode": code, "uuid": "", "answer1": "", "answer2": "", "otpCode": "",
         "skipTmpReAuth": "false"}, xhr=True, ref=ref, cookie=cas_cookie)
    if "reAuth_success" not in b: return None, b
    _, _, u, _ = _cas(CAS_BASE + "/login?service=" + urllib.parse.quote(service, safe=""), ref=ref, cookie=cas_cookie)
    m = re.search(r"[?&]code=([A-Za-z0-9\-_.]{10,})", u)
    return (m.group(1) if m else None), u

def oauth_to_welink(oauth_code):
    """OAuth code → (we_link_token, refresh_token)"""
    url = MAG_BASE + "/v7/callback/LoginReg"
    st, b, sc = _request(url, data={"code": oauth_code, "tenantid": _rsa_tenant(),
        "thirdAuthType": "3", "authType": "phone"})
    if st != 200: return None, None, st, b
    d = json.loads(b)
    tok = next((re.match(r"token=([^;]+)", c).group(1) for c in (sc or []) if c.startswith("token=")), None)
    return tok, d.get("refresh_token"), st, b
