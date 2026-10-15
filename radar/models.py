from django.conf import settings
from django.db import models
from django.utils import timezone


class Source(models.Model):
    name = models.CharField("名称", max_length=120)
    slug = models.SlugField("标识", max_length=80, unique=True)
    base_url = models.URLField("来源地址")
    enabled = models.BooleanField("启用", default=True)
    config = models.JSONField("配置", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "数据源"
        verbose_name_plural = "数据源"

    def __str__(self):
        return self.name


class JobEvent(models.Model):
    class Mode(models.TextChoices):
        OFFLINE = "offline", "线下"
        ONLINE = "online", "线上"
        UNKNOWN = "unknown", "未知"

    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="items", verbose_name="数据源")
    source_item_id = models.CharField("来源记录 ID", max_length=120)
    title = models.CharField("标题", max_length=500)
    company = models.CharField("单位", max_length=300, blank=True)
    event_date = models.DateField("举办日期", null=True, blank=True, db_index=True)
    event_start = models.DateTimeField("开始时间", null=True, blank=True, db_index=True)
    event_end = models.DateTimeField("结束时间", null=True, blank=True)
    mode = models.CharField("形式", max_length=16, choices=Mode.choices, default=Mode.UNKNOWN, db_index=True)
    location = models.CharField("地点", max_length=500, blank=True)
    school = models.CharField("学校", max_length=300, blank=True)
    source_status = models.CharField("来源状态", max_length=40, blank=True, db_index=True)
    contact_name = models.CharField("联系人", max_length=120, blank=True)
    contact_phone = models.CharField("联系电话", max_length=120, blank=True)
    source_url = models.URLField("原始页面", max_length=1000)
    description_text = models.TextField("详情文本", blank=True)
    raw_data = models.JSONField("原始数据", default=dict, blank=True)
    content_hash = models.CharField("内容哈希", max_length=64)
    content_version = models.PositiveIntegerField("内容版本", default=1)
    first_seen_at = models.DateTimeField("首次发现", default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField("最近发现", default=timezone.now, db_index=True)
    last_changed_at = models.DateTimeField("最近变更", default=timezone.now, db_index=True)
    is_active = models.BooleanField("仍在来源中", default=True, db_index=True)
    missed_runs = models.PositiveIntegerField("连续缺失次数", default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "source_item_id"], name="unique_source_item")
        ]
        ordering = ["event_start", "-first_seen_at"]
        indexes = [
            models.Index(fields=["source", "is_active", "event_date"]),
            models.Index(fields=["is_active", "event_start"]),
            models.Index(fields=["content_version", "last_changed_at"]),
        ]
        verbose_name = "招聘活动"
        verbose_name_plural = "招聘活动"

    def __str__(self):
        return self.title

    @property
    def source_status_label(self):
        return {
            "0": "已举办",
            "1": "未举办",
            "2": "进行中",
            "10": "置顶中",
        }.get(self.source_status, self.source_status or "未知")


class UserItemState(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_states")
    item = models.ForeignKey(JobEvent, on_delete=models.CASCADE, related_name="user_states")
    read_version = models.PositiveIntegerField("已读版本", default=0)
    read_at = models.DateTimeField("阅读时间", null=True, blank=True)
    starred = models.BooleanField("收藏", default=False)
    hidden = models.BooleanField("隐藏", default=False)
    note = models.TextField("备注", blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "item"], name="unique_user_item_state")]
        indexes = [
            models.Index(fields=["user", "starred"]),
            models.Index(fields=["user", "hidden"]),
        ]
        verbose_name = "用户阅读状态"
        verbose_name_plural = "用户阅读状态"

    def __str__(self):
        return f"{self.user} / {self.item}"


