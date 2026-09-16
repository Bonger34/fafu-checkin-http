# -*- coding: utf-8 -*-
"""
首次登录（仅需一次短信）：CAS 登录 + MFA → 保存 refresh_token 到 state.json。
之后日常运行 rootless_checkin.py 即可，无需再收短信。

用法：
    python3 login_once.py            # 交互式：登录后提示输入短信验证码
    python3 login_once.py 123456     # 直接传入验证码
"""
import sys
from fafu_config import CFG, mask, setup_console
from fafu_login import login, submit_mfa, oauth_to_welink

setup_console()          # 中文 Windows 控制台默认 GBK，不处理会在打印 ✅ 时崩栈

def main():
    CFG.require("username", "password", "device_id", "tenant_id")
    print("[1] CAS 登录（滑块 + 密码）...")
    svc, cookie = login()
    if not svc:
        raise SystemExit("❌ 登录未到达 MFA（账号/密码/滑块可能有问题）")
    print("    → 已到达 MFA 页")
    code = sys.argv[1] if len(sys.argv) > 1 else input("[2] 请输入短信验证码: ").strip()
    print("[3] 提交 MFA ...")
    oauth, info = submit_mfa(svc, cookie, code)
    if not oauth: raise SystemExit(f"❌ MFA 失败: {str(info)[:150]}")
    print("    → OAuth code:", mask(oauth, 10))
    print("[4] 换取 WeLink token ...")
    wt, rt, st, body = oauth_to_welink(oauth)
    if not wt: raise SystemExit(f"❌ 换取失败({st}): {str(body)[:150]}")
    CFG.save_state(we_link_token=wt, refresh_token=rt)
    print("✅ 登录成功！refresh_token 已保存（持续运行可无限续期，无需再收短信）")
    print("   后续运行： python3 rootless_checkin.py refresh")

if __name__ == "__main__":
    main()
