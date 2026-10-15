#!/usr/bin/env python3
"""Build a source-only release directory and Docker Compose archive from an allowlist."""

import argparse
import re
import shutil
import tarfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.2.0"
ROOT_FILES = {
    ".env.example", ".gitignore", ".dockerignore", "Dockerfile", "docker-compose.yml",
    "entrypoint.sh", "manage.py", "requirements.txt", "research.schema.json",
    "README.md", "LICENSE", "TODO.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
}
SOURCE_SUFFIXES = {
    "config": {".py"},
    "radar": {".py", ".html", ".css", ".js", ".svg"},
    "templates": {".html"},
    "tools": {".py"},
}
# Non-code files are individually approved: arbitrary JSON/TXT/YAML may contain
# credentials, downloaded recruitment data, AI responses or execution traces.
SOURCE_FILES = {
    ".github/workflows/ci.yml",
    "docs/assets/readme-poster.png",
    "deploy/job-radar-ai-worker.service.template",
    "deploy/job-radar-ai-worker.timer",
    "prompts/employer_research.txt",
}
EXCLUDED_PARTS = {
    "__pycache__", "work", "staticfiles", "node_modules", "data", "logs",
    "snapshots", "backups", "private", "secrets", "credentials", "outputs",
}


def release_files(root=PROJECT_ROOT):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            continue
        if len(relative.parts) == 1:
            if relative.name in ROOT_FILES:
                yield path
            continue
        if any(
            part.lower() in EXCLUDED_PARTS
            or (part.startswith(".") and not (index == 0 and part == ".github"))
            for index, part in enumerate(relative.parts)
        ):
            continue
        if relative.as_posix() in SOURCE_FILES:
            yield path
        elif path.suffix in SOURCE_SUFFIXES.get(relative.parts[0], set()):
            yield path


def private_env_values(root):
    path = root / ".env"
    if not path.exists():
        return []
    secrets = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(word in key.upper() for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY")):
            value = value.strip().strip('"').strip("'")
            if len(value) >= 12 and not value.startswith(("replace-", "change-", "development-")):
                secrets.append(value.encode("utf-8"))
    return secrets


def build_release(output_dir, version=VERSION, root=PROJECT_ROOT):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", version):
        raise ValueError("版本号只能包含字母、数字、点和连字符")
    output_dir = Path(output_dir).resolve()
    release_root = output_dir / f"job-radar-{version}"
    archive = output_dir / f"job-radar-{version}.tar.gz"
    if release_root.exists() or archive.exists():
        raise FileExistsError(f"发布目标已存在，请指定新的输出目录或版本号：{release_root}")
    files = list(release_files(root))
    secrets = private_env_values(root)
    for path in files:
        content = path.read_bytes()
        if any(secret in content for secret in secrets):
            raise ValueError(f"检测到本地密钥出现在待发布文件中：{path.relative_to(root)}（值已隐藏）")
    release_root.mkdir(parents=True)
    for path in files:
        target = release_root / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def portable_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(release_root, arcname=release_root.name, filter=portable_metadata)
    return release_root, archive, len(files)


def main():
    parser = argparse.ArgumentParser(description="生成不含环境密钥、数据库和运行日志的开源部署包")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--version", default=VERSION)
    args = parser.parse_args()
    folder, archive, count = build_release(args.output_dir, args.version)
    print(f"已打包 {count} 个源码文件\n目录：{folder}\n压缩包：{archive}")


if __name__ == "__main__":
    main()