class JobEventChange(models.Model):
    item = models.ForeignKey(
        JobEvent,
        on_delete=models.CASCADE,
        related_name="changes",
        verbose_name="招聘活动",
    )
    version = models.PositiveIntegerField("内容版本")
    changed_at = models.DateTimeField("变更时间", default=timezone.now, db_index=True)
    changes = models.JSONField("字段变更", default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="unique_item_change_version")
        ]
        ordering = ["-version"]
        verbose_name = "招聘活动变更"
        verbose_name_plural = "招聘活动变更"

    def __str__(self):
        return f"{self.item} / v{self.version}"


class ResearchTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待研究"
        RUNNING = "running", "研究中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"

    item = models.ForeignKey(
        JobEvent,
        on_delete=models.CASCADE,
        related_name="research_tasks",
        verbose_name="招聘活动",
    )
    pipeline_version = models.PositiveIntegerField("研究流程版本", default=1)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField("尝试次数", default=0)
    lease_token = models.CharField("任务租约", max_length=64, blank=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)
    error = models.TextField("错误信息", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "pipeline_version"],
                name="unique_item_research_pipeline",
            )
        ]
        ordering = ["created_at"]
        verbose_name = "AI 研究任务"
        verbose_name_plural = "AI 研究任务"

    def __str__(self):
        return f"{self.item} / AI v{self.pipeline_version} / {self.get_status_display()}"


class ResearchSnapshot(models.Model):
    item = models.ForeignKey(
        JobEvent,
        on_delete=models.CASCADE,
        related_name="research_snapshots",
        verbose_name="招聘活动",
    )
    task = models.OneToOneField(
        ResearchTask,
        on_delete=models.CASCADE,
        related_name="snapshot",
        verbose_name="研究任务",
    )
    pipeline_version = models.PositiveIntegerField("研究流程版本", default=1)
    provider = models.CharField("执行方式", max_length=40, default="codex_cli")
    model_name = models.CharField("模型", max_length=120, blank=True)
    generated_at = models.DateTimeField("生成时间", default=timezone.now, db_index=True)
    input_tokens = models.PositiveBigIntegerField("输入 Token", null=True, blank=True)
    cached_input_tokens = models.PositiveBigIntegerField("缓存输入 Token", null=True, blank=True)
    output_tokens = models.PositiveBigIntegerField("输出 Token", null=True, blank=True)
    reasoning_output_tokens = models.PositiveBigIntegerField(
        "推理输出 Token",
        null=True,
        blank=True,
    )
    result = models.JSONField("结构化研究结果", default=dict)
    raw_output = models.TextField("原始输出", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "pipeline_version"],
                name="unique_item_research_snapshot",
            )
        ]
        ordering = ["-generated_at"]
        verbose_name = "AI 研究快照"
        verbose_name_plural = "AI 研究快照"

    def __str__(self):
        return f"{self.item} / AI 快照 v{self.pipeline_version}"

    @property
    def provider_label(self):
        return {"codex_cli": "Codex", "claude_cli": "Claude Code", "opencode_cli": "OpenCode"}.get(
            self.provider, self.provider
        )

    @property
    def has_token_usage(self):
        return self.input_tokens is not None and self.output_tokens is not None

    @property
    def total_tokens(self):
        if not self.has_token_usage:
            return None
        return self.input_tokens + self.output_tokens


class SyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        SKIPPED = "skipped", "跳过"

    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="sync_runs")
    trigger = models.CharField("触发方式", max_length=40, default="manual")
    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.RUNNING, db_index=True)
    started_at = models.DateTimeField("开始时间", default=timezone.now, db_index=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    fetched_count = models.PositiveIntegerField("获取数量", default=0)
    created_count = models.PositiveIntegerField("新增数量", default=0)
    updated_count = models.PositiveIntegerField("更新数量", default=0)
    unchanged_count = models.PositiveIntegerField("未变数量", default=0)
    deactivated_count = models.PositiveIntegerField("失效数量", default=0)
    message = models.TextField("信息", blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "同步记录"
        verbose_name_plural = "同步记录"

    def __str__(self):
        source_name = self.source.name if self.source else "未知数据源"
        return f"{source_name} {self.started_at:%Y-%m-%d %H:%M}"
