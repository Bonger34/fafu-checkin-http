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
import os, json, random, configparser, datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_DIR, "config.ini")
_STATE_PATH = os.path.join(_DIR, "state.json")

# 客户端公共常量（非机密，所有用户一致）
CLIENT_SECRET = "AtPs2O1xEnhwkKDV"     # H5 前端硬编码签名密钥
SCHOOL_NO = "fafu"
API_BASE = "http://stuhtapi.fafu.edu.cn/health-api"
MAG_BASE = "https://api.welink.huaweicloud.com/mcloud/mag"
CAS_BASE = "http://auth.fafu.edu.cn/authserver"
USER_AGENT = "HWorks.Android/7.49.17"

# 打卡窗口以北京时间为准（中国全年 UTC+8）
TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")

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
        self._state = {}
        if os.path.exists(_STATE_PATH):
            try:
                self._state = json.load(open(_STATE_PATH, encoding="utf-8"))
            except Exception:
                self._state = {}

    # ---- 会话态（自动持久化到 state.json，避免脚本间手工传递）----
    # 已知的会话态键；未设置的返回空串，拼错的名字则抛 AttributeError（避免掩盖 bug）
    _STATE_KEYS = frozenset({"we_link_token", "refresh_token", "fafu_token", "last_refresh_ts"})

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

    def save_state(self, **kw):
        self._state.update(kw)
        # 原子写入：临时文件名含 PID+随机数，避免多进程并发互相覆盖
        tmp = f"{_STATE_PATH}.{os.getpid()}.{random.randrange(1<<30)}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _STATE_PATH)          # 同目录 rename 是原子操作
            try:
                os.chmod(_STATE_PATH, 0o600)
            except Exception:
                pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def require(self, *names):
        """校验必填项，缺失则给出清晰报错（而非 KeyError）"""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise SystemExit(
                "❌ 缺少配置项：%s\n   请在 config.ini 的 [account] 中填写，"
                "或设置对应环境变量。" % "、".join(missing)
            )


CFG = Config()
