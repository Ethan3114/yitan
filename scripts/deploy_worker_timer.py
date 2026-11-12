import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from cloudrun_tools import CLI, ENV_ID, ROOT

TRIGGER_NAME = "yitan-worker-every-twelve-hours"
TRIGGER_CRON = "0 0 */12 * * * *"
PREVIOUS_TRIGGER_NAME = "yitan-worker-every-five-minutes"


def read_detail(process_env):
    inspected = subprocess.run([str(CLI), "fn", "detail", "yitan-worker-timer", "-e", ENV_ID, "--json"], capture_output=True, text=True, env=process_env)
    if inspected.returncode:
        raise RuntimeError("无法核实云端定时函数配置")
    return json.loads(inspected.stdout)["data"]


def inspect(process_env):
    detail = read_detail(process_env)
    timers = detail.get("Triggers", [])
    if len(timers) != 1 or timers[0]["TriggerName"] != TRIGGER_NAME or json.loads(timers[0]["TriggerDesc"])["cron"] != TRIGGER_CRON or detail["Timeout"] != 900 or detail["MemorySize"] != 128 or detail["Status"] != "Active":
        raise ValueError("云端定时触发器或资源配置不符")
    print(json.dumps({"timer": TRIGGER_NAME, "verified": True, "schedule": TRIGGER_CRON, "trigger_count": len(timers), "status": detail["Status"], "timeout_seconds": detail["Timeout"], "memory_mb": detail["MemorySize"]}, ensure_ascii=False))
    return detail


def trigger_command(process_env, arguments):
    result = subprocess.run([str(CLI), "--yes", "fn", "trigger", *arguments, "-e", ENV_ID, "--json"], capture_output=True, text=True, env=process_env)
    if result.returncode:
        raise RuntimeError("忆谈定时触发器更新失败；原始响应已隐藏")


def update_schedule(process_env):
    before = read_detail(process_env)
    timers = before.get("Triggers", [])
    if before["Status"] != "Active" or before["Timeout"] != 900 or before["MemorySize"] != 128:
        raise ValueError("定时函数状态或资源配置不符，尚未更新")
    if any(row["TriggerName"] not in {PREVIOUS_TRIGGER_NAME, TRIGGER_NAME} for row in timers):
        raise ValueError("存在其他触发器，尚未更新")
    desired = [row for row in timers if row["TriggerName"] == TRIGGER_NAME]
    if desired and (len(desired) != 1 or json.loads(desired[0]["TriggerDesc"])["cron"] != TRIGGER_CRON):
        raise ValueError("同名触发器配置不符，尚未更新")
    if not desired:
        trigger_command(process_env, ["create", "yitan-worker-timer", "--trigger-name", TRIGGER_NAME, "--cron", TRIGGER_CRON])
        created = read_detail(process_env)
        installed = [row for row in created.get("Triggers", []) if row["TriggerName"] == TRIGGER_NAME]
        if len(installed) != 1 or json.loads(installed[0]["TriggerDesc"])["cron"] != TRIGGER_CRON:
            raise ValueError("新定时触发器未通过核实")
    if any(row["TriggerName"] == PREVIOUS_TRIGGER_NAME for row in timers):
        trigger_command(process_env, ["delete", "yitan-worker-timer", PREVIOUS_TRIGGER_NAME])
    after = inspect(process_env)
    preserved = {name: before[name] == after[name] for name in ("Runtime", "Handler", "Timeout", "MemorySize", "Environment")}
    if not all(preserved.values()):
        raise ValueError("定时函数运行配置发生变化")
    receipt = {"checked_at": datetime.now(timezone.utc).isoformat(), "function": "yitan-worker-timer", "trigger": TRIGGER_NAME, "cron": TRIGGER_CRON, "runs_per_day": 2, "trigger_count": len(after["Triggers"]), "configuration_preserved": preserved, "model_calls": 0}
    path = ROOT / ".intermediate/public-runtime/timer-schedule.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"updated": True, "runs_per_day": 2, "configuration_preserved": True, "model_calls": 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="部署或核实忆谈后台任务的定时触发函数")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--update-schedule", action="store_true", help="只更新既有函数触发周期，不重新部署函数或业务容器")
    args = parser.parse_args()
    process_env = dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp"))
    if args.inspect:
        inspect(process_env)
        return
    if args.update_schedule:
        update_schedule(process_env)
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
