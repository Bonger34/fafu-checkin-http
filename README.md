# fafu-checkin-http · 数字FAFU 自动签到（免 root）

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

> 术语：「**打卡**」=「**签到**」= 调用 `sign_in/{id}/student/sign`；「**保活**」= 周期性查询以维持 token 有效。

## 环境要求

- Python **3.9+**
- ```bash
  pip install -r requirements.txt
  ```

  | 库 | 用途 | 日常签到是否必需 |
  |---|---|---|
  | `cryptography` | AES 加密（登录）、RSA-OAEP（租户 ID） | token 有效时不需；刷新时需要 |
  | `opencv-python` | 滑块验证码识别 | 仅首次登录需要 |
  | `numpy` | 滑块图像处理 | 仅首次登录需要 |

  > 已登录且 token 有效时，纯签到只需 Python 标准库。

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
./run.sh start | status | log | stop        # Linux
run.bat start | status | log | stop         # Windows
```

其他方式（cron / systemd / 任务计划 / Termux）见 [docs/deploy.md](docs/deploy.md)。

## 会话续期

首次登录产出 **refresh_token**（滑动续期，每次刷新重置 30 天）。
**只要持续运行就不会过期**；仅当**连续 30 天以上未运行**才需重新 `login_once.py`。
机制细节见 [docs/reverse-notes.md](docs/reverse-notes.md)。

## 安全

- `config.ini` 与 `state.json` 含真实凭据，已加入 `.gitignore` 且权限 600——**切勿提交到版本库**。
- 日志中的 token / 学号 / 设备 ID 已自动脱敏。
