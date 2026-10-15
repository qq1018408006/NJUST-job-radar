from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from radar.models import (
    JobEvent,
    JobEventChange,
    ResearchSnapshot,
    ResearchTask,
    Source,
    UserItemState,
)


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader", password="pass-12345")
        source = Source.objects.create(name="测试源", slug="test", base_url="https://example.com")
        self.item = JobEvent.objects.create(
            source=source,
            source_item_id="1",
            title="测试招聘信息",
            event_date=date(2026, 10, 1),
            mode="offline",
            source_url="https://example.com/1",
            content_hash="hash",
        )

    def test_list_requires_login(self):
        response = self.client.get(reverse("job-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_detail_marks_current_version_read(self):
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        state = UserItemState.objects.get(user=self.user, item=self.item)
        self.assertEqual(state.read_version, self.item.content_version)
        self.assertIsNotNone(state.read_at)

    def test_updated_view_contains_item_after_new_version(self):
        UserItemState.objects.create(user=self.user, item=self.item, read_version=1)
        self.item.content_version = 2
        self.item.save(update_fields=["content_version"])
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-list"), {"view": "updated"})
        self.assertContains(response, "测试招聘信息")
        self.assertContains(response, "有更新")

    def test_detail_shows_field_level_changes_then_marks_version_read(self):
        UserItemState.objects.create(user=self.user, item=self.item, read_version=1)
        self.item.title = "修改后的招聘信息"
        self.item.content_version = 2
        self.item.save(update_fields=["title", "content_version"])
        JobEventChange.objects.create(
            item=self.item,
            version=2,
            changes=[
                {
                    "field": "title",
                    "label": "标题",
                    "before": "测试招聘信息",
                    "after": "修改后的招聘信息",
                }
            ],
        )

        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))

        self.assertContains(response, "具体更新内容")
        self.assertContains(response, "自上次阅读以来")
        self.assertContains(response, "测试招聘信息")
        self.assertContains(response, "修改后的招聘信息")
        state = UserItemState.objects.get(user=self.user, item=self.item)
        self.assertEqual(state.read_version, 2)

    def test_legacy_updated_item_explains_unavailable_early_diff(self):
        self.item.content_version = 2
        self.item.save(update_fields=["content_version"])
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))
        self.assertContains(response, "无法准确还原旧值")

    def test_read_star_hidden_and_note_states_are_isolated_per_user(self):
        other = get_user_model().objects.create_user(username="other", password="pass-12345")
        first_state = UserItemState.objects.create(
            user=self.user,
            item=self.item,
            read_version=1,
            starred=True,
            hidden=True,
            note="用户一的私人备注",
        )

        self.client.login(username="other", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "用户一的私人备注")

        other_state = UserItemState.objects.get(user=other, item=self.item)
        self.assertFalse(other_state.starred)
        self.assertFalse(other_state.hidden)
        self.assertEqual(other_state.note, "")
        first_state.refresh_from_db()
        self.assertTrue(first_state.starred)
        self.assertTrue(first_state.hidden)
        self.assertEqual(first_state.note, "用户一的私人备注")

    def test_detail_displays_completed_ai_research_snapshot(self):
        task = ResearchTask.objects.create(item=self.item, status=ResearchTask.Status.SUCCEEDED)
        ResearchSnapshot.objects.create(
            item=self.item,
            task=task,
            input_tokens=12000,
            provider="claude_cli",
            model_name="test-model",
            cached_input_tokens=8000,
            output_tokens=900,
            reasoning_output_tokens=100,
            result={
                "organization": {
                    "official_name": "测试科技股份有限公司",
                    "organization_type": "上市公司",
                    "listed_status": "已上市",
                    "stock_codes": ["000001.SZ"],
                    "overview": "这是一段经过公开资料整理的单位简介。",
                },
                "business_segments": [
                    {"name": "核心产品", "description": "主营业务说明", "source_ids": ["S1"]}
                ],
                "affiliation": {
                    "parent_organization": "测试集团",
                    "affiliation_chain": ["测试集团", "测试科技"],
                    "administrative_level": "二级单位",
                    "level_basis": "官网组织架构",
                    "source_ids": ["S1"],
                },
                "financials": [],
                "career_assessment": {
                    "development_outlook": "适合关注研发岗位。",
                    "growth_drivers": ["行业需求"],
                    "risks": ["周期波动"],
                    "job_seeker_takeaways": ["确认培养机制"],
                },
                "sources": [
                    {
                        "id": "S1",
                        "title": "测试公司官网",
                        "url": "https://example.com/company",
                        "publisher": "测试公司",
                        "published_at": "2026-01-01",
                        "accessed_at": "2026-09-20",
                    }
                ],
                "unknowns": [],
                "confidence": "中",
            },
        )
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))
        self.assertContains(response, "单位研究快照")
        self.assertContains(response, "Claude Code")
        self.assertContains(response, "test-model")
        self.assertContains(response, "测试科技股份有限公司")
        self.assertContains(response, "适合关注研发岗位")
        self.assertContains(response, "测试公司官网")
        self.assertContains(response, "本次 AI 用量")
        self.assertContains(response, "12000")
        self.assertContains(response, "900")
        self.assertContains(response, "12900")

    def test_detail_explains_missing_usage_for_legacy_snapshot(self):
        task = ResearchTask.objects.create(item=self.item, status=ResearchTask.Status.SUCCEEDED)
        ResearchSnapshot.objects.create(
            item=self.item,
            task=task,
            result={
                "organization": {},
                "business_segments": [],
                "affiliation": {},
                "financials": [],
                "career_assessment": {},
                "sources": [],
                "unknowns": [],
                "confidence": "低",
            },
        )
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-detail", args=[self.item.pk]))
        self.assertContains(response, "未记录用量")

    def test_list_read_action_has_ajax_and_anchor_fallback(self):
        self.client.login(username="reader", password="pass-12345")
        response = self.client.get(reverse("job-list"))
        self.assertContains(response, "data-read-form")
        self.assertContains(response, f"#item-{self.item.pk}")

    def test_ajax_read_action_returns_json_without_redirect(self):
        self.client.login(username="reader", password="pass-12345")
        response = self.client.post(
            reverse("item-state", args=[self.item.pk]),
            {"action": "read", "next": reverse("job-list")},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["action"], "read")
        state = UserItemState.objects.get(user=self.user, item=self.item)
        self.assertEqual(state.read_version, self.item.content_version)

    def test_bulk_old_read_requires_double_confirmation_marker(self):
        self.item.event_date = timezone.localdate() - timedelta(days=1)
        self.item.save(update_fields=["event_date"])
        self.client.login(username="reader", password="pass-12345")
        response = self.client.post(reverse("bulk-mark-old-read"), {"next": reverse("job-list")})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserItemState.objects.filter(user=self.user, item=self.item).exists())

    def test_bulk_old_read_marks_yesterday_but_not_today(self):
        today = timezone.localdate()
        self.item.event_date = today - timedelta(days=1)
        self.item.save(update_fields=["event_date"])
        today_item = JobEvent.objects.create(
            source=self.item.source,
            source_item_id="today",
            title="今天的招聘信息",
            event_date=today,
            mode="offline",
            source_url="https://example.com/today",
            content_hash="today-hash",
        )
        self.client.login(username="reader", password="pass-12345")
        response = self.client.post(
            reverse("bulk-mark-old-read"),
            {
                "next": reverse("job-list"),
                "confirmed_twice": "yes",
            },
        )
        self.assertRedirects(response, reverse("job-list"))
        old_state = UserItemState.objects.get(user=self.user, item=self.item)
        self.assertEqual(old_state.read_version, self.item.content_version)
        self.assertFalse(UserItemState.objects.filter(user=self.user, item=today_item).exists())
