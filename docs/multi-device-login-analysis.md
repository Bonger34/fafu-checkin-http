# 数字FAFU（cn.edu.fafu.iportal）多设备登录校验 逆向分析

> 📎 本文是 WeLink 客户端多设备/踢下线机制的**完整逆向记录**；登录链与设备锁的**精简版**见 [reverse-notes.md](reverse-notes.md)。

样本：`iportal-production-7_49_17-807-7851-arm64-v8a-release-20250715.apk`
- 大小 210,716,045 B；MD5 `de85013a18fb1f934fa1716c8bf860d9`；SHA256 `465f9ced074d61286815472684eacf271611fe27916c55cbaea58bd9ffdc93e5`
- 包名 `cn.edu.fafu.iportal`，实质是**华为 WeLink（Works/eSpace）的白标定制**（大量 `com.huawei.works` / `com.huawei.it.w3m` / `com.huawei.im.esdk` 组件）
- 22 个 dex + `lib/arm64-v8a` 原生库；WebView 混合架构

> 结论先行：**多设备登录的“判定权”在服务端**。客户端主要负责
> ①上报自己的客户端类型；②保存服务端下发的“已登录设备列表/各端在线状态”；
> ③接收“多端上线/下线”推送并刷新状态；④接收“被踢”推送并执行登出。
> 客户端本地的“校验”只是**基于服务端状态的布尔判断**（PC/手机/Pad 是否在线、是否需要踢 PC、是否允许 PC+手机同时在线），
> 不存在可被本地绕过的强校验（如设备数上限、设备指纹白名单等）。

---

## 1. 设备/终端类型定义

`com.huawei.hwmbiz.login.api.LoginDeviceType`（枚举，classes19）

| 枚举 | value | 说明 |
|---|---|---|
| MOBILE | 50 | 手机客户端 |
| PAD | 51 | PAD 客户端 |
| PC | 52 | pc 客户端 |
| TV | 53 | 电视客户端 |
| BOARD | 54 | 大屏客户端 |

登录/在线状态里另一套**整数编码**（用于 `setLogout`、`UserMultiDeviceOptNotifyV2`、`entity.g`）：

| 编码 | 含义 |
|---|---|
| 0 | PC |
| 1 | Mobile |
| 3 | Pad |

> 注意 `0/1/3` 与上面的 `50~54` 是两套不同语境的值，别混。

---

## 2. 关键类与命令（IPC/MIP 消息）

### 2.1 命令码 `com.huawei.ecs.mip.common.CmdCode`（classes17）
- `CC_GetLoginDevice` —— 查询账号的已登录设备列表
- `CC_OperationLoginDevice` —— 对某个登录设备执行操作（踢出等）
- `CC_QueryMultiDeviceStateByAccounts` / `...Ack` —— 按账号批量查询多端在线状态
- `CC_UserKickoutV2` / `CC_UserKickoutV2Ack` —— 被踢通知
- `CC_UserMultiDeviceOptNotifyV2` —— 多端上线/下线通知

### 2.2 消息体（`com.huawei.ecs.mip.msg`，classes17）
| 类 | 字段 |
|---|---|
| `GetLoginDevice` | actionType, user |
| `GetLoginDeviceAck$DeviceInfo` | id, name, detail, time（root=`deviceInfo`） |
| `OperationLoginDevice` | actionType, user, **type(short), deviceID, detail** |
| `UserKickoutV2` | clientType(int) |
| `UserMultiDeviceOptNotifyV2` | userAccount, **clientType(short), actionFlag(short), timestamp(long)** |

### 2.3 数据实体
- `com.huawei.im.esdk.data.LoginDeviceRespV2`（classes20）：由 `UserMultiDeviceOptNotifyV2` 包装 → `clientType`、`actionFlag`、`timetamp`
- `com.huawei.im.esdk.data.entity.UserDeviceStateInfoEntity`（classes20）：`userAccount, state, stateDesc, clientType, clientDesc, updateTime`
- `com.huawei.im.esdk.data.entity.g`（classes20）：`a()=clientType`，`<init>(int, long)`（用于设备列表项）

### 2.4 客户端状态容器 `com.huawei.im.esdk.contacts.MyOtherInfo`（classes20）
保存**本账号在多端登录上的权威本地视图**：

```
deviceList / deviceListV2 : Collection<LoginProto$LoginDevice | entity.g>
isPConline, isMobileOnline, isPadOnline : boolean   // 各端是否在线
clientsBothOnline : int                              // 服务端配置：是否允许 PC+手机同时在线
isDeviceInfoFromLogin : boolean
```

相关方法：
- `setDeviceList(Collection, boolean)` / `setDeviceListV2(Collection, boolean)`：**核心解析**
- `isPConline()/isMobileOnline()/isPadOnline()`
- `isOtherTerminalOffline()`：除本端外是否已无其它在线端
- `setLogout(int removeMode)`：按被踢端置位
- `enableClientsBothOnline()`：`clientsBothOnline == 1`
- `setLogoutKickoutPc/Pad/Mobile(Collection)`：把对应端置为离线并从设备列表移除

---

## 3. 登录时的“校验”数据流

### 3.1 登录响应里带设备列表
调用 `setDeviceList*` 的位置（classes20）：
- `LoginResp.<init>(LoginProto$LoginResponse)` / `LoginResp.decodeFromInitUserAck(InitUserAck)`
- `UserLoginRespV2.<init>(UserLoginV2Ack)`
- `UserLoginRespV2.decodeFromGetServiceProfileAck(UserGetServProfileV2Ack)`
- `UserLoginRespV2.decodeMyOtherInfo(UserLoginV2Ack)`
- `msghandler.pushmsg.x.e(BaseMsg)`（多端状态推送）

### 3.2 解析逻辑（反编译还原）
`setDeviceList`（设备类型用字符串）：
```java
isPConline = isMobileOnline = isPadOnline = false;
for (LoginProto.LoginDevice d : list) {
    String t = d.getDeviceType();
    if (t.equals("PC"))          isPConline    = true;
    else if (t.equalsIgnoreCase("Mobile")) isMobileOnline = true;
    else if (t.equalsIgnoreCase("Pad"))    isPadOnline    = true;
}
```
`setDeviceListV2`（设备类型用整数 `entity.g.a()`）：
```java
isPConline = isMobileOnline = isPadOnline = false;
for (entity.g d : list) {
    switch (d.a()) { case 0: isPConline=true; case 1: isMobileOnline=true; case 3: isPadOnline=true; }
}
```
> 即：**登录那一刻，服务端把“当前有哪些端在线”告诉客户端，客户端只做布尔落位**。是否允许并发、是否要踢人，客户端不做决定。

---

## 4. 多端上线/下线的实时同步（push）

`com.huawei.im.esdk.msghandler.pushmsg.x.e(BaseMsg)`（classes20）处理 `UserMultiDeviceOptNotifyV2`：

```
遍历 deviceListV2：
  若 item.clientType == notify.clientType:
      actionFlag == 1  → 标记移除（该端下线）
      actionFlag == 0  → 保留并刷新 timestamp（该端上线/心跳）
  若列表中没有该 clientType 且 actionFlag == 0 → 追加新端
按新列表调用 setDeviceListV2(list, false)   // 重算 isPConline/isMobileOnline/isPadOnline
再广播 Intent: "com.huawei.espace.module.multi_teminal_login"，附带 LoginDeviceRespV2
```

