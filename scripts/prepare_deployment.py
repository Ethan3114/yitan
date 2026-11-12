import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ALLOW = {
    "backend": ["app", "requirements.txt", "Dockerfile", ".dockerignore"],
    "frontend": ["app", "components", "lib", "content", "public", "package.json", "package-lock.json", "server.mjs", "next.config.js", "postcss.config.js", "tailwind.config.js", "tsconfig.json", "next-env.d.ts", "Dockerfile", ".dockerignore"],
}
DENY = {"__pycache__", ".pytest_cache", "node_modules", ".next", ".intermediate", "data", "tests", ".git"}


def main():
    parser = argparse.ArgumentParser(description="生成只含运行代码的忆谈云托管发布目录")
    parser.add_argument("component", choices=ALLOW)
    args = parser.parse_args()
    source = ROOT / args.component
    target = ROOT / ".intermediate/deployment" / args.component
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    secret_values = []
    for path in [ROOT / "backend/.env", ROOT / "backend/.env.deploy", ROOT / "frontend/.env.deploy"]:
        if path.exists():
            for key, value in dotenv_values(path).items():
                if value and len(value) >= 12 and any(word in key for word in ["KEY", "SECRET", "TOKEN", "PASSWORD", "PEPPER", "INVITE"]):
                    secret_values.append(value.encode())
    count, size = 0, 0
    manifest = []
    for entry in ALLOW[args.component]:
        original = source / entry
        if not original.exists():
            raise ValueError(f"缺少发布必需文件 {entry}")
        for path in original.rglob("*") if original.is_dir() else [original]:
            if path.is_dir():
                continue
            relative = path.relative_to(source)
            if any(part in DENY or part.startswith(".env") for part in relative.parts) or path.suffix in {".pyc", ".pem", ".log", ".db", ".sqlite"}:
                continue
            if path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.py")) or path.name.startswith("test_"):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("发布目录不允许符号链接")
            content = path.read_bytes()
            if any(value in content for value in secret_values):
                raise ValueError(f"发布内容出现受保护配置：{relative}")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            count += 1
            size += len(content)
            manifest.append({"path": relative.as_posix(), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    receipt = {"component": args.component, "files": manifest, "file_count": count, "bytes": size, "secret_scan_passed": True}
    (target.parent / f"{args.component}-manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"component": args.component, "files": count, "bytes": size, "secret_scan_passed": True, "source": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
