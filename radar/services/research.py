from django.conf import settings

from radar.models import ResearchTask


def enqueue_research(item, *, force=False):
    if not force and not settings.AI_RESEARCH_ENABLED:
        return None, False
    return ResearchTask.objects.get_or_create(
        item=item,
        pipeline_version=settings.AI_RESEARCH_PIPELINE_VERSION,
    )
