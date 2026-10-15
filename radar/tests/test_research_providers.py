import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from tools import ai_research_worker as worker
from tools.research_providers import (
    ResearchRun, parse_claude_usage, parse_opencode_output, resolve_provider,
    run_research, validate_result, validate_sources,
)


SCHEMA_PATH = worker.PROJECT_ROOT / "research.schema.json"


def sample_result():
    return {
        "organization": {"official_name": "测试单位", "organization_type": "企业", "listed_status": "非上市",
                         "stock_codes": [], "overview": "测试简介", "source_ids": ["S1"]},
        "business_segments": [{"name": "研发", "description": "测试业务", "source_ids": ["S1"]}],
        "affiliation": {"parent_organization": "", "affiliation_chain": [], "administrative_level": "",
                        "level_basis": "", "source_ids": []},
        "financials": [],
        "career_assessment": {"development_outlook": "待核验", "growth_drivers": [], "risks": [],
                              "job_seeker_takeaways": [], "source_ids": ["S1"]},
        "sources": [{"id": "S1", "title": "测试官网", "url": "https://example.com/organization",
                     "publisher": "测试单位", "published_at": "", "accessed_at": "2026-10-14"}],
        "unknowns": [], "confidence": "中",
    }


def opencode_events(result):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in [
        {"type": "text", "part": {"messageID": "search", "text": "我将开始检索"}},
        {"type": "step_finish", "part": {"id": "step1", "messageID": "search", "reason": "tool-calls",
            "tokens": {"input": 100, "output": 20, "reasoning": 10, "cache": {"read": 500, "write": 200}}}},
        {"type": "text", "part": {"messageID": "final", "text": json.dumps(result, ensure_ascii=False)}},
        {"type": "step_finish", "part": {"id": "step2", "messageID": "final", "reason": "stop",
            "tokens": {"input": 200, "output": 60, "reasoning": 0, "cache": {"read": 600, "write": 0}}}},
    ])


class ResearchProviderTests(SimpleTestCase):
    def test_claude_includes_both_cache_buckets_in_input(self):
        self.assertEqual(parse_claude_usage({"usage": {
            "input_tokens": 100, "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 200, "output_tokens": 80,
        }}), {"input_tokens": 800, "cached_input_tokens": 500, "output_tokens": 80})
        self.assertEqual(parse_claude_usage({}), {})

    def test_opencode_uses_final_answer_and_all_research_steps(self):
        result, usage = parse_opencode_output(opencode_events(sample_result()))
        self.assertEqual(result, sample_result())
        self.assertEqual(usage, {
            "input_tokens": 1600, "cached_input_tokens": 1100,
            "output_tokens": 80, "reasoning_output_tokens": 10,
        })

    def test_opencode_deduplicates_replayed_steps(self):
        log = opencode_events(sample_result())
        _, usage = parse_opencode_output(log + "\n" + log.splitlines()[-1])
        self.assertEqual(usage["output_tokens"], 80)

    def test_opencode_rejects_error_and_incomplete_runs(self):
        for log in [json.dumps({"type": "error", "error": {"message": "offline"}}),
                    '\n'.join(opencode_events(sample_result()).splitlines()[:2])]:
            with self.subTest(log=log[:40]), self.assertRaises(RuntimeError):
                parse_opencode_output(log)

    def test_opencode_missing_final_usage_does_not_report_partial_usage_as_total(self):
        log = '\n'.join(opencode_events(sample_result()).splitlines()[:-1])
        result, usage = parse_opencode_output(log)
        self.assertEqual(result, sample_result())
        self.assertEqual(usage, {})

    def test_nested_schema_validation_and_source_checks(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        result = sample_result()
        validate_result(result, schema)
        validate_sources(result)
        result["business_segments"][0]["source_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "不存在"):
            validate_sources(result)
        result["business_segments"][0]["description"] = {"bad": "object"}
        with self.assertRaisesRegex(ValueError, "description"):
            validate_result(result, schema)

    def test_source_urls_and_confidence_are_checked(self):
        result = sample_result()
        result["sources"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            validate_sources(result)
        result["sources"] = []
        result["organization"]["source_ids"] = []
        result["business_segments"] = []
        result["career_assessment"]["source_ids"] = []
        with self.assertRaises(ValueError):
            validate_sources(result)
        result["confidence"] = "低"
        validate_sources(result)

    @patch("tools.research_providers.shutil.which", return_value="/fake/cli")
    def test_per_provider_model_overrides_global_and_opencode_format_is_checked(self, which):
        with patch.dict(os.environ, {"AI_RESEARCH_MODEL": "global", "AI_RESEARCH_CLAUDE_MODEL": "specific"}, clear=True):
            self.assertEqual(resolve_provider("claude")[1], "specific")
            with self.assertRaisesRegex(ValueError, "provider/model"):
                resolve_provider("opencode")

    @patch("tools.research_providers.resolve_provider", return_value=("/fake/cli", "test/model"))
    def test_all_three_cli_adapters_return_validated_metadata(self, resolve):
        def fake_run(command, **kwargs):
            self.assertIn("开始", kwargs["input"])
            self.assertNotIn("shell", kwargs)
            if "exec" in command:
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text(json.dumps(sample_result()), encoding="utf-8")
                data = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}})
                self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            elif "--print" in command:
                self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
                data = json.dumps({"subtype": "success", "is_error": False, "structured_output": sample_result(),
                                   "usage": {"input_tokens": 12, "output_tokens": 3}})
            else:
                config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
                self.assertEqual(config["permission"], {"*": "deny", "websearch": "allow", "webfetch": "allow"})
                self.assertIn("JSON Schema", kwargs["input"])
                data = opencode_events(sample_result())
            return subprocess.CompletedProcess(command, 0, stdout=data, stderr="")

        with patch("tools.research_providers.subprocess.run", side_effect=fake_run):
            for provider in ("codex", "claude", "opencode"):
                with self.subTest(provider=provider):
                    run = run_research(provider, "开始研究", SCHEMA_PATH, 123)
                    self.assertEqual(run.provider, f"{provider}_cli")
                    self.assertEqual(run.model_name, "test/model")
                    self.assertEqual(run.result, sample_result())
                    self.assertIn("output_tokens", run.usage)

    @patch("tools.research_providers.resolve_provider", return_value=("/fake/claude", ""))
    def test_claude_zero_exit_with_error_is_not_success(self, resolve):
        with patch("tools.research_providers.subprocess.run", return_value=subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"is_error": True, "subtype": "error_max_turns"}), stderr=""
        )), self.assertRaisesRegex(RuntimeError, "error_max_turns"):
            run_research("claude", "研究", SCHEMA_PATH, 10)


