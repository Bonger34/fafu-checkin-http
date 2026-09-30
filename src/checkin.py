# -*- coding: utf-8 -*-
"""
日常签到入口（免 root）：纯 HTTP 登录链 + 仓库同款打卡逻辑。
凭据从 config.ini 读取；会话态自动存于 state.json。

用法：
    python3 checkin.py status     # 查看 token / 任务状态
    python3 checkin.py refresh    # 用 refresh_token 刷新会话
    python3 checkin.py once       # 立即检查并签到（幂等）
"""
import sys
from fafu_config import CFG, fmt_hm, mask, setup_console
from fafu_lib import api, ensure_token, refresh_wait, sign_once, SignStatus, _has

setup_console()          # 中文 Windows 控制台默认 GBK，不处理会在打印 ✅ 时崩栈

def get_fafu_token():
    """取可用 token；失效则用 refresh_token 刷新。

    处于失败退避期时给出准确提示，而不是笼统地说 refresh_token 无效
    （退避中并未尝试刷新，token 未必真的失效）。
    """
    tok = ensure_token()
    if tok:
        return tok
    wait = int(refresh_wait())
    if wait > 0:
        raise SystemExit(f"⏳ 刷新退避中（已连续失败 {CFG.get_state('refresh_fail', 0)} 次），"
                         f"约 {wait // 60} 分 {wait % 60} 秒后自动重试")
    raise SystemExit("❌ 无法获取 token：请检查 refresh_token 是否有效"
                     "（过期需重新运行 login_once.py）")

def cmd_status():
    tok = CFG.fafu_token
    print("账号:", mask(CFG.username, 3), "| 设备:", mask(CFG.device_id, 6))
    print("WeLink token:", mask(CFG.we_link_token, 8) if CFG.we_link_token else "(无)")
    print("refresh_token:", mask(CFG.refresh_token, 12) if CFG.refresh_token else "(无)")
    print("打卡 token:", mask(tok, 10) if tok else "(无)")
    wait = int(refresh_wait())
    if wait > 0:
        print(f"刷新退避: 约 {wait // 60} 分 {wait % 60} 秒后允许重试"
              f"（已连续失败 {CFG.get_state('refresh_fail', 0)} 次）")
    if tok:
        st, b = api("sign_in/student/my/page", "rows=1&pageNum=1", tok)
        print("接口测试:", "✅ 有效" if _has(b, "records") else f"⚠️ {st} {b[:80]}")

def cmd_refresh():
    tok = get_fafu_token()
    print("✅ 会话有效，打卡 token:", mask(tok, 10))

def cmd_once():
    """立即检查并签到（幂等）。

    判定与提交都在 fafu_lib.sign_once 后面；这里只负责把结果打成
    人类可读的三行：任务、窗口、结论。
    """
    tok = get_fafu_token()
    out = sign_once(tok)
    if out.task:
        t = out.task
        # endTime 可能缺失（那时是凭补签截止在签），别拿 None 去格式化
        end = fmt_hm(t.end_ms) if t.end_ms else "--:--"
        print(f"任务: {t.name} id={t.id} signState={t.sign_state}")
        print(f"窗口: {fmt_hm(t.begin_ms)}~{end} 补签至 {fmt_hm(t.deadline_ms)}")
    print("→", out.detail)
    # 失败要让 cron / 脚本能从退出码看出来——只打 stdout 会静默「成功」
    if out.status in (SignStatus.QUERY_FAILED, SignStatus.BAD_RECORD, SignStatus.SUBMIT_FAILED):
        raise SystemExit(1)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cmds = {"status": cmd_status, "refresh": cmd_refresh, "once": cmd_once}
    if cmd not in cmds:
        raise SystemExit(f"未知命令 '{cmd}'，可用：{' | '.join(cmds)}")
    cmds[cmd]()
