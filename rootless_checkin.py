# -*- coding: utf-8 -*-
"""
免 root 全自动签到：纯 HTTP 登录链 + 仓库同款打卡逻辑。
凭据从 config.ini 读取；会话态自动存于 state.json。

用法：
    python3 rootless_checkin.py status     # 查看 token / 任务状态
    python3 rootless_checkin.py refresh    # 用 refresh_token 刷新会话
    python3 rootless_checkin.py once       # 立即检查并签到（幂等）
"""
import sys, time
from fafu_config import CFG, fmt_hm, mask
from fafu_lib import api, ensure_token, query_task, refresh_wait, _has

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
    print("账号:", CFG.username, "| 设备:", CFG.device_id)
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
    tok = get_fafu_token()
    r0 = query_task(tok, rows=3)
    if not r0: raise SystemExit("❌ 查询任务失败（token 或网络问题）")
    rid, name = r0.get("id"), r0.get("name", "签到")
    bt, et = r0.get("beginTime"), r0.get("endTime")
    dl = r0.get("supplementEndTime") or et                  # 补签截止，缺失时用 endTime 兜底
    if rid is None or bt is None or et is None:
        raise SystemExit(f"❌ 任务记录字段异常：{list(r0)[:6]}")
    ss = (r0.get("signInStudent") or {}).get("signState")
    now = int(time.time()) * 1000
    print(f"任务: {name} id={rid} signState={ss}")
    print(f"窗口: {fmt_hm(bt)}~{fmt_hm(et)} 补签至 {fmt_hm(dl)}")
    if ss is not None and ss != 0:
        print(f"→ 已签到（状态{ss}），无需操作"); return
    if ss is None:
        print("→ 签到状态未知(signInStudent 缺失)，保守尝试签到")
    if not (bt <= now <= dl):
        print("→ 不在签到时段"); return
    st2, r2 = api(f"sign_in/{rid}/student/sign", "lng=119.243462&lat=26.088417", tok)
    print("签到结果:", "✅ 成功" if _has(r2, "timestamp") else f"❌ {st2} {r2[:120]}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cmds = {"status": cmd_status, "refresh": cmd_refresh, "once": cmd_once}
    if cmd not in cmds:
        raise SystemExit(f"未知命令 '{cmd}'，可用：{' | '.join(cmds)}")
    cmds[cmd]()
