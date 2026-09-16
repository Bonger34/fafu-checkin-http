# -*- coding: utf-8 -*-
"""
统一配置加载 —— 所有脚本的凭据/会话态来源。

优先级：环境变量 > config.ini > 默认值
  FAFU_USERNAME / FAFU_PASSWORD / FAFU_DEVICE_ID / FAFU_TENANT_ID

用法：
    from fafu_config import CFG
    CFG.username, CFG.password, CFG.device_id, CFG.tenant_id
    CFG.save_state(we_link_token=..., refresh_token=...)   # 自动落盘
    CFG.we_link_token                                      # 读取上次保存的
"""
import os, sys, json, random, time, configparser, datetime, contextlib

_DIR = os.path.dirname(os.path.abspath(__file__))

def setup_console():
    """让中文 Windows 控制台不再因 emoji 崩栈。

    控制台默认代码页是 GBK，而这些脚本会打印 ✅ / ⚠️ 之类的字符，GBK 编不出来，
    于是 print 直接抛 UnicodeEncodeError——`e2e_verify.py` 必崩、
    `rootless_checkin.py status` 必崩。这里只把不可编码的字符降级成 '?'，
    不改变输出编码，避免控制台出现乱码。

    各入口脚本导入后应立即调用一次。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
_CFG_PATH = os.path.join(_DIR, "config.ini")
_STATE_PATH = os.path.join(_DIR, "state.json")

# ---- state.json 跨进程互斥 ----
_LOCK_TIMEOUT = 5.0     # 获取锁的最长等待（秒）
_LOCK_STALE = 30.0      # 锁文件超过此时长视为残留（持有者已崩溃）

def _unlink(path):
    """删除文件，忽略不存在等错误"""
    try:
        os.remove(path)
    except OSError:
        pass

def _lock_is_stale(path):
    """锁文件是否已过期（持有者被强杀后会留下残留锁）"""
    try:
        return time.time() - os.path.getmtime(path) > _LOCK_STALE
    except OSError:
        return False

@contextlib.contextmanager
def _state_lock():
    """state.json 的「读—改—写」必须跨进程串行。

    用 O_CREAT|O_EXCL 锁文件实现（Windows/Linux 通用，不依赖 fcntl/msvcrt）。
    持有者被强杀会留下残留锁，按 mtime 判过期，避免后续进程永久阻塞。
    """
    path = _STATE_PATH + ".lock"
    deadline = time.time() + _LOCK_TIMEOUT
    fd = None
    while fd is None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_stale(path):
                _unlink(path)                       # 残留锁：持有者已异常退出
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"等待 state 锁超时：{path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        _unlink(path)

# 客户端公共常量（非机密，所有用户一致）
CLIENT_SECRET = "AtPs2O1xEnhwkKDV"     # H5 前端硬编码签名密钥
SCHOOL_NO = "fafu"
API_BASE = "http://stuhtapi.fafu.edu.cn/health-api"
MAG_BASE = "https://api.welink.huaweicloud.com/mcloud/mag"
CAS_BASE = "http://auth.fafu.edu.cn/authserver"
USER_AGENT = "HWorks.Android/7.49.17"

# 打卡窗口以北京时间为准（中国全年 UTC+8）
TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")

# 打卡提交时上报的定位（默认校区坐标）。可在 config.ini 覆盖：
#   [location]
#   lng = 119.243462
#   lat = 26.088417
# 或环境变量 FAFU_LNG / FAFU_LAT。
DEFAULT_LNG = "119.243462"
DEFAULT_LAT = "26.088417"

def fmt_hm(ts_ms):
    """毫秒时间戳 → 北京时间 HH:MM"""
    return datetime.datetime.fromtimestamp(ts_ms / 1000, TZ).strftime("%H:%M")

def mask(s, keep=4):
    """脱敏：仅保留前 keep 位，其余用 * 代替"""
    s = str(s or "")
    return s if len(s) <= keep else s[:keep] + "*" * (len(s) - keep)

def now_cst():
    """当前北京时间"""
    return datetime.datetime.now(TZ)


class Config:
    def __init__(self):
        # interpolation=None：关闭 % 插值，否则密码含 % 会抛 InterpolationSyntaxError
        c = configparser.ConfigParser(interpolation=None)
        c.read(_CFG_PATH, encoding="utf-8")
        g = lambda s, k, env, d="": (os.environ.get(env) or (c.get(s, k, fallback=d) if c.has_section(s) else d)).strip()
        self.username = g("account", "username", "FAFU_USERNAME")
        self.password = g("account", "password", "FAFU_PASSWORD")
        self.device_id = g("account", "device_id", "FAFU_DEVICE_ID")
        self.tenant_id = g("account", "tenant_id", "FAFU_TENANT_ID")
        self.lng = g("location", "lng", "FAFU_LNG", DEFAULT_LNG)
        self.lat = g("location", "lat", "FAFU_LAT", DEFAULT_LAT)
        self._state = self._read_state_file()

    # ---- 会话态（自动持久化到 state.json，避免脚本间手工传递）----
    # 已知的会话态键；未设置的返回空串，拼错的名字则抛 AttributeError（避免掩盖 bug）
    _STATE_KEYS = frozenset({"we_link_token", "refresh_token", "fafu_token",
                             "last_refresh_ts", "next_refresh_after", "refresh_fail"})

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.__dict__:                 # 配置项（username 等）走正常属性
            return self.__dict__[name]
        state = self.__dict__.get("_state", {})
        if name in state:                         # 已保存的会话态
            return state[name]
        if name in self._STATE_KEYS:              # 已知但尚未设置 → 空串
            return ""
        raise AttributeError(                     # 拼写错误 → 明确报错
            f"Config 无此属性 '{name}'；会话态键仅限 {sorted(self._STATE_KEYS)}")

    def get_state(self, key, default=None):
        """读取会话态（公开接口，避免直接访问 _state）"""
        return self._state.get(key, default)

    def _read_state_file(self):
        """读取磁盘上的会话态；文件缺失或损坏时返回空 dict（不抛异常）"""
        try:
            with open(_STATE_PATH, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, **kw):
        """写入会话态：跨进程串行 + 增量合并 + 原子落盘。

        每次都以磁盘最新内容为基准、只应用本次的 kw。daemon 这类长驻进程的
        内存快照可能早已过期，若按内存全量覆盖，就会抹掉其他进程刚写入的键
        —— refresh_token 每次刷新都会轮换，被抹掉即需重新短信登录。
        """
        with _state_lock():
            merged = self._read_state_file()
            merged.update(kw)
            self._state = merged
            # 原子写入：临时文件名含 PID+随机数，避免多进程并发互相覆盖
            tmp = f"{_STATE_PATH}.{os.getpid()}.{random.randrange(1<<30)}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=1)
                os.replace(tmp, _STATE_PATH)      # 同目录 rename 是原子操作
                try:
                    os.chmod(_STATE_PATH, 0o600)
                except OSError:
                    pass
            finally:
                _unlink(tmp)

    def require(self, *names):
        """校验必填项，缺失则给出清晰报错（而非 KeyError）"""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise SystemExit(
                "❌ 缺少配置项：%s\n   请在 config.ini 的 [account] 中填写，"
                "或设置对应环境变量。" % "、".join(missing)
            )


CFG = Config()
