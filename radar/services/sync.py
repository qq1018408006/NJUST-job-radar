import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import date, datetime

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from radar.models import JobEvent, JobEventChange, Source, SyncRun

from .njust import NjustClient, normalize_record, normalize_recruitment_record
from .research import enqueue_research


SYNC_LOCK_ID = 923_880_217
_local_lock = threading.Lock()

CHANGE_FIELD_LABELS = {
    "title": "标题",
    "company": "单位",
    "event_date": "举办日期",
    "event_start": "开始时间",
    "event_end": "结束时间",
    "mode": "形式",
    "location": "地点",
    "school": "学校",
    "source_status": "来源状态",
    "contact_name": "联系人",
    "contact_phone": "联系电话",
    "source_url": "原始页面",
    "description_text": "详情",
}


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def content_hash(record: dict) -> str:
    relevant = {key: value for key, value in record.items() if key not in {"raw_data"}}
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_status_only_change(item: JobEvent, record: dict) -> bool:
    """Return true when only the upstream lifecycle status changed."""
    new_status = str(record.get("source_status") or "")
    if item.source_status == new_status:
        return False
    record_with_previous_status = {**record, "source_status": item.source_status}
    return item.content_hash == content_hash(record_with_previous_status)


def _change_json_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def collect_field_changes(item: JobEvent, record: dict) -> list[dict]:
    """Build a JSON-safe field-level diff before mutating the item."""
    changes = []
    for field, label in CHANGE_FIELD_LABELS.items():
        if field not in record:
            continue
        before = _change_json_value(getattr(item, field))
        after = _change_json_value(record[field])
        if before == after:
            continue
        changes.append(
            {
                "field": field,
                "label": label,
                "before": before,
                "after": after,
            }
        )
    return changes


@contextmanager
def sync_lock():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [SYNC_LOCK_ID])
            acquired = bool(cursor.fetchone()[0])
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [SYNC_LOCK_ID])
    else:
        acquired = _local_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _local_lock.release()


def get_njust_source() -> Source:
    source, _ = Source.objects.get_or_create(
        slug="njust-91job",
        defaults={
            "name": "南京理工大学 · 宣讲会",
            "base_url": settings.NJUST_BASE_URL,
            "config": {"school_code": settings.NJUST_SCHOOL_CODE, "category": "lecture"},
        },
    )
    changed = False
    if source.name != "南京理工大学 · 宣讲会":
        source.name = "南京理工大学 · 宣讲会"
        changed = True
    if source.base_url != settings.NJUST_BASE_URL:
        source.base_url = settings.NJUST_BASE_URL
        changed = True
    expected_config = {
        **source.config,
        "school_code": settings.NJUST_SCHOOL_CODE,
        "category": "lecture",
    }
    if source.config != expected_config:
        source.config = expected_config
        changed = True
    if changed:
        source.save(update_fields=["name", "base_url", "config", "updated_at"])
    return source


def get_njust_recruitment_source() -> Source:
    source, _ = Source.objects.get_or_create(
        slug="njust-91job-recruitment",
        defaults={
            "name": "南京理工大学 · 招聘会",
            "base_url": settings.NJUST_BASE_URL,
            "config": {"school_code": settings.NJUST_SCHOOL_CODE, "category": "recruitment"},
        },
    )
    changed = False
    if source.name != "南京理工大学 · 招聘会":
        source.name = "南京理工大学 · 招聘会"
        changed = True
    if source.base_url != settings.NJUST_BASE_URL:
        source.base_url = settings.NJUST_BASE_URL
        changed = True
    expected_config = {
        **source.config,
        "school_code": settings.NJUST_SCHOOL_CODE,
        "category": "recruitment",
    }
    if source.config != expected_config:
        source.config = expected_config
        changed = True
    if changed:
        source.save(update_fields=["name", "base_url", "config", "updated_at"])
    return source


