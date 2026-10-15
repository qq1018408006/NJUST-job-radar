from django.db.models import (
    BooleanField,
    Case,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import JobEvent, UserItemState


def items_for_user(user):
    states = UserItemState.objects.filter(user=user, item=OuterRef("pk"))
    today = timezone.localdate()
    return (
        JobEvent.objects.select_related("source")
        .annotate(
            user_read_version=Coalesce(
                Subquery(states.values("read_version")[:1], output_field=IntegerField()),
                Value(0),
            ),
            user_starred=Coalesce(
                Subquery(states.values("starred")[:1], output_field=BooleanField()),
                Value(False),
            ),
            user_hidden=Coalesce(
                Subquery(states.values("hidden")[:1], output_field=BooleanField()),
                Value(False),
            ),
            unread_flag=Case(
                When(content_version__gt=F("user_read_version"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            new_flag=Case(
                When(user_read_version=0, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            updated_flag=Case(
                When(user_read_version__gt=0, content_version__gt=F("user_read_version"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            future_rank=Case(
                When(event_date__gte=today, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
    )


def apply_item_filters(queryset, params):
    today = timezone.localdate()
    view = params.get("view", "all")
    if view == "inactive":
        queryset = queryset.filter(is_active=False)
    else:
        queryset = queryset.filter(is_active=True)

    if view == "new":
        queryset = queryset.filter(new_flag=1, unread_flag=1, user_hidden=False)
    elif view == "updated":
        queryset = queryset.filter(updated_flag=1, user_hidden=False)
    elif view == "unread":
        queryset = queryset.filter(unread_flag=1, user_hidden=False)
    elif view == "upcoming":
        queryset = queryset.filter(event_date__gte=today, user_hidden=False)
    elif view == "starred":
        queryset = queryset.filter(user_starred=True, user_hidden=False)
    elif view == "hidden":
        queryset = queryset.filter(user_hidden=True)
    elif view == "past":
        queryset = queryset.filter(event_date__lt=today, user_hidden=False)
    elif view != "inactive":
        queryset = queryset.filter(user_hidden=False)

    q = params.get("q", "").strip()
    if q:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(company__icontains=q)
            | Q(location__icontains=q)
            | Q(school__icontains=q)
        )
    if params.get("mode") in {"online", "offline", "unknown"}:
        queryset = queryset.filter(mode=params["mode"])
    if params.get("source"):
        queryset = queryset.filter(source__slug=params["source"])
    if params.get("status"):
        queryset = queryset.filter(source_status=params["status"])
    if params.get("date_from"):
        queryset = queryset.filter(event_date__gte=params["date_from"])
    if params.get("date_to"):
        queryset = queryset.filter(event_date__lte=params["date_to"])

    sort = params.get("sort", "smart")
    ordering = {
        "event_asc": ("event_start", "event_date", "title"),
        "event_desc": ("-event_start", "-event_date", "title"),
        "newest": ("-first_seen_at", "event_start"),
        "changed": ("-last_changed_at", "event_start"),
        "title": ("title", "event_start"),
        "company": ("company", "event_start", "title"),
    }.get(sort, ("-unread_flag", "-new_flag", "future_rank", "event_start", "-first_seen_at"))
    return queryset.order_by(*ordering)