即 **`actionFlag`：0=上线，1=下线；`clientType`：0=PC，1=手机，3=Pad**。

接收方 `com.huawei.hwespace.function.MultiTerminalFunc$b.onReceive`（classes18）：
- 收到 `multi_teminal_login` → 若平台适配层 `config.b.c()` 为真，调用 `MultiTerminalFunc.handleMultiTerminalNotify(...)`，再 `MultiTerminalManager.d()` 刷新，并发 EventBus `cb0` 事件
- `handleMultiTerminalNotify` 处理 `LoginDeviceRespV2`：
  - `actionFlag == 0`（某端上线）：若上线的是手机端且 VoIP 已注册 → `callStopRefreshRegister()`；
  - `actionFlag == 1`（某端下线）：若 `isPConline()==false && isMobileOnline()==false`（其它端全离线）或 `clientType==0` → `registerVoip()`（重新注册 VoIP）

---

## 5. 被踢下线（强制登出）处理

### 5.1 消息链路
`com.huawei.im.esdk.msghandler.im.f`（classes20）：
```java
getAction() -> "com.huawei.espace.module.login_out_success"
q(BaseMsg msg):                       // 处理 UserKickoutV2 / UserKickoutV2Ack
    Intent i = new Intent("com.huawei.espace.module.multi_teminal_kickout");
    // 包 LogoutResp（setRemoveMode(getClientType())）到 "data"
    // result=1/0 表示是否 Ack
    LocalBroadcast.f(i);
s(BaseMsg msg):                       // 记录被踢端
    if (msg instanceof UserKickoutV2) this.j = ((UserKickoutV2)msg).getClientType();
    ...
```

### 5.2 广播消费
`MultiTerminalFunc$b.onReceive` 收到 `multi_teminal_kickout`：
```java
if (receiveData.check()) {
    if (onKickPCListener != null) onKickPCListener.onKickSuccess();
    if (data instanceof LogoutResp) {
        MyOtherInfo info = ...n();
        info.setLogout(logoutResp.getRemoveMode());   // removeMode = clientType
        if (info.isOtherTerminalOffline()) {          // 除本端外无在线端
            MultiTerminalManager.d();
            EventBus.post(new cb0());
            if (config.b.c() && platform.isPad()) MultiTerminalFunc.c(); // registerVoip
            else if (onKickPCListener != null) onKickPCListener.onKickFail();
        }
    }
}
```

### 5.3 removeMode → 被踢端
`MyOtherInfo.setLogout(int)`：
```
case 0 -> setLogoutKickoutPc(list)      // PC 被踢，isPConline=false
case 1 -> setLogoutKickoutMobile(list)  // 手机被踢，isMobileOnline=false
case 3 -> setLogoutKickoutPad(list)     // Pad 被踢，isPadOnline=false
default -> warn "unknow operate type:"
```
`setLogoutKickout*` 会把对应端在线位置 false，并从 `deviceList` 中移除该端设备。

### 5.4 踢出原因枚举 `com.huawei.hwmsdk.enums.KickoutReason`
| value | 常量 | 文案 |
|---|---|---|
| 0 | KICKOUT_BY_LOGIN_ELSEWHERE | 在其他终端上登录 |
| 1 | KICKOUT_BY_ACCOUNT_STOP_USE | 帐号被服务端停止使用 |
| 2 | KICKOUT_BY_ACCOUNT_EXPIRED | 帐号过期 |
| 3 | KICKOUT_BY_MODIFIED_PASSWORD | Portal 修改密码 |
| 4 | KICKOUT_REASON_BUTT | TODO |

---

## 6. 客户端本地的“校验/判定”逻辑

### 6.1 `MultipleTerminalService.getKickType()`（classes18）
```java
if (info.isPConline() && platform.isPad() && info.isMobileOnline()) return 4;
if (platform.isPad() && info.isMobileOnline())                      return 1;
return -1;
```
（返回 -1 表示无需操作；4/1 用于 UI/桥接层判断“是否需要踢 PC”）

### 6.2 `MultipleTerminalService.getTerminalStatus()`（classes18）
组装 `TerminalStatusEntity`：
- `isPConline()` → onlineDevices += "pc"
- `isMobileOnline()` → onlineDevices += "phone"
- `isPadOnline()` → onlineDevices += "pad"
- 依组合给出提示文案资源：
  `im_meeting_pc_phone_online_hint` / `im_meeting_pc_online_hint` / `im_meeting_phone_online_hint`
- 若 `getKickType() != -1` → `isNeedKick = true`，`message = ql0.g(kickType)`

### 6.3 `MyOtherInfo.isOtherTerminalOffline()`（classes20）
```java
if (platform.isPad())   return !isMobileOnline && !isPConline;   // Pad 视角：其它端=手机/PC
else                    return !isPadOnline    && !isPConline;   // 手机视角：其它端=Pad/PC
```

### 6.4 能力/配置开关
- `MyOtherInfo.decodeOtherUserInfo(Map,Map)` 从服务端配置解出 `clientsBothOnline`（是否允许 PC+手机同时在线）
- `enableClientsBothOnline() == (clientsBothOnline == 1)`
- `MultiTerminalManager.c() = enableClientsBothOnline() && !isOtherTerminalOffline() && !mute`（是否“手机多端静音”）
- 平台适配层 `config.b.c() = Lnk2.e()`（`Lbq2` = `com.huawei.welink.security.mdmadapter.impl.PlatformAdapterAPIImpl`，`e()=isIntranet()`）——**多端登录/踢出的处理逻辑仅在特定部署模式（内网版）下生效**

### 6.5 H5 桥接接口（`method://welink.im/...`，供 WebView 调用）
- `queryMultiDeviceStateByAccounts?bundleName=im&accountList=`（`H5COpenService.hasHarmonyOrMacCallee` 等）
- `getTerminalStatus`
- `getKickType`
- `queryMultiDeviceStateByAccounts`（`framework.util.g` / `ConferenceService`）

---

## 7. UI 与用户可见文案（资源实测）

关键字符串（`resources.arsc`，`cn.edu.fafu.iportal`）：

| ID | 文案 |
|---|---|
| 0x7f120910 | 您的帐号已在其他设备登录 |
| 0x7f120917 | PC客户端已登录 |
| 0x7f12091a | PC端已退出 |
| 0x7f12090f | 确定退出PC客户端吗？ |
| 0x7f1208f8 | 退出PC端 |
| 0x7f12092c | 同时在线通知 |
| 0x7f120918 / 0x7f120919 | PC端同时在线时，收到新消息将(同时/不)推送提醒 |
| 0x7f120f5a | 当前 PC 端在线，移动端无法呼叫。是否退出 PC 端？ |
| 0x7f1204d1 | 您已入会的其他设备应用版本较低，不支持多端入会，请升级后重试。 |
| 0x7f120f59 | 多端入会冲突，请重新入会 |
| 0x7f121327 | 电脑端在线时移动端同时接收会议呼叫 |

