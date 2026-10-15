from django import forms


class ResearchRangeForm(forms.Form):
    DATE_BASIS_CHOICES = (
        ("first_seen", "首次入库日期"),
        ("event_date", "举办日期"),
    )

    date_basis = forms.ChoiceField(
        label="日期口径",
        choices=DATE_BASIS_CHOICES,
        initial="first_seen",
        help_text="推荐按首次入库日期补齐；按举办日期时，没有举办日期的条目不会被选中。",
    )
    date_from = forms.DateField(
        label="开始日期",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="结束日期",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    overwrite_existing = forms.BooleanField(
        label="覆盖已有总结",
        required=False,
        help_text=(
            "勾选后会选择日期范围内的全部招聘信息；已有 AI 快照会在新调研成功后被替换。"
            "默认关闭。"
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("开始日期不能晚于结束日期。")
        return cleaned_data
