from datetime import date, datetime, timedelta, timezone as datetime_timezone

from django.test import TestCase, override_settings

from radar.models import JobEvent, JobEventChange, ResearchTask, Source
from radar.services.sync import upsert_records


def record(item_id="one", title="第一场", status="0"):
    return {
        "source_item_id": item_id,
        "title": title,
        "company": "测试单位",
        "event_date": date(2026, 10, 1),
        "event_start": None,
        "event_end": None,
        "mode": "offline",
        "location": "A101",
        "school": "测试大学",
        "source_status": status,
        "contact_name": "",
        "contact_phone": "",
        "source_url": f"https://example.com/{item_id}",
        "description_text": "",
        "raw_data": {"id": item_id},
    }


class SyncTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(name="测试源", slug="test", base_url="https://example.com")

    def test_upsert_is_idempotent_and_increments_version_on_change(self):
        first = upsert_records(self.source, [record()], trigger="test")
        self.assertEqual(first.created_count, 1)
        item = JobEvent.objects.get()
        self.assertEqual(item.content_version, 1)

        second = upsert_records(self.source, [record()], trigger="test")
        self.assertEqual(second.unchanged_count, 1)
        item.refresh_from_db()
        self.assertEqual(item.content_version, 1)

        third = upsert_records(self.source, [record(title="标题已修改")], trigger="test")
        self.assertEqual(third.updated_count, 1)
        item.refresh_from_db()
        self.assertEqual(item.title, "标题已修改")
        self.assertEqual(item.content_version, 2)
        change = JobEventChange.objects.get(item=item, version=2)
        self.assertEqual(
            change.changes,
            [
                {
                    "field": "title",
                    "label": "标题",
                    "before": "第一场",
                    "after": "标题已修改",
                }
            ],
        )

    @override_settings(AI_RESEARCH_ENABLED=True, AI_RESEARCH_PIPELINE_VERSION=1)
    def test_new_item_enqueues_exactly_one_research_task(self):
        upsert_records(self.source, [record()], trigger="test")
        upsert_records(self.source, [record()], trigger="test")
        task = ResearchTask.objects.get()
        self.assertEqual(task.item, JobEvent.objects.get())
        self.assertEqual(task.status, ResearchTask.Status.PENDING)
        self.assertEqual(task.pipeline_version, 1)

    def test_completion_status_only_does_not_create_new_content_version(self):
        upsert_records(self.source, [record(status="1")], trigger="test")
        item = JobEvent.objects.get()
        original_changed_at = item.last_changed_at
        original_hash = item.content_hash

        run = upsert_records(self.source, [record(status="0")], trigger="test")
        item.refresh_from_db()
        self.assertEqual(run.updated_count, 1)
        self.assertEqual(item.source_status, "0")
        self.assertNotEqual(item.content_hash, original_hash)
        self.assertEqual(item.content_version, 1)
        self.assertEqual(item.last_changed_at, original_changed_at)
        self.assertFalse(JobEventChange.objects.filter(item=item).exists())

        repeated = upsert_records(self.source, [record(status="0")], trigger="test")
        self.assertEqual(repeated.unchanged_count, 1)

    def test_completion_with_other_change_creates_new_version(self):
        upsert_records(self.source, [record(status="1")], trigger="test")
        upsert_records(self.source, [record(title="标题也变了", status="0")], trigger="test")
        item = JobEvent.objects.get()
        self.assertEqual(item.content_version, 2)
        self.assertEqual(item.title, "标题也变了")
        change = JobEventChange.objects.get(item=item, version=2)
        changed_fields = {entry["field"] for entry in change.changes}
        self.assertEqual(changed_fields, {"title", "source_status"})

    def test_status_only_transition_to_in_progress_does_not_create_version(self):
        upsert_records(self.source, [record(status="1")], trigger="test")
        upsert_records(self.source, [record(status="2")], trigger="test")
        item = JobEvent.objects.get()
        self.assertEqual(item.source_status, "2")
        self.assertEqual(item.content_version, 1)
        self.assertFalse(JobEventChange.objects.filter(item=item).exists())

    def test_change_history_does_not_report_equivalent_timezone_values(self):
        first = record()
        first["event_start"] = datetime(2026, 9, 22, 8, tzinfo=datetime_timezone.utc)
        upsert_records(self.source, [first], trigger="test")

        second = record()
        second["event_start"] = datetime(
            2026,
            9,
            22,
            16,
            tzinfo=datetime_timezone(timedelta(hours=8)),
        )
        second["location"] = "第二报告厅"
        upsert_records(self.source, [second], trigger="test")

        change = JobEventChange.objects.get(version=2)
        self.assertEqual([entry["field"] for entry in change.changes], ["location"])

    @override_settings(MISSING_RUNS_TO_INACTIVE=2)
    def test_missing_item_is_deactivated_only_after_threshold(self):
        upsert_records(self.source, [record()], trigger="test")
        upsert_records(self.source, [], trigger="test")
        item = JobEvent.objects.get()
        self.assertTrue(item.is_active)
        self.assertEqual(item.missed_runs, 1)

        run = upsert_records(self.source, [], trigger="test")
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertEqual(run.deactivated_count, 1)