多端管理页 `com.huawei.hwespace.module.main.ui.MultiTerminalActivity`（classes16）：
- 注册广播：`com.huawei.action.multi.terminal.mute.notify`、`...mute.state`、`com.huawei.espace.module.multi_teminal_kickout`、`com.huawei.espace.module.multi_teminal_login`
- 图标随在线组合切换：`im_multi_terminal_all / _pc_pad / _mobile`（及 `_mute` 变体）
- `refreshKickButton()`：若 PC/手机/Pad 任一在线则显示“踢出/弹出”按钮
- 服务 `com.huawei.espacebundlesdk.service.MultipleTerminalService`，广播 action `com.huawei.espace.module.multi_teminal_kickout`

---

## 8. 对参考仓库 `fafu-checkin` 的影响

参考仓库通过 WebView `Local Storage/leveldb` 取会话、并做白天保活 + 失效静默刷新。结合本次分析：

1. **多设备登录不会在客户端被“本地拦截”**——它表现为服务端下发 `UserKickoutV2` → 客户端执行 `setLogout` → 会话失效。
2. 一旦同一账号在 PC/其它手机登录且企业策略不允许并发（`clientsBothOnline != 1`），本机可能被踢，**checkin 脚本的 token 会失效**，必须依赖其 `refresh` 逻辑重新登录；若脚本被踢后未及时刷新，会漏签。
3. `KickoutReason=3 (Portal修改密码)`、`=2 (帐号过期)` 也会导致同样效果，脚本的“刷新后仍失败”分支需区分处理。
4. 判断“是否被踢”的可靠信号是 `com.huawei.espace.module.multi_teminal_kickout` 广播 / `login_out_success`，而非仅看 HTTP 返回码。

---

## 8.5 登录时上报的设备标识（补充，重要）

> 修正前文的简化说法：**登录时确实会上报设备标识**（但只是 Android ID 级别的“设备标识”，不是风控级指纹）。

### 8.5.1 HTTP 登录请求头 `CloudLoginUtils.buildLoginHeader()`（classes21）
登录请求体只有 `loginName/password/publicKeyFlag/thirdAuthType/tenantid/sliderCode/authType/rsauserName/rsapassword`，
设备信息**全部放在 HTTP Header**：

| Header | 取值来源 | 实际含义 |
|---|---|---|
| `uuid` | `core.utility.e.a()` | **Android ID** |
| `deviceId` | `core.utility.e.a()` | **Android ID**（与 uuid 同值） |
| `sid` | `core.utility.e.e()` | `Build.getSerial()`（需 READ_PHONE_STATE） |
| `deviceName` | `rx0.k()` = `getDeviceNameHeader()` | `厂商_品牌_型号` 拼接后 URL 编码 |
| `deviceType` | `ok2.a()` | 平台类型（手机/Pad） |
| `appName` | `al2.a().getAppName()` | 应用名 |
| `appVersion` / `buildCode` / `businessVersionCode` | `PackageUtils` / `system.h` | 版本信息 |
| `osTarget` | 常量 `"0"` | Android |
| `networkType` | `core.utility.p.a()` | Wifi/4G… |
| `User-Agent` | `rx0.u()` | UA |
| `nflag` / `traceId` / `lang` / `Accept-Language` / `needSF` | 常量/工具 | 协议字段 |

Android ID 取值逻辑（`core.utility.e.b()`）：
```
Settings.Secure.getString(cr, "android_id")
  if 为空 / == "9774d56d682e549c"(已知坏值) / 匹配 ^0+$ → e.d() 随机 ID
结果持久化在 SharedPreferences "mjet_preferences" 的 "device_android_id"
serial 持久化在同 prefs 的 "serialNumber"
```
即：**设备指纹 ≈ Android ID（+ Serial + 型号）**，并且会持久化——同一台机器重登不变。

### 8.5.2 IM 登录消息 `UserLoginV2`（classes17/20）
由 `com.huawei.im.esdk.service.login.m.D(k)` 组装：
- `deviceId` = `k.e()` = `com.huawei.im.esdk.device.a.f()` → 平台适配层 `nk2.l()`=`getDeviceAndroidId()` → **Android ID**
- `deviceName` = `m.B()` = `"Android|" + (Pad|Mobile) + "|" + …`
- `clientType`（50/51/52…）、`appId`、`loginAddr`、`token`、`language`、`loginExtData`（能力位 JSON，键如 `0x111C`，非设备指纹）、`versionNumber`

### 8.5.3 设备标识在多端控制里的作用
- `OperationLoginDevice` 消息带 `deviceID` 字段 → 服务端按 **deviceId** 定位要踢的设备
- `GetLoginDeviceAck$DeviceInfo{id,name,detail,time}` 的 `id` 即设备标识
- 所以“多端登录控制”在服务端以 **deviceId(=Android ID)** 为键

### 8.5.4 另一条并行链路：安全域设备 ID（BYOD/MDM，非普通登录）
- `com.huawei.anyoffice.sdk.keyspace.DeviceIdInfo.getDeviceId()`
  - native 读写（`libanyofficesdk.so` 的 `nativeReadDeviceID/nativeSaveDeviceID`）
  - 支持由 `SDKContextOption.deviceID` 覆盖
  - 有防篡改：`equalsSavedDeviceId()` 不一致则删 `shared_prefs/DeviceId.xml` 并重生成（最多 3 次）
  - 备选：`getImaiId()`=IMEI、`getSysSerialnoId()`=`ro.serialno`、`getSysDevieId()`=UUID、MAC
- 该 ID 仅被 `KeySpace` 使用，而 `KeySpace` 只被 `com.csizg.security.*`、`com.huawei.byod.sdk.*`、`com.huawei.anyoffice.sdk.*`（安全沙箱/MDM）调用 → **与普通账号登录无关**
- 另外 `com.csizg.encrypt.utils.DevicesUtils.getSerialNumber()`、`com.huawei.svn.sdk.mdm.DeviceIdInfo` 属同类安全域组件

### 8.5.5 未发现的东西
- 登录链路里**没有**风控级设备指纹 SDK（无 deviceFingerprint/blackBox/riskToken 之类），只有 Android ID 系标识
- 有 root 检测文案：`手机已root，可能存在安全风险，当前应用不可用。`

## 8.6 AndroidID 到底在“哪一层”被使用（补充）

样本里 AndroidID 的传播路径已完全确认，分为**三个互不相同的层次**：

### 层次 A：WeLink 原生 HTTP 栈（AndroidID 会出现在每个原生请求头里）
`com.huawei.it.w3m.core.http.g` 是 w3m 的 OkHttp 请求构造/拦截工具：
- `g.k(Interceptor$Chain, RetrofitRequest, Map)` = `getRequestBuilder(...)`（**Interceptor 内调用**）
- 依次调 `g.d/e/f/q`：
  - `g.d` = `addBuildConfigHeader`：`buildCode / businessVersionCode / appVersion / osTarget / deviceType`
  - `g.e` = `addHeader`：`lang / uuid / sid / **deviceId** / deviceName / appName / Cpu-Abi-List`
  - `g.f` = `addNetWorkHeader`：`networkType / User-Agent`
  - `g.q` = `setAuthorization`：`Cookie`
- 另有 `Lh21.a()` = `buildLoginHeaderBuilder`（带 `guid`，无 deviceId）、`Lry0.d()` = `getLogoutHeaders`（带 deviceId）

> 即：**凡走 w3m OkHttp/Retrofit 栈的原生业务请求，都会自动带上 `deviceId`(=AndroidID) 等头**。