def upsert_records(source: Source, normalized_records: list[dict], trigger: str = "manual") -> SyncRun:
    now = timezone.now()
    run = SyncRun.objects.create(source=source, trigger=trigger, status=SyncRun.Status.RUNNING)
    counters = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    seen_ids = []
    try:
        with transaction.atomic():
            for record in normalized_records:
                source_item_id = str(record["source_item_id"])
                seen_ids.append(source_item_id)
                new_hash = content_hash(record)
                defaults = {**record, "content_hash": new_hash, "first_seen_at": now, "last_seen_at": now, "last_changed_at": now}
                defaults.pop("source_item_id", None)
                item, created = JobEvent.objects.get_or_create(
                    source=source,
                    source_item_id=source_item_id,
                    defaults=defaults,
                )
                if created:
                    enqueue_research(item)
                    counters["created"] += 1
                    continue

                changed = item.content_hash != new_hash
                update_fields = ["last_seen_at", "is_active", "missed_runs"]
                item.last_seen_at = now
                item.is_active = True
                item.missed_runs = 0
                if changed:
                    status_only_change = is_status_only_change(item, record)
                    field_changes = collect_field_changes(item, record)
                    for field, value in record.items():
                        if field != "source_item_id":
                            setattr(item, field, value)
                            update_fields.append(field)
                    item.content_hash = new_hash
                    update_fields.append("content_hash")
                    if not status_only_change:
                        item.content_version += 1
                        item.last_changed_at = now
                        update_fields.extend(["content_version", "last_changed_at"])
                    counters["updated"] += 1
                else:
                    counters["unchanged"] += 1
                item.save(update_fields=list(dict.fromkeys(update_fields)))
                if changed and not status_only_change:
                    JobEventChange.objects.create(
                        item=item,
                        version=item.content_version,
                        changed_at=now,
                        changes=field_changes,
                    )

            missing = source.items.filter(is_active=True).exclude(source_item_id__in=seen_ids)
            threshold = settings.MISSING_RUNS_TO_INACTIVE
            counters["deactivated"] = missing.filter(missed_runs__gte=threshold - 1).count()
            missing.update(missed_runs=F("missed_runs") + 1)
            source.items.filter(is_active=True, missed_runs__gte=threshold).update(is_active=False)

        run.status = SyncRun.Status.SUCCESS
        run.finished_at = timezone.now()
        run.fetched_count = len(normalized_records)
        run.created_count = counters["created"]
        run.updated_count = counters["updated"]
        run.unchanged_count = counters["unchanged"]
        run.deactivated_count = counters["deactivated"]
        run.message = "同步完成"
        run.save()
        return run
    except Exception as exc:
        run.status = SyncRun.Status.FAILED
        run.finished_at = timezone.now()
        run.message = str(exc)[:4000]
        run.save(update_fields=["status", "finished_at", "message"])
        raise


def sync_njust(trigger: str = "manual") -> SyncRun:
    source = get_njust_source()
    with sync_lock() as acquired:
        if not acquired:
            return SyncRun.objects.create(
                source=source,
                trigger=trigger,
                status=SyncRun.Status.SKIPPED,
                finished_at=timezone.now(),
                message="已有同步任务在运行",
            )
        client = NjustClient()
        raw_records = client.fetch_records()
        normalized = []
        for raw in raw_records:
            detail = {}
            if settings.FETCH_DETAILS:
                detail = client.fetch_detail(str(raw.get("xjhid")))
            normalized.append(normalize_record({**raw, **detail}, client.base_url, client.school_code))
        return upsert_records(source, normalized, trigger=trigger)


def sync_njust_recruitment(trigger: str = "manual") -> SyncRun:
    source = get_njust_recruitment_source()
    with sync_lock() as acquired:
        if not acquired:
            return SyncRun.objects.create(
                source=source,
                trigger=trigger,
                status=SyncRun.Status.SKIPPED,
                finished_at=timezone.now(),
                message="已有同步任务在运行",
            )
        client = NjustClient()
        raw_records = client.fetch_recruitment_records()
        normalized = []
        for raw in raw_records:
            detail = {}
            if settings.FETCH_DETAILS:
                detail = client.fetch_recruitment_detail(str(raw.get("zphid")))
            normalized.append(
                normalize_recruitment_record({**raw, **detail}, client.base_url, client.school_code)
            )
        return upsert_records(source, normalized, trigger=trigger)


def sync_all_njust(trigger: str = "manual") -> list[SyncRun]:
    return [
        sync_njust(trigger=trigger),
        sync_njust_recruitment(trigger=trigger),
    ]


def ingest_batch(source: Source, records: list[dict], trigger: str = "api") -> SyncRun:
    required = {"source_item_id", "title", "source_url"}
    cleaned = []
    for index, record in enumerate(records):
        missing = required - set(record)
        if missing:
            raise ValueError(f"第 {index + 1} 条记录缺少字段：{', '.join(sorted(missing))}")
        cleaned.append(
            {
                "source_item_id": str(record["source_item_id"]),
                "title": str(record["title"]).strip(),
                "company": str(record.get("company") or "").strip(),
                "event_date": record.get("event_date"),
                "event_start": record.get("event_start"),
                "event_end": record.get("event_end"),
                "mode": record.get("mode") if record.get("mode") in {"online", "offline", "unknown"} else "unknown",
                "location": str(record.get("location") or "").strip(),
                "school": str(record.get("school") or "").strip(),
                "source_status": str(record.get("source_status") or ""),
                "contact_name": str(record.get("contact_name") or "").strip(),
                "contact_phone": str(record.get("contact_phone") or "").strip(),
                "source_url": str(record["source_url"]),
                "description_text": str(record.get("description_text") or "").strip(),
                "raw_data": record.get("raw_data") or record,
            }
        )
    return upsert_records(source, cleaned, trigger=trigger)
