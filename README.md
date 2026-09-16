# fafu-checkin-http · 数字FAFU 自动签到（免 root）

> ## ⚠️ 免责声明
>
> - 本项目**仅供个人学习与安全研究**，用于理解 OAuth/CAS 登录流程与接口签名机制。
> - **严禁**用于代签、替他人签到、伪造考勤等任何违反校规或学术诚信的用途。
> - 使用本项目的**全部风险与后果由使用者自行承担**；作者不对账号封禁、纪律处分、数据丢失等任何直接或间接损失负责。
> - 本项目**与福建农林大学及华为 WeLink 无关**，非官方工具。
> - 接口与签名**可能随时变更**，项目不保证持续可用；若校方明确禁止，请立即停止使用并删除本项目。
> - 下载、克隆或运行本项目，即表示你已阅读并同意上述条款。详见 [DISCLAIMER.md](DISCLAIMER.md)。

> 纯 HTTP 复现 WeLink/CAS 登录链 + 打卡接口，**无需 root、无需安装 App**。
> 与 [Bonger34/fafu-checkin](https://github.com/Bonger34/fafu-checkin)（Magisk/KernelSU 模块，读 App LevelDB 取 token）的区别：
> 本项目从零复现登录流程，可在任意环境运行，但需要账号密码与设备锁绑定的 deviceId。

## 文档导航

| 我想…… | 看 |
|---|---|
| 装好、跑起来 | **本 README** |
| 部署到 Linux / Windows / NAS / Termux | [docs/deploy.md](docs/deploy.md) |
| 二次开发 / 接手维护 | [DEVELOPMENT.md](DEVELOPMENT.md) |
| 搞懂登录链、设备锁、限流 | [docs/reverse-notes.md](docs/reverse-notes.md) |
| 深入 WeLink 多设备/踢下线机制 | [docs/multi-device-login-analysis.md](docs/multi-device-login-analysis.md) |
| 查看使用条款 | [DISCLAIMER.md](DISCLAIMER.md) · [LICENSE](LICENSE) |

> 术语：「**打卡**」=「**签到**」= 调用 `sign_in/{id}/student/sign`；「**保活**」= 周期性查询以维持 token 有效。

## 环境要求

- Python **3.9+**
- ```bash
  pip install -r requirements.txt
  ```

  | 库 | 用途 | 日常签到是否必需 |
  |---|---|---|
  | `cryptography` | AES 加密（登录）、RSA-OAEP（租户 ID） | **始终需要**（`fafu_lib` 顶层导入） |
  | `opencv-python` | 滑块缺口定位 | 仅首次登录需要 |
  | `numpy` | 滑块图像处理 | 仅首次登录需要 |
  | `ddddocr` | 图形验证码 OCR（可选） | 仅当服务端要求图形码时需要 |

  > 已登录且 token 有效时，日常签到只需 `cryptography`——即便不触发刷新也要装上，
  > 因为 `fafu_lib` 在模块顶层就 import 了它，缺了会直接 `ImportError`。
  > CAS 会**按风控**在「滑块」与「图形码」之间切换；只有被要求图形码时才用得上 `ddddocr`，
  > 未安装时脚本会明确提示改用手机 App 扫码登录，而不是抛堆栈。

## 快速开始

```bash
# 1. 配置
cp config.example.ini config.ini
chmod 600 config.ini
vim config.ini          # 填 username / password / device_id（tenant_id 已预置）
# 或用环境变量：export FAFU_USERNAME=... FAFU_PASSWORD=... FAFU_DEVICE_ID=...

# 2. 首次登录（收到短信后输入验证码）
python3 login_once.py

# 3. 之后日常（无需再收短信）
python3 rootless_checkin.py status     # 查看状态
python3 rootless_checkin.py refresh    # 刷新会话
python3 rootless_checkin.py once       # 立即检查签到
```

## 自动运行

推荐**守护进程**（自带调度：白天保活 + 21:30 签到 + 补签兜底）：

```bash
python run.py start     # 启动
python run.py status    # 状态（守护 + 会话）
python run.py log       # 最近日志（加 -f 持续跟随）
python run.py stop      # 停止
```

三端**就这一个入口**（Windows / macOS / Linux 同一份代码）。POSIX 下已设可执行位，
也可以直接 `./run.py start`。

> 以前的 `run.bat` / `run.sh` 已删除：两份脚本会各自演化（停止方式、日志行数、
> status 内容都曾不一致），转发壳省下的那几个字符不值得再养一套行为。

> **Windows 上 `start` 会弹出一个窗口，日志实时打在里面；关掉那个窗口就是停止签到。**
> 在窗口里拖选文本不会卡住后台：控制台 QuickEdit 已被主动关闭。不关的话，
> 一旦进入标记模式，写日志的进程会阻塞在 `WriteConsole` 上——daemon 一卡
> 可能就是几小时，正好错过签到窗口。

> 子命令要**分开写**：`./run.py start | status` 会被 shell 当成管道，而不是「任选其一」。

其他方式（cron / systemd / 任务计划 / Termux）见 [docs/deploy.md](docs/deploy.md)。

## 会话续期

首次登录产出 **refresh_token**（滑动续期，每次刷新重置 30 天）。
**只要持续运行就不会过期**；仅当**连续 30 天以上未运行**才需重新 `login_once.py`。
机制细节见 [docs/reverse-notes.md](docs/reverse-notes.md)。

## 安全

- `config.ini` 与 `state.json` 含真实凭据，已加入 `.gitignore` 且权限 600——**切勿提交到版本库**。
- 日志中的 token / 学号 / 设备 ID 已自动脱敏。
- ⚠️ **校方接口为明文 HTTP**（`http://stuhtapi.fafu.edu.cn`、`http://auth.fafu.edu.cn`）。密码虽经 AES 加密上传，但加密密钥 `pwdEncryptSalt` 本身就是通过明文 HTTP 下发的——中间人可替换登录页并解出密码；`deviceId`、token、学籍信息同样明文传输。这是校方服务器的限制，本项目无法修复，**请勿在公共 Wi-Fi、代理或任何不可信网络下运行**。

## 测试

```bash
python3 -m unittest discover -s tests -v    # 离线运行，不发任何真实请求
```

覆盖刷新退避、state 并发写、响应判定、窗口边界与 PID 文件生命周期。

`tests/test_flow.py` 另做 6 条完整业务链路的离线演练：只把最底层的网络出口换成假实现，签名、JSON 解析、会话保障、刷新退避、签到决策全部走真实代码，可覆盖「token 失效 → 自动刷新 → 签到成功」「刷新失败 → 退避不空转」等路径。

`tests/test_login_flow.py` 覆盖 CAS 登录链的三个关键前置步骤（`checkNeedCaptcha` / `toSliderCaptcha` / 二进制取图）、滑块轨迹的累计语义，以及图形码 OCR 的长度过滤。未装 numpy/opencv 时该组自动跳过。