### 层次 B：登录/认证头
`CloudLoginUtils.buildLoginHeader()` 显式带 `uuid / sid / deviceId / deviceName`（见 §8.5.1），
IM 登录 `UserLoginV2` 带 `deviceId / deviceName`（见 §8.5.2）。

### 层次 C：H5（签到页所在）—— **拿不到 AndroidID**
- 签到页是远程 H5（`http://stuhealth.fafu.edu.cn/declarew/#/fafu/login`），**APK 内没有其本地副本**（assets/res 中搜不到 declarew/stuhealth/stuhtapi）
- APK 的 H5 JSBridge 共 530 个方法，**没有任何**返回 AndroidID/deviceId 的桥方法（device 相关只有会议/云会议类）
- WebView 的 `shouldInterceptRequest`（`H5WebViewClient` / `SafeWebViewClient` cloud、intranet）**都不注入 deviceId**
- 全局扫描：没有任何 WebView 代码路径把 deviceId 写进请求头

### ⚠️ 更正：H5 其实“拿得到”AndroidID —— 通过 JS 桥 `getDeviceInfo`

上面「H5 拿不到 AndroidID」的说法**只对了一半**：H5 不能被动拿到（没有请求头注入），
但可以**主动调用 WeLink 的 JS 桥**拿到。证据链：

1. WeLink H5 桥公开方法表 `MethodConstants.publicMethods`（classes21）包含
   `getDeviceInfo`、`getAuthCode`、`getUserInfo`、`getAuthorizationCode` 等。
2. 桥实现 `com.huawei.it.w3m.core.h5.bridge.methods.AppBridgeMethod.getDeviceInfo(Params)`（classes21）
   返回 JSON：

   | 字段 | 取值 |
   |---|---|
   | `deviceName` | `Build.DEVICE` |
   | `osVersion` | `SDK_INT` |
   | **`deviceID`** | **手机端 = `l31.l()` = `getDeviceAndroidId()` = `core.utility.e.a()` = AndroidID**；Pad 端 = `l31.A()` = `getMCloudUUID()` |
   | `osType` | `"android"` |
   | `deviceModel` | `"android_" + Build.MODEL + "_" + Build.VERSION.RELEASE` |
   | `statusBarHeight` / `screenWidth` / `screenHeight` | 屏幕信息 |

   （`l31` 的实现是 `Lm31`（classes22）：`l()` 在手机分支走 `core.utility.e.a()`，即 AndroidID。）

3. H5 测试页 `assets/safebrowser/we_test.html` 里有现成示例：
   `function getDeviceInfo(){ callMethod("getDeviceInfo", {}, alertFunc); }`
   —— 证明该桥在线上 H5 中可直接使用。

### 真正的“设备锁”在哪
- 用户看到的提示「**同一账号仅允许在同一台手机登录，若需更换手机，请联系辅导员解除设备锁**」
  **不在 APK 内**（全包搜索无此串；`classes12.dex` 的“设备锁”是 anyoffice MDM 的远程锁设备，
  `resources.arsc` 的“更换手机”是“更换手机号”，均无关）→ 该文案由**服务端（健康打卡系统）返回**。
- 因此设备锁是 **FAFU 健康打卡业务系统在服务端实现的**：登录时 H5 调 `getDeviceInfo` 拿到
  `deviceID`(=AndroidID) 上报，服务端把账号绑定到该 `deviceID`；换机后 AndroidID 不同 → 服务端拒绝。

### 修正后的结论
- **AndroidID 确实参与签到相关的校验**——但不是通过 App/请求头，而是
  **H5 主动调 `getDeviceInfo` 桥拿到后上报给健康打卡服务端**。
- 「进签到页面」这一步就会触发（登录/免密登录时上报并比对 `deviceID`）→ 所以换机会提示设备锁。
- 「签到接口」本身的 `Authorization` 签名（`ts:nonce:md5(...):token`）确实不含设备信息，
  设备校验发生在**登录/绑定环节**，不在每次签名的报文里。
- 对脚本的含义：设备锁绑定的是**登录时那台机器的 AndroidID**；脚本在原机复用 token 不受影响，
  但**换机登录会被服务端拦住**（除非辅导员解绑）。

## 8.7 线上实测：FAFU 设备锁开关（2026-09-15）

### 已确认（无需登录）
`GET http://stuhtapi.fafu.edu.cn/health-api/sys_config/no_login/go_out_auth_config?schoolId=1`
→ HTTP 200（**登录前可匿名读**）：

```json
{ "isOpenDeviceLock": 1, "goOutNeedArea": 1, "backEndGoOutAuth": 1,
  "leaveRequiredCancel": 1, "forceConfirmStudentInfo": 0, ... }
```

**结论：FAFU 设备锁已开启（`isOpenDeviceLock = 1`）。**
（对比：`sys_config/go_out_auth_config` 需鉴权，未带 token 返回 401。）

### 学生端提示原文不在前端
- 全量下载 H5 的 132 个 JS（5.2MB）搜索：`同一台手机`/`仅允许在同一`/`更换手机`/`再次登录即可`/`请联系辅导员` **均为 0 命中**。
- H5 登录失败时显示的是**服务端 message**：
  `alert({title:"提示", message: e.data.message})`。
- 前端自有的登录文案仅：`登录授权码获取失败，请退出页面重新登录！`、`登录超时，请退出页面重新登录`、
  `您无权访问，请联系管理员！！`、`您的手机时间与北京时间不一致…`。
- 管理端（辅导员）文案在 `chunk-5e57db5f`：`解除设备锁`、`解除设备锁后,学生即可在新的手机登录,是否确认？`、
  `解除设备锁成功`；对应接口 `GET user/unbind/device/student_file/{id}`。

### 登录触发链（H5 原文，已确认）
```js
HWH5.getAuthorizationCode() → code
  → GET sys_config/no_login/go_out_auth_config?schoolId=<id>   // 读 isOpenDeviceLock
  → if (isOpenDeviceLock) { HWH5.getDeviceInfo() → deviceID(=AndroidID);
                            getAuthCode(code, deviceID) }      // 带 deviceId
     else getAuthCode(code)                                    // 不带
// 真正登录：
POST third_party/welink/login
  params = { schoolNo, clientType:1, code, deviceId:"" }        // deviceId 仅开锁时填
```

### 待验证：设备锁的**错误码**
- `code` 由 WeLink SDK 在 App 内签发（短时效、一次性），脱离 App 无法构造。
- **服务端校验顺序已实测确认（2026-09-15）**：
  1. 学校信息：`GET school/no/wechat?no=fafu&wechatType=1` → 200（`{"id":1,"name":"福建农林大学","no":"fafu"}`，匿名可读）
  2. 登录：`POST third_party/welink/login {schoolNo:"fafu",clientType:1,code,deviceId}`：
     - `code=""` → `Missing request body 'code' for method parameter of type String`
     - `code="FAKE"`（deviceId 空/假）→ **`authorization code does not exist or has expired`**
  - ⇒ **先校验 code（换用户身份），通过后才比对 deviceId**；无有效 code 无法到达设备锁逻辑。
- 抓取方式：在触发设备锁的设备上用 mitmproxy 记录 `third_party/welink/login` 的响应体，
  或持有有效 `code` 时用 `fafu-checkin-http/fafu_probe.py login` 复现（可用同一 code 做“绑定设备 vs 陌生设备”对照）。

