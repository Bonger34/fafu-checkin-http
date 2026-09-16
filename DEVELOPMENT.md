# 开发文档

> **面向**：接手本项目的开发者 / AI agent。
> 使用见 [README](README.md) · 逆向细节见 [docs/reverse-notes.md](docs/reverse-notes.md) · 部署见 [docs/deploy.md](docs/deploy.md)。
> ⚠️ 仅供学习研究，使用前请阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## 一、定位：去哪改什么

| 要改的 | 文件 |
|---|---|
| 配置 / 会话态 / 公共常量 / 时间 / 脱敏 | `fafu_config.py` |
| 签名 / HTTP / 限流 / 登录链后半 / 会话保障 | `fafu_lib.py` |
| CAS 登录（滑块 / 密码 / MFA / OAuth） | `fafu_login.py` |
| 首次登录入口 | `login_once.py` |
| 日常命令（status / refresh / once） | `rootless_checkin.py` |
| 调度（保活 / 签到窗口） | `daemon.py` |
| 启动 / 停止 / 状态 / 日志（**唯一入口**） | `run.py` |
| 端到端验证 | `e2e_verify.py` |
| 离线测试 | `tests/` |
| 接口参数 / 逆向证据 | `docs/reverse-notes.md` |

## 二、架构

```
【管理】启动器（唯一入口）
  run.py start | stop | status | log
    → 拉起 / 停止 daemon.py；PID 文件与身份校验见下方不变量

【一次性】登录
  login_once.py
    → fafu_login.login()          CAS 滑块 + 密码
      fafu_login.submit_mfa()     MFA（短信）
      fafu_login.oauth_to_welink() OAuth code → WeLink token
    → state.json { refresh_token, we_link_token }

【循环】日常
  daemon.py / rootless_checkin.py
    → fafu_lib.ensure_token()
        ├ 本地 token 有效 → 复用（1 次请求验证）
        └ 失效 → refresh_we_link → fetch_authcode → exchange_fafu_token
    → fafu_lib.query_task() → 判断 signState → api(.../student/sign)
```

**模块职责**（每块只做一件事）：

| 模块 | 职责 |
|---|---|
| `fafu_config` | 配置加载 + 会话态持久化 + 公共常量 + `TZ`/`mask`/`fmt_hm` |
| `fafu_lib` | 签名、HTTP（限流/退避/安全 JSON）、登录链后半、`ensure_token` |
| `fafu_login` | CAS 登录前半（滑块/密码/MFA/OAuth） |

## 三、不变量（改代码前必读）

硬约束。每条：规则 → 为什么 → 在哪。

1. **`deviceId` 精确匹配且区分大小写**。服务端做字符串比对（空值、末位差 1、大写均被拒）。→ `fafu_lib.exchange_fafu_token`
2. **外发请求间隔 ≥2s**。实测 <1s 连发触发 WAF（端点级，冷却 15–20 分钟）。→ `fafu_lib.MIN_GAP` / `_throttle`
3. **签到窗口按北京时间（UTC+8）**。不能用系统时区，否则 UTC 机器把 21:30 算成 13:30。→ `fafu_config.TZ` / `daemon.now`
4. **`signState is None` 时保守签到**。只有明确 `!= 0` 才算已签；`None` 视为未知并尝试签到（幂等，宁可多签不可漏签）。→ `daemon.try_sign`
5. **`configparser(interpolation=None)`**。否则密码含 `%` 抛 `InterpolationSyntaxError`。→ `fafu_config.Config.__init__`
6. **token 落日志前脱敏**。用 `fafu_config.mask`，避免学号/设备/token 明文。→ `daemon` / `rootless_checkin` / `e2e_verify`
7. **会话态写入必须「加锁 + 增量合并 + 原子落盘」**。三者缺一不可：锁让「读—改—写」跨进程串行；**增量合并**保证不拿陈旧内存快照覆盖别的进程刚写的键（`refresh_token` 每次刷新都轮换，被抹掉就得重新短信登录）；`PID+随机数` 的 tmp 再 `os.replace` 保证不写坏文件。→ `fafu_config.save_state` / `_state_lock`
8. **签名 URL 不含 query**。`mk_auth` 只签路径，query 单独拼（与参考仓库一致，服务端接受）。→ `fafu_lib.api`
9. **刷新失败也必须写退避**。`ensure_token` 的每条失败分支都要落 `next_refresh_after`；否则窗口内每分钟重打整条刷新链（3 个 auth 请求），实测 ~20 分钟 30 次即触发 WAF 端点限流 15–20 分钟——正好覆盖签到窗口，直接漏签。→ `fafu_lib._refresh_state`
10. **登录前必须先过 `checkNeedCaptcha`，滑块还要先过 `toSliderCaptcha`**。这两个前置调用缺任何一个，`openSliderCaptcha` 照常返回图片，但 `verifySliderCaptcha` 永远回通用错误——调 moveLength、改 tracks、换加密都无效。验证码类型由服务端按风控在图形码/滑块间切换。→ `fafu_login._need_captcha` / `_to_slider` / `login`
11. **CAS 全流程必须共用同一个 CookieJar**。登录成功会 302 到 MFA 页并下发新的 `JSESSIONID`，裸 `urlopen` 自动跟随重定向会丢弃它；拿旧 cookie 调 MFA 接口只会得到「请求超时重定向」。而且验证码结果存在会话里，提交 `/login` 也必须带同一会话。→ `fafu_login._new_opener` / `_cas(opener=...)`
12. **提交 `/login` 的字段集只有 8 个**：`username/password/captcha/_eventId/cllt/dllt/lt/execution`。`captcha` 在滑块模式下是**空串也必须存在**；多带 `userPassword`/`rememberMe`/`agreeProtocol`/`uuid` 服务端不认。→ `fafu_login.login`

