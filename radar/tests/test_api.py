import json

from django.test import TestCase, override_settings
from django.urls import reverse

from radar.models import JobEvent, ResearchSnapshot, ResearchTask, Source


def research_result():
    return {
        "organization": {
            "official_name": "测试科技股份有限公司",
            "organization_type": "上市公司",
            "listed_status": "已上市",
            "stock_codes": ["000001.SZ"],
            "overview": "测试单位简介",
        },
        "business_segments": [],
        "affiliation": {
            "parent_organization": "",
            "affiliation_chain": [],
            "administrative_level": "",
            "level_basis": "",
            "source_ids": [],
        },
        "financials": [],
        "career_assessment": {
            "development_outlook": "测试发展判断",
            "growth_drivers": [],
            "risks": [],
            "job_seeker_takeaways": [],
        },
        "sources": [],
        "unknowns": [],
        "confidence": "中",
    }


@override_settings(APP_API_TOKEN="test-secret")
class ApiTests(TestCase):
    def test_ingest_requires_authentication(self):
        response = self.client.post(reverse("api-ingest"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_token_can_ingest_and_query_records(self):
        headers = {"HTTP_AUTHORIZATION": "Bearer test-secret"}
        payload = {
            "source": "custom",
            "source_name": "自定义源",
            "base_url": "https://example.com",
            "records": [
                {
                    "source_item_id": "abc",
                    "title": "外部导入职位",
                    "source_url": "https://example.com/abc",
                    "mode": "online",
                    "event_date": "2026-10-03",
                }
            ],
        }
        response = self.client.post(
            reverse("api-ingest"),
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(JobEvent.objects.count(), 1)

        response = self.client.get(reverse("api-items"), **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["title"], "外部导入职位")

    def test_research_worker_claim_and_complete_requires_token(self):
        source = Source.objects.create(name="测试源", slug="research", base_url="https://example.com")
        item = JobEvent.objects.create(
            source=source,
            source_item_id="research-1",
            title="测试科技校园招聘",
            company="测试科技股份有限公司",
            source_url="https://example.com/research-1",
            content_hash="hash",
        )
        ResearchTask.objects.create(item=item)

        denied = self.client.post(reverse("api-research-claim"), data="{}", content_type="application/json")
        self.assertEqual(denied.status_code, 401)

        headers = {"HTTP_AUTHORIZATION": "Bearer test-secret"}
        claimed = self.client.post(
            reverse("api-research-claim"), data="{}", content_type="application/json", **headers
        )
        task_data = claimed.json()["task"]
        self.assertEqual(task_data["item"]["id"], item.pk)

        completed = self.client.post(
            reverse("api-research-complete", args=[task_data["id"]]),
            data=json.dumps(
                {
                    "lease_token": task_data["lease_token"],
                    "provider": "codex_cli",
                    "model_name": "test-model",
                    "result": research_result(),
                    "usage": {
                        "input_tokens": 24763,
                        "cached_input_tokens": 24448,
                        "output_tokens": 122,
                        "reasoning_output_tokens": 0,
                    },
                }
            ),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        snapshot = ResearchSnapshot.objects.get()
        self.assertEqual(snapshot.result["confidence"], "中")
        self.assertEqual(snapshot.input_tokens, 24763)
        self.assertEqual(snapshot.cached_input_tokens, 24448)
        self.assertEqual(snapshot.output_tokens, 122)
        self.assertEqual(snapshot.reasoning_output_tokens, 0)
        self.assertEqual(snapshot.total_tokens, 24885)
        task = ResearchTask.objects.get()
        self.assertEqual(task.status, ResearchTask.Status.SUCCEEDED)

        empty = self.client.post(
            reverse("api-research-claim"), data="{}", content_type="application/json", **headers
        )
        self.assertIsNone(empty.json()["task"])
