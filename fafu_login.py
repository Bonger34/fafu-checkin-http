# -*- coding: utf-8 -*-
"""CAS 登录（按需验证码 + 密码 + MFA），产出 WeLink token / refresh_token。凭据来自 fafu_config。

登录顺序（对照 reverse/cas/login.js，缺一步都会静默失败）：
  1. GET  /oauth2.0/authorize            → 登录页、cookie、pwdEncryptSalt、captchaSwitch
  2. GET  /checkNeedCaptcha.htl          → 服务端此刻是否要求验证码
  3. 需要时按 captchaSwitch 分流：
       "1" → GET /getCaptcha.htl（图形码，需 OCR）
       "2" → GET /common/toSliderCaptcha.htl 先把会话切到滑块模式，再 open/verify
  4. POST /login                         → 图形码时带 captcha 字段
"""
import json, time, random, base64, re, urllib.parse, urllib.request, urllib.error
import numpy as np, cv2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fafu_config import CFG, CAS_BASE, MAG_BASE
from fafu_lib import _request, _rsa_tenant, _json

# 滑块轨道宽度：登录页 #sliderDiv 写死 280px，插件 DEFAULTS.width 亦为 280
_CANVAS = 280

_AC = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
def _rnd(n): return "".join(random.choice(_AC) for _ in range(n))
def _aes_cbc(p, k, iv):
    k = k.strip().encode(); pad = 16 - len(p.encode()) % 16
    e = Cipher(algorithms.AES(k), modes.CBC(iv.encode())).encryptor()
    return base64.b64encode(e.update(p.encode() + bytes([pad]) * pad) + e.finalize()).decode()
def _enc(o, s): return _aes_cbc(_rnd(64) + json.dumps(o, separators=(",", ":")), s, _rnd(16))

def _cas(u, data=None, xhr=False, ref=None, cookie=None, binary=False):
    """CAS 请求。binary=True 返回原始 bytes —— 验证码图片是 JPEG，
    按 UTF-8 解码会直接抛 UnicodeEncodeError。"""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(u, data=body, method="POST" if body is not None else "GET")
    r.add_header("User-Agent", "Mozilla/5.0 (Linux; Android 12) Mobile Safari/537.36")
    if cookie: r.add_header("Cookie", cookie)
    if body is not None: r.add_header("Content-Type", "application/x-www-form-urlencoded")
    if xhr: r.add_header("X-Requested-With", "XMLHttpRequest")
    if ref: r.add_header("Referer", ref)
    def _decode(raw):
        return raw if binary else raw.decode("utf-8", "replace")
    try:
        rr = urllib.request.urlopen(r, timeout=20)
        return rr.status, _decode(rr.read()), rr.url, rr.headers
    except urllib.error.HTTPError as e:
        return e.code, _decode(e.read()), e.headers.get("Location", u), e.headers

def _need_captcha(username, cookie=None, ref=None):
    """服务端此刻是否要求验证码。

    login.js 在提交前必调此接口。跳过它会让后续所有验证都落在「未进入验证流程」
    的会话里——openSliderCaptcha 照样返回图片，但 verifySliderCaptcha 永远回
    {"errorCode":0,"errorMsg":"error"}，与 moveLength、轨迹、加密都无关。
    """
    ts = int(time.time() * 1000)
    url = f"{CAS_BASE}/checkNeedCaptcha.htl?username={urllib.parse.quote(username)}&_={ts}"
    st, b, _, _ = _cas(url, xhr=True, cookie=cookie, ref=ref)
    d = _json(b, {})
    return bool(isinstance(d, dict) and d.get("isNeed"))

def _to_slider(cookie=None, ref=None):
    """把会话切到滑块模式：GET toSliderCaptcha.htl 返回滑块 HTML 片段。

    这一步同样不能省，否则 verifySliderCaptcha 一直返回通用错误。
    """
    st, b, _, _ = _cas(CAS_BASE + "/common/toSliderCaptcha.htl", xhr=True,
                       cookie=cookie, ref=ref)
    return st == 200 and "sliderDiv" in b

def _tracks(mv, steps=26):
    """滑动轨迹。字段语义对照 longbow.js 的 mousemove 记录：
        a = 累计 X 位移、b = 累计 Y 位移、c = 距滑动开始的累计时间(ms)

    注意三者都是「累计值」：原实现把 c 写成每步随机 20~50（时间非单调），
    是很容易被识别出来的机器特征。
    """
    tr = [{"a": 0, "b": 0, "c": 0}]
    t = 0; dy = 0
    for i in range(1, steps):
        p = 1 - (1 - i / (steps - 1)) ** 2.2
        t += random.randint(20, 50)
        dy += random.choice((0, 0, 0, 1, -1))          # 水平滑动，Y 仅微抖
        tr.append({"a": int(round(mv * p)), "b": dy, "c": t})
    tr[-1] = {"a": int(mv), "b": dy, "c": t}
    return tr

def solve_slider(cookie=None, ref=None):
    """滑块验证：最多 5 轮，每轮取新图后试 5 个候选位移。"""
    for _ in range(5):
        _, b, _, _ = _cas(CAS_BASE + "/common/openSliderCaptcha.htl", xhr=True,
                          cookie=cookie, ref=ref)
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
        base_mv = loc[0] * _CANVAS / big.shape[1]
        for mv in range(int(base_mv) + 3, int(base_mv) + 8):
            _, b2, _, _ = _cas(CAS_BASE + "/common/verifySliderCaptcha.htl",
                {"sign": _enc({"canvasLength": _CANVAS, "moveLength": mv,
                               "tracks": _tracks(mv)}, safe)},
                xhr=True, cookie=cookie, ref=ref)
            d = _json(b2, {})
            if isinstance(d, dict) and d.get("errorCode") == 1:
                return True
            time.sleep(0.3)
        time.sleep(2)
    return False

def login(username=None, password=None):
    """完整登录：按需验证 → 密码 → 返回 (service, cas_cookie)，此时应到达 MFA 页。"""
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
    m_switch = re.search(r'captchaSwitch\s*=\s*"([^"]*)"', h)
    switch = m_switch.group(1) if m_switch else "2"

    captcha = ""
    if _need_captcha(username, cookies, url):
        if switch == "1":
            raise SystemExit("❌ 服务端当前要求图形验证码（captchaSwitch=1），本版本尚未支持 OCR。\n"
                             "   风控状态会随时间变化，可稍后重试；也可改用手机 App 扫码登录。")
        if not _to_slider(cookies, url):
            raise SystemExit("❌ 无法切换到滑块验证（toSliderCaptcha 未返回滑块页面）")
        if not solve_slider(cookies, url):
            raise SystemExit("❌ 滑块验证失败")

    data = {"username": username, "password": _aes_cbc(_rnd(64) + password, salt, _rnd(16)),
            "userPassword": "", "execution": ex.group(1) if ex else "e1s1",
            "_eventId": "submit", "cllt": "userNameLogin", "dllt": "generalLogin",
            "rememberMe": "true", "agreeProtocol": "true"}
    # lt / uuid 是服务端下发的隐藏字段，有值就一并提交（原实现把 lt 硬编码为空）
    for key, pat in (("lt", r'id="lt"[^>]*value="([^"]*)"'),
                     ("uuid", r'id="uuid"[^>]*value="([^"]*)"')):
        m = re.search(pat, h)
        data[key] = m.group(1) if m else ""
    if captcha:
        data["captcha"] = captcha

    st, b, furl, hd2 = _cas(CAS_BASE + "/login", data, ref=url)
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
