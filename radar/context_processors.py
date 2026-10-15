from django.conf import settings


def app_context(request):
    return {
        "app_name": "求职雷达",
        "sync_enabled": settings.SYNC_ENABLED,
        "sync_schedule": settings.SYNC_SCHEDULE,
    }
