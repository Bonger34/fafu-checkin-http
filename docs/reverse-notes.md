# 逆向笔记

> **定位**：本项目的技术追溯——登录链、设备锁、限流、参考仓库对比的逆向过程与实测证据。
> 使用见 [../README.md](../README.md)；开发约定见 [../DEVELOPMENT.md](../DEVELOPMENT.md)。
> WeLink 多设备/踢下线机制的完整逆向见 [multi-device-login-analysis.md](multi-device-login-analysis.md)。记录日期：2026-09-15。

## 一、登录链（纯 HTTP，全部打通）

```
1. POST MAG/FreeProxyForText/wemiddle/api/v1/enterprise/auth/info {tenantid}
   → 返回 thirdLoginUrl（含 WeLink 生成的 state=UUID）★关键：不能自己编

2. GET  CAS /oauth2.0/authorize (用上面的 URL)
   → 登录页；提取 pwdEncryptSalt / execution

3. 验证码（由风控决定用哪种，两个 ★ 前置调用都不能跳）
   ★ GET /checkNeedCaptcha.htl?username=<学号>&_=<时间戳> → {"isNeed":true|false}
     （login.js 在提交前必调；跳过它后面怎么调都失败）
   · captchaSwitch="1" 图形码：GET /getCaptcha.htl → 80x30 JPEG，固定 4 位字母数字，
     识别结果随 /login 的 captcha 字段提交
   · captchaSwitch="2" 滑块：
     ★ GET /common/toSliderCaptcha.htl   先把会话切到滑块模式（返回滑块 HTML 片段）
       GET /common/openSliderCaptcha.htl  取图（PNG 尾部 16 字节即 AES 密钥）
       POST /common/verifySliderCaptcha.htl
         sign = AES-128-CBC(randomString(64)+JSON, key=尾部16字节, iv=randomString(16))
         JSON = {canvasLength:280, moveLength, tracks}
         tracks 三项均为**累计值**：a=累计X位移、b=累计Y位移、c=距开始累计毫秒

4. POST CAS /login  (password = AES-CBC(randomString(64)+pwd, salt, randomIV))
   → 302 到 reAuthLoginView.do?isMultifactor=true

5. POST CAS /dynamicCode/getDynamicCodeByReauth.do  → 发短信
6. POST CAS /reAuthCheck/reAuthSubmit.do
   {service, reAuthType:3, isMultifactor:true, dynamicCode:<码>, ..., skipTmpReAuth:"false"}
   → {"msg":"认证成功","code":"reAuth_success"}

7. GET  CAS /login?service=<callbackAuthorize>  → 302 → magcallback?code=<OAuth code>

8. POST https://api.welink.huaweicloud.com/mcloud/mag/v7/callback/LoginReg
   form: {code, tenantid=<RSA-OAEP(SHA256)加密>, thirdAuthType:"3", authType:"phone"}
   → Set-Cookie: token=<...>; Max-Age=7200
     body: {refresh_token, uid, expires_in:7200, ...}
```

### 关键根因

| 问题 | 真相 |
|---|---|
| MFA 后仍要求 MFA | **`skipTmpReAuth=true` 破坏流程**——服务端尝试绑定设备指纹失败，MFA 未真正完成。改用 **`false`**（"仅本次登录"）立刻成功 |
| 换 token 报 4006 | ① 当时判定要带 **WeLink `auth/info` 返回的真实 state**（⚠ 后续实测显示自编 state 也能端到端成功，见下方 41567 归因，此条不宜再当作硬性要求）；② 参数缺 **`thirdAuthType=3`** 和 **`authType=phone`** |
| 滑块验证永远失败 | **漏了第 3 步的两个前置调用**（`checkNeedCaptcha` 与 `toSliderCaptcha`）。跳过时 `openSliderCaptcha` 照常返回图片，但 `verifySliderCaptcha` 一律回 `{"errorCode":0,"errorMsg":"error"}`——与 moveLength 精度、tracks 形态、加密方式**全都无关**，调参数是白费力气。另：`canvasLength` 是 280（登录页 `#sliderDiv` 写死 280px）而非 278 |
| 图形码 OCR 漏字 | ddddocr 偶发漏识别时**置信度仍有 0.99+**（高于部分正确样本），置信度阈值挡不住；验证码固定 4 位，只能用长度过滤 + 换图重试 |
| 登录后 MFA 接口报「请求超时重定向」 | **重定向过程中丢 cookie**。POST `/login` 成功会 302 到 MFA 页并下发新的 `JSESSIONID`，裸 `urlopen` 自动跟随重定向会丢弃中间响应的 `Set-Cookie`，拿旧 cookie 调 `/dynamicCode/*` 或 `/reAuthCheck/*` 一律被判为新会话。必须用 `http.cookiejar` 自动管理 |
| 提交 `/login` 后原地返回登录页 | 两个坑叠加：① 未带会话 cookie（验证码结果存在会话里）；② 缺 `captcha` 字段——**滑块模式下它必须是空串而不是不提交**（浏览器抓包确认）。另外字段集只需 8 个，多带的 `userPassword`/`rememberMe`/`agreeProtocol`/`uuid` 服务端不认 |
| 换 token 报 41567 `Get authorization username fail` | **通用错误，语义被泛化**：假 code / 空 code / 已消费的真 code / 截断一位的真 code，四种输入返回完全相同的响应。**触发条件未确定**，实测呈间歇性，与代码版本无关。详见下方实测 |

