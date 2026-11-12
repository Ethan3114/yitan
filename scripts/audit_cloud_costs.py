import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tencentcloud.common.common_client import CommonClient
from tencentcloud.common.profile.client_profile import ClientProfile

from cloudrun_tools import ENV_ID, ROOT, SERVICES, cloudrun_client, request
from rotate_cloud_credentials import cloudbase_client, read_timer


MODULES = ["FLEXDB", "TDSQL", "SCF", "AI", "EKS", "COS", "HOSTING", "Auth", "APIInvocation", "HTTPInvocation", "VM", "Workflow", "Other", "PostgreSQL", "Token"]
POINT_FIELDS = ["DeductValue", "PackageDeductValue", "ReportValue", "OriginCredits"]
METRIC_FIELDS = ["MetricName", "ResourceType", "Unit", "BillingCycleType", "Value", "CreditsValue", *POINT_FIELDS]
VERSION_FIELDS = ["VersionName", "Status", "CurrentReplicas", "MinReplicas", "MaxReplicas", "Percent"]
SHANGHAI = ZoneInfo("Asia/Shanghai")
CURRENT_ACTION = "initialize"


def failure_hook(kind, value, traceback):
    result = {"audit_failed": True, "error_type": kind.__name__, "operation": CURRENT_ACTION}
    code = getattr(value, "code", None)
    if isinstance(code, str) and len(code) < 120:
        result["error_code"] = code
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)


def pick(value, fields):
    return {name: value[name] for name in fields if name in value}


def tcb_read(client, action, **values):
    global CURRENT_ACTION
    if action not in {"DescribeEnvInfo", "DescribeBillingInfo", "DescribeEnvAccountCircle", "DescribeCreditsUsage", "DescribeCreditsUsageDetail"}:
        raise ValueError("审计只允许读取环境计费信息")
    CURRENT_ACTION = action
    return client.call_json(action, {"EnvId": ENV_ID, **values})["Response"]


def billing_window(client, start, end, label):
    params = {"StartDate": start.isoformat(), "EndDate": end.isoformat()}
    summary = tcb_read(client, "DescribeCreditsUsage", **params)
    detail = tcb_read(client, "DescribeCreditsUsageDetail", **params, Modules=MODULES, NeedUsageDetails=False)
    modules = [{
        **pick(row, ["Module", "CreditsValue", *POINT_FIELDS]),
        "metrics": [pick(metric, METRIC_FIELDS) for metric in row.get("MetricUsageDetail", []) if metric.get("Value") or metric.get("CreditsValue")],
    } for row in detail["Usages"] if row.get("CreditsValue")]
    return {
        "label": label, "start_date": params["StartDate"], "end_date": params["EndDate"],
        "summary": pick(summary, POINT_FIELDS), "module_credit_total": round(sum(row.get("CreditsValue", 0) for row in modules), 2),
        "modules": modules,
    }


def daily_samples(client, day):
    detail = tcb_read(
        client, "DescribeCreditsUsageDetail", StartDate=day.isoformat(), EndDate=day.isoformat(),
        Modules=["PostgreSQL", "SCF", "EKS"], NeedUsageDetails=True,
    )
    return {
        "day": day.isoformat(),
        "modules": [{
            "module": row["Module"],
            "metrics": [{
                **pick(metric, METRIC_FIELDS),
                "samples": [pick(point, ["CalcTime", "RawValue", "CreditsValue"]) for point in metric.get("ValueDetailList", [])],
            } for metric in row.get("MetricUsageDetail", []) if metric.get("Value") or metric.get("CreditsValue")],
        } for row in detail["Usages"]],
    }


def services_snapshot():
    global CURRENT_ACTION
    client = cloudrun_client()
    rows = []
    for service in sorted(SERVICES):
        CURRENT_ACTION = "DescribeCloudRunServerDetail"
        detail = request(client, "DescribeCloudRunServerDetail", service)
        CURRENT_ACTION = "DescribeReleaseOrder"
        release = request(client, "DescribeReleaseOrder", service).get("ReleaseOrderInfo") or {}
        rows.append({
            "service": service, "status": detail["BaseInfo"]["Status"],
            "configuration": pick(detail["ServerConfig"], ["Cpu", "Mem", "MinNum", "MaxNum", "Port", "PolicyDetails", "TimerScale"]),
            "online_versions": [pick(version, ["VersionName", "FlowRatio"]) for version in detail.get("OnlineVersionInfos", [])],
            "release": pick(release, ["IsReleasing", "ReleaseStatus", "GrayStatus"]),
            "previous_version": pick(release.get("CurrentVersion") or {}, VERSION_FIELDS),
            "latest_version": pick(release.get("ReleaseVersion") or {}, VERSION_FIELDS),
        })
    return rows


def timer_trigger_snapshot(client, timer):
    global CURRENT_ACTION
    CURRENT_ACTION = "ListTriggers"
    profile = ClientProfile()
    profile.httpProfile.reqTimeout = 60
    profile.httpProfile.endpoint = "scf.tencentcloudapi.com"
    scf = CommonClient("scf", "2018-04-16", client.credential, "ap-shanghai", profile=profile)
    response = scf.call_json("ListTriggers", {
        "FunctionName": "yitan-worker-timer", "Namespace": timer["Namespace"], "Offset": 0, "Limit": 100,
    })["Response"]
    if response["TotalCount"] != len(response["Triggers"]):
        raise ValueError("定时函数触发器列表不完整")
    return [{
        **pick(row, ["TriggerName", "Type", "Enable", "AvailableStatus", "Qualifier", "AddTime", "ModTime"]),
        "cron": json.loads(row["TriggerDesc"]).get("cron"),
    } for row in response["Triggers"]]


