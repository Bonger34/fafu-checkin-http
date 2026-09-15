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

3. 滑块验证：/common/openSliderCaptcha.htl → /common/verifySliderCaptcha.htl
   （AES-128-CBC 签名，canvasLength=278，moveLength≈缺口x*280/大图宽+3..7）

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
| 换 token 报 4006 | ① 必须用 **WeLink `auth/info` 返回的真实 state**；② 参数缺 **`thirdAuthType=3`** 和 **`authType=phone`** |

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

**四条硬规则**：请求间隔 ≥2s；auth 端点指数退避（1→5→30→120 分钟）；保活失败先判网络再刷新；token 本地缓存复用。

## 五、未解之谜

### "信任此设备"——未能复现

- **无独立接口**，仅把 `reAuthSubmit.do` 的 `skipTmpReAuth` 设为 `true`；`reAuthParams` 中无任何设备字段。
- 实测：`skip=true` 提交后，**新会话重新登录仍要求 MFA** → 信任未注册。
- **结论**：可信设备注册需要**客户端设备标识**（与设备锁同一 `deviceId`/指纹体系），纯 HTTP 无法提供 → 服务端静默失败。**对打卡无影响**（用 `skip=false` 完成一次性 MFA 即可）。

### FAFU token 有效期

- **滑动过期**：实测 46 分钟仍有效、5.4 小时后失效；无独立 refresh 接口。
- 与 WeLink token（`expires_in:7200`，固定 2h）不同。
