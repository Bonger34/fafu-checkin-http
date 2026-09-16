# 部署指南

> 覆盖 Linux / NAS / 服务器 / Windows / Termux。下文用 `/path/to/fafu-checkin-http` 代表项目实际路径。

## 一、Linux / NAS / 服务器

### 方式 1：守护进程（推荐，跨平台）

```bash
cd /path/to/fafu-checkin-http
python3 run.py start      # 启动（后台）
python3 run.py status     # 状态（守护进程 + 会话）
python3 run.py log -f     # 实时日志（Ctrl+C 退出查看，不影响签到）
python3 run.py stop       # 停止
```
自带调度：白天保活、21:30 签到、失败补签，无需配置 cron。

三端同一份 `run.py`（`run.sh` / `run.bat` 已删除）。`start` 后 POSIX 静默后台运行，
Windows 会开一个可见窗口且**关掉窗口即停止**；平台差异详见文末「平台差异对照」。

> `run.py` 自己管 PID 文件与重复启动检查，**不要**再手工 `nohup ... &` 加
> `echo $! > daemon.pid`：那会把 daemon 写的 JSON 格式 PID 文件覆盖成纯数字，
> 身份校验随之降级，`run.py stop` 会因为「无法确认这个 PID 是不是本项目的
> 进程」而拒绝执行（这是有意的，避免 PID 复用误杀）。
>
> 确实想手工启动的话，日志交给 daemon 自己写即可，别重定向到 `daemon.log`：
> ```bash
> nohup python3 daemon.py > /dev/null 2>&1 &
> ```

### 方式 2：cron

```cron
# 保活：07:00-21:25 每 20 分钟（续 token）
*/20 7-21 * * *  cd /path/to/fafu-checkin-http && python3 rootless_checkin.py refresh >> cron.log 2>&1
# 主签到：21:30-22:30 每分钟（幂等，已签自动跳过）
30-59/1 21 * * * cd /path/to/fafu-checkin-http && python3 rootless_checkin.py once >> cron.log 2>&1
* 22 * * *       cd /path/to/fafu-checkin-http && python3 rootless_checkin.py once >> cron.log 2>&1
```
> 签到窗口按**北京时间（UTC+8）**判断。服务器时区不同请换算，或设 `TZ=Asia/Shanghai`。
>
> ⚠️ **不要与 `daemon.py` 或其他调度方式同时运行**：并发刷新会用同一个旧 `refresh_token` 打两次刷新链，可能互相作废并触发失败退避。选一种方式即可。

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
set PYTHONUTF8=1              :: 让 ✅ 等 emoji 正常显示（不设也不崩，会降级成 ?）
python login_once.py
```
按提示输入短信验证码。成功后 `state.json` 生成，之后**持续运行即无需再收短信**
（refresh_token 为滑动续期，每次刷新重置 30 天；仅当连续 30 天未运行才需重登）。

### 4. 自动运行（三选一）

**A. 守护进程**
```cmd
python run.py start
python run.py status
python run.py log -f
python run.py stop
```
> `start` 会弹出一个窗口实时显示日志，**关掉窗口即停止签到**。

**B. 任务计划程序（推荐）**
1. 打开「任务计划程序」→「创建任务」
2. **常规**：勾选「不管用户是否登录都要运行」「使用最高权限运行」
3. **触发器**：新建 →「登录时」+「每天 07:00」
4. **操作**：程序 `python`，参数 `daemon.py`，起始于 `C:\path\to\fafu-checkin-http`
   （任务计划本身就是"守护者"，与 systemd 同理，不需要 `run.py`；但也**别再叠加**
   `run.py start`——两者同时跑会并发刷新，见方式 2 的告警）
5. **设置**：勾选「如果任务失败，按以下频率重新启动」→ 1 分钟

> 也可只让任务在 21:25 启动一次，`daemon.py` 会自己等到 21:30 签到。

**C. 启动文件夹**
`Win+R` → `shell:startup` → 新建 `fafu.bat`：
```bat
@echo off
cd /d C:\path\to\fafu-checkin-http
python run.py start
```
> 这里走 `run.py` 而不是直接 `python daemon.py`：启动文件夹不是"守护者"，没有它就没有
> 重复启动检查，之后也没法 `run.py status` / `stop`。`run.py start` 会另开一个新窗口，
> 这个 bat 窗口随即关闭。

### 5. Windows 常见问题

| 问题 | 解决 |
|---|---|
| 控制台里 ✅/⚠️ 显示成 `?` | 运行前 `set PYTHONUTF8=1`，或 `chcp 65001`。不设也不会崩——各入口的 `setup_console()` 会把无法编码的字符降级成 `?`，中文本身在 GBK 下是正常的 |
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
| 启动 | `python3 run.py start` | `python run.py start` |
| 启动后的样子 | 静默后台；`run.py log -f` 看日志 | 弹出窗口实时显示日志，关窗口即停止 |
| 定时 | cron / systemd | 任务计划程序 |
| 文件权限 | `chmod 600` | 依赖 NTFS（默认仅当前用户可读） |

> 启动器只有 `run.py` 一份，平台差异集中在两处：拉起子进程
> （`CREATE_NEW_CONSOLE` / `start_new_session`）与停止进程
> （psutil，缺失时退回 `taskkill` / `killpg`）。
