import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from radar.services.sync import sync_all_njust


class Command(BaseCommand):
    help = "运行轻量定时同步循环"

    def _run(self, trigger):
        try:
            runs = sync_all_njust(trigger=trigger)
            self.stdout.write(
                "同步完成："
                f"获取 {sum(run.fetched_count for run in runs)}，"
                f"新增 {sum(run.created_count for run in runs)}，"
                f"更新 {sum(run.updated_count for run in runs)}"
            )
        except Exception as exc:
            self.stderr.write(f"同步失败：{exc}")

    def handle(self, *args, **options):
        if not settings.SYNC_ENABLED:
            self.stdout.write("定时同步已禁用")
            return
        schedule = set(settings.SYNC_SCHEDULE)
        self.stdout.write(f"定时同步已启动：{', '.join(sorted(schedule))}")
        if settings.RUN_SYNC_ON_STARTUP:
            self._run("startup")
        last_key = None
        while True:
            now = timezone.localtime()
            minute = now.strftime("%H:%M")
            key = now.strftime("%Y-%m-%d %H:%M")
            if minute in schedule and key != last_key:
                last_key = key
                self._run("schedule")
            time.sleep(20)
