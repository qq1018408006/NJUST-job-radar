from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .models import JobEvent, Source, UserItemState
from .selectors import apply_item_filters, items_for_user
from .services.sync import sync_all_njust


def _change_value_display(field, value):
    if value in (None, ""):
        return "未提供"
    if field == "source_status":
        return {"0": "已举办", "1": "未举办", "2": "进行中", "10": "置顶中"}.get(
            str(value), str(value)
        )
    if field == "mode":
        return {"offline": "线下", "online": "线上", "unknown": "未知"}.get(
            str(value), str(value)
        )
    if field in {"event_date", "event_start", "event_end"}:
        return str(value).replace("T", " ")
    return str(value)


def _change_entries(item, previous_read_version):
    entries = []
    for change_record in item.changes.all()[:20]:
        fields = []
        for change in change_record.changes:
            field = change.get("field", "")
            fields.append(
                {
                    "label": change.get("label") or field or "内容",
                    "before": _change_value_display(field, change.get("before")),
                    "after": _change_value_display(field, change.get("after")),
                }
            )
        entries.append(
            {
                "version": change_record.version,
                "changed_at": change_record.changed_at,
                "fields": fields,
                "is_pending": change_record.version > previous_read_version,
            }
        )
    return entries


@require_GET
def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return HttpResponse("ok", content_type="text/plain")


@login_required
def job_list(request):
    queryset = items_for_user(request.user)
    active_visible = queryset.filter(is_active=True, user_hidden=False)
    today = timezone.localdate()
    old_unread_cutoff = today - timedelta(days=1)
    counts = {
        "all": active_visible.count(),
        "new": active_visible.filter(new_flag=1, unread_flag=1).count(),
        "updated": active_visible.filter(updated_flag=1).count(),
        "unread": active_visible.filter(unread_flag=1).count(),
        "upcoming": active_visible.filter(event_date__gte=today).count(),
        "starred": active_visible.filter(user_starred=True).count(),
        "hidden": queryset.filter(is_active=True, user_hidden=True).count(),
        "past": active_visible.filter(event_date__lt=today).count(),
        "old_unread": queryset.filter(
            is_active=True,
            event_date__lte=old_unread_cutoff,
            unread_flag=1,
        ).count(),
    }
    filtered = apply_item_filters(queryset, request.GET)
    paginator = Paginator(filtered, 25)
    page = paginator.get_page(request.GET.get("page"))
    for item in page.object_list:
        if item.user_read_version == 0:
            item.inbox_label = "新出现"
        elif item.content_version > item.user_read_version:
            item.inbox_label = "有更新"
        else:
            item.inbox_label = "已读"

    query = request.GET.copy()
    query.pop("page", None)
    current_view = request.GET.get("view", "all")
    request.session["last_job_list_url"] = request.get_full_path()
    return render(
        request,
        "radar/job_list.html",
        {
            "page": page,
            "counts": counts,
            "sources": Source.objects.filter(enabled=True),
            "current_view": current_view,
            "query_without_page": query.urlencode(),
            "filter_active": any(
                request.GET.get(name)
                for name in ("q", "mode", "source", "status", "date_from", "date_to")
            ),
        },
    )


@login_required
def job_detail(request, pk):
    item = get_object_or_404(JobEvent.objects.select_related("source"), pk=pk)
    state, _ = UserItemState.objects.get_or_create(user=request.user, item=item)
    previous_read_version = state.read_version
    change_entries = _change_entries(item, previous_read_version)
    has_unavailable_legacy_changes = (
        item.content_version > 1 and not item.changes.filter(version=2).exists()
    )
    research_snapshot = item.research_snapshots.order_by("-pipeline_version", "-generated_at").first()
    research_task = item.research_tasks.order_by("-pipeline_version", "-created_at").first()
    if state.read_version < item.content_version or not state.read_at:
        state.read_version = item.content_version
        state.read_at = timezone.now()
        state.save(update_fields=["read_version", "read_at", "updated_at"])
    back_url = request.session.get("last_job_list_url") or reverse("job-list")
    return render(
        request,
        "radar/job_detail.html",
        {
            "item": item,
            "state": state,
            "back_url": back_url,
            "change_entries": change_entries,
            "has_unavailable_legacy_changes": has_unavailable_legacy_changes,
            "research_snapshot": research_snapshot,
            "research_result": research_snapshot.result if research_snapshot else None,
            "research_task": research_task,
        },
    )


@login_required
@require_POST
def item_state(request, pk):
    item = get_object_or_404(JobEvent, pk=pk)
    state, _ = UserItemState.objects.get_or_create(user=request.user, item=item)
    action = request.POST.get("action")
    fields = []
    if action == "read":
        state.read_version = item.content_version
        state.read_at = timezone.now()
        fields = ["read_version", "read_at"]
    elif action == "unread":
        state.read_version = max(item.content_version - 1, 0)
        state.read_at = None
        fields = ["read_version", "read_at"]
    elif action == "star":
        state.starred = True
        fields = ["starred"]
    elif action == "unstar":
        state.starred = False
        fields = ["starred"]
    elif action == "hide":
        state.hidden = True
        fields = ["hidden"]
    elif action == "unhide":
        state.hidden = False
        fields = ["hidden"]
    elif action == "note":
        state.note = request.POST.get("note", "").strip()[:10000]
        fields = ["note"]
        messages.success(request, "备注已保存")
    if fields:
        state.save(update_fields=[*fields, "updated_at"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": bool(fields),
                "action": action,
                "item": item.pk,
                "read_version": state.read_version,
                "starred": state.starred,
                "hidden": state.hidden,
            }
        )
    next_url = request.POST.get("next") or reverse("job-list")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse("job-list")
    return HttpResponseRedirect(next_url)


@login_required
@require_POST
def bulk_mark_old_read(request):
    if request.POST.get("confirmed_twice") != "yes":
        return HttpResponseBadRequest("批量操作需要完成两次确认")

    cutoff = timezone.localdate() - timedelta(days=1)
    candidates = list(
        items_for_user(request.user)
        .filter(is_active=True, event_date__lte=cutoff, unread_flag=1)
        .values_list("pk", "content_version")
    )
    read_at = timezone.now()
    with transaction.atomic():
        for item_id, content_version in candidates:
            UserItemState.objects.update_or_create(
                user=request.user,
                item_id=item_id,
                defaults={"read_version": content_version, "read_at": read_at},
            )

    messages.success(request, f"已将昨天及以前的 {len(candidates)} 条未读信息整理为已读")
    next_url = request.POST.get("next") or reverse("job-list")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("job-list")
    return HttpResponseRedirect(next_url)


@staff_member_required
@require_POST
def sync_now(request):
    try:
        runs = sync_all_njust(trigger="web")
        successful = [run for run in runs if run.status == "success"]
        if successful:
            messages.success(
                request,
                "同步完成："
                f"新增 {sum(run.created_count for run in runs)}，"
                f"更新 {sum(run.updated_count for run in runs)}，"
                f"未变化 {sum(run.unchanged_count for run in runs)}",
            )
        else:
            messages.info(request, "同步任务已跳过，已有任务正在运行")
    except Exception as exc:
        messages.error(request, f"同步失败：{exc}")
    return redirect("job-list")