### 工具（工作区）
- `/workspace/fafu-checkin-http/fafu_probe.py`：签名 + 读配置 + 复现登录
- `/workspace/fafu-checkin-http/capture_welinklogin.py`：mitmproxy 插件，只记录登录/配置请求

## 8.8 纯 HTTP 模拟 WeLink OAuth 登录（账号密码 → token → authCode）可行性

### 完整链路（APK 内已确认）

```
① 登录（账号密码）
POST {tenantBase}/v11/LoginReg            ← 或 FreeProxyForText/fusion/v8/LoginReg
  form: loginName, password, publicKeyFlag, thirdAuthType, tenantid, sliderCode, authType
        （RSA 模式追加 rsauserName / rsapassword）
  headers: uuid, sid, deviceId(=AndroidID), deviceName, appName, buildCode,
           businessVersionCode, appVersion, osTarget, deviceType, networkType,
           User-Agent, nflag, traceId, lang, needSF, ...
  → SSO 会话；cryptToken 存入 LoginUtil（"welink_login_info"）
  ※ 类：AccountPwdLoginRequest（classes21）；基址 http.k.c（b="/mcloud/mag/"）
  ※ 密码加密：rx0.o()=getRSAEncrypt → rx0.m()=getPublicKeyByString（X509+Base64 公钥）

② 换取授权码
POST {tenantBase}/sso/v1/users/login/auth/code
  header: x-wlk-Authorization: {cryptToken}     ← LoginAPIImpl.b()=getCryptToken()
  body(JSON): { "codeType": "h5"|"we", "codeInfo": <getCodeInfo()> }
  → { "code": <authCode> }
  ※ 类：AuthV2Requester / LoginAPIImpl(m31) / e11.j|k / BrowserH5LoginFreeAPI

③ 换 FAFU 打卡 token
POST http://stuhtapi.fafu.edu.cn/health-api/third_party/welink/login
  form: schoolNo, clientType=1, code=<authCode>, deviceId（开设备锁时）
  → { isRegister, userLoginResp: { token, user, needReset } }
```

### 可行性分级

| 方案 | 可行性 | 说明 |
|---|---|---|
| 从零纯 HTTP（账号密码） | ❌ 不现实 | 需 tenant 专属基址 + RSA 公钥 + 过滑块验证码(`sliderCode`) + 设备风控 |
| 复用 `cryptToken` → 换 authCode | ✅ 可行 | 第②步只要 `cryptToken`，**完全跳过密码与滑块** |
| 复用 FAFU token（现脚本做法） | ✅ 最简 | token 滑动过期，持续保活即可 |

### 关键结论
- **第②步（换 authCode）只依赖 `cryptToken`**，不需要密码/验证码。
  所以「纯 HTTP」的最佳路径是：**取一次 `cryptToken`（App 内 prefs / 抓一次登录）→ 之后用 HTTP 反复换 authCode**。
- `cryptToken` 存在 App 的 `welink_login_info` prefs（`LoginUtil.getCryptToken/saveCryptToken`），可从设备提取。
- 从零模拟的硬阻塞：tenant 基址（`http.k.c`，非硬编码）、RSA 公钥、滑块验证码、登录设备风控。

### 更正：tenant 基址**是硬编码的**（原判断有误）

原以为基址运行时注入、APK 里没有 —— 错。FAFU 定制的
`com.huawei.it.w3m.core.system.i.a()`（`EnvironmentHelper.resetEnvironment()`，classes21）
把全部环境常量写死在 APK 里：

| 字段 | 值 | 含义 |
|---|---|---|
| `h.i` | `api.welink.huaweicloud.com` | **apiDomain** |
| `h.E` | `"https://" + h.i` | `getDomainUrl()` 兜底 |
| `h.j` | `cn.edu.fafu.iportal` | 包名 |
| `h.f` | `DigitalFAFU` | 应用名 |
| `h.h` | `HW_MDM_GROUP_CN_EDU_FAFU_IPORTAL` | MDM 组 |
| `h.y` | `RSA/ECB/OAEPPadding` | **密码加密算法** |
| `h.x` | `AOEMIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAwjPIz2fK…` | **RSA 公钥(Base64 X509)** |
| `h.v` | `914ac9ca1a0043389b52f40c51680892` | appId |
| `h.w` | `a1414f149fa347c3bd822a74903007a6` | appKey |
| `h.p` | `7851` | versionCode（与 APK 一致） |
| `h.N`=`h.M` | `https://mtbook.fafu.edu.cn/iportal/privacy.html` | 隐私声明 |

`getDomainUrl()` 实现（`core.system.h.f()`）：
```java
String apiDomain = al2.a().a("apiDomain");     // 可被设置覆盖
return TextUtils.isEmpty(apiDomain) ? h.E : "https://" + apiDomain;   // 默认 https://api.welink.huaweicloud.com
```

基址拼接（`core.http.k.a()`=resetBaseUrl）：
```java
k.a = getDomainUrl();                    // https://api.welink.huaweicloud.com
k.b = k.a + "/mcloud/mag/";
k.c = getDomainUrl() + "/mcloud/mag/";   // 登录基址
```

### 线上实测（端点存活）
```
POST https://api.welink.huaweicloud.com/mcloud/mag/v11/LoginReg
  → {"errorCode":"5001","errorMessage":"Tenant invalid."}
POST https://api.welink.huaweicloud.com/mcloud/mag/v7/callback/LoginReg
  → {"errorCode":"5001","errorMessage":"Tenant invalid."}
POST .../mcloud/mag/sso/v1/users/login/auth/code
  → {"code":"501","message":"接口不存在!"}   （路径/前缀待定）
GET  .../mcloud/mag/ProxyForText/wemiddle/api/v1/switch/tenantinfo/list
  → {"errorCode":1000,"errorMessage":"用户未登录！"}
```

### 修正后的可行性
| 方案 | 可行性 | 说明 |
|---|---|---|
| 从零纯 HTTP | ⚠️ 差一步 | 基址 ✅、RSA 公钥 ✅ 已硬编码；仍缺 **tenantid**（需登录流程获取）+ **滑块验证码** |
| 复用 cryptToken → authCode | ✅ 可行 | 基址已知，第②步只需 cryptToken |
| 复用 FAFU token（现脚本） | ✅ 最简 | — |

### 8.8.1 登录参数已完全打通（实测）

**tenantId 也硬编码**：`h.t()` = `getTenantId()` 返回 `h.d` = **`4DFC1E0256214BDC84F39D6E9FF41C38`**。