### 41567 归因（2026-09-16 实测，共 6 次端到端运行）

`login_once.py` 当天 6 次真实运行，**3 成 3 败，且成败与代码版本无关**：

| # | 时间 | OAuth state | OAuth code | 结果 |
|---|---|---|---|---|
| 1 | 14:06 | 自编 | `OC36311FN8…` | ❌ 41567 |
| 2 | 14:10 | **自编** | `OC3633yTBW…` | ✅ 成功 |
| 3 | 14:50 | 真实 | `OC3652PFvQ…` | ❌ 41567 |
| 4 | 14:54 | 真实 | — | ❌ 41567 |
| 5 | 15:33 | 真实 | `OC3661xQ8nGy4hUCorPMYV1OmvRNpVkDkxRZl` | ✅ 成功 |
| 6 | 16:03 | 真实 | `OC3673qc36IRYGjJ7kF8o4hMgCkCTSCFulJBZ` | ✅ 成功 |

结论：

1. **41567 与 `state` 无因果关系**：第 2 次用的就是 `random.randint` 自编 state，端到端成功。
   改用 WeLink 下发的真实 state 依然正确（对齐真实浏览器行为），但**不要再声称它是根因**。
2. **41567 与 code 提取方式无因果关系**：第 4 次与第 5、6 次代码**逐字节相同**，一败两成。
3. **41567 是通用错误**，四种输入返回**完全相同**的响应
   （HTTP 200 + `{"errorCode":"41567","errorMessage":"Get authorization username fail"}`）：
   假 code、空 code、已消费的真 code、已消费真 code 去掉末位。
   因此该错误码**无法区分**「code 无效」与「服务端拒绝」。
4. 三次捕获到的真实 code 均为 **37 位纯字母数字**，在 URL 中原样出现、无百分号转义；
   旧正则 `[?&]code=([A-Za-z0-9\-_.]{10,})` 本可完整匹配。

**仍未确定**：41567 的触发条件。三次失败当时用的包装器没有打印完整 URL 与 code，
无从比对；#3 / #4 只留下了 code 的前 10 位。
code 提取改为 `[^&\s]+` + `unquote`，按「不做字符白名单截断」保留，不再声称它是根因。

## 二、设备锁

打卡 token 获取：

```
9. POST MAG/ProxyForText/sso/auth/v2/code
   header: x-wlk-Authorization: <token>
   body(JSON): {codeType:"h5", codeInfo:"stuhealth.fafu.edu.cn"}
   → {code:"<FAFU authCode>"}   （一次性，用完即失效）

10. POST http://stuhtapi.fafu.edu.cn/health-api/third_party/welink/login
    header: Authorization: base64(ts:nonce:md5("AtPs2O1xEnhwkKDV"+url+ts+nonce):token)
    form: {schoolNo:"fafu", clientType:1, code:<authCode>, deviceId:<AndroidID>}
    → deviceId 不匹配 → "同一账号仅允许在同一台手机登录，若需更换手机，请联系辅导员解除设备锁后再次登录即可！"
```

### 精确性实测

