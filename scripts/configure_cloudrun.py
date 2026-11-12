import argparse
import json
import time

from dotenv import dotenv_values
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException

from cloudrun_tools import ENV_ID, ROOT, SERVICES, cloudrun_client, request


def main():
    parser = argparse.ArgumentParser(description="通过官方 SDK 将受保护的配置写入忆谈云托管")
    parser.add_argument("service", choices=sorted(SERVICES))
    parser.add_argument("--file", required=True)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    path = (ROOT / args.file).resolve(strict=True)
    if not path.is_relative_to(ROOT) or path.name != ".env.deploy":
        raise ValueError("必须使用项目中被忽略的 .env.deploy 文件")
    params = {key: value for key, value in dotenv_values(path).items() if value is not None}
    client = cloudrun_client()
    if args.inspect:
        detail = request(client, "DescribeCloudRunServerDetail", args.service)
        print(json.dumps({"service": args.service, "fields": list(detail)}, ensure_ascii=False))
        return
    records = request(client, "DescribeCloudRunDeployRecord", args.service)["DeployRecords"]
    if not records:
        raise ValueError("服务没有已构建镜像")
    image = max(records, key=lambda row: row["DeployTime"])["ImageUrl"]
    port = 8080 if args.service == "yitan-api" else 3000
    response = client.call_json("UpdateCloudRunServer", {
        "EnvId": ENV_ID, "ServerName": args.service,
        "DeployInfo": {"DeployType": "image", "ImageUrl": image, "ReleaseType": "FULL"},
        "Items": [
            {"Key": "EnvParam", "Value": json.dumps(params)},
            {"Key": "CpuSpecs", "FloatValue": 0.25}, {"Key": "MemSpecs", "FloatValue": 0.5},
            {"Key": "MinNum", "IntValue": 0}, {"Key": "MaxNum", "IntValue": 1}, {"Key": "Port", "IntValue": port},
        ],
    })["Response"]
    task_id = response["TaskId"]
    print(json.dumps({"service": args.service, "task_id": task_id, "configuration_names": sorted(params), "cpu": 0.25, "memory_gib": 0.5, "min_instances": 0, "max_instances": 1}, ensure_ascii=False), flush=True)
    for _ in range(90):
        time.sleep(10)
        response = client.call_json("DescribeServerManageTask", {"EnvId": ENV_ID, "ServerName": args.service, "TaskId": task_id})["Response"]
        task = response.get("Task") or response
        if task.get("Status") in {"finished", "failed"}:
            print(json.dumps({"service": args.service, "status": task["Status"]}, ensure_ascii=False))
            if task["Status"] == "failed":
                raise RuntimeError("云托管配置发布失败，请读取经过脱敏的运行日志")
            return
    raise RuntimeError("云托管配置发布等待超时")


if __name__ == "__main__":
    try:
        main()
    except TencentCloudSDKException as error:
        raise SystemExit(json.dumps({"error_code": error.code, "request_id": error.requestId}, ensure_ascii=False)) from None
