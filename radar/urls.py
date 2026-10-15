from django.urls import path

from . import api, views


urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("", views.job_list, name="job-list"),
    path("jobs/<int:pk>/", views.job_detail, name="job-detail"),
    path("jobs/<int:pk>/state/", views.item_state, name="item-state"),
    path("jobs/bulk-read-old/", views.bulk_mark_old_read, name="bulk-mark-old-read"),
    path("sync-now/", views.sync_now, name="sync-now"),
    path("api/v1/items/", api.api_items, name="api-items"),
    path("api/v1/items/<int:pk>/state/", api.api_item_state, name="api-item-state"),
    path("api/v1/sources/<slug:slug>/sync/", api.api_sync, name="api-sync"),
    path("api/v1/ingest/batch/", api.api_ingest, name="api-ingest"),
    path("api/v1/sync-runs/", api.api_sync_runs, name="api-sync-runs"),
    path("api/v1/research/tasks/claim/", api.api_research_claim, name="api-research-claim"),
    path(
        "api/v1/research/tasks/<int:pk>/complete/",
        api.api_research_complete,
        name="api-research-complete",
    ),
    path(
        "api/v1/research/tasks/<int:pk>/fail/",
        api.api_research_fail,
        name="api-research-fail",
    ),
]
