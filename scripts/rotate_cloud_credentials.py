import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone

import httpx
from dotenv import dotenv_values, set_key
from tencentcloud.common.common_client import CommonClient
from tencentcloud.common.credential import Credential
from tencentcloud.common.profile.client_profile import ClientProfile

from cloudrun_tools import CLI, ENV_ID, ROOT, cloudrun_client, request


PRIVATE = ROOT / ".intermediate/private/credential-rotation.json"
LOCAL_PATHS = [ROOT / "backend/.env", ROOT / "backend/.env.deploy"]
ROTATED = {"CLOUDBASE_API_KEY", "WORKER_TOKEN"}


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cloudbase_client():
    result = subprocess.run(
        [str(CLI), "secrets", "get", "--json"], capture_output=True, text=True,
        env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp")),
    )
    if result.returncode:
        raise RuntimeError("CloudBase 命令行登录无效")
    values = json.loads(result.stdout)["data"]
    profile = ClientProfile()
    profile.httpProfile.reqTimeout = 60
    profile.httpProfile.endpoint = "tcb.tencentcloudapi.com"
    return CommonClient(
        "tcb", "2018-06-08", Credential(values["secretId"], values["secretKey"], values["token"]),
        "ap-shanghai", profile=profile,
    )


def call(client, action, **values):
    return client.call_json(action, {"EnvId": ENV_ID, **values})["Response"]


def key_list(client):
    response = call(client, "DescribeApiKeyList", KeyType="api_key", PageNumber=1, PageSize=10)
    rows = response.get("Data", [])
    if len(rows) != response.get("Total", len(rows)) or len(rows) > 5:
        raise ValueError("服务端 API Key 列表不完整")
    return rows


def local_values():
    values = [dict(dotenv_values(path)) for path in LOCAL_PATHS]
    if any(row.get("CLOUDBASE_ENV_ID") != ENV_ID or not row.get("CLOUDBASE_API_KEY") for row in values):
        raise ValueError("本地 CloudBase 配置缺失或环境不符")
    if values[0]["CLOUDBASE_API_KEY"] != values[1]["CLOUDBASE_API_KEY"]:
        raise ValueError("本地 API Key 配置不一致")
    if not values[1].get("WORKER_TOKEN") or len(values[1]["WORKER_TOKEN"]) < 32:
        raise ValueError("云端 Worker 配置缺失")
    return values


def masked_matches(masked, token):
    if not masked:
        return False
    if "*" not in masked:
        return secrets.compare_digest(masked, token)
    parts = re.split(r"\*+", masked)
    if len(parts) != 2 or len(parts[1]) < 4:
        return False
    return token.startswith(parts[0]) and token.endswith(parts[1])


def save_state(state):
    PRIVATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE.parent.chmod(0o700)
    pending = PRIVATE.with_suffix(".pending")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(PRIVATE)


def load_state():
    state = json.loads(PRIVATE.read_text(encoding="utf-8"))
    if state["environment"] != ENV_ID or not state["new_key_name"].startswith("yitan-runtime-"):
        raise ValueError("凭据轮换记录归属不符")
    return state


def verify_database(token, *, revoked=False):
    with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
        response = client.get(
            f"https://{ENV_ID}.api.tcloudbasegateway.com/v1/rdb/rest/yitan_accounts",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            params={"select": "owner_id", "limit": "0"},
        )
    if revoked:
        if response.status_code not in {401, 403}:
            raise RuntimeError("旧 API Key 尚未被网关拒绝")
        return
    if response.status_code not in {200, 206} or response.json() != []:
        raise RuntimeError("新 API Key 的真实只读校验未通过")


def read_timer():
    result = subprocess.run(
        [str(CLI), "fn", "detail", "yitan-worker-timer", "-e", ENV_ID, "--json"],
        capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp")),
    )
    if result.returncode:
        raise RuntimeError("无法核实定时函数配置")
    return json.loads(result.stdout)["data"]


def timer_configuration(timer):
    values = timer["Environment"]["Variables"]
    if isinstance(values, list):
        return {row["Key"]: row["Value"] for row in values}
    if not isinstance(values, dict):
        raise ValueError("定时函数配置结构不符")
    return values


def verify_worker(url, new_token, old_token):
    with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
        old = client.get(url, headers={"X-Worker-Token": old_token})
        new = client.get(url, headers={"X-Worker-Token": new_token})
    if old.status_code != 403 or new.status_code != 405:
        raise RuntimeError("Worker 凭据真实鉴权校验未通过")


