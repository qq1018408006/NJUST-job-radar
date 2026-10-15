from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from radar.models import JobEvent, ResearchSnapshot, ResearchTask, Source


@override_settings(AI_RESEARCH_PIPELINE_VERSION=2, AI_RESEARCH_MAX_ATTEMPTS=3)
class ResearchRangeAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.admin)
        self.source = Source.objects.create(
            name="测试源",
            slug="admin-range-test",
            base_url="https://example.com",
        )

    def create_item(self, item_id, title, first_seen, event_date=None):
        return JobEvent.objects.create(
            source=self.source,
            source_item_id=item_id,
            title=title,
            event_date=event_date,
            source_url=f"https://example.com/{item_id}",
            content_hash=f"hash-{item_id}",
            first_seen_at=timezone.make_aware(datetime.combine(first_seen, datetime.min.time())),
        )

    def test_task_changelist_links_to_range_enqueue_page(self):
        response = self.client.get(reverse("admin:radar_researchtask_changelist"))
        self.assertContains(response, "按日期批量 AI 研究")
        self.assertContains(response, reverse("admin:radar_researchtask_enqueue_range"))

    def test_preview_only_lists_items_without_current_snapshot_in_range(self):
        candidate = self.create_item("candidate", "范围内待研究", date(2026, 9, 20))
        summarized = self.create_item("summarized", "范围内已研究", date(2026, 9, 20))
        outside = self.create_item("outside", "范围外待研究", date(2026, 9, 19))
        task = ResearchTask.objects.create(
            item=summarized,
            pipeline_version=2,
            status=ResearchTask.Status.SUCCEEDED,
        )
        ResearchSnapshot.objects.create(
            item=summarized,
            task=task,
            pipeline_version=2,
            result={},
        )

        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "first_seen",
                "date_from": "2026-09-20",
                "date_to": "2026-09-20",
                "action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["candidate_count"], 1)
        self.assertContains(response, candidate.title)
        self.assertNotContains(response, summarized.title)
        self.assertNotContains(response, outside.title)
        self.assertFalse(ResearchTask.objects.filter(item=candidate).exists())

    def test_overwrite_preview_includes_existing_snapshots(self):
        candidate = self.create_item("candidate", "首次总结条目", date(2026, 9, 20))
        summarized = self.create_item("summarized", "覆盖总结条目", date(2026, 9, 20))
        task = ResearchTask.objects.create(
            item=summarized,
            pipeline_version=2,
            status=ResearchTask.Status.SUCCEEDED,
        )
        ResearchSnapshot.objects.create(
            item=summarized,
            task=task,
            pipeline_version=2,
            result={"marker": "old-snapshot"},
        )

        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "first_seen",
                "date_from": "2026-09-20",
                "date_to": "2026-09-20",
                "overwrite_existing": "on",
                "action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["candidate_count"], 2)
        self.assertEqual(response.context["existing_snapshot_count"], 1)
        self.assertTrue(response.context["overwrite_existing"])
        self.assertContains(response, candidate.title)
        self.assertContains(response, summarized.title)
        self.assertContains(response, "覆盖现有总结")

    def test_overwrite_confirm_requeues_completed_task_without_deleting_snapshot(self):
        summarized = self.create_item("summarized", "等待覆盖的总结", date(2026, 9, 20))
        task = ResearchTask.objects.create(
            item=summarized,
            pipeline_version=2,
            status=ResearchTask.Status.SUCCEEDED,
            attempts=2,
            finished_at=timezone.now(),
        )
        snapshot = ResearchSnapshot.objects.create(
            item=summarized,
            task=task,
            pipeline_version=2,
            result={"marker": "old-snapshot"},
        )

        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "first_seen",
                "date_from": "2026-09-20",
                "date_to": "2026-09-20",
                "overwrite_existing": "on",
                "action": "enqueue",
                "confirmed": "yes",
            },
        )

        self.assertRedirects(response, reverse("admin:radar_researchtask_changelist"))
        task.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(task.status, ResearchTask.Status.PENDING)
        self.assertEqual(task.attempts, 0)
        self.assertIsNone(task.finished_at)
        self.assertEqual(snapshot.result, {"marker": "old-snapshot"})

    def test_confirm_enqueues_new_items_and_resets_failed_tasks(self):
        new_item = self.create_item("new", "新建任务", date(2026, 9, 20))
        failed_item = self.create_item("failed", "重试失败任务", date(2026, 9, 20))
        queued_item = self.create_item("queued", "已经排队", date(2026, 9, 20))
        outside = self.create_item("outside", "范围外", date(2026, 9, 19))
        failed_task = ResearchTask.objects.create(
            item=failed_item,
            pipeline_version=2,
            status=ResearchTask.Status.FAILED,
            attempts=3,
            lease_token="old-lease",
            started_at=timezone.now(),
            finished_at=timezone.now(),
            error="旧错误",
        )
        queued_task = ResearchTask.objects.create(
            item=queued_item,
            pipeline_version=2,
            status=ResearchTask.Status.PENDING,
            attempts=1,
        )

        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "first_seen",
                "date_from": "2026-09-20",
                "date_to": "2026-09-20",
                "action": "enqueue",
                "confirmed": "yes",
            },
        )

        self.assertRedirects(response, reverse("admin:radar_researchtask_changelist"))
        new_task = ResearchTask.objects.get(item=new_item, pipeline_version=2)
        self.assertEqual(new_task.status, ResearchTask.Status.PENDING)
        failed_task.refresh_from_db()
        self.assertEqual(failed_task.status, ResearchTask.Status.PENDING)
        self.assertEqual(failed_task.attempts, 0)
        self.assertEqual(failed_task.lease_token, "")
        self.assertIsNone(failed_task.started_at)
        self.assertIsNone(failed_task.finished_at)
        self.assertEqual(failed_task.error, "")
        queued_task.refresh_from_db()
        self.assertEqual(queued_task.attempts, 1)
        self.assertFalse(ResearchTask.objects.filter(item=outside).exists())

    def test_enqueue_requires_explicit_confirmation(self):
        item = self.create_item("not-confirmed", "没有最终确认", date(2026, 9, 20))
        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "first_seen",
                "date_from": "2026-09-20",
                "date_to": "2026-09-20",
                "action": "enqueue",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请勾选最终确认后再加入队列")
        self.assertFalse(ResearchTask.objects.filter(item=item).exists())

    def test_event_date_basis_uses_event_date(self):
        selected = self.create_item(
            "event-date",
            "按举办日期命中",
            date(2026, 9, 1),
            event_date=date(2026, 10, 8),
        )
        self.create_item(
            "wrong-event-date",
            "举办日期未命中",
            date(2026, 10, 8),
            event_date=date(2026, 10, 9),
        )
        response = self.client.post(
            reverse("admin:radar_researchtask_enqueue_range"),
            {
                "date_basis": "event_date",
                "date_from": "2026-10-08",
                "date_to": "2026-10-08",
                "action": "preview",
            },
        )
        self.assertEqual(response.context["candidate_count"], 1)
        self.assertContains(response, selected.title)
