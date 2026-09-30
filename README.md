# fafu-checkin-http · 数字FAFU 自动签到（免 root）

纯 HTTP 复现 WeLink/CAS 登录链与打卡接口，**无需 root、无需安装 App**。

与 [Bonger34/fafu-checkin](https://github.com/Bonger34/fafu-checkin)（Magisk/KernelSU 模块，读 App 的 LevelDB 取 token）的区别：本项目从零复现登录流程，因此可在任意环境运行——代价是需要账号密码，以及设备锁绑定的 `device_id`。

> ## ⚠️ 免责声明
>
> - 本项目**仅供个人学习与安全研究**，用于理解 OAuth/CAS 登录流程与接口签名机制。
> - **严禁**用于代签、替他人签到、伪造考勤等任何违反校规或学术诚信的用途。
> - 使用本项目的**全部风险与后果由使用者自行承担**；作者不对账号封禁、纪律处分、数据丢失等任何直接或间接损失负责。
> - 本项目**与福建农林大学及华为 WeLink 无关**，非官方工具；接口与签名**可能随时变更**，不保证持续可用。若校方明确禁止，请立即停止使用并删除本项目。
> - 下载、克隆或运行本项目，即表示你已阅读并同意上述条款。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 文档导航

| 我想…… | 看 |
|---|---|
| 装好、跑起来 | **本 README** |
| 部署到 Linux / Windows / NAS / Termux | [docs/deploy.md](docs/deploy.md) |
| 二次开发 / 接手维护 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 查术语（签到 / 保活 / 会话保障…） | [CONTEXT.md](CONTEXT.md) |
| 搞懂登录链、设备锁、限流 | [docs/reverse-notes.md](docs/reverse-notes.md) |
| 深入 WeLink 多设备/踢下线机制 | [docs/multi-device-login-analysis.md](docs/multi-device-login-analysis.md) |
| 查看使用条款 | [DISCLAIMER.md](DISCLAIMER.md) · [LICENSE](LICENSE) |

## 工作原理

一次登录换来一份**会自己续期**的会话，此后只需循环「取 token → 查任务 → 按需签到」。

```
【一次性】login_once.py
   CAS 登录（滑块 + 密码）→ MFA 短信 → OAuth code → WeLink token
     └─ 产出 state.json { refresh_token, we_link_token }

【日常】daemon.py / checkin.py
   ensure_token()                        会话保障
     ├ 本地 token 仍有效 → 复用
     └ 失效 → refresh_we_link → fetch_authcode → exchange_fafu_token
   sign_once(token)                      一轮签到
     └ 查询任务 → 判定 signState → 需要时提交
```

- `refresh_token` **滑动续期**：每次刷新重置 30 天，持续运行即永不过期。
- 会话态全部落在 `state.json`；日常运行不再需要短信，**也不需要密码**——只有会话彻底失效时才要求重新登录一次。
- 签到判定只有一处实现（`fafu_lib.sign_once`），守护进程与 CLI 共用同一套逻辑。

## 快速开始

> **命令约定**：本文所有命令统一写作 `python3` / `cp`；**Windows 用户请替换为 `python` / `copy`**。
> 全部命令都在**仓库根目录**执行（模块在 `src/`，测试在 `tests/`，配置与会话态在根目录）。

```bash
pip install -r requirements.txt     # 1. 装依赖
cp config.example.ini config.ini    # 2. 建配置，填 username / password / device_id
python3 src/login_once.py           # 3. 首次登录（收一次短信验证码）
python3 src/run.py start            # 4. 启动守护进程，此后全自动
python3 src/run.py status           # 5. 确认守护进程与会话都正常
```

其中第 2 步的 `device_id` 最容易填错，说明见「配置」。以下各节按这个顺序展开。

## 环境要求

- Python **3.9+**

```bash
pip install -r requirements.txt
```

| 库 | 用途 | 何时需要 |
|---|---|---|
| `cryptography` | AES 加密（登录）、RSA-OAEP（租户 ID） | **始终**——`fafu_lib` 顶层导入，缺了直接 `ImportError` |
| `numpy` + `opencv-python` | 滑块缺口定位 | 仅首次登录 |
| `psutil` | `run.py` 的进程身份校验 | 用 `run.py` 管理守护进程时 |
| `ddddocr` | 图形验证码 OCR（可选） | 仅当风控要求图形码时 |

> CAS 会**按风控**在「滑块」与「图形码」两条路径之间切换，只有被要求图形码时才用得上
> `ddddocr`；未安装时脚本会明确提示改用手机 App 扫码登录，而不是抛堆栈。

## 配置

```bash
cp config.example.ini config.ini
chmod 600 config.ini        # 仅 POSIX；Windows 跳过此步，依赖 NTFS 默认权限
```

`[account]` 下需要你填的只有三项：`username`（学号）、`password`（CAS 密码）、`device_id`。

`device_id` 必须是该账号**已绑定设备**的标识，精确匹配且区分大小写——填错会被服务端以「同一账号仅允许在同一台手机登录…」拒绝（见「故障排查」）。

`tenant_id` 已预置为福建农林大学固定值（全校一致），**只有换到其他学校时才需要改**。

也可以用环境变量覆盖同名配置项，优先级更高：`FAFU_USERNAME` / `FAFU_PASSWORD` / `FAFU_DEVICE_ID` / `FAFU_TENANT_ID`。

打卡上报的定位默认是校区坐标，属可选项。要改就取消注释 `[location]` 的 `lng` / `lat`，或设 `FAFU_LNG` / `FAFU_LAT`。

## 首次登录

```bash
python3 src/login_once.py            # 交互式：收到短信后输入验证码
python3 src/login_once.py 123456     # 也可直接把验证码作为参数传入
```

第二种写法传的是**本轮刚下发的那条**验证码，不能复用旧短信里的码。

成功后会打印 `✅ 登录成功！refresh_token 已保存`，并生成 `state.json`。只要守护进程（或等价的定时任务）**持续在跑**，就再也不需要短信——仅当连续 30 天以上未运行才需重跑（见「会话续期与状态文件」）。

> 若控制台把 ✅/⚠️ 显示成 `?`，先 `set PYTHONUTF8=1`——不设也不会崩，字符会降级成 `?`，中文本身是正常的。

## 日常运行

```bash
python3 src/checkin.py status     # 状态：token / 退避余量 / 接口连通性（会发一次真实查询）
python3 src/checkin.py refresh    # 刷新会话
python3 src/checkin.py once       # 立即检查并签到（幂等）
```

`once` 只跑一轮「查询 → 判定 → 需要时提交」，**不重试、也不等待窗口**：判定为已签到或请假时不会重复提交；失败时以**退出码 1** 结束，方便 cron 与脚本判断。

## 自动运行

推荐**守护进程**——调度内置，你不必自己写 cron：

```bash
python3 src/run.py start     # 启动
python3 src/run.py status    # 状态（守护进程 + 会话）
python3 src/run.py log -f    # 实时日志（Ctrl+C 退出查看，不影响签到）
python3 src/run.py stop      # 停止
```

内置调度时间表（窗口按**北京时间 UTC+8** 判定，与系统时区无关）：

| 时段 | 行为 |
|---|---|
| 07:00–21:25 | 保活：每 ~20 分钟查询一次，确认 token 仍有效（有效时只是查询，不触发刷新） |
| 21:30–22:30 | **主签到窗口**：每分钟检查，未签即提交 |
| 22:30–23:00 | **补签兜底**：继续重试 |
| 其余时间 | 低频检查，最长 1 小时一次 |

> ⚠️ **守护进程跑在你自己的机器上——关机、休眠、断网期间它不会签到，也没有开机补签。**
> 补签截止由**服务端**的 `supplementEndTime` 决定，守护进程本地最晚只会试到 23:00。
> 也就是说，**当天 23:00 之后才开机，这一次就补不回来了**。要长期稳定签到，请让机器在
> 晚查寝时段保持开机联网（NAS / 服务器 / 常开的小主机是更合适的载体）。

> **Windows 上 `start` 会弹出一个窗口，日志实时打在里面；关掉那个窗口就是停止签到。**
> 窗口内的控制台 QuickEdit 已被主动关闭——否则在里面拖选文本会让写日志的守护进程阻塞在
> `WriteConsole` 上，一卡可能就是几小时，正好错过签到窗口。

三端**就这一个入口**（Windows / macOS / Linux 同一份代码）。POSIX 下 `src/run.py` 已设可执行位，也可以直接 `./src/run.py start`。cron / systemd / 任务计划 / Termux 见 [docs/deploy.md](docs/deploy.md)。

> ⚠️ **不要同时跑两种调度方式**（如 `run.py start` 加 cron）：并发刷新会用同一个旧
> `refresh_token` 打两次刷新链，可能互相作废并触发退避。

## 会话续期与状态文件

`state.json` 保存全部会话态：`refresh_token`、`we_link_token`、`fafu_token`、`last_refresh_ts`、`next_refresh_after`、`refresh_fail`。其中 **`refresh_token` 是唯一的重登凭证**，丢了就得重新短信登录。

- **滑动续期**：每次刷新重置 30 天。持续运行永不过期；仅当**连续 30 天以上未运行**才需重跑 `src/login_once.py`。
- **刷新失败会退避**：阶梯为 1 → 5 → 30 → 120 分钟，成功后进入 30 分钟冷却。退避期内不再重打刷新链，以免把端点打到限流而错过签到窗口。`src/checkin.py status` 会显示当前退避余量。
- **写入是原子的**：加锁 + 增量合并 + 原子落盘，多进程并发写不会互相覆盖。但请注意这只保护**文件本身**——并发**刷新会话**仍会互相作废，所以不要同时跑两种调度方式（见「自动运行」）。

> 要备份只需 `state.json`（连同 `config.ini`）——**两者都含真实凭据，切勿提交到版本库**。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `❌ 缺少配置项：device_id` | `config.ini` 未填或字段为空 | 按「配置」一节填写，或设 `FAFU_DEVICE_ID` |
| `❌ 换取失败(200): 同一账号仅允许在同一台手机登录…` | `device_id` 与账号绑定的设备不一致 | 核对 `device_id` 是否与该账号绑定设备完全一致（含大小写） |
| `❌ 无法获取 token：请检查 refresh_token 是否有效` | 会话态失效或缺失——最常见的原因是超过 30 天未运行 | 重跑 `python3 src/login_once.py`，重新收一次短信 |
| `⏳ 刷新退避中（已连续失败 N 次）` | 刷新失败进入退避，本轮并未尝试刷新 | 等提示的时长即可；若反复出现，说明 `refresh_token` 已失效或端点变更 |
| `❌ 未安装 ddddocr，无法识别图形验证码` | 风控切到了图形码路径 | `pip install ddddocr`，或改用手机 App 扫码登录 |
| `❌ 未能从登录页提取 pwdEncryptSalt` | 登录页结构变更，或 IP 被拦 | 换网络重试；确认是否被校园网出口风控 |
| `❌ 无法切换到滑块验证` / `❌ 滑块验证失败` | 风控状态变化，或滑块识别不准 | 重试；多次失败后改用手机 App 登录刷新会话 |
| `⚠️ 无法确认 PID xxx 就是本项目的守护进程` | 缺 `psutil`，身份判据降级 | `pip install psutil`。这是**有意拒绝执行**，以免 PID 复用误杀无关进程 |
| `❌ 守护进程启动后立即退出（退出码 N）` | 配置或依赖有问题 | 看日志末尾（`python3 src/run.py log`）：最常见是缺配置项或没装 `cryptography` |
| 日志出现 `签到状态不是已知取值` | 服务端新增了 `signState` 取值 | 项目会**保守提交**（宁可多签不可漏签），但这属于未知情况，建议[提 issue](https://github.com/Bonger34/fafu-checkin-http/issues) 并附 `src/run.py log` 的输出 |
| 窗口内一直没签上 | 触发 WAF 端点限流（冷却 15–20 分钟） | 检查是否有多个实例在跑——请求间隔由代码强制 ≥2s，但并行运行多份会绕过它 |

`signState` 语义（无官方文档，由实测比对确证）：`0` 未签、`1` 已签到、`2` 请假、缺失为未知。**只有 `1` 与 `2` 算当天已了结**，其余取值一律尝试提交。

## 安全

- `config.ini` 与 `state.json` 含真实凭据，已加入 `.gitignore`（POSIX 下权限 600，Windows 依赖 NTFS 默认权限）——**切勿提交到版本库**。
- 日志中的 token / 学号 / 设备 ID 已自动脱敏。
- ⚠️ **校方接口为明文 HTTP**（`http://stuhtapi.fafu.edu.cn`、`http://auth.fafu.edu.cn`）。密码虽经 AES 加密上传，但加密密钥 `pwdEncryptSalt` 本身就是通过明文 HTTP 下发的——中间人可替换登录页并解出密码；`deviceId`、token、学籍信息同样明文传输。这是校方服务器的限制，本项目无法修复，**请勿在公共 Wi-Fi、代理或任何不可信网络下运行**。

## 测试

```bash
python3 -m unittest discover -s tests -v    # 离线运行，不发任何真实请求
python3 src/e2e_verify.py                   # 端到端验证：会发真实请求，消耗真实会话
```

文件职责表、不变量、模块边界与完整测试覆盖清单见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 许可

[DISCLAIMER.md](DISCLAIMER.md) · [LICENSE](LICENSE)