def inspect(client):
    values = local_values()
    token = values[1]["CLOUDBASE_API_KEY"]
    rows = key_list(client)
    timer = read_timer()
    emit(
        environment=ENV_ID, phase="inspect", creation_available=len(rows) < 5,
        keys=[{
            **{name: row.get(name) for name in ["KeyId", "Name", "CreateAt", "ExpireAt"]},
            "matches_local_mask": masked_matches(row.get("ApiKey"), token),
        } for row in rows if "yitan" in str(row.get("Name", ""))], key_count=len(rows),
        local_configuration_consistent=True, rotation_state_exists=PRIVATE.exists(),
        timer_status=timer["Status"], timer_configuration_names=sorted(timer_configuration(timer)),
    )


def prepare(client, old_key_id):
    if PRIVATE.exists():
        raise ValueError("已有凭据轮换记录，请核实当前阶段")
    values = local_values()
    rows = key_list(client)
    matched = [row for row in rows if row.get("KeyId") == old_key_id]
    if len(matched) != 1 or len(rows) >= 5:
        raise ValueError("旧 API Key 不存在或新 Key 配额已满")
    old = matched[0]
    candidates = [row for row in rows if masked_matches(row.get("ApiKey"), values[1]["CLOUDBASE_API_KEY"])]
    if len(candidates) != 1 or candidates[0]["KeyId"] != old_key_id:
        raise ValueError("旧 API Key 无法唯一匹配本地配置")
    if not str(old.get("Name", "")).startswith("yitan-"):
        raise ValueError("旧 API Key 名称没有表明忆谈专用归属")
    old_expiry = old.get("ExpireAt")
    expiry = 0
    if old_expiry:
        expiry = int((datetime.fromisoformat(old_expiry.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds())
        if expiry < 7200:
            raise ValueError("旧 API Key 有效期不足两小时，请明确新凭据有效期")
    key_name = "yitan-runtime-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    created = call(client, "CreateApiKey", KeyType="api_key", KeyName=key_name, ExpireIn=expiry)
    if not created.get("ApiKey") or not created.get("KeyId") or created.get("Name") != key_name:
        raise RuntimeError("新 API Key 创建响应不完整")
    state = {
        "environment": ENV_ID, "phase": "created", "old_key_id": old_key_id,
        "old_key_name": old.get("Name"), "old_api_key": values[1]["CLOUDBASE_API_KEY"],
        "old_worker_token": values[1]["WORKER_TOKEN"], "new_key_id": created["KeyId"],
        "new_key_name": key_name, "new_api_key": created["ApiKey"],
        "new_worker_token": secrets.token_urlsafe(48), "new_key_expiry": created.get("ExpireAt"),
        "preserved": [{key: digest(value) for key, value in row.items() if key not in ROTATED and value is not None} for row in values],
        "original_targets": [{key: digest(value) for key, value in row.items() if key in ROTATED and value is not None} for row in values],
    }
    save_state(state)
    verify_new()


def verify_new():
    state = load_state()
    if state["phase"] not in {"created", "prepared"}:
        raise ValueError("当前阶段不允许重新校验新凭据")
    verify_database(state["new_api_key"])
    state["phase"] = "prepared"
    save_state(state)
    emit(phase="prepared", new_key_id=state["new_key_id"], new_key_name=state["new_key_name"], expires_at=state["new_key_expiry"], database_read_verified=True, configuration_names=sorted(ROTATED))


def activate_local():
    state = load_state()
    if state["phase"] != "prepared":
        raise ValueError("新凭据尚未完成准备")
    values = [dict(dotenv_values(path)) for path in LOCAL_PATHS]
    for index, before in enumerate(values):
        preserved = {key: digest(value) for key, value in before.items() if key not in ROTATED and value is not None}
        if preserved != state["preserved"][index] or before["CLOUDBASE_API_KEY"] not in {state["old_api_key"], state["new_api_key"]}:
            raise ValueError("本地配置已变化，请核实配置后继续")
        if "WORKER_TOKEN" in before and digest(before["WORKER_TOKEN"] or "") not in {state["original_targets"][index].get("WORKER_TOKEN"), digest(state["new_worker_token"])}:
            raise ValueError("本地 Worker 配置已变化，请核实配置后继续")
    for path, before in zip(LOCAL_PATHS, values, strict=True):
        path.chmod(0o600)
        set_key(path, "CLOUDBASE_API_KEY", state["new_api_key"], quote_mode="always")
        if "WORKER_TOKEN" in before or path.name == ".env.deploy":
            set_key(path, "WORKER_TOKEN", state["new_worker_token"], quote_mode="always")
        after = dict(dotenv_values(path))
        expected = {**before, "CLOUDBASE_API_KEY": state["new_api_key"]}
        if "WORKER_TOKEN" in before or path.name == ".env.deploy":
            expected["WORKER_TOKEN"] = state["new_worker_token"]
        if after != expected:
            raise ValueError("本地配置保存校验不符")
    state["phase"] = "local_activated"
    save_state(state)
    emit(phase=state["phase"], configuration_names=sorted(ROTATED), preserved_configuration_verified=True)


def verify_deployed():
    state = load_state()
    if state["phase"] not in {"local_activated", "deployment_verified"}:
        raise ValueError("本地配置尚未启用新凭据")
    values = dict(dotenv_values(LOCAL_PATHS[1]))
    if values["CLOUDBASE_API_KEY"] != state["new_api_key"] or values["WORKER_TOKEN"] != state["new_worker_token"]:
        raise ValueError("本地配置没有使用本次新凭据")
    detail = request(cloudrun_client(), "DescribeCloudRunServerDetail", "yitan-api")
    deployed = json.loads(detail["ServerConfig"]["EnvParams"])
    for name, value in values.items():
        if value is not None and deployed.get(name) != value:
            raise ValueError("后端服务配置校验不符")
    timer = read_timer()
    timer_values = timer_configuration(timer)
    for name in ["CLOUDBASE_ENV_ID", "CLOUDBASE_API_KEY", "WORKER_TOKEN", "WORKER_URL"]:
        if timer_values.get(name) != values[name]:
            raise ValueError("定时函数配置校验不符")
    verify_database(state["new_api_key"])
    verify_worker(values["WORKER_URL"], state["new_worker_token"], state["old_worker_token"])
    state["phase"] = "deployment_verified"
    save_state(state)
    emit(phase=state["phase"], backend_configuration_verified=True, timer_configuration_verified=True, database_read_verified=True, protected_configuration_verified=True, worker_authorization_verified=True, model_calls=0)


def revoke(client):
    state = load_state()
    if state["phase"] != "deployment_verified":
        raise ValueError("云端部署尚未完成核实")
    verify_deployed()
    rows = key_list(client)
    old = [row for row in rows if row.get("KeyId") == state["old_key_id"]]
    new = [row for row in rows if row.get("KeyId") == state["new_key_id"]]
    if len(old) != 1 or len(new) != 1 or old[0].get("Name") != state["old_key_name"] or new[0].get("Name") != state["new_key_name"]:
        raise ValueError("云端 API Key 归属校验不符")
    call(client, "DeleteApiKey", KeyId=state["old_key_id"])
    state["phase"] = "revocation_submitted"
    save_state(state)
    emit(phase=state["phase"], old_key_revocation_submitted=True)


def verify_revoked(client):
    state = load_state()
    if state["phase"] != "revocation_submitted":
        raise ValueError("旧 API Key 撤销尚未提交")
    if any(row.get("KeyId") == state["old_key_id"] for row in key_list(client)):
        raise RuntimeError("旧 API Key 仍在服务端列表中")
    verify_database(state["old_api_key"], revoked=True)
    verify_database(state["new_api_key"])
    state["phase"] = "revoked"
    state.pop("old_api_key")
    state.pop("old_worker_token")
    state.pop("new_api_key")
    state.pop("new_worker_token")
    save_state(state)
    emit(phase=state["phase"], old_key_revoked=True, new_key_verified=True)


def main():
    parser = argparse.ArgumentParser(description="分步骤轮换忆谈 CloudBase 与 Worker 凭据，输出仅含状态和元数据")
    parser.add_argument("phase", choices=["inspect", "prepare", "verify-new", "activate-local", "verify-deployed", "revoke", "verify-revoked"])
    parser.add_argument("--old-key-id")
    args = parser.parse_args()
    os.umask(0o077)
    if args.phase == "verify-new":
        verify_new()
    elif args.phase == "activate-local":
        activate_local()
    elif args.phase == "verify-deployed":
        verify_deployed()
    else:
        client = cloudbase_client()
        if args.phase == "inspect":
            inspect(client)
        elif args.phase == "prepare":
            if not args.old_key_id:
                raise ValueError("必须明确旧 KeyId 元数据")
            prepare(client, args.old_key_id)
        elif args.phase == "revoke":
            revoke(client)
        else:
            verify_revoked(client)


if __name__ == "__main__":
    main()
