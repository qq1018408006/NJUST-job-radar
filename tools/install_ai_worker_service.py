#!/usr/bin/env python3
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_SYSTEMD = Path.home() / ".config" / "systemd" / "user"


def main():
    service_template = (PROJECT_ROOT / "deploy" / "job-radar-ai-worker.service.template").read_text(
        encoding="utf-8"
    )
    service = service_template.replace("__PROJECT_ROOT__", str(PROJECT_ROOT)).replace(
        "__WORKER_PATH__", str(PROJECT_ROOT / "tools" / "ai_research_worker.py")
    )
    timer = (PROJECT_ROOT / "deploy" / "job-radar-ai-worker.timer").read_text(encoding="utf-8")
    USER_SYSTEMD.mkdir(parents=True, exist_ok=True)
    (USER_SYSTEMD / "job-radar-ai-worker.service").write_text(service, encoding="utf-8")
    (USER_SYSTEMD / "job-radar-ai-worker.timer").write_text(timer, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "job-radar-ai-worker.timer"],
        check=True,
    )
    subprocess.run(
        ["systemctl", "--user", "status", "job-radar-ai-worker.timer", "--no-pager"],
        check=False,
    )


if __name__ == "__main__":
    main()
