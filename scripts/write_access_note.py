import json
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    record = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())
    origin = dotenv_values(ROOT / "frontend/.env.deploy")["PUBLIC_ORIGIN"]
    output = ROOT / ".intermediate/private/忆谈登录资料.txt"
    output.write_text(
        "忆谈登录资料\n\n"
        f"产品地址：{origin}\n"
        f"登录地址：{origin}/login\n"
        f"管理面板：{origin}/admin\n\n"
        f"账号：{record['username']}\n"
        f"密码：{record['password']}\n\n"
        "请只复制密码本身，不包含前面的“密码：”。\n"
        "第一次看到腾讯云“页面访问提示”时，等待倒计时结束，点击“确定访问”。\n"
        "登录后可以阅读家庭完整回忆录；在“管理面板 → 账户邀请”创建一次性邀请码。\n"
        "每个邀请码仅供一个新家庭注册，请通过私人方式交给对应的使用者。\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(json.dumps({"access_note_saved": True, "permissions": "0600"}))


if __name__ == "__main__":
    main()