class ResearchWorkerTests(SimpleTestCase):
    @patch("tools.ai_research_worker.check_provider", return_value=("/fake/claude", ""))
    @patch("tools.ai_research_worker.api_request")
    @patch("tools.ai_research_worker.run_research")
    def test_env_defaults_are_resolved_after_loading_and_metadata_is_submitted(self, research, api, checked):
        task = {"id": 9, "lease_token": "lease", "item": {"title": "测试招聘"}}
        api.side_effect = [{"task": task}, {"item_id": 7}]
        research.return_value = ResearchRun(sample_result(), "{}", "", {"input_tokens": 12, "output_tokens": 3}, "claude_cli", "actual-model")
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("AI_RESEARCH_PROVIDER=claude\nAI_RESEARCH_TIMEOUT_SECONDS=17\nJOB_RADAR_BASE_URL=http://example.test:9999\nAPP_API_TOKEN=test-only-token\n")
            self.assertEqual(worker.main(["--env-file", str(env_file)]), 0)
        checked.assert_called_once_with("claude")
        self.assertEqual(research.call_args.args[0], "claude")
        self.assertEqual(research.call_args.args[3], 17)
        self.assertEqual(api.call_args.args[0], "http://example.test:9999")
        self.assertEqual(api.call_args.args[3]["provider"], "claude_cli")
        self.assertEqual(api.call_args.args[3]["model_name"], "actual-model")

    @patch("tools.ai_research_worker.api_request")
    @patch("tools.ai_research_worker.check_provider", side_effect=RuntimeError("CLI missing"))
    def test_missing_cli_does_not_claim_or_consume_attempts(self, checked, api):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(worker.main(["--env-file", "/nonexistent", "--provider", "opencode"]), 1)
        api.assert_not_called()

    @patch("tools.ai_research_worker.api_request")
    @patch("tools.ai_research_worker.run_research")
    @patch("tools.ai_research_worker.check_provider", return_value=("/fake/codex", ""))
    def test_check_and_cli_override_do_not_call_model_or_api(self, checked, research, api):
        with patch.dict(os.environ, {"AI_RESEARCH_PROVIDER": "claude"}, clear=True):
            self.assertEqual(worker.main(["--env-file", "/nonexistent", "--provider", "codex", "--check"]), 0)
        checked.assert_called_once_with("codex")
        research.assert_not_called()
        api.assert_not_called()

    @patch("tools.ai_research_worker.check_provider", return_value=("/fake/cli", ""))
    @patch("tools.ai_research_worker.api_request")
    @patch("tools.ai_research_worker.run_research", side_effect=ValueError("invalid output"))
    def test_invalid_results_report_failure_instead_of_completing(self, research, api, checked):
        api.side_effect = [{"task": {"id": 9, "lease_token": "lease", "item": {"title": "测试"}}}, {"ok": True}]
        with patch.dict(os.environ, {"APP_API_TOKEN": "test-only-token"}, clear=True):
            self.assertEqual(worker.main(["--env-file", "/nonexistent"]), 1)
        self.assertTrue(api.call_args.args[2].endswith("/fail/"))
        self.assertIn("[codex]", api.call_args.args[3]["error"])