**RSA 加密分支**（`rx0.K` = `shouldUseNewRSACipher(tenantId)`）：
`rx0.a`（`rx0.L()` 初始化）是 **15 个租户 ID 白名单**：
```
8739095DAFDC4E4494BD8D99089AB15F  C0638D382C4F480FA187883A61B1F43F
F85AA69347E04E81ADD0D82593C83CC4  876D6C2E1AA241939C4FC9DBEC8CC2EE
FC2CCD0A88BF4499ABD8D6C26580D621  9478218EA8AE4B5E9E81383AD9AA30E4
6C7772C1DC85408D8D5FCD7374172449  532250D5F3E04289B3026B9ACF36062D
9E7BD0654DA244BC9624AAA76A415561  45F97CCE939840DA93761693D7AE9BFD
C860E7AC026A40C680B757458AA4522D  795B7543850D4AEC8E0463D9A69D10F1
F820E1A9F2C7407A956CDB1DCE0A5195  137D3E86F28C4517A6FFE3C0CA4BCD49
12A3FC9415234BADA894A7EE898C4456
```
**FAFU 的 `4DFC...` 不在其中** → 走 **legacy cipher**（`rx0.l`）：
- 新：`OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA256,…)`
- 旧（FAFU）：`Cipher.getInstance("RSA/ECB/OAEPPadding")` + `init(ENCRYPT,key,SecureRandom)` → 默认 **SHA-1 OAEP**

**公钥**：`h.x`，用 `substring(3)` 去掉 `AOE` 前缀 → `MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA…`
（验证：去前缀后为合法 DER `308201a2 300d0609 2a864886 f70d0101…`，422 字节）

**请求体**（`AccountPwdLoginRequest.buildRequestBody`，实测确认）：
```
loginName = RSA(loginName)      ← 加密
password  = RSA(password)       ← 加密
tenantid  = RSA(tenantId)       ← 也加密！（关键，最初漏了）
publicKeyFlag = "0"
authType  = "phone"             （LoginInfoManager 默认值）
sliderCode = （有验证码时）
thirdAuthType = （TenantInfo）
```
请求头：`nflag, Accept-Language, traceId, lang, uuid, sid, deviceId, deviceName,
appName, buildCode, businessVersionCode, appVersion, osTarget, deviceType, networkType, User-Agent, needSF`

### 实测进展（本机公网）
| 参数组合 | 服务端返回 |
|---|---|
| 不加密 tenantid, flag=1 | `1030` Failed to decrypt the password |
| 不加密 tenantid, flag=0 | `1102` Failed to decrypt the tenantid |
| **tenantid 加密, flag=0** | **`4006` Login session changed. Please log in again.** |

→ **加密已完全正确**（不再有 decrypt 错误），当前卡在 `4006` 会话/风控层。
服务端会下发 **Huawei WAF cookie**：`HWWAFSESID` / `HWWAFSESTIME`（`api.welink.huaweicloud.com` 有 WAF 防护）。

### 结论
- 纯 HTTP 登录的**密码学部分已完全打通**（公钥/算法/字段/flag 全部确定）。
- 剩余 `4006` 属**会话建立/WAF 风控**，可能需：先建立会话（traceId/预热请求）、
  或该错误本身就是反自动化策略。
- 对打卡脚本而言：**仍推荐复用 cryptToken/token 路线**，不必硬闯 4006。

### 工具（工作区）
- `/workspace/fafu-checkin-http/welink_login.py` / `welink_login2.py` / `welink_login3.py`：
  RSA-OAEP 加密 + LoginReg 模拟（参数矩阵）

## 8.9 决定性突破：正确登录路径是 FAFU CAS + OAuth2（非 WeLink 密码直登）

### 关键结论
FAFU 租户 `thirdAuthType = "3"` → **走 OAuth 三方登录**。
所以 `v11/LoginReg` 密码直登**本来就是错路径**（返回 `4006` 不是加密问题，是路径问题）。

### 实测租户配置（可匿名获取）
`POST https://api.welink.huaweicloud.com/mcloud/mag/FreeProxyForText/wemiddle/api/v1/enterprise/auth/info`
`{"tenantid":"4DFC1E0256214BDC84F39D6E9FF41C38"}` → 200：
```json
{ "code":200, "data":{
  "tenantId":"4DFC1E0256214BDC84F39D6E9FF41C38",
  "tenantNameCn":"福建农林大学", "tenantNameEn":"福建农林大学",
  "thirdAuthType":"3",
  "thirdLoginUrl":"http://auth.fafu.edu.cn/authserver/oauth2.0/authorize?client_id=998592105751490560&redirect_uri=https://api.welink.huaweicloud.com/sso/oauth2/magcallback.html&state=<uuid>&response_type=code",
  "skipCodeLogin":1, "realNameAuth":true, "regionalSite":"CN01" }}
```

### 正确登录流程（纯 HTTP 可模拟）
```
1) GET  http://auth.fafu.edu.cn/authserver/oauth2.0/authorize
          ?client_id=998592105751490560
          &redirect_uri=https://api.welink.huaweicloud.com/sso/oauth2/magcallback.html
          &state=<uuid>&response_type=code
   → 302 → GET /authserver/login?service=<...callbackAuthorize...>
   → 200  CAS 登录页「统一身份认证平台」
        提取 lt / execution(=e1s1) / pwdEncryptSalt；保存 cookie(route, JSESSIONID)

2) POST http://auth.fafu.edu.cn/authserver/login?service=<...>
        username, password=AES(password, pwdEncryptSalt),
        lt, execution=e1s1, _eventId=submit, cllt=userNameLogin, dllt=generalLogin
   → 302 CAS ticket → /oauth2.0/callbackAuthorize → 302 → redirect_uri?code=<OAuth2 code>

3) WeLink 用 code 换 token（magcallback）
```

### CAS 站点实测
- `http://auth.fafu.edu.cn/authserver/login` → 200，**统一身份认证平台**（Apereo CAS）
- `https://auth.fafu.edu.cn` → 连接被重置（**只支持 http**）
- 表单：`action=/authserver/login`，字段 `username / password(→saltPassword) / lt / execution=e1s1 /
  _eventId=submit / cllt=userNameLogin / dllt=generalLogin / captcha / dynamicCode`
- 密码前端加密：`encrypt.js`（CryptoJS）→ `encryptPassword(pwd, $("#pwdEncryptSalt").val())`
- 验证码：`checkNeedCaptcha.htl` 决定是否需要；`captchaSwitch=="2"` → **滑块验证码** `createSliderCaptcha()`
- 其他登录方式：手机验证码登录（`dynamicCode/getDynamicCode.htl`）、FIDO

### 对「纯 HTTP 模拟」的最终判断
| 项 | 状态 |
|---|---|
| 正确路径 | ✅ **FAFU CAS OAuth2**（非 WeLink 密码直登） |
| 端点/参数/加密 | ✅ 全部拿到 |
| 密码加密 | ✅ CryptoJS AES + `pwdEncryptSalt` |
| **滑块验证码** | ⚠️ 可能触发，是纯 HTTP 自动化的**主要障碍** |
| 设备锁 | ❌ 仍在 WeLink 侧（换机被拦，需辅导员解绑） |

> 与 `fafu-checkin` 的关系：该脚本用 WebView `getAuthorizationCode()` 免密登录，
> 相当于**跳过 CAS 密码登录**（App 内已登录态），因此不受 CAS 滑块影响——
> 这也是为什么现脚本路线仍是最优。

### 工具（工作区）
- `/workspace/fafu-checkin-http/cas/login.html`、`encrypt.js`、`login.js`：CAS 登录页与加密逻辑
- `/workspace/fafu-checkin-http/welink_login*.py`：WeLink 侧 RSA 登录模拟（错路径，留作对照）

## 8.10 滑块验证码（sliderCaptcha）逆向 —— 已完全打通

