import http from "node:http";
import https from "node:https";
import fs from "node:fs";
import next from "next";
import httpProxy from "http-proxy";

const port = Number(process.env.PORT || 3011);
process.env.NODE_ENV ||= "development";
const hostname = process.env.HOST || "127.0.0.1";
if (Boolean(process.env.TLS_CERT) !== Boolean(process.env.TLS_KEY)) throw new Error("TLS_CERT 和 TLS_KEY 必须同时配置");
const secure = Boolean(process.env.TLS_CERT);
const app = next({ dev: process.env.NODE_ENV !== "production", hostname, port });
await app.prepare();
const handle = app.getRequestHandler();
const upgrade = app.getUpgradeHandler();
const proxy = httpProxy.createProxyServer({ target: process.env.BACKEND_URL || "http://127.0.0.1:8011", ws: true });
proxy.on("error", (_error, _request, response) => {
  if (response instanceof http.ServerResponse && !response.headersSent) {
    response.writeHead(502, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { code: "backend_unavailable", message: "服务暂时没有连接，请稍后重试" } }));
  } else response.destroy();
});
const listener = (request, response) => {
  request.headers["x-forwarded-proto"] = secure ? "https" : "http";
  response.setHeader("Referrer-Policy", "no-referrer");
  if (request.url?.startsWith("/api/") || request.url?.startsWith("/join") || request.url?.startsWith("/family")) response.setHeader("Cache-Control", "private, no-store");
  if (request.url?.startsWith("/api/")) proxy.web(request, response);
  else handle(request, response);
};
const server = process.env.TLS_CERT && process.env.TLS_KEY
  ? https.createServer({ cert: fs.readFileSync(process.env.TLS_CERT), key: fs.readFileSync(process.env.TLS_KEY) }, listener)
  : http.createServer(listener);
server.on("upgrade", (request, socket, head) => {
  request.headers["x-forwarded-proto"] = secure ? "https" : "http";
  if (request.url?.startsWith("/api/")) proxy.ws(request, socket, head);
  else upgrade(request, socket, head);
});
server.listen(port, hostname, () => console.log(`忆谈已启动，端口 ${port}`));