| deviceId | 结果 |
|---|---|
| `<DEVICE_ID>`（绑定值） | ✅ isRegister=1，下发 token `2_...` |
| `""` / `0000000000000000` | ❌ 设备锁提示 |
| `<DEVICE_ID>` 末位差 1 | ❌ 设备锁提示 |
| `<DEVICE_ID>` 全大写 | ❌ 设备锁提示 |

**结论**：设备锁 = 服务端对 `deviceId` 做**精确、区分大小写**的字符串比对。
成功响应含完整学籍信息（`studentFileId` 为服务端学籍标识）。
解绑接口：`GET http://stuhtapi.fafu.edu.cn/health-api/user/unbind/device/student_file/{studentFileId}`

### 关键参数
- `schoolNo` = **`"fafu"`**（不是学号）
- 签名密钥（H5 硬编码）= **`AtPs2O1xEnhwkKDV`**

## 三、参考仓库对比（Bonger34/fafu-checkin）

| 项 | 参考仓库 | 本项目 |
|---|---|---|
| 形态 | Magisk/KernelSU 模块（`fafu_checkin.sh` 626 行） | 纯 Python HTTP |
| token 来源 | 读 App 私有目录 LevelDB | 从零登录获取 |
| 依赖 | root + App 已登录 | 仅账号密码 + 绑定 deviceId |
| 设备锁 | **不处理**（复用 App 已登录态） | 显式传入 deviceId |
| SECRET | `AtPs2O1xEnhwkKDV` | ✅ 一致 |
| 签名 | `b64(ts:nonce:MD5(SECRET+url+ts+nonce):token)` | ✅ 一致，服务端接受 |
| 查询接口 | `sign_in/student/my/page` | ✅ 一致 |
| 签到接口 | `sign_in/{id}/student/sign` | ✅ 一致 |
| token 格式 | `2_<32hex>` | ✅ 一致 |

**实测**：用真实 token 复刻 `mk_auth`+`api`，成功查到任务
`{id:306764, name:"晚查寝签到", signState:0}`，窗口 21:30~22:30（补签至 23:00）。

**仓库模块本体**需 root+App，Linux 沙箱无法运行；核心逻辑（签名+API）已实测跑通。

## 四、限流（WAF）与保活

**并非 IP 封禁**：`sys_config` 无签名头 → 401；带签名头 → 200。真实情况是**端点级临时限流**。

| 观测 | 结论 |
|---|---|
| ~20 分钟内调 `third_party/welink/login` ~30 次 → 401 | 触发阈值 |
| 同期 `sign_in/student/my/page` 正常 | 按端点限流 |
| 约 15–20 分钟后自动恢复 | 临时冷却 |

### 保活频率

| 动作 | 端点 | 频率 |
|---|---|---|
| 日常保活 | `sign_in/student/my/page` | 15–30 分钟（±抖动） |
| token 刷新 | `third_party/welink/login` | 仅失败时，冷却 30 分钟 |
| WeLink 刷新 | `v7/refresh/LoginReg` | 仅 2h 过期时 |
| 签到提交 | `sign_in/{id}/student/sign` | 窗口内每分钟，成功即停 |

**四条硬规则**：请求间隔 ≥2s（`fafu_lib.MIN_GAP`）；auth 端点指数退避 1→5→30→120 分钟（`fafu_lib._BACKOFF`，**成功与失败都要写 `next_refresh_after`**，否则窗口内失败风暴会把自己打进限流）；保活失败先判网络再刷新；token 本地缓存复用。

## 五、未解之谜

### "信任此设备"——未能复现

- **无独立接口**，仅把 `reAuthSubmit.do` 的 `skipTmpReAuth` 设为 `true`；`reAuthParams` 中无任何设备字段。
- 实测：`skip=true` 提交后，**新会话重新登录仍要求 MFA** → 信任未注册。
- **结论**：可信设备注册需要**客户端设备标识**（与设备锁同一 `deviceId`/指纹体系），纯 HTTP 无法提供 → 服务端静默失败。**对打卡无影响**（用 `skip=false` 完成一次性 MFA 即可）。

### FAFU token 有效期

- **滑动过期**：实测 46 分钟仍有效、5.4 小时后失效；无独立 refresh 接口。
- 与 WeLink token（`expires_in:7200`，固定 2h）不同。