CAS 登录页 `captchaSwitch = "2"` → **启用滑块验证码**。组件由
`GET /authserver/common/toSliderCaptcha.htl` 动态加载，实际脚本：
- `/authserver/fafuThemeb/static/js/plugin/sliderCaptcha/js/longbow.slidercaptcha.js`
- `/authserver/fafuThemeb/static/js/plugin/sliderCaptcha/js/ids-sliderCaptcha.js`

### 取图接口（实测）
`GET /authserver/common/openSliderCaptcha.htl` → 200：
```json
{ "smallImage": "<PNG base64>", "bigImage": "<JPEG base64>",
  "tagWidth": 93, "yHeight": 0 }
```
实测尺寸：**大图 590×360**，**小图 93×360**（与另一份分析一致）。

### safeSecure 计算（ids-sliderCaptcha.js 原文）
```js
var d = window.atob(f.smallImage);          // base64 解码小图
var b = d.length;
for (var c = b-16; c < b; c++) safeSecure.value += String.fromCharCode(d.charCodeAt(c));
// → safeSecure = 小图字节的【最后 16 字节】
```

### 校验接口（longbow.slidercaptcha.js 原文）
```js
verify: function (j, i, f) {          // j=canvasLength, i=moveLength, f=tracks
  var h = this.safeSecure.value;
  var e = { canvasLength: j, moveLength: i, tracks: f };
  $.ajax(contextPath + "/common/verifySliderCaptcha.htl",
         { data: { sign: encryptPassword(JSON.stringify(e), h) },   // AES，key=safeSecure
           type: "POST", dataType: "json",
           success: function (k) { if (k.errorCode == 1) { /* 成功 */ } } });
}
```

### 轨迹结构（原文）
```js
var A = { a: v, b: u, c: t };   // a=位移x, b=位移y, c=时间差(ms)
e.push(A);
var y = $("#sliderDiv").width();   // canvasLength = 画布显示宽度
var z = r - n;                     // moveLength = 滑块拖动距离
i.verify(y, z, e);
```

### 缩放关系（原文，重要）
```js
var m = i.options.width / $("#slider-img1")[0].naturalWidth;   // scale = 画布宽 / 大图自然宽
var l = $("#slider-img2")[0].naturalWidth * m;                 // 滑块显示宽
i.canvasCtx.drawImage(f, 0, 0, i.options.width-2, i.options.height);
```
- `DEFAULTS.width = 280`，`#sliderDiv` 内联 `width:280px` → **canvasLength ≈ 280**（运行时读 DOM）
  （注：另一份分析记为 300，实测默认主题为 280；移动主题可能不同，应以运行时 `$("#sliderDiv").width()` 为准）
- `DEFAULTS.height = 155`，`sliderL = 42`，`sliderR = 9`，`offset = 5`
- 缩放：`scale = canvasLength / 590`；`moveLength = 缺口x × scale`

### 完整绕过流程（纯 HTTP 可行）
```
1) GET  /authserver/common/openSliderCaptcha.htl
   → bigImage, smallImage, tagWidth(93), yHeight
2) safeSecure = base64_decode(smallImage)[-16:]
3) OpenCV 模板匹配：在 bigImage(590×360) 里定位缺口 → gap_x
4) canvasLength = 280（或运行时取）；moveLength = gap_x × (280/590)
5) 构造拟人轨迹 tracks=[{a,b,c},...]（首点 {a:0,b:0,c:0}）
6) sign = AES( JSON.stringify({canvasLength,moveLength,tracks}), key=safeSecure )
7) POST /authserver/common/verifySliderCaptcha.htl  {sign}  → errorCode==1 即通过
8) 通过后自动提交登录表单 → CAS ticket → OAuth2 code → WeLink token
```

> 结论：**滑块验证码是纯前端的（longbow sliderCaptcha），可被 OpenCV + AES 签名绕过**，
> 但服务端很可能校验轨迹形状（拟人度），需要构造合理轨迹。

### 8.10.1 实测：滑块已被成功破解 ✅

**已复现通过**（服务端返回 `{"errorCode":1,"errorMsg":"success"}`）：

```
gap=110/590 base=52.2 -> 命中 moveLength=60   {"errorCode":1,"errorMsg":"success"}
gap=158/590 -> 命中 81
gap=193/590 -> 命中 99
gap=218/590 -> 命中 111
gap=248/590 -> 命中 124
（另有多组历史命中：canvas=278 move=151 -> success）
```

**成功率约 4/5（80%）**，失败均因**服务端频率限制**（短时间大量请求后 `errorCode` 恒为 0，冷却后可恢复）。

### 已确认的完整参数
| 项 | 值（实测） |
|---|---|
| `safeSecure` | `base64_decode(smallImage)[-16:]`，为 **16 字节 ASCII**（如 `BKTHLivUtrIHRv2S`）→ 直接作 AES-128 key |
| 签名 | `AES-128-CBC/PKCS7( randomString(64) + JSON , key=safeSecure , iv=randomString(16) )` → base64 |
| `canvasLength` | **278**（= `options.width(280) - 2`） |
| `moveLength` | `round(gap_x × 280 / W) + 约 5`（实测偏移 +4.4~+7.8；建议在 `base+2..base+8` 窗口搜索） |
| `tracks` | `[{a:累计dx, b:累计dy, c:时间差ms}, …]`，首点 `{a:0,b:0,c:0}`，末点 `a=moveLength` |
| 成功判据 | `verifySliderCaptcha.htl` 返回 `errorCode == 1` |
| 失败重试 | **失败不使验证码失效**，可继续尝试；但请求过密会触发 IP 级限流 |

### 关键实现细节（易踩坑）
1. `safeSecure` 是**小图末尾 16 字节**（服务端随机生成并附加在 PNG 后），非透明通道；
2. AES 明文**必须前置 64 个随机字符**（`randomString(64)`）——这也是为何 IV 随机也能解密
   （CBC 下错误 IV 只污染首块，真实数据在第 64 字节之后，服务端剥离前 64 字符即可）；
3. 大图宽度会变（实测 **500 与 590** 两种），`moveLength` 必须按实际 `W` 缩放；
4. `canvasLength` 用 **278** 而非 280。

### 工具（工作区）
- `/workspace/fafu-checkin-http/cas_slider_solver.py` —— **可用的滑块求解器**（取图→识别→签名→验证）
- `/workspace/fafu-checkin-http/cas/`：`login.html` / `login.js` / `encrypt.js` /
  `slider.html` / `longbow.js` / `ids-slider.js` / `slider.json` / `slider.css`
- `/workspace/fafu-checkin-http/fafu_probe.py`、`welink_login*.py` —— FAFU 签名与 WeLink 登录模拟

## 8.11 全流程实测结论（2026-09-15，使用真实测试账号）

### 实测通过的环节
| 步骤 | 结果 |
|---|---|
| 1. 取滑块图 `openSliderCaptcha.htl` | ✅ |
| 2. `safeSecure` = 小图末 16 字节 | ✅ |
| 3. OpenCV 识别缺口 | ✅ |
| 4. AES 签名 | ✅ |
| 5. 滑块验证 `verifySliderCaptcha.htl` | ✅ `{"errorCode":1,"errorMsg":"success"}` |
| 6. 提交登录表单（账号 + AES 密码 + lt + execution） | ✅ **密码被接受** |
| 7. MFA 短信验证码提交 `reAuthSubmit.do` | ✅ `{"msg":"认证成功","code":"reAuth_success"}` |

