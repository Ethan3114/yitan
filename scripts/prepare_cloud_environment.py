import argparse
import json
import secrets
import sys
from urllib.parse import urlparse

from dotenv import dotenv_values, set_key

from cloudrun_tools import ENV_ID, ROOT

sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings


def origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("必须填写不带路径的 HTTPS 域名")
    return value.rstrip("/")


def save(path, values):
    if path.exists() and set(dotenv_values(path)) - set(values):
        raise ValueError("现有云端配置包含额外字段，请核实配置字段后继续")
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    for name, value in values.items():
        set_key(path, name, str(value), quote_mode="always")
    if dict(dotenv_values(path)) != {key: str(value) for key, value in values.items()}:
        raise ValueError("云端配置保存校验不符")


def main():
    parser = argparse.ArgumentParser(description="准备忆谈的受保护云端运行配置")
    parser.add_argument("--web-origin", required=True, type=origin)
    parser.add_argument("--backend-origin", required=True, type=origin)
    args = parser.parse_args()
    backend_path = ROOT / "backend/.env.deploy"
    frontend_path = ROOT / "frontend/.env.deploy"
    previous = dotenv_values(backend_path) if backend_path.exists() else {}
    settings = Settings(_env_file=ROOT / "backend/.env")
    backend = {name.upper(): str(value).lower() if isinstance(value, bool) else str(value) for name, value in settings.model_dump().items() if value is not None}
    source = dotenv_values(ROOT / "backend/.env")
    backend.update({key: value for key, value in previous.items() if key in backend and value is not None})
    backend.update({key: value for key, value in source.items() if key in backend and value is not None})
    backend.update({"CLOUDBASE_ENV_ID": ENV_ID, "CLOUDBASE_API_KEY": source["CLOUDBASE_API_KEY"]})
    for key in ["AUTH_PEPPER", "PROXY_SHARED_SECRET", "WORKER_TOKEN"]:
        backend[key] = previous.get(key) or secrets.token_urlsafe(48)
    backend.update({
        "DEPLOYMENT_MODE": "cloud", "COOKIE_SECURE": "true", "WEB_ORIGIN": args.web_origin,
        "WORKER_URL": args.backend_origin + "/api/internal/work", "DATA_DIR": "/app/runtime", "APP_PORT": "8080",
        "DATABASE_URL": "",
    })
    frontend = {
        "DEPLOYMENT_MODE": "cloud", "NODE_ENV": "production", "HOST": "0.0.0.0", "PORT": "3000",
        "PUBLIC_ORIGIN": args.web_origin, "BACKEND_URL": args.backend_origin,
        "PROXY_SHARED_SECRET": backend["PROXY_SHARED_SECRET"], "NEXT_TELEMETRY_DISABLED": "1",
    }
    save(backend_path, backend)
    save(frontend_path, frontend)
    print(json.dumps({"backend_names": sorted(backend), "frontend_names": sorted(frontend), "mode": "cloud", "configuration_saved": True, "secrets_reused": bool(previous)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
