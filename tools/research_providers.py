"""Local CLI adapters, using the Python standard library only."""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROVIDERS = ("codex", "claude", "opencode")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


@dataclass
class ResearchRun:
    result: dict
    raw_output: str
    event_log: str
    usage: dict
    provider: str
    model_name: str


def _events(event_log):
    for line in event_log.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict):
            yield event


def _count(value):
    return value if type(value) is int and value >= 0 else None


def parse_codex_usage(event_log):
    usage = None
    for event in _events(event_log):
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return {key: value for key in USAGE_FIELDS if (value := _count((usage or {}).get(key))) is not None}


def parse_claude_usage(response):
    usage = response.get("usage") or {}
    result = {}
    # Claude reports uncached input, cache reads, and cache writes separately.
    base = _count(usage.get("input_tokens"))
    read = _count(usage.get("cache_read_input_tokens"))
    write = _count(usage.get("cache_creation_input_tokens"))
    if base is not None:
        result["input_tokens"] = base + (read or 0) + (write or 0)
    if read is not None:
        result["cached_input_tokens"] = read
    if (output := _count(usage.get("output_tokens"))) is not None:
        result["output_tokens"] = output
    return result


def parse_opencode_output(event_log):
    texts = {}
    last_message = None
    usage = {}
    seen_steps = set()
    final_reason = None
    finished_message = None
    for event in _events(event_log):
        if event.get("type") == "error":
            raise RuntimeError(f"OpenCode 返回错误：{json.dumps(event.get('error'), ensure_ascii=False)}")
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            last_message = part.get("messageID") or "final"
            texts.setdefault(last_message, []).append(part["text"])
        if event.get("type") != "step_finish":
            continue
        final_reason = part.get("reason")
        finished_message = part.get("messageID")
        step_id = part.get("id")
        if step_id and step_id in seen_steps:
            continue
        if step_id:
            seen_steps.add(step_id)
        tokens = part.get("tokens") or {}
        cache = tokens.get("cache") or {}
        base = _count(tokens.get("input"))
        read = _count(cache.get("read"))
        write = _count(cache.get("write"))
        mapped = {
            "input_tokens": base + (read or 0) + (write or 0) if base is not None else None,
            "cached_input_tokens": read,
            "output_tokens": _count(tokens.get("output")),
            "reasoning_output_tokens": _count(tokens.get("reasoning")),
        }
        for key, value in mapped.items():
            if value is not None:
                usage[key] = usage.get(key, 0) + value
    if final_reason not in {None, "stop", "tool-calls"}:
        raise RuntimeError(f"OpenCode 未正常完成（最后停止原因：{final_reason}）")
    # Some CLI versions omit the closing step_finish. A complete final JSON can
    # still be validated; partial step usage must not be reported as the total.
    if final_reason != "stop":
        if finished_message and finished_message == last_message:
            raise RuntimeError("OpenCode 最后一步仍为工具调用，未返回最终结果")
        usage = {}
    text = "".join(texts.get(last_message, [])).strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    if not text:
        raise RuntimeError("OpenCode 未返回最终 JSON 文本")
    return json.loads(text), usage


def validate_result(value, schema, path="result"):
    """Validate the object/array/string/enum subset used by research.schema.json."""
    expected = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str}
    if expected not in kinds:
        raise ValueError(f"不支持的研究 Schema 类型：{expected}")
    if not isinstance(value, kinds[expected]):
        raise ValueError(f"{path} 必须为 {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在允许的取值内")
    if expected == "object":
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path} 缺少字段：{', '.join(sorted(missing))}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError(f"{path} 包含 Schema 未允许的字段")
        for key, child in value.items():
            if key in properties:
                validate_result(child, properties[key], f"{path}.{key}")
    elif expected == "array":
        for index, child in enumerate(value):
            validate_result(child, schema["items"], f"{path}[{index}]")


