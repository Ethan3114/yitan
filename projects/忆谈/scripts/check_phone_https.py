import asyncio
import json
import ssl
import sys
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings


async def main():
    settings = get_settings()
    tls = ssl.create_default_context(cafile=str(ROOT / ".intermediate/tls/ca/rootCA.pem"))
    async with httpx.AsyncClient(base_url="https://127.0.0.1:3443", verify=tls) as client:
        response = await client.post("/api/v1/auth/login", json={"code": settings.invite_code})
        response.raise_for_status()
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert (await client.get("/api/v1/me")).status_code == 200
        profiles = await client.get("/api/v1/profiles")
        profiles.raise_for_status()
        assert profiles.json()["items"]
        await client.post("/api/v1/auth/logout")
        assert (await client.get("/api/v1/me")).status_code == 401
    async with websockets.connect("wss://127.0.0.1:3443/api/v1/calls/no-session/live", ssl=tls) as socket:
        event = json.loads(await socket.recv())
        assert event["type"] == "error" and event["error"]["code"] == "not_found"
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:3080/yitan-ca.crt")
        response.raise_for_status()
        assert response.content == (ROOT / ".intermediate/tls/ca/rootCA.pem").read_bytes()
        assert (await client.get("http://127.0.0.1:3080/rootCA-key.pem")).status_code == 404
    print("证书链、HTTPS 登录、Secure/HttpOnly Cookie、读取与退出、WSS 未授权拒绝、公开证书下载及私钥路径拒绝通过；未调用语音模型。")


if __name__ == "__main__":
    asyncio.run(main())
