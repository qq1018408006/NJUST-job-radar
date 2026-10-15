from django.core.management.base import BaseCommand, CommandError

from radar.services.sync import sync_all_njust, sync_njust, sync_njust_recruitment


class Command(BaseCommand):
    help = "同步指定招聘数据源"

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", default="njust")
        parser.add_argument("--trigger", default="cli")

    def handle(self, *args, **options):
        source = options["source"]
        if source not in {"njust", "njust-91job", "njust-91job-recruitment", "recruitment"}:
            raise CommandError(f"未知数据源：{options['source']}")
        if source == "njust":
            runs = sync_all_njust(trigger=options["trigger"])
        elif source == "njust-91job":
            runs = [sync_njust(trigger=options["trigger"])]
        else:
            runs = [sync_njust_recruitment(trigger=options["trigger"])]
        failed = next((run for run in runs if run.status == "failed"), None)
        if failed:
            raise CommandError(failed.message)
        self.stdout.write(
            self.style.SUCCESS(
                "成功："
                f"获取 {sum(run.fetched_count for run in runs)}，"
                f"新增 {sum(run.created_count for run in runs)}，"
                f"更新 {sum(run.updated_count for run in runs)}"
            )
        )
