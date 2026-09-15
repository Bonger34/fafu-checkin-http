# 部署指南

> 覆盖 Linux / NAS / 服务器 / Windows / Termux。下文用 `/path/to/fafu-checkin-http` 代表项目实际路径。

## 一、Linux / NAS / 服务器

### 方式 1：守护进程（推荐，跨平台）

```bash
cd /path/to/fafu-checkin-http
nohup python3 daemon.py >> daemon.log 2>&1 &
echo $! > daemon.pid          # 记录 PID；停止：kill $(cat daemon.pid)
```
自带调度：白天保活、21:30 签到、失败补签，无需配置 cron。
也可用管理脚本：`./run.sh start | status | log | stop`。

### 方式 2：cron

```cron
# 保活：07:00-21:25 每 20 分钟（续 token）
*/20 7-21 * * *  cd /path/to/fafu-checkin-http && python3 rootless_checkin.py refresh >> cron.log 2>&1
# 主签到：21:30-22:30 每分钟（幂等，已签自动跳过）
30-59/1 21 * * * cd /path/to/fafu-checkin-http && python3 rootless_checkin.py once >> cron.log 2>&1
* 22 * * *       cd /path/to/fafu-checkin-http && python3 rootless_checkin.py once >> cron.log 2>&1
```
> 签到窗口按**北京时间（UTC+8）**判断。服务器时区不同请换算，或设 `TZ=Asia/Shanghai`。

### 方式 3：systemd（最稳）

`/etc/systemd/system/fafu-checkin.service`：
```ini
[Unit]
Description=数字FAFU 自动签到
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/fafu-checkin-http
ExecStart=/usr/bin/python3 daemon.py
Restart=always
RestartSec=60
User=youruser
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now fafu-checkin
journalctl -u fafu-checkin -f
```

## 二、Windows

### 1. 安装 Python
到 https://www.python.org/downloads/ 下载 3.9+，安装时**务必勾选** `Add Python to PATH`。
验证：`python --version`

### 2. 安装依赖
```cmd
cd C:\path\to\fafu-checkin-http
pip install -r requirements.txt
```
（`pip` 不可用时用 `python -m pip install -r requirements.txt`）

> `opencv-python` / `numpy` 仅**首次登录**（滑块识别）需要；`cryptography` 用于加密与刷新。

### 3. 配置与首次登录
```cmd
copy config.example.ini config.ini
notepad config.ini            :: 填 username / password / device_id
set PYTHONUTF8=1              :: 避免中文乱码
python login_once.py
```
按提示输入短信验证码。成功后 `state.json` 生成，之后**持续运行即无需再收短信**
（refresh_token 为滑动续期，每次刷新重置 30 天；仅当连续 30 天未运行才需重登）。

### 4. 自动运行（三选一）

**A. 守护进程**
```cmd
run.bat start | stop | status | log
```

**B. 任务计划程序（推荐）**
1. 打开「任务计划程序」→「创建任务」
2. **常规**：勾选「不管用户是否登录都要运行」「使用最高权限运行」
3. **触发器**：新建 →「登录时」+「每天 07:00」
4. **操作**：程序 `python`，参数 `daemon.py`，起始于 `C:\path\to\fafu-checkin-http`
5. **设置**：勾选「如果任务失败，按以下频率重新启动」→ 1 分钟

> 也可只让任务在 21:25 启动一次，`daemon.py` 会自己等到 21:30 签到。

**C. 启动文件夹**
`Win+R` → `shell:startup` → 新建 `fafu.bat`：
```bat
@echo off
cd /d C:\path\to\fafu-checkin-http
set PYTHONUTF8=1
python daemon.py
```

### 5. Windows 常见问题

| 问题 | 解决 |
|---|---|
| 控制台中文乱码 | 运行前 `set PYTHONUTF8=1`，或 `chcp 65001` |
| `pip` 不是命令 | 用 `python -m pip install ...` |
| `ModuleNotFoundError: cv2` | 装 `opencv-python` |
| 时区/时间不对 | 窗口按北京时间判断（代码固定 UTC+8）；签名依赖时间戳，建议同步 NTP |
| 防火墙拦截 | 允许 Python 出站（仅 443/80） |

## 三、安卓 Termux

```bash
pkg install python cronie
pip install -r requirements.txt
termux-setup-storage
# 同「cron」方式配置 crontab（路径换成 ~/fafu-checkin-http）
```
> Termux 需保持后台不被杀（可在通知栏锁定），否则调度会中断。

## 四、平台差异对照

| 项 | Linux | Windows |
|---|---|---|
| 启动脚本 | `run.sh` | `run.bat` |
| 定时 | cron / systemd | 任务计划程序 |
| 后台运行 | `nohup ... &` | `start /min` 或任务计划 |
| 文件权限 | `chmod 600` | 依赖 NTFS（默认仅当前用户可读） |

> 代码已做跨平台处理（路径用 `os.path.join`，无 POSIX 专属调用）。
