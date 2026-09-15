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
| 端到端验证 | `e2e_verify.py` |
| 接口参数 / 逆向证据 | `docs/reverse-notes.md` |

## 二、架构

```
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

## 四、约定与坑

- **`signState` 语义**：`0`=未签；非 0=已签（`2` 疑为请假）；缺失=未知。无官方文档，依参考仓库推断。
- **`refresh_token` 滑动续期**：每次刷新重置 30 天（实测 `refresh_expires_in` 恒 `2592008`s）。持续运行永不过期；仅连续 30 天未运行才需重登。
- **`authCode` 一次性**：用完即失效，不可缓存。
- **滑块不走全局限流**：`fafu_login._cas` 用独立请求（需 0.3s 级时序）；仅登录时触发，频率低。
- **`CFG` 属性**：拼错抛 `AttributeError`；会话态键限 `_STATE_KEYS`（`we_link_token`/`refresh_token`/`fafu_token`/`last_refresh_ts`/`next_refresh_after`/`refresh_fail`）。
- **刷新退避阶梯**：`_BACKOFF = 60/300/1800/7200` 秒（1→5→30→120 分钟）；刷新成功后写 `cooldown`（默认 30 分钟）并清零 `refresh_fail`。`rootless_checkin.py status` 会显示当前退避余量。
- **响应字段判定统一用 `fafu_lib._has`**：优先按 JSON 键判定，非 JSON（WAF 的 HTML 拦截页）回退子串匹配。不要再用 `'"records"' in body` 这类裸子串判断——服务端改个空格就会静默失效。
- **PID 文件由 `daemon.py` 自己写**（`_write_pid`/`_clear_pid`），管理脚本只读不写。Windows 上删除文件前必须先关闭句柄，否则 `os.remove` 抛 `PermissionError` 被静默吞掉。
- **`signState` 未知值的探测**：当前只处理 `{0,1,2,None}`；遇到其他值会保守尝试签到。若发现新值，记入日志并更新本条。

## 五、验证

| 目的 | 命令 |
|---|---|
| 单元测试（离线，不发真实请求） | `python3 -m unittest discover -s tests -v` |
| 导入检查 | `python3 -c "import fafu_config,fafu_lib,fafu_login,daemon"` |
| 会话状态 | `python3 rootless_checkin.py status` |
| 刷新会话 | `python3 rootless_checkin.py refresh` |
| 立即签到 | `python3 rootless_checkin.py once` |
| 全链路 | `python3 e2e_verify.py` |
| 调度一轮 | `python3 daemon.py --once` |

> 依赖：`pip install -r requirements.txt`。日常签到（token 有效）只需标准库。

## 六、当前状态与待办

**已完成**：登录链复现、设备锁、滑动续期、守护调度、跨平台（Linux/Windows/Termux）、四轮代码检查（29 项修复）、离线回归测试 `tests/`、刷新失败退避、state 并发写保护、PID 文件防重复启动。

**待人工**：
1. 改 CAS 密码（`config.ini` 明文存储）
2. 踢 `pixel_8_14` 设备

**已知限制**：
- "信任此设备"纯 HTTP 无法复现（需客户端设备指纹），不影响打卡。
- `signState` 语义未获官方确认。

## 七、接手提示（agent）

- **安全**：`config.ini` / `state.json` 含真实凭据，已 gitignore，**切勿提交或外泄**。
- **改前先跑** `e2e_verify.py` 建立基线；**改后重跑**确认不破坏链路。
- **验证优先于声明**：签名/窗口/限流等行为以实测为准——本项目每条结论都有实测支撑。
- **建议技能**：继续逆向/写文档 → `writing-for-agents`；需要交接 → `handoff`。
