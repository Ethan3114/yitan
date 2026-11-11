import os
import signal
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    for relative in ("backend/.venv/bin/python", "backend/.env", "frontend/.next/BUILD_ID"):
        if not (ROOT / relative).exists():
            raise RuntimeError(f"缺少 {relative}，请按 README 完成安装与构建")
    for port in (8011, 3011, 3443, 3080):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", port))

    tls = ROOT / ".intermediate/tls"
    logs = ROOT / ".intermediate/runtime"
    logs.mkdir(parents=True, exist_ok=True)
    (tls / "ca").mkdir(parents=True, exist_ok=True)
    phone_ip = subprocess.check_output(["ipconfig", "getifaddr", "en0"], text=True).strip()
    if not phone_ip:
        raise RuntimeError("请先连接 Wi-Fi")
    env = dict(os.environ, CAROOT=str(tls / "ca"), NODE_ENV="production")
    subprocess.run([
        "mkcert", "-cert-file", str(tls / "server.pem"), "-key-file",
        str(tls / "server-key.pem"), phone_ip, "localhost", "127.0.0.1",
    ], env=env, check=True)
    commands = [
        ("backend", [str(ROOT / "backend/.venv/bin/python"), "-m", "uvicorn",
                     "app.main:app_factory", "--factory", "--host", "127.0.0.1", "--port", "8011"], ROOT / "backend", {}),
        ("web", ["node", "server.mjs"], ROOT / "frontend",
         {"HOST": "127.0.0.1", "PORT": "3011", "TLS_CERT": "", "TLS_KEY": ""}),
        ("phone", ["node", "server.mjs"], ROOT / "frontend",
         {"HOST": "0.0.0.0", "PORT": "3443", "TLS_CERT": str(tls / "server.pem"), "TLS_KEY": str(tls / "server-key.pem")}),
        ("certificate", ["node", "scripts/serve-phone-ca.mjs"], ROOT, {}),
    ]
    children = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for name, command, cwd, overrides in commands:
            with (logs / f"{name}.log").open("ab") as output:
                process = subprocess.Popen(command, cwd=cwd, env=env | overrides,
                                           stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            children.append((name, process))
        print(f"正在启动忆谈：电脑 http://127.0.0.1:3011 · 手机 https://{phone_ip}:3443", flush=True)
        print("按 Control+C 停止全部服务；运行日志位于 .intermediate/runtime/", flush=True)
        while not stopping:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} 服务退出，请检查 .intermediate/runtime/{name}.log")
            time.sleep(0.25)
    finally:
        for _name, process in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for _name, process in children:
            process.wait(timeout=30)


if __name__ == "__main__":
    main()