## 四、约定与坑

- **`signState` 语义**（无官方文档，依参考仓库推断）：`0`=未签；非 0=已签（`2` 疑为请假）；缺失=未知。当前只处理 `{0,1,2,None}`，遇到其他值一律保守尝试签到（见不变量 4）；若发现新值，记入日志并更新本条。
- **`refresh_token` 滑动续期**：每次刷新重置 30 天（实测 `refresh_expires_in` 恒 `2592008`s）。持续运行永不过期；仅连续 30 天未运行才需重登。
- **`authCode` 一次性**：用完即失效，不可缓存。
- **滑块不走全局限流**：`fafu_login._cas` 用独立请求（需 0.3s 级时序）；仅登录时触发，频率低。
- **验证码有两条路径，风控决定用哪条**：`captchaSwitch="1"` 图形码（`getCaptcha.htl`，80x30 JPEG，固定 4 位，OCR 走 `solve_captcha`）；`"2"` 滑块（`solve_slider`）。两者都要保留，不能只留一条。
- **OCR 只能靠长度过滤，不能靠置信度**：实测 ddddocr 漏字时置信度仍有 0.99+（高于部分正确样本）。`_pick_captcha` 以长度为准、不合格就换图——换图不消耗登录次数，提交才消耗。`ddddocr` 为可选依赖，惰性导入。
- **`_cas` 的 `binary=True`**：验证码图片是 JPEG，默认的 `.decode("utf-8","replace")` 会直接抛 `UnicodeEncodeError`。
- **`CFG` 属性**：拼错抛 `AttributeError`；会话态键限 `_STATE_KEYS`（`we_link_token`/`refresh_token`/`fafu_token`/`last_refresh_ts`/`next_refresh_after`/`refresh_fail`）。
- **刷新退避阶梯**：`_BACKOFF = 60/300/1800/7200` 秒（1→5→30→120 分钟）；刷新成功后写 `cooldown`（默认 30 分钟）并清零 `refresh_fail`。`rootless_checkin.py status` 会显示当前退避余量。
- **响应字段判定统一用 `fafu_lib._has`**：优先按 JSON 键判定，非 JSON（WAF 的 HTML 拦截页）回退子串匹配。不要再用 `'"records"' in body` 这类裸子串判断——服务端改个空格就会静默失效。
- **不要再引入 `run.sh` / `run.bat` 这类平台专用启动脚本**。它们曾经各自演化出不一致的行为——停止方式（`taskkill /T /F` vs `kill`）、`log` 行数（全量 vs `tail -30`）、`status` 是否报守护状态、PID 文件由谁写——"统一"最后变成了"两套规范"。现在入口只有 `run.py`，平台差异只允许出现在 `_spawn_daemon` 与 `_stop_proc` 两处。
- **`run.py start` 之后 daemon 必须活下来**。Windows 走 `CREATE_NEW_CONSOLE`（顺带给用户一个看日志的窗口），POSIX 走 `start_new_session`。这条性质在沙箱里会被进程树回收掩盖，验证时要放在后台任务里做——否则会误判成"启动失败"。
- **PID 文件的生命周期只有一条链**：`daemon.py` 自己写（`_write_pid`/`_clear_pid`，内容是 `{pid, started}`），管理脚本**只读不写**；`run.py` 只在**确认进程已结束**后才删文件——kill/taskkill 失败却仍删的话，旧实例还在跑但 PID 没了，下次 start 检测不到进程就会重复拉起第二个实例（多实例会并发空打刷新链）。身份判据是**进程创建时间**而非进程名：只按名字（`python*`）判断会被 PID 复用骗到——daemon 被强杀后 PID 分给别的 python 进程，start 就误判「已在运行」拒绝启动，而 stop 会去杀一个完全无关的进程；缺 psutil 时判据降级为「仅看 PID 存在」，此时 **stop 会拒绝执行**而不是冒险。Windows 上删文件前还必须先关闭句柄，否则 `os.remove` 抛 `PermissionError` 被静默吞掉。
- **Windows 上可见窗口必须关掉控制台 QuickEdit**。用户在窗口里拖选文本会让控制台进入标记模式，之后任何写 stdout 的进程都阻塞在 `WriteConsole` 上。daemon 一卡可能就是几小时，正好错过签到窗口——这是「把日志放进可见窗口」必须付的代价，不是可选项。
- **日志由 daemon 自己写文件**（`FileHandler(encoding="utf-8")`），不靠 shell 重定向。shell 重定向在中文 Windows 下按控制台代码页写，`daemon.log` 会变成 GBK。手工启动时**不要**把 stdout 重定向到 `daemon.log`，否则与 FileHandler 双写。

