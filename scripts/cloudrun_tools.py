import json
import os
import subprocess
from pathlib import Path

from tencentcloud.common.common_client import CommonClient
from tencentcloud.common.credential import Credential
from tencentcloud.common.profile.client_profile import ClientProfile

ROOT = Path(__file__).resolve().parents[1]
ENV_ID = "xhs-titile-d0g1g5e3481a57b71"
SERVICES = {"yitan-api", "yitan-web"}
CLI = ROOT / ".intermediate/deploy-tools/node_modules/.bin/tcb"


def cloudrun_client():
    result = subprocess.run([str(CLI), "secrets", "get", "--json"], capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp")))
    if result.returncode:
        raise RuntimeError("CloudBase 命令行登录无效")
    credential = json.loads(result.stdout)["data"]
    profile = ClientProfile()
    profile.httpProfile.reqTimeout = 60
    profile.httpProfile.endpoint = "tcbr.tencentcloudapi.com"
    client = CommonClient("tcbr", "2022-02-17", Credential(credential["secretId"], credential["secretKey"], credential["token"]), "ap-shanghai", profile=profile)
    return client


def request(client, action: str, service: str):
    if service not in SERVICES:
        raise ValueError("仅允许操作忆谈专用服务")
    return client.call_json(action, {"EnvId": ENV_ID, "ServerName": service})["Response"]


def service_detail(client, service: str):
    return request(client, "DescribeCloudRunServerDetail", service)
