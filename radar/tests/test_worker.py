import json

from django.test import SimpleTestCase

from tools.codex_research_worker import parse_codex_usage


class CodexWorkerUsageTests(SimpleTestCase):
    def test_parse_usage_from_final_completed_turn(self):
        event_log = "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                "not-json",
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 24763,
                            "cached_input_tokens": 24448,
                            "output_tokens": 122,
                            "reasoning_output_tokens": 0,
                        },
                    }
                ),
            ]
        )
        self.assertEqual(
            parse_codex_usage(event_log),
            {
                "input_tokens": 24763,
                "cached_input_tokens": 24448,
                "output_tokens": 122,
                "reasoning_output_tokens": 0,
            },
        )

    def test_missing_usage_returns_empty_object(self):
        self.assertEqual(parse_codex_usage('{"type":"turn.started"}'), {})