### 被拦截的环节：多因子认证（MFA）
登录成功后跳转：
```
/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true&service=...
页面提示：你本次登录为非可信客户端登录，需完成多因子认证
reAuthUserId=<学号>  reAuthType=3(短信)  isMultifactor=true  isSleepAccount=0
```

MFA 相关接口：
- 发送短信：`POST /dynamicCode/getDynamicCodeByReauth.do`
  `{userName, authCodeTypeName:"reAuthDynamicCodeType"}` → `{"res":"success","mobile":"195****7221","codeTime":120}`
  （**120 秒有效 + 发送冷却约 73 秒**）
- 提交：`POST /reAuthCheck/reAuthSubmit.do`
  `{service, reAuthType, isMultifactor, password:"", **dynamicCode**, uuid:"", answer1:"", answer2:"", otpCode:""}`
  → `{"msg":"认证成功","code":"reAuth_success"}`
  （**坑：短信验证码字段名是 `dynamicCode`，不是 `otpCode`**；`otpCode` 仅用于 reAuthType=10 的 OTP 绑定）

### 最终结论
- **密码 + 滑块已完全自动化通过**（含真实登录，服务端接受密码）。
- **MFA 短信可编程提交**（已拿到 `认证成功`）。
- **但**：完成 MFA 后再请求 `/login?service=<OAuth>` 或重新 `authorize`，**仍被要求 MFA**；
  即使提交时带 `skipTmpReAuth=true`（信任此设备）也未生效。
  → CAS 对该 OAuth 应用**每次授权都要求 MFA**，而「信任设备」需绑定设备指纹（纯 HTTP 客户端无法提供）。

**所以：从零纯 HTTP 模拟 WeLink OAuth 登录，最终被 CAS 的 MFA 策略阻断，无法完全自动化。**

### ✅ 完整复现（2026-09-15 最终结论，见 `fafu-checkin-http/REPRODUCED.md`）
**纯 HTTP 全链路已打通，设备锁已 100% 复现。**

**两个关键根因（此前卡住的原因）：**
1. **MFA 后仍要求 MFA** → 因为提交时带了 `skipTmpReAuth=true`（"信任此设备"）。
   服务端尝试绑定设备指纹失败，反而导致 MFA **未真正完成**。改为 **`false`**（"仅本次登录"）立即通过。
2. **换 token 报 `4006 Login session changed`** → 两处错误：
   - 必须使用 WeLink `enterprise/auth/info` 返回的**真实 state**（UUID），不能自编；
   - 请求参数缺 **`thirdAuthType=3`** 与 **`authType=phone`**（`LoginUtil.getLoginAuthType()` 默认 "phone"）。

**完整链路：**
```
① POST MAG/FreeProxyForText/wemiddle/api/v1/enterprise/auth/info {tenantid}
   → thirdLoginUrl（含 WeLink 生成 state=UUID）
② GET  CAS /oauth2.0/authorize → 登录页
③ 滑块 /common/openSliderCaptcha.htl → /common/verifySliderCaptcha.htl
④ POST CAS /login（AES-CBC(rawKey, salt)）→ 302 reAuthLoginView.do?isMultifactor=true
⑤ POST /dynamicCode/getDynamicCodeByReauth.do → 短信
⑥ POST /reAuthCheck/reAuthSubmit.do（dynamicCode, skipTmpReAuth=false）→ reAuth_success
⑦ GET  /login?service=<callbackAuthorize> → 302 magcallback?code=<OAuth code>
⑧ POST MAG/v7/callback/LoginReg {code, tenantid=RSA-OAEP, thirdAuthType:"3", authType:"phone"}
   → Set-Cookie: token=...; Max-Age=7200；body: refresh_token / uid
```

**设备锁复现：**
```
⑨ POST MAG/ProxyForText/sso/auth/v2/code  header x-wlk-Authorization:<token>
   body {codeType:"h5", codeInfo:"stuhealth.fafu.edu.cn"} → {code:<FAFU authCode>}
⑩ POST http://stuhtapi.fafu.edu.cn/health-api/third_party/welink/login
   header Authorization: base64(ts:nonce:md5("AtPs2O1xEnhwkKDV"+url+ts+nonce):token)
   form {schoolNo:"fafu", clientType:1, code:<authCode>, deviceId:<AndroidID>}
   → 陌生/空 deviceId ⇒ "同一账号仅允许在同一台手机登录，若需更换手机，请联系辅导员解除设备锁后再次登录即可！"
```

**关键常量：**
- `schoolNo` = **`"fafu"`**（非学号）
- H5 签名密钥（硬编码）= **`AtPs2O1xEnhwkKDV`**
- `authCode` 一次性，用完即失效
- 设备锁 = 服务端比对 `deviceId` 与账号绑定值，不一致即拒绝（与页面提示一致）

> 该结论**独立复现**了参考分析中「用陌生 AndroidID 调 `third_party/welink/login` 可复现设备锁提示」的说法。

### 对 `fafu-checkin` 的最终印证
App 内 `HWH5.getAuthorizationCode()` 走的是**已登录的 App 会话**（App 是「可信客户端」，不走 CAS 密码 + 滑块 + MFA）。
这从反面证明了：**复用 App 内 token 才是唯一稳定的自动化路线**，重放 CAS 登录不可行。

### 工具（工作区）
- `/workspace/fafu-checkin-http/cas_slider_solver.py` —— 滑块求解器（已验证可用）
- `/workspace/fafu-checkin-http/cas_login_full.py` —— 滑块 + 登录表单
- `/workspace/fafu-checkin-http/mfa_login.py` —— 含 MFA 的完整流程（send / verify）
- `/workspace/fafu-checkin-http/mfa_complete.py` —— MFA 提交 + 完成登录

## 9. 关键类速查表

| 功能 | 类（dex） |
|---|---|
| 登录设备列表消息 | `com.huawei.ecs.mip.msg.GetLoginDevice(Ack$DeviceInfo)`（17） |
| 操作登录设备 | `com.huawei.ecs.mip.msg.OperationLoginDevice`（17） |
| 多端上下线通知 | `com.huawei.ecs.mip.msg.UserMultiDeviceOptNotifyV2`（17） |
| 被踢通知 | `com.huawei.ecs.mip.msg.UserKickoutV2`（17） |
| 多端状态推送处理 | `com.huawei.im.esdk.msghandler.pushmsg.x.e()`（20） |
| 被踢消息处理 | `com.huawei.im.esdk.msghandler.im.f.q()/s()`（20） |
| 本地状态容器 | `com.huawei.im.esdk.contacts.MyOtherInfo`（20） |
| 多端业务逻辑 | `com.huawei.hwespace.function.MultiTerminalFunc`（18） |
| 多端状态刷新 | `com.huawei.hwespace.function.MultiTerminalManager`（18） |
| 多端服务/桥接 | `com.huawei.espacebundlesdk.service.MultipleTerminalService`（18） |
| 多端管理页 | `com.huawei.hwespace.module.main.ui.MultiTerminalActivity`（16） |
| 终端类型枚举 | `com.huawei.hwmbiz.login.api.LoginDeviceType`（19） |
| 踢出原因枚举 | `com.huawei.hwmsdk.enums.KickoutReason`（16/19/20） |
