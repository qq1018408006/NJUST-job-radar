from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from .forms import ResearchRangeForm

from .models import (
    JobEvent,
    JobEventChange,
    ResearchSnapshot,
    ResearchTask,
    Source,
    SyncRun,
    UserItemState,
)
from .services.research import enqueue_research


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "base_url", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name", "slug", "base_url")


@admin.register(JobEvent)
class JobEventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company",
        "event_start",
        "mode",
        "source_status",
        "is_active",
        "content_version",
        "first_seen_at",
    )
    list_filter = ("source", "mode", "source_status", "is_active", "event_date")
    search_fields = ("title", "company", "location", "school", "source_item_id")
    readonly_fields = (
        "content_hash",
        "content_version",
        "first_seen_at",
        "last_seen_at",
        "last_changed_at",
        "raw_data",
    )
    date_hierarchy = "event_date"


@admin.register(UserItemState)
class UserItemStateAdmin(admin.ModelAdmin):
    list_display = ("user", "item", "read_version", "read_at", "starred", "hidden", "updated_at")
    list_filter = ("starred", "hidden")
    search_fields = ("user__username", "item__title")


@admin.register(JobEventChange)
class JobEventChangeAdmin(admin.ModelAdmin):
    list_display = ("item", "version", "changed_at")
    list_filter = ("changed_at",)
    search_fields = ("item__title", "item__company")
    readonly_fields = ("item", "version", "changed_at", "changes")


@admin.register(ResearchTask)
class ResearchTaskAdmin(admin.ModelAdmin):
    change_list_template = "admin/radar/researchtask/change_list.html"
    list_display = ("item", "pipeline_version", "status", "attempts", "created_at", "finished_at")
    list_filter = ("status", "pipeline_version")
    search_fields = ("item__title", "item__company")
    readonly_fields = ("lease_token", "created_at", "updated_at")

    def get_urls(self):
        custom_urls = [
            path(
                "enqueue-range/",
                self.admin_site.admin_view(self.enqueue_range_view),
                name="radar_researchtask_enqueue_range",
            )
        ]
        return custom_urls + super().get_urls()

    def _range_items(self, cleaned_data):
        current_snapshot = ResearchSnapshot.objects.filter(
            item_id=OuterRef("pk"),
            pipeline_version=settings.AI_RESEARCH_PIPELINE_VERSION,
        )
        items = JobEvent.objects.select_related("source").annotate(
            has_current_ai_snapshot=Exists(current_snapshot)
        )
        date_from = cleaned_data["date_from"]
        date_to = cleaned_data["date_to"]
        if cleaned_data["date_basis"] == "event_date":
            items = items.filter(event_date__range=(date_from, date_to)).order_by(
                "event_date", "id"
            )
        else:
            items = items.filter(first_seen_at__date__range=(date_from, date_to)).order_by(
                "first_seen_at", "id"
            )
        if not cleaned_data["overwrite_existing"]:
            items = items.filter(has_current_ai_snapshot=False)
        return items.distinct()

    def _enqueue_selected_items(self, items):
        counters = {"created": 0, "already_queued": 0, "reset": 0}
        max_attempts = settings.AI_RESEARCH_MAX_ATTEMPTS
        with transaction.atomic():
            for item in items.iterator():
                task, created = enqueue_research(item, force=True)
                if created:
                    counters["created"] += 1
                    continue
                needs_reset = task.status in {
                    ResearchTask.Status.FAILED,
                    ResearchTask.Status.SUCCEEDED,
                } or (
                    task.status == ResearchTask.Status.PENDING
                    and task.attempts >= max_attempts
                )
                if not needs_reset:
                    counters["already_queued"] += 1
                    continue
                task.status = ResearchTask.Status.PENDING
                task.attempts = 0
                task.lease_token = ""
                task.started_at = None
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
                counters["reset"] += 1
        return counters

    def enqueue_range_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        today = timezone.localdate()
        initial = {
            "date_basis": "first_seen",
            "date_from": today,
            "date_to": today,
            "overwrite_existing": False,
        }
        form = ResearchRangeForm(request.POST or None, initial=initial)
        action = request.POST.get("action") if request.method == "POST" else ""
        previewed = False
        overwrite_existing = False
        candidate_count = 0
        existing_snapshot_count = 0
        already_queued_count = 0
        preview_items = []
        preview_limit = 100

        if request.method == "POST" and form.is_valid():
            overwrite_existing = form.cleaned_data["overwrite_existing"]
            items = self._range_items(form.cleaned_data)
            candidate_count = items.count()
            existing_snapshot_count = items.filter(has_current_ai_snapshot=True).count()
            already_queued_count = items.filter(
                research_tasks__pipeline_version=settings.AI_RESEARCH_PIPELINE_VERSION,
                research_tasks__status__in=[
                    ResearchTask.Status.PENDING,
                    ResearchTask.Status.RUNNING,
                ],
            ).distinct().count()
            preview_items = list(items[:preview_limit])
            previewed = True

            if action == "enqueue":
                if request.POST.get("confirmed") != "yes":
                    form.add_error(None, "请勾选最终确认后再加入队列。")
                else:
                    counters = self._enqueue_selected_items(items)
                    self.message_user(
                        request,
                        (
                            f"处理完成：新建 {counters['created']} 个任务，"
                            f"重新入队 {counters['reset']} 个任务，"
                            f"已有 {counters['already_queued']} 个任务正在等待或运行。"
                        ),
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(reverse("admin:radar_researchtask_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "按日期批量 AI 调研与总结",
            "form": form,
            "previewed": previewed,
            "candidate_count": candidate_count,
            "existing_snapshot_count": existing_snapshot_count,
            "already_queued_count": already_queued_count,
            "overwrite_existing": overwrite_existing,
            "preview_items": preview_items,
            "preview_limit": preview_limit,
            "pipeline_version": settings.AI_RESEARCH_PIPELINE_VERSION,
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request,
            "admin/radar/researchtask/enqueue_range.html",
            context,
        )


@admin.register(ResearchSnapshot)
class ResearchSnapshotAdmin(admin.ModelAdmin):
    list_display = ("item", "pipeline_version", "provider", "model_name", "generated_at")
    list_filter = ("provider", "pipeline_version", "generated_at")
    search_fields = ("item__title", "item__company")
    readonly_fields = [field.name for field in ResearchSnapshot._meta.fields]


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "trigger",
        "status",
        "started_at",
        "finished_at",
        "fetched_count",
        "created_count",
        "updated_count",
        "deactivated_count",
    )
    list_filter = ("status", "source", "trigger")
    readonly_fields = [field.name for field in SyncRun._meta.fields]
