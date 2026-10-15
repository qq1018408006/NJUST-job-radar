#!/usr/bin/env python3
"""Compatibility entry point for existing systemd services; honors AI_RESEARCH_PROVIDER."""

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ai_research_worker import api_request, build_prompt, load_env, main
from tools.research_providers import parse_codex_usage, run_research


def run_codex(task, schema_path, timeout_seconds):
    run = run_research("codex", build_prompt(task), schema_path, timeout_seconds)
    return run.result, run.raw_output, run.event_log, run.usage


if __name__ == "__main__":
    raise SystemExit(main())
