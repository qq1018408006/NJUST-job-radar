import hmac
import json
import uuid
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import JobEvent, ResearchSnapshot, ResearchTask, Source, SyncRun, UserItemState
from .services.sync import ingest_batch, sync_all_njust, sync_njust, sync_njust_recruitment


def _has_api_token(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    supplied = header.removeprefix("Bearer ").strip()
    expected = settings.APP_API_TOKEN
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def api_authorized(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated or _has_api_token(request):
            return view(request, *args, **kwargs)
        return JsonResponse({"error": "authentication_required"}, status=401)

    return wrapped


def api_token_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if _has_api_token(request):
            return view(request, *args, **kwargs)
        return JsonResponse({"error": "api_token_required"}, status=401)

    return wrapped


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError("JSON 格式无效") from exc


def _serialize_item(item):
    return {
        "id": item.id,
        "source": item.source.slug,
        "source_item_id": item.source_item_id,
        "title": item.title,
        "company": item.company,
        "event_date": item.event_date.isoformat() if item.event_date else None,
        "event_start": item.event_start.isoformat() if item.event_start else None,
        "event_end": item.event_end.isoformat() if item.event_end else None,
        "mode": item.mode,
        "location": item.location,
        "school": item.school,
        "source_status": item.source_status,
        "source_url": item.source_url,
        "content_version": item.content_version,
        "first_seen_at": item.first_seen_at.isoformat(),
        "last_seen_at": item.last_seen_at.isoformat(),
        "last_changed_at": item.last_changed_at.isoformat(),
        "is_active": item.is_active,
    }


@require_http_methods(["GET"])
@api_authorized
def api_items(request):
    limit = max(1, min(int(request.GET.get("limit", "100")), 200))
    offset = max(0, int(request.GET.get("offset", "0")))
    queryset = JobEvent.objects.select_related("source").all()
    if request.GET.get("source"):
        queryset = queryset.filter(source__slug=request.GET["source"])
    if request.GET.get("active", "true").lower() in {"true", "1", "yes"}:
        queryset = queryset.filter(is_active=True)
    if request.GET.get("q"):
        from django.db.models import Q

        q = request.GET["q"]
        queryset = queryset.filter(Q(title__icontains=q) | Q(company__icontains=q) | Q(location__icontains=q))
    total = queryset.count()
    items = queryset.order_by("event_start", "-first_seen_at")[offset : offset + limit]
    return JsonResponse({"total": total, "limit": limit, "offset": offset, "items": [_serialize_item(i) for i in items]})


@csrf_exempt
@require_http_methods(["POST"])
@api_authorized
def api_sync(request, slug="njust-91job"):
    if slug not in {"njust", "njust-91job", "njust-91job-recruitment"}:
        return JsonResponse({"error": "unknown_source"}, status=404)
    try:
        if slug == "njust":
            runs = sync_all_njust(trigger="api")
        elif slug == "njust-91job":
            runs = [sync_njust(trigger="api")]
        else:
            runs = [sync_njust_recruitment(trigger="api")]
    except Exception as exc:
        return JsonResponse({"status": "failed", "message": str(exc)}, status=502)
    return JsonResponse(
        {
            "status": "success" if any(run.status == "success" for run in runs) else "skipped",
            "fetched": sum(run.fetched_count for run in runs),
            "created": sum(run.created_count for run in runs),
            "updated": sum(run.updated_count for run in runs),
            "unchanged": sum(run.unchanged_count for run in runs),
            "deactivated": sum(run.deactivated_count for run in runs),
            "runs": [
                {
                    "id": run.id,
                    "source": run.source.slug if run.source else None,
                    "status": run.status,
                    "message": run.message,
                }
                for run in runs
            ],
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@api_authorized
def api_ingest(request):
    try:
        body = _json_body(request)
        records = body.get("records")
        if not isinstance(records, list) or not records or len(records) > 1000:
            raise ValueError("records 必须是 1–1000 条记录的数组")
        source_slug = str(body.get("source") or "external").strip()
        source, _ = Source.objects.get_or_create(
            slug=source_slug,
            defaults={
                "name": str(body.get("source_name") or source_slug),
                "base_url": str(body.get("base_url") or "https://example.invalid"),
            },
        )
        run = ingest_batch(source, records, trigger="api-ingest")
        return JsonResponse({"status": run.status, "created": run.created_count, "updated": run.updated_count})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@require_http_methods(["GET"])
@api_authorized
def api_sync_runs(request):
    rows = SyncRun.objects.select_related("source")[:50]
    return JsonResponse(
        {
            "runs": [
                {
                    "id": row.id,
                    "source": row.source.slug if row.source else None,
                    "trigger": row.trigger,
                    "status": row.status,
                    "started_at": row.started_at.isoformat(),
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                    "fetched": row.fetched_count,
                    "created": row.created_count,
                    "updated": row.updated_count,
                    "deactivated": row.deactivated_count,
                    "message": row.message,
                }
                for row in rows
            ]
        }
    )


@require_http_methods(["POST", "PATCH"])
@login_required
def api_item_state(request, pk):
    item = get_object_or_404(JobEvent, pk=pk)
    state, _ = UserItemState.objects.get_or_create(user=request.user, item=item)
    try:
        body = _json_body(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if "starred" in body:
        state.starred = bool(body["starred"])
    if "hidden" in body:
        state.hidden = bool(body["hidden"])
    if "note" in body:
        state.note = str(body["note"])[:10000]
    if body.get("read") is True:
        state.read_version = item.content_version
        state.read_at = timezone.now()
    elif body.get("read") is False:
        state.read_version = max(item.content_version - 1, 0)
        state.read_at = None
    state.save()
    return JsonResponse(
        {
            "item": item.id,
            "read_version": state.read_version,
            "read_at": state.read_at.isoformat() if state.read_at else None,
            "starred": state.starred,
            "hidden": state.hidden,
            "note": state.note,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@api_token_required
def api_research_claim(request):
    now = timezone.now()
    lease_cutoff = now - timedelta(minutes=settings.AI_RESEARCH_LEASE_MINUTES)
    max_attempts = settings.AI_RESEARCH_MAX_ATTEMPTS

    with transaction.atomic():
        ResearchTask.objects.filter(
            status=ResearchTask.Status.RUNNING,
            started_at__lt=lease_cutoff,
            attempts__lt=max_attempts,
        ).update(status=ResearchTask.Status.PENDING, lease_token="", error="任务租约超时，等待重试")
        ResearchTask.objects.filter(
            status=ResearchTask.Status.RUNNING,
            started_at__lt=lease_cutoff,
            attempts__gte=max_attempts,
        ).update(
            status=ResearchTask.Status.FAILED,
            lease_token="",
            finished_at=now,
            error="任务租约超时且已达到最大尝试次数",
        )
        task = (
            ResearchTask.objects.select_for_update()
            .select_related("item", "item__source")
            .filter(status=ResearchTask.Status.PENDING, attempts__lt=max_attempts)
            .order_by("created_at")
            .first()
        )
        if task is None:
            return JsonResponse({"task": None})
        task.status = ResearchTask.Status.RUNNING
        task.attempts += 1
        task.lease_token = uuid.uuid4().hex
        task.started_at = now
        task.finished_at = None
        task.error = ""
        task.save(
            update_fields=[
                "status",
                "attempts",
                "lease_token",
                "started_at",
                "finished_at",
                "error",
                "updated_at",
            ]
        )

    item = task.item
    return JsonResponse(
        {
            "task": {
                "id": task.pk,
                "lease_token": task.lease_token,
                "pipeline_version": task.pipeline_version,
                "attempt": task.attempts,
                "item": {
                    "id": item.pk,
                    "title": item.title,
                    "company": item.company,
                    "source_name": item.source.name,
                    "source_url": item.source_url,
                    "event_date": item.event_date.isoformat() if item.event_date else None,
                    "location": item.location,
                    "school": item.school,
                    "description": item.description_text[:12000],
                },
            }
        }
    )


def _research_task_from_lease(pk, lease_token):
    return ResearchTask.objects.select_related("item").filter(
        pk=pk,
        status=ResearchTask.Status.RUNNING,
        lease_token=lease_token,
    ).first()


def _valid_research_result(result):
    required = {
        "organization",
        "business_segments",
        "affiliation",
        "financials",
        "career_assessment",
        "sources",
        "unknowns",
        "confidence",
    }
    return isinstance(result, dict) and required.issubset(result) and isinstance(result.get("sources"), list)


RESEARCH_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def _parse_research_usage(body):
    usage = body.get("usage")
    if usage is None:
        return {field: None for field in RESEARCH_USAGE_FIELDS}
    if not isinstance(usage, dict):
        raise ValueError("usage 必须是对象")
    parsed = {}
    for field in RESEARCH_USAGE_FIELDS:
        value = usage.get(field)
        if value is None:
            parsed[field] = None
            continue
        if type(value) is not int or value < 0 or value > 9_223_372_036_854_775_807:
            raise ValueError(f"usage.{field} 必须是非负整数")
        parsed[field] = value
    return parsed


@csrf_exempt
@require_http_methods(["POST"])
@api_token_required
def api_research_complete(request, pk):
    try:
        body = _json_body(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    task = _research_task_from_lease(pk, str(body.get("lease_token") or ""))
    if task is None:
        return JsonResponse({"error": "invalid_or_expired_lease"}, status=409)
    result = body.get("result")
    if not _valid_research_result(result):
        return JsonResponse({"error": "invalid_research_result"}, status=400)
    try:
        usage = _parse_research_usage(body)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    now = timezone.now()
    with transaction.atomic():
        ResearchSnapshot.objects.update_or_create(
            item=task.item,
            pipeline_version=task.pipeline_version,
            defaults={
                "task": task,
                "provider": str(body.get("provider") or "codex_cli")[:40],
                "model_name": str(body.get("model_name") or "")[:120],
                "generated_at": now,
                **usage,
                "result": result,
                "raw_output": str(body.get("raw_output") or "")[:100000],
            },
        )
        task.status = ResearchTask.Status.SUCCEEDED
        task.finished_at = now
        task.lease_token = ""
        task.error = ""
        task.save(update_fields=["status", "finished_at", "lease_token", "error", "updated_at"])
    return JsonResponse({"ok": True, "item_id": task.item_id})


@csrf_exempt
@require_http_methods(["POST"])
@api_token_required
def api_research_fail(request, pk):
    try:
        body = _json_body(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    task = _research_task_from_lease(pk, str(body.get("lease_token") or ""))
    if task is None:
        return JsonResponse({"error": "invalid_or_expired_lease"}, status=409)

    retry = task.attempts < settings.AI_RESEARCH_MAX_ATTEMPTS
    task.status = ResearchTask.Status.PENDING if retry else ResearchTask.Status.FAILED
    task.finished_at = None if retry else timezone.now()
    task.lease_token = ""
    task.error = str(body.get("error") or "未知错误")[:4000]
    task.save(update_fields=["status", "finished_at", "lease_token", "error", "updated_at"])
    return JsonResponse({"ok": True, "will_retry": retry})
