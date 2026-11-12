import { execFileSync } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { setTimeout as wait } from "node:timers/promises";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.env.TMPDIR = path.join(root, ".intermediate/tmp");
await mkdir(process.env.TMPDIR, { recursive: true, mode: 0o700 });
const require = createRequire(path.join(root, ".intermediate/deploy-tools/package.json"));
const CloudBase = require("@cloudbase/manager-node");
const dotenv = require("dotenv");
const { values } = parseArgs({ options: { service: { type: "string" }, config: { type: "string" }, inspect: { type: "boolean", default: false } } });
const services = { "yitan-api": "backend", "yitan-web": "frontend" };
const component = services[values.service];
if (!component) throw new Error("只允许操作 yitan-api 或 yitan-web");
const environmentId = "xhs-titile-d0g1g5e3481a57b71";
const cli = path.join(root, ".intermediate/deploy-tools/node_modules/.bin/tcb");

async function main() {
  const credentials = JSON.parse(execFileSync(cli, ["secrets", "get", "--json"], { encoding: "utf8", env: { ...process.env, TMPDIR: path.join(root, ".intermediate/tmp") }, stdio: ["ignore", "pipe", "pipe"] })).data;
  const manager = new CloudBase({ envId: environmentId, region: "ap-shanghai", secretId: credentials.secretId, secretKey: credentials.secretKey, token: credentials.token });
  if (values.inspect) {
    const list = await manager.cloudrun.list({ pageSize: 20, pageNum: 1 });
    console.log(JSON.stringify({ connected: true, existing_services: list.ServerList.map(item => item.ServerName) }));
    return;
  }
  if (!values.config) throw new Error("缺少受保护配置文件");
  const configPath = path.resolve(root, values.config);
  if (configPath !== path.join(root, component, ".env.deploy")) throw new Error("配置必须来自对应组件的 .env.deploy");
  const configuration = dotenv.parse(await readFile(configPath));
  if (configuration.DEPLOYMENT_MODE !== "cloud" || !configuration.PROXY_SHARED_SECRET || configuration.PROXY_SHARED_SECRET.length < 32) throw new Error("缺少公网运行配置");
  const packaged = JSON.parse(execFileSync(path.join(root, "backend/.venv/bin/python"), [path.join(root, "scripts/prepare_deployment.py"), component], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }));
  console.log(JSON.stringify({ service: values.service, files: packaged.files, bytes: packaged.bytes, secret_scan_passed: packaged.secret_scan_passed, configuration_names: Object.keys(configuration).sort() }));
  const submitted = await manager.cloudrun.deploy({
    serverName: values.service, targetPath: packaged.source, deployInfo: { ReleaseType: "FULL" },
    serverConfig: {
      OpenAccessTypes: ["PUBLIC"], Cpu: 0.25, Mem: 0.5, MinNum: 0, MaxNum: 1,
      Port: component === "backend" ? 8080 : 3000, Dockerfile: "Dockerfile", EnvParams: JSON.stringify(configuration),
    },
  });
  console.log(JSON.stringify({ service: values.service, submitted: true, request_id: submitted.RequestId }));
  let previous = "";
  for (let iteration = 0; iteration < 180; iteration += 1) {
    const result = await manager.cloudrun.describeServerManageTask({ serverName: values.service, taskId: submitted.TaskId });
    const task = result.Task;
    if (task && task.Status !== previous) {
      console.log(JSON.stringify({ service: values.service, task_id: task.Id, status: task.Status }));
      previous = task.Status;
    }
    if (task?.Status === "failed") throw new Error("deployment_failed");
    if (task?.Status === "finished") {
      const detail = await manager.cloudrun.detail({ serverName: values.service });
      const runtime = detail.ServerConfig;
      if (runtime.Cpu !== 0.25 || runtime.Mem !== 0.5 || runtime.MinNum !== 0 || runtime.MaxNum !== 1) throw new Error("deployment_resource_configuration_mismatch");
      console.log(JSON.stringify({ service: values.service, status: "finished", domain: detail.BaseInfo.DefaultDomainName, cpu: runtime.Cpu, memory_gib: runtime.Mem, min_instances: runtime.MinNum, max_instances: runtime.MaxNum }));
      return;
    }
    await wait(10_000);
  }
  throw new Error("deployment_wait_timeout");
}

main().catch(error => {
  console.error(JSON.stringify({ service: values.service, failed: true, code: typeof error.code === "string" ? error.code : "deployment_failed", request_id: error.requestId }));
  process.exitCode = 1;
});
