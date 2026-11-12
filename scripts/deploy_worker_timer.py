import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from cloudrun_tools import CLI, ENV_ID, ROOT


def inspect(process_env):
    inspected = subprocess.run([str(CLI), "fn", "detail", "yitan-worker-timer", "-e", ENV_ID, "--json"], capture_output=True, text=True, env=process_env)
    if inspected.returncode:
        raise RuntimeError("无法核实云端定时函数配置")
    detail = json.loads(inspected.stdout)["data"]
    timers = [row for row in detail.get("Triggers", []) if row["TriggerName"] == "yitan-worker-every-five-minutes"]
    if len(timers) != 1 or json.loads(timers[0]["TriggerDesc"])["cron"] != "0 */5 * * * * *" or detail["Timeout"] != 900 or detail["MemorySize"] != 128:
        raise ValueError("云端定时触发器或资源配置不符")
    print(json.dumps({"timer": "yitan-worker-every-five-minutes", "verified": True, "schedule": "0 */5 * * * * *", "status": detail["Status"], "timeout_seconds": detail["Timeout"], "memory_mb": detail["MemorySize"]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="部署或核实忆谈后台任务的定时触发函数")
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    process_env = dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp"))
    if args.inspect:
        inspect(process_env)
        return
    values = dotenv_values(ROOT / "backend/.env.deploy")
    names = ["CLOUDBASE_ENV_ID", "CLOUDBASE_API_KEY", "WORKER_URL", "WORKER_TOKEN"]
    configuration = {name: values[name] for name in names}
    if any(not value for value in configuration.values()) or configuration["CLOUDBASE_ENV_ID"] != ENV_ID:
        raise ValueError("定时任务配置缺失")
    template = json.loads((ROOT / "deploy/cloudbaserc.json").read_text())
    template["functionRoot"] = str(ROOT / "deploy")
    function = template["functions"][0]
    function["dir"] = str(ROOT / "deploy/worker-timer")
    function["envVariables"] = configuration
    private = ROOT / ".intermediate/private/timer-cloudbaserc.json"
    private.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.parent.chmod(0o700)
    descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(template, stream)
        stream.flush()
        os.fsync(stream.fileno())
    result = subprocess.run([str(CLI), "--config-file", str(private), "--yes", "fn", "deploy", "yitan-worker-timer", "--force", "--json", "-e", ENV_ID], capture_output=True, text=True, env=process_env, cwd=ROOT / "deploy")
    if result.returncode:
        raise RuntimeError("忆谈定时函数部署失败；原始响应已隐藏")
    print(json.dumps({"function": "yitan-worker-timer", "deployed": True, "configuration_names": names, "timeout_seconds": 900, "memory_mb": 128}, ensure_ascii=False), flush=True)
    inspect(process_env)


if __name__ == "__main__":
    main()
