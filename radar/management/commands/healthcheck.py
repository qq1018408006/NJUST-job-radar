from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Docker 健康检查"

    def handle(self, *args, **options):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone()[0] != 1:
                    raise RuntimeError("数据库返回异常")
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("ok")
