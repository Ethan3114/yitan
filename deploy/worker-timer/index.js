"use strict";

exports.main = async function main() {
  const { CLOUDBASE_ENV_ID: environment, CLOUDBASE_API_KEY: key, WORKER_URL: workerUrl, WORKER_TOKEN: workerToken } = process.env;
  if (!environment || !key || !workerUrl || !workerToken || workerToken.length < 32) throw new Error("worker_configuration_missing");
  const target = new URL(workerUrl);
  if (target.protocol !== "https:" || target.pathname !== "/api/internal/work") throw new Error("worker_url_invalid");
  const endpoint = new URL(`https://${environment}.api.tcloudbasegateway.com/v1/rdb/rest/yitan_draft_tasks`);
  endpoint.searchParams.set("select", "id");
  endpoint.searchParams.set("or", `(status.eq.queued,and(status.eq.running,lease_until.lt.${new Date().toISOString()}))`);
  endpoint.searchParams.set("limit", "1");
  const pending = await fetch(endpoint, { headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(20_000) });
  if (!pending.ok) throw new Error(`worker_queue_read_http_${pending.status}`);
  const tasks = await pending.json();
  if (!Array.isArray(tasks)) throw new Error("worker_queue_format_invalid");
  if (tasks.length === 0) return { status: "idle" };
  const response = await fetch(target, {
    method: "POST", headers: { "X-Worker-Token": workerToken }, signal: AbortSignal.timeout(840_000),
  });
  if (!response.ok || !response.body) throw new Error(`worker_http_${response.status}`);
  // 持续读取服务端心跳，整个请求存活期间保持云托管实例运行。
  for await (const _chunk of response.body) { /* 响应正文留在业务服务端，不记入函数日志。 */ }
  return { status: "worker_request_completed" };
};
