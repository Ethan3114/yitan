import argparse
import json
import re

from dotenv import dotenv_values
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException

from cloudrun_tools import ENV_ID, ROOT, SERVICES, cloudrun_client, request


def main():
    parser = argparse.ArgumentParser(description="读取忆谈服务状态和经过脱敏的日志")
    parser.add_argument("service", choices=sorted(SERVICES))
    parser.add_argument("--logs", choices=["build", "runtime"])
    parser.add_argument("--task", action="store_true")
    args = parser.parse_args()
    client = cloudrun_client()
    detail = request(client, "DescribeCloudRunServerDetail", args.service)
    config = detail["ServerConfig"]
    base = detail["BaseInfo"]
    print(json.dumps({
        "service": args.service, "status": base["Status"], "domain": base["DefaultDomainName"],
        "cpu": config["Cpu"], "memory_gib": config["Mem"], "min_instances": config["MinNum"], "max_instances": config["MaxNum"],
        "configuration_names": sorted(json.loads(config["EnvParams"])),
    }, ensure_ascii=False))
    if args.task:
        status = client.call_json("DescribeServerManageTask", {"EnvId": ENV_ID, "ServerName": args.service, "TaskId": 0})["Response"]
        task = status.get("Task") or {}
        print(json.dumps({"task_id": task.get("Id"), "status": task.get("Status"), "steps": [{key: step.get(key) for key in ["Name", "Status", "StartTime", "EndTime"]} for step in task.get("Steps", [])]}, ensure_ascii=False))
    if not args.logs:
        return
    records = request(client, "DescribeCloudRunDeployRecord", args.service)["DeployRecords"]
    record = max(records, key=lambda row: row["DeployTime"])
    if args.logs == "build":
        logs = client.call_json("DescribeCloudRunBuildLog", {"EnvId": ENV_ID, "ServerName": args.service, "BuildId": record["BuildId"], "ServerVersion": "", "Offset": 0})["Response"]
    else:
        logs = client.call_json("DescribeCloudRunProcessLog", {"EnvId": ENV_ID, "RunId": record["RunId"]})["Response"]
    if args.logs == "build":
        log = logs["Log"]
        text = log.get("Text", "")
        print(json.dumps({"build_id": record["BuildId"], "fail_type": log.get("FailType"), "request_id": logs.get("RequestId")}, ensure_ascii=False))
    else:
        text = "\n".join(logs.get("Logs", []))
    for path in [ROOT / "backend/.env", ROOT / "backend/.env.deploy", ROOT / "frontend/.env.deploy"]:
        if path.exists():
            for key, value in dotenv_values(path).items():
                if value and len(value) >= 12 and any(word in key for word in ["KEY", "SECRET", "TOKEN", "PASSWORD", "PEPPER", "INVITE"]):
                    text = text.replace(value, "<protected>")
    text = re.sub(r'https?://[^\s"\\]*\?[^\s"\\]*', "<protected-url>", text)
    text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "<protected-token>", text)
    print("\n".join(text.splitlines()[-40:]))


if __name__ == "__main__":
    try:
        main()
    except TencentCloudSDKException as error:
        raise SystemExit(json.dumps({"error_code": error.code, "request_id": error.requestId}, ensure_ascii=False)) from None
