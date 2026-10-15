#!/usr/bin/env python3
"""Claim one task, run a selected local CLI, and return a validated snapshot."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.research_providers import PROVIDERS, check_provider, run_research


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "employer_research.txt"


def load_env(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_request(base_url, token, path, payload):
    request = Request(
        f"{base_url.rstrip('/')}{path}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"API {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc


def build_prompt(task, prompt_path=None):
    template = Template(Path(prompt_path or DEFAULT_PROMPT_PATH).read_text(encoding="utf-8"))
    return template.substitute(
        research_date=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        item_json=json.dumps(task["item"], ensure_ascii=False, indent=2),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="领取一条 AI 单位研究任务，支持 Codex / Claude Code / OpenCode")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--base-url", help="覆盖 JOB_RADAR_BASE_URL")
    parser.add_argument("--provider", choices=PROVIDERS, help="覆盖 AI_RESEARCH_PROVIDER，默认 codex")
    parser.add_argument("--schema", type=Path, default=PROJECT_ROOT / "research.schema.json")
    parser.add_argument("--prompt-file", type=Path, help="覆盖 AI_RESEARCH_PROMPT_FILE")
    parser.add_argument("--timeout", type=int, help="覆盖 AI_RESEARCH_TIMEOUT_SECONDS")
    parser.add_argument("--check", action="store_true", help="检查配置和 CLI 参数，不领取任务、不调用模型")
    args = parser.parse_args(argv)
    # .env must be loaded before resolving defaults (the former worker did this too late).
    load_env(args.env_file)
    provider = args.provider or os.getenv("AI_RESEARCH_PROVIDER", "codex").strip().lower() or "codex"
    base_url = args.base_url or os.getenv("JOB_RADAR_BASE_URL", "").strip() or f"http://127.0.0.1:{os.getenv('WEB_PORT', '8080')}"
    prompt_path = args.prompt_file or Path(os.getenv("AI_RESEARCH_PROMPT_FILE", "").strip() or DEFAULT_PROMPT_PATH)
    try:
        timeout = args.timeout if args.timeout is not None else int(os.getenv("AI_RESEARCH_TIMEOUT_SECONDS", "1800"))
        if timeout <= 0:
            raise ValueError("AI_RESEARCH_TIMEOUT_SECONDS 必须大于 0")
        schema_path = args.schema.resolve()
        json.loads(schema_path.read_text(encoding="utf-8"))
        build_prompt({"item": {}}, prompt_path)
        executable, model = check_provider(provider)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"Worker 配置错误：{exc}", file=sys.stderr)
        return 1
    if args.check:
        print(f"配置检查通过：{provider} / {executable} / 模型 {model or 'CLI 默认'}；未领取任务、未调用模型")
        return 0
    token = os.getenv("APP_API_TOKEN", "").strip()
    if not token or token in {"change-this-api-token", "replace-with-a-long-random-api-token"}:
        print("APP_API_TOKEN 未配置", file=sys.stderr)
        return 1
    try:
        claimed = api_request(base_url, token, "/api/v1/research/tasks/claim/", {})
    except Exception as exc:
        print(f"领取研究任务失败：{exc}", file=sys.stderr)
        return 1
    task = claimed.get("task")
    if not task:
        print("没有待处理的 AI 研究任务")
        return 0
    print(f"开始研究任务 {task['id']}（{provider}）：{task['item']['title']}", flush=True)
    try:
        run = run_research(provider, build_prompt(task, prompt_path), schema_path, timeout)
        response = api_request(base_url, token, f"/api/v1/research/tasks/{task['id']}/complete/", {
            "lease_token": task["lease_token"], "provider": run.provider, "model_name": run.model_name,
            "result": run.result, "raw_output": run.raw_output, "usage": run.usage,
        })
        print(f"研究完成，已写入招聘条目 {response['item_id']}（{provider}；输入 {run.usage.get('input_tokens', '未报告')}，输出 {run.usage.get('output_tokens', '未报告')} Token）")
        if os.getenv("AI_RESEARCH_KEEP_EVENT_LOG", "false").lower() in {"1", "true", "yes"}:
            try:
                log_dir = PROJECT_ROOT / "work"
                log_dir.mkdir(exist_ok=True)
                log_path = log_dir / f"research-task-{task['id']}-{provider}.jsonl"
                log_path.write_text(run.event_log, encoding="utf-8")
                print(f"执行日志：{log_path}")
            except OSError as exc:
                print(f"快照已成功保存，但执行日志无法写入：{exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        try:
            api_request(base_url, token, f"/api/v1/research/tasks/{task['id']}/fail/", {
                "lease_token": task["lease_token"], "error": f"[{provider}] {exc}",
            })
        except Exception as callback_exc:
            print(f"回报失败状态时出错：{callback_exc}", file=sys.stderr)
        print(f"研究失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