## 五、验证

| 目的 | 命令 |
|---|---|
| 单元测试（离线，不发真实请求） | `python3 -m unittest discover -s tests -v` |
| 导入检查（最小依赖） | `python3 -c "import fafu_config,fafu_lib,daemon"` |
| 导入检查（含登录模块，需 numpy/opencv） | `python3 -c "import fafu_login"` |
| 会话状态 | `python3 rootless_checkin.py status` |
| 刷新会话 | `python3 rootless_checkin.py refresh` |
| 立即签到 | `python3 rootless_checkin.py once` |
| **守护进程管理** | `python3 run.py start / status / log / stop` |
| 全链路 | `python3 e2e_verify.py` |
| 调度一轮 | `python3 daemon.py --once` |

> 依赖：`pip install -r requirements.txt`。日常签到（token 有效）仍需要 `cryptography`
> ——`fafu_lib` 顶层 import 了它，缺了直接 `ImportError`；`numpy`/`opencv-python` 仅登录需要。

## 六、当前状态与待办

**已完成**：登录链复现、设备锁、滑动续期、守护调度、跨平台（Linux/Windows/Termux）、四轮代码检查（29 项修复）、离线回归测试 `tests/`、刷新失败退避、state 并发写保护、PID 文件防重复启动、登录链前置调用修复（`checkNeedCaptcha`/`toSliderCaptcha`）、图形验证码 OCR 路径、**真实账号端到端跑通**（登录→滑块→MFA→短信→OAuth→WeLink token→FAFU token→查询任务）、**启动器统一为 `run.py` 单一入口**（`run.sh`/`run.bat` 已删除，Windows 侧带可见日志窗口）。

**待人工**：
1. 改 CAS 密码（`config.ini` 明文存储）
2. 踢 `pixel_8_14` 设备

**已知限制**：
- "信任此设备"纯 HTTP 无法复现（需客户端设备指纹），不影响打卡。
- `signState` 语义未获官方确认。
- 图形验证码路径（`captchaSwitch=1`）尚未遇到真实场景：实测该账号始终是滑块（`captchaSwitch=2`），OCR 只做到「取图+识别+长度过滤」的离线与半在线验证。
- 风控会按账号/时段变化：`checkNeedCaptcha` 返回 `isNeed` 与 `captchaSwitch` 都可能在登录页上变化，抓包与实测都要以当次响应为准。

## 七、接手提示（agent）

- **安全**：`config.ini` / `state.json` 含真实凭据，已 gitignore，**切勿提交或外泄**。
- **改前先跑** `python3 -m unittest discover -s tests`（离线、秒级）；涉及真实链路时再跑 `e2e_verify.py` 建立基线，改后重跑确认不破坏。
- **改登录链之前先读 `reverse/cas/login.js`**：验证码流程有多个「不调用就静默失败」的前置接口，只看 Python 侧完全看不出来（是哪几个、为什么见不变量 10）。
- **验证优先于声明**：签名/窗口/限流等行为以实测为准——本项目每条结论都有实测支撑。
- **建议技能**：继续逆向/写文档 → `writing-for-agents`；需要交接 → `handoff`。
