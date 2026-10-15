from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from radar.models import JobEvent
from radar.services.research import enqueue_research


class Command(BaseCommand):
    help = "将指定招聘条目加入一次性 AI 研究队列"

    def add_arguments(self, parser):
        parser.add_argument("--item-id", type=int)
        parser.add_argument("--today", action="store_true", help="选择今天首次发现的条目")
        parser.add_argument("--limit", type=int, default=1)

    def handle(self, *args, **options):
        if bool(options["item_id"]) == bool(options["today"]):
            raise CommandError("必须且只能指定 --item-id 或 --today")
        limit = max(1, min(options["limit"], 20))
        queryset = JobEvent.objects.filter(is_active=True)
        if options["item_id"]:
            queryset = queryset.filter(pk=options["item_id"])
        else:
            queryset = queryset.filter(first_seen_at__date=timezone.localdate()).order_by(
                "-first_seen_at", "-pk"
            )
        items = list(queryset[:limit])
        if not items:
            raise CommandError("没有找到符合条件的招聘条目")

        created_count = 0
        for item in items:
            task, created = enqueue_research(item, force=True)
            created_count += int(created)
            self.stdout.write(f"任务 {task.pk}: 条目 {item.pk} / {item.title} / {task.status}")
        self.stdout.write(self.style.SUCCESS(f"完成：选择 {len(items)} 条，新建任务 {created_count} 条"))
