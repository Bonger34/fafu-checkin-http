# -*- coding: utf-8 -*-
"""端到端衔接验证：refresh_token → WeLink token → authCode → FAFU token → 打卡查询。"""
import json
from fafu_config import CFG, mask
from fafu_lib import api, refresh_we_link, fetch_authcode, exchange_fafu_token

def main():
    CFG.require("device_id", "tenant_id")
    log = lambda n, m: print(f"[{n}] {m}")
    rt = CFG.refresh_token
    if not rt: raise SystemExit("❌ 无 refresh_token，请先运行 login_once.py")
    log(1, "用 refresh_token 刷新 WeLink token ...")
    wt, rt2, st, body = refresh_we_link(rt)
    assert wt, f"刷新失败({st}): {body[:150]}"
    log(2, f"WeLink token: {mask(wt, 8)}")
    log(3, "换 authCode ...")
    ac, st, _ = fetch_authcode(wt)
    assert ac, f"authCode 失败({st})"
    log(4, f"authCode: {mask(ac, 10)}")
    log(5, "换 FAFU token ...")
    tok, st, msg = exchange_fafu_token(ac)
    assert tok, f"FAFU 登录失败({st}): {str(msg)[:150]}"
    log(6, f"FAFU token: {mask(tok, 10)}")
    log(7, "查询任务（仓库同款 api）...")
    st, resp = api("sign_in/student/my/page", "rows=1&pageNum=1", tok)
    assert '"records"' in resp, f"查询失败({st}): {resp[:120]}"
    recs = json.loads(resp).get("records") or []
    if not recs:
        raise SystemExit("❌ 查询成功但无任务记录")
    r0 = recs[0]
    log(8, f"{r0.get('name','?')} id={r0.get('id')} signState={(r0.get('signInStudent') or {}).get('signState')}")
    CFG.save_state(we_link_token=wt, refresh_token=rt2, fafu_token=tok)
    print("\n✅ 全链路衔接成功（全程无需短信）")

if __name__ == "__main__":
    main()
