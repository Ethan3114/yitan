import http from "node:http";
import https from "node:https";
import fs from "node:fs";
import { isIP } from "node:net";
import next from "next";
import httpProxy from "http-proxy";

const port = Number(process.env.PORT || 3011);
process.env.NODE_ENV ||= "development";
const hostname = process.env.HOST || "127.0.0.1";
if (Boolean(process.env.TLS_CERT) !== Boolean(process.env.TLS_KEY)) throw new Error("TLS_CERT 和 TLS_KEY 必须同时配置");
const secure = Boolean(process.env.TLS_CERT);
const cloud = process.env.DEPLOYMENT_MODE === "cloud";
const publicOrigin = process.env.PUBLIC_ORIGIN ? new URL(process.env.PUBLIC_ORIGIN) : null;
const proxySecret = process.env.PROXY_SHARED_SECRET || "";
if (cloud && (!publicOrigin || publicOrigin.protocol !== "https:" || publicOrigin.pathname !== "/" || publicOrigin.search || publicOrigin.hash || proxySecret.length < 32)) {
  throw new Error("公网运行需要 HTTPS PUBLIC_ORIGIN 和 PROXY_SHARED_SECRET");
}
const backend = new URL(process.env.BACKEND_URL || "http://127.0.0.1:8011");
if (cloud && backend.protocol !== "https:") throw new Error("公网后端必须使用 HTTPS");
const app = next({ dev: process.env.NODE_ENV !== "production", hostname, port });
await app.prepare();
const handle = app.getRequestHandler();
const upgrade = app.getUpgradeHandler();
const proxy = httpProxy.createProxyServer({ target: backend.origin, ws: true, changeOrigin: true });
proxy.on("error", (_error, _request, response) => {
  if (response instanceof http.ServerResponse && !response.headersSent) {
    response.writeHead(502, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { code: "backend_unavailable", message: "服务暂时没有连接，请稍后重试" } }));
  } else response.destroy();
});
function prepareProxy(request) {
  const platformAddress = request.headers["x-envoy-external-address"];
  delete request.headers["x-client-ip"];
  delete request.headers["x-proxy-secret"];
  delete request.headers["x-forwarded-for"];
  delete request.headers["x-real-ip"];
  delete request.headers["forwarded"];
  delete request.headers["x-cloudbase-context"];
  if (cloud) {
    const address = typeof platformAddress === "string" && isIP(platformAddress) ? platformAddress : "";
    if (!address) return false;
    request.headers["x-client-ip"] = address;
    request.headers["x-proxy-secret"] = proxySecret;
  }
  request.headers["x-forwarded-proto"] = cloud || secure ? "https" : "http";
  request.headers["x-forwarded-host"] = publicOrigin?.host || request.headers.host;
  return true;
}

function originAllowed(request, required = false) {
  if (!cloud) return true;
  const origin = request.headers.origin;
  return origin ? origin === publicOrigin.origin : !required;
}

function reject(response, code, message) {
  response.writeHead(403, { "Content-Type": "application/json", "Cache-Control": "private, no-store" });
  response.end(JSON.stringify({ error: { code, message } }));
}

const listener = (request, response) => {
  response.setHeader("Referrer-Policy", "no-referrer");
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.setHeader("X-Frame-Options", "DENY");
  if (cloud) response.setHeader("Strict-Transport-Security", "max-age=31536000");
  if (request.url === "/health") {
    response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    response.end('{"ok":true}');
    return;
  }
  if (request.url?.startsWith("/api/") || request.url?.startsWith("/join") || request.url?.startsWith("/family")) response.setHeader("Cache-Control", "private, no-store");
  if (request.url?.startsWith("/api/")) {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method) && !originAllowed(request)) {
      reject(response, "origin_denied", "请求来源不被允许");
      return;
    }
    if (!prepareProxy(request)) {
      reject(response, "platform_source_missing", "服务入口未正确配置");
      return;
    }
    proxy.web(request, response);
  } else handle(request, response);
};
const server = process.env.TLS_CERT && process.env.TLS_KEY
  ? https.createServer({ cert: fs.readFileSync(process.env.TLS_CERT), key: fs.readFileSync(process.env.TLS_KEY) }, listener)
  : http.createServer(listener);
server.on("upgrade", (request, socket, head) => {
  if (request.url?.startsWith("/api/")) {
    if (!originAllowed(request, true) || !prepareProxy(request)) {
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    proxy.ws(request, socket, head);
  }
  else upgrade(request, socket, head);
});
server.headersTimeout = 30_000;
server.requestTimeout = 120_000;
server.listen(port, hostname, () => console.log(`忆谈已启动，端口 ${port}`));