def validate_sources(result):
    source_ids = [source["id"] for source in result["sources"]]
    if any(not value for value in source_ids) or len(source_ids) != len(set(source_ids)):
        raise ValueError("sources 的 id 必须非空且唯一")
    for source in result["sources"]:
        parsed = urlparse(source["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("sources.url 必须是完整 HTTP/HTTPS 地址")

    def visit(value):
        if isinstance(value, dict):
            if "source_ids" in value and set(value["source_ids"]) - set(source_ids):
                raise ValueError("source_ids 引用了不存在的 sources.id")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    if not source_ids and result["confidence"] != "低":
        raise ValueError("没有可核验来源时，confidence 必须为低")


def resolve_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError(f"AI_RESEARCH_PROVIDER 必须是 {', '.join(PROVIDERS)}")
    configured_bin = os.getenv(f"AI_RESEARCH_{provider.upper()}_BIN", provider).strip() or provider
    executable = shutil.which(configured_bin)
    if not executable:
        raise RuntimeError(f"找不到 {provider} 命令：请安装 CLI 或配置 AI_RESEARCH_{provider.upper()}_BIN")
    model = os.getenv(f"AI_RESEARCH_{provider.upper()}_MODEL", "").strip() or os.getenv("AI_RESEARCH_MODEL", "").strip()
    if provider == "opencode" and model and "/" not in model:
        raise ValueError("OpenCode 模型需使用 provider/model 格式")
    return executable, model


def check_provider(provider):
    executable, model = resolve_provider(provider)
    help_args = {"codex": ["exec", "--help"], "claude": ["--help"], "opencode": ["run", "--help"]}
    required = {
        "codex": ["--output-schema", "--ephemeral", "--ignore-user-config", "--ignore-rules"],
        "claude": ["--json-schema", "--no-session-persistence", "--tools", "--permission-mode"],
        "opencode": ["--format", "--model", "--agent"],
    }
    checked = subprocess.run([executable, *help_args[provider]], capture_output=True, text=True, timeout=15, check=False)
    output = checked.stdout + checked.stderr
    if checked.returncode or any(flag not in output for flag in required[provider]):
        raise RuntimeError(f"{provider} CLI 版本不兼容，请更新 CLI；需要参数：{', '.join(required[provider])}")
    return executable, model


def _run(command, prompt, temp_dir, timeout_seconds, env=None):
    completed = subprocess.run(
        command, input=prompt, text=True, encoding="utf-8", stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, cwd=temp_dir, timeout=timeout_seconds, check=False, env=env,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout)[-4000:]
        raise RuntimeError(f"{Path(command[0]).name} 退出码 {completed.returncode}: {detail}")
    return completed


def run_research(provider, prompt, schema_path, timeout_seconds):
    executable, model = resolve_provider(provider)
    schema_path = Path(schema_path).resolve()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="job-radar-research-") as temp_dir:
        if provider == "codex":
            output_path = Path(temp_dir) / "result.json"
            command = [
                executable, "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
                "--ignore-user-config", "--ignore-rules", "-c", 'web_search="live"',
                "-c", 'approval_policy="never"', "-c", "features.multi_agent=false",
                "--output-schema", str(schema_path), "--output-last-message", str(output_path), "--json",
            ]
            if model:
                command += ["--model", model]
            command.append("-")
            completed = _run(command, prompt, temp_dir, timeout_seconds)
            if not output_path.exists():
                raise RuntimeError("Codex 未生成结构化结果文件")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            usage = parse_codex_usage(completed.stdout)
        elif provider == "claude":
            command = [
                executable, "--print", "--output-format", "json", "--json-schema", json.dumps(schema),
                "--no-session-persistence", "--permission-mode", "dontAsk", "--tools", "WebSearch,WebFetch",
                "--allowedTools", "WebSearch,WebFetch", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--setting-sources", "", "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
            ]
            if model:
                command += ["--model", model]
            completed = _run(command, prompt, temp_dir, timeout_seconds)
            response = json.loads(completed.stdout)
            if response.get("is_error") or response.get("subtype") != "success":
                raise RuntimeError(f"Claude 研究失败：{response.get('subtype', '未知错误')} {response.get('errors', '')}")
            if any(entry.get("tool_name") in {"WebSearch", "WebFetch"} for entry in response.get("permission_denials", [])):
                raise RuntimeError("Claude 联网工具权限被拒绝，请检查 CLI/账号配置")
            result = response.get("structured_output")
            usage = parse_claude_usage(response)
            actual_models = list((response.get("modelUsage") or {}).keys())
            model = model or ", ".join(actual_models)
        else:
            # --format json emits events, not a schema-constrained final object.
            prompt += "\n\n输出必须符合以下 JSON Schema：\n" + json.dumps(schema, ensure_ascii=False)
            permissions = {"*": "deny", "websearch": "allow", "webfetch": "allow"}
            config = {
                "share": "disabled", "autoupdate": False, "permission": permissions,
                "agent": {"job-radar-research": {
                    "description": "Research employers using public web sources only",
                    "mode": "primary", "permission": permissions,
                    "prompt": "只使用 websearch 和 webfetch。返回符合用户 Schema 的最终 JSON，不运行命令或读取本机文件。",
                }},
            }
            env = os.environ.copy()
            env.update({
                "OPENCODE_CONFIG_CONTENT": json.dumps(config), "OPENCODE_PERMISSION": json.dumps(permissions),
                "OPENCODE_ENABLE_EXA": "1", "OPENCODE_AUTO_SHARE": "false",
                "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true",
            })
            command = [executable, "run", "--format", "json", "--agent", "job-radar-research", "--title", "Job Radar employer research"]
            if model:
                command += ["--model", model]
            completed = _run(command, prompt, temp_dir, timeout_seconds, env=env)
            result, usage = parse_opencode_output(completed.stdout)

    validate_result(result, schema)
    validate_sources(result)
    return ResearchRun(
        result=result, raw_output=json.dumps(result, ensure_ascii=False), event_log=completed.stdout,
        usage=usage, provider=f"{provider}_cli", model_name=model or "configured-default",
    )