def monitor_snapshot(client, timer, checked_at):
    global CURRENT_ACTION
    profile = ClientProfile()
    profile.httpProfile.reqTimeout = 60
    profile.httpProfile.endpoint = "monitor.tencentcloudapi.com"
    monitor = CommonClient("monitor", "2018-07-24", client.credential, "ap-shanghai", profile=profile)
    start = datetime.strptime(timer["AddTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
    rows = {}
    for metric in ["Invocation", "Duration", "BillDuration", "Error"]:
        CURRENT_ACTION = "GetMonitorData:" + metric
        response = monitor.call_json("GetMonitorData", {
            "Namespace": "QCE/SCF_V2", "MetricName": metric, "Period": 3600,
            "StartTime": start.isoformat(timespec="seconds"), "EndTime": checked_at.isoformat(timespec="seconds"),
            "Instances": [{"Dimensions": [
                {"Name": "functionName", "Value": "yitan-worker-timer"},
                {"Name": "namespace", "Value": timer["Namespace"]},
                {"Name": "version", "Value": timer["FunctionVersion"]},
            ]}],
        })["Response"]
        series = response.get("DataPoints", [])
        if len(series) > 1:
            raise ValueError("函数监控返回多个资源序列")
        rows[metric] = [{
            "time": datetime.fromtimestamp(stamp, timezone.utc).astimezone(SHANGHAI).isoformat(), "value": value,
        } for stamp, value in zip(series[0]["Timestamps"], series[0]["Values"], strict=True)] if series else []
    invocation = {point["time"]: point["value"] for point in rows["Invocation"]}
    duration = {point["time"]: point["value"] for point in rows["Duration"]}
    total = sum(invocation.values())
    return {
        "function": "yitan-worker-timer", "window_start": start.isoformat(), "window_end": checked_at.isoformat(),
        "period_seconds": 3600, "invocations": total, "errors": sum(point["value"] for point in rows["Error"]),
        "bill_duration_ms": sum(point["value"] for point in rows["BillDuration"]),
        "weighted_mean_duration_ms": round(sum(duration.get(time, 0) * count for time, count in invocation.items()) / total, 3) if total else None,
        "last_reported_point": max(invocation, default=None), "series": rows,
    }


def save_report(report, path):
    path = path.resolve()
    destination = ROOT / ".intermediate/cloud-cost-audit"
    if path.parent != destination or path.suffix != ".json":
        raise ValueError("审计结果必须保存至项目忽略目录")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description="只读核实共享环境资源点及忆谈专用资源，仅保存安全计量字段")
    parser.add_argument("--previous-start", type=date.fromisoformat, required=True)
    parser.add_argument("--previous-end", type=date.fromisoformat, required=True)
    parser.add_argument("--day", type=date.fromisoformat, default=datetime.now(SHANGHAI).date())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.previous_start > args.previous_end:
        raise ValueError("计费日期顺序不符")
    checked_at = datetime.now(SHANGHAI)
    client = cloudbase_client()
    environment = tcb_read(client, "DescribeEnvInfo")["EnvInfo"]
    cycle = tcb_read(client, "DescribeEnvAccountCircle")
    billing = tcb_read(client, "DescribeBillingInfo")
    selected = [row for row in billing["EnvBillingInfoList"] if row.get("EnvId") == ENV_ID]
    if len(selected) != 1:
        raise ValueError("计费环境归属无法唯一核实")
    timer = read_timer()
    report = {
        "checked_at": checked_at.isoformat(), "billing_scope": "shared_environment", "business_data_read": False,
        "billing": pick(selected[0], ["Status", "PackageId", "PackageName", "PayMode", "ExpireTime", "EnableOverrun"]),
        "current_cycle": pick(cycle, ["StartTime", "EndTime"]),
        "environment_status": environment["EnvBaseInfo"]["Status"],
        "windows": [
            billing_window(client, args.previous_start, args.previous_end, "previous_trial_date_window"),
            billing_window(client, date.fromisoformat(cycle["StartTime"][:10]), date.fromisoformat(cycle["EndTime"][:10]), "current_personal_date_window"),
        ],
        "daily_samples": daily_samples(client, args.day), "services": services_snapshot(),
        "timer": {
            **pick(timer, ["FunctionName", "Status", "MemorySize", "Timeout", "AddTime", "ModTime"]),
            "triggers": timer_trigger_snapshot(client, timer),
        },
        "timer_monitor": monitor_snapshot(client, timer, checked_at),
        "interpretation_constraints": [
            "环境资源点包含共用环境的所有产品，不可全部归因忆谈。",
            "升级日的按日期查询包含升级前后用量，重叠日期窗口不可相加。",
            "汇总与明细更新时间不同，当前汇总为零不代表没有实际消耗。",
            "HourlyStorage 的 MB 是计量接口字段，不作为当前实际存储容量。",
            "ReportValue 是资源点字段，不是人民币金额。",
            "监控平均运行时间使用各小时调用次数加权；未上报时段不计入。",
        ],
    }
    output = args.output or ROOT / ".intermediate/cloud-cost-audit" / (checked_at.strftime("%Y%m%d-%H%M%S") + ".json")
    save_report(report, output)
    print(json.dumps({
        "checked_at": report["checked_at"], "output": str(output), "billing_scope": report["billing_scope"],
        "module_totals": [{"label": row["label"], "credit_total": row["module_credit_total"]} for row in report["windows"]],
        "timer_invocations": report["timer_monitor"]["invocations"], "timer_bill_duration_ms": report["timer_monitor"]["bill_duration_ms"],
        "timer_weighted_mean_duration_ms": report["timer_monitor"]["weighted_mean_duration_ms"],
        "last_timer_monitor_point": report["timer_monitor"]["last_reported_point"], "cloud_mutations": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    sys.excepthook = failure_hook
    main()
