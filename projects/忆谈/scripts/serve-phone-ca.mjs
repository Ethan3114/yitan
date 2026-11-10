import http from "node:http";
import fs from "node:fs";

const certificate = fs.readFileSync(new URL("../.intermediate/tls/ca/rootCA.pem", import.meta.url));
http.createServer((request, response) => {
  if (request.method !== "GET" || request.url !== "/yitan-ca.crt") {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    "Content-Type": "application/x-x509-ca-cert",
    "Content-Disposition": 'attachment; filename="yitan-ca.crt"',
    "Cache-Control": "no-store",
  });
  response.end(certificate);
}).listen(3080, "0.0.0.0", () => console.log("手机公开证书下载已启动，端口 3080"));
