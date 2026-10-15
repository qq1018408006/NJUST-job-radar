import json
import re
import time
from datetime import datetime, timedelta
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags


TIME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?P<start>\d{1,2}:\d{2})(?:-(?P<end>\d{1,2}:\d{2}))?)?")
DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


class SourceRequestError(RuntimeError):
    pass


def _plain_text(value) -> str:
    if not value:
        return ""
    text = strip_tags(str(value))
    return " ".join(unescape(text).split())


def parse_event_times(raw: dict):
    value = str(raw.get("kssjd") or raw.get("jbrq") or "").strip()
    match = TIME_RE.search(value)
    if not match:
        return None, None, None
    date_value = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
    if not match.group("start"):
        return date_value, None, None
    start_naive = datetime.combine(date_value, datetime.strptime(match.group("start"), "%H:%M").time())
    start = timezone.make_aware(start_naive, timezone.get_current_timezone())
    end = None
    if match.group("end"):
        end_naive = datetime.combine(date_value, datetime.strptime(match.group("end"), "%H:%M").time())
        if end_naive < start_naive:
            end_naive += timedelta(days=1)
        end = timezone.make_aware(end_naive, timezone.get_current_timezone())
    return date_value, start, end


def parse_datetime(value):
    value = str(value or "").strip()
    if not value:
        return None
    for value_format in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(value, value_format)
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        except ValueError:
            continue
    return None


def normalize_record(raw: dict, base_url: str, school_code: str) -> dict:
    source_item_id = str(raw.get("xjhid") or "").strip()
    if not source_item_id:
        raise ValueError("宣讲会记录缺少 xjhid")
    event_date, event_start, event_end = parse_event_times(raw)
    mode = "offline" if str(raw.get("xjlbdm")) == "1" else "online"
    detail_url = f"{base_url}/sub-station/lectureDetail?xjhid={source_item_id}&xxdm={school_code}"
    return {
        "source_item_id": source_item_id,
        "title": _plain_text(raw.get("xjhmc")) or "未命名宣讲会",
        "company": _plain_text(raw.get("dwmc")),
        "event_date": event_date,
        "event_start": event_start,
        "event_end": event_end,
        "mode": mode,
        "location": "空中宣讲会" if mode == "online" else _plain_text(raw.get("jbdd")),
        "school": _plain_text(raw.get("jbxx")),
        "source_status": str(raw.get("xjhzt") or ""),
        "contact_name": _plain_text(raw.get("zplxr")),
        "contact_phone": _plain_text(raw.get("lxdh")),
        "source_url": detail_url,
        "description_text": _plain_text(raw.get("ggnr") or raw.get("dwjj")),
        "raw_data": raw,
    }


def normalize_recruitment_record(raw: dict, base_url: str, school_code: str) -> dict:
    source_item_id = str(raw.get("zphid") or "").strip()
    if not source_item_id:
        raise ValueError("招聘会记录缺少 zphid")
    event_start = parse_datetime(raw.get("jbkssj"))
    event_end = parse_datetime(raw.get("jbjssj"))
    mode_code = str(raw.get("zphlbdm") or "")
    mode = "offline" if mode_code == "1" else "online" if mode_code else "unknown"
    detail_url = f"{base_url}/sub-station/recruitmentDetail?zphid={source_item_id}&xxdm={school_code}"
    return {
        "source_item_id": source_item_id,
        "title": _plain_text(raw.get("zphmc")) or "未命名招聘会",
        "company": _plain_text(raw.get("zbdw") or raw.get("cbdw")),
        "event_date": event_start.date() if event_start else None,
        "event_start": event_start,
        "event_end": event_end,
        "mode": mode,
        "location": "线上招聘会" if mode == "online" else _plain_text(raw.get("jbcd")),
        "school": _plain_text(raw.get("jbzd")),
        "source_status": str(raw.get("zphzt") or ""),
        "contact_name": _plain_text(raw.get("lxr")),
        "contact_phone": _plain_text(raw.get("lxdh")),
        "source_url": detail_url,
        "description_text": _plain_text(raw.get("zphsmnr")),
        "raw_data": raw,
    }


class NjustClient:
    def __init__(self):
        self.base_url = settings.NJUST_BASE_URL
        self.school_code = settings.NJUST_SCHOOL_CODE
        self.timeout = settings.SYNC_TIMEOUT_SECONDS
        self.retries = settings.SYNC_RETRIES
        self.page_size = settings.SYNC_PAGE_SIZE

    def _request_json(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": f"{self.base_url}/sub-station/lectureList?xxdm={self.school_code}",
            "User-Agent": "JobRadar/1.0 (+personal low-frequency archive)",
        }
        last_error = None
        for attempt in range(self.retries):
            try:
                request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
                with urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if data.get("code") not in {0, 10, 200}:
                    raise SourceRequestError(data.get("message") or f"来源接口返回错误码 {data.get('code')}")
                return data
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, SourceRequestError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 4))
        raise SourceRequestError(f"请求 {path} 失败：{last_error}") from last_error

    def _fetch_page_records(self, path: str) -> list[dict]:
        records = []
        current = 1
        while True:
            payload = {
                "current": current,
                "xxxs": "",
                "fbsj": "",
                "ssxx": "",
                "keyword": "",
                "size": self.page_size,
                "xxdm": self.school_code,
            }
            data = self._request_json(path, payload)
            result = data.get("result") or {}
            page_records = result.get("records") or []
            records.extend(page_records)
            total = int(result.get("total") or len(records))
            if not page_records or len(records) >= total:
                break
            current += 1
            if current > 1000:
                raise SourceRequestError("分页数量异常，已停止同步")
        return records

    def fetch_records(self) -> list[dict]:
        return self.fetch_lecture_records()

    def fetch_lecture_records(self) -> list[dict]:
        return self._fetch_page_records("/web/wsjysc/lbxq/getXjhPageList")

    def fetch_recruitment_records(self) -> list[dict]:
        return self._fetch_page_records("/web/wsjysc/lbxq/getZphPageList")

    def fetch_detail(self, source_item_id: str) -> dict:
        data = self._request_json(f"/web/wsjysc/lbxq/getXjhxq/{source_item_id}")
        return data.get("result") or {}

    def fetch_recruitment_detail(self, source_item_id: str) -> dict:
        data = self._request_json(f"/web/wsjysc/lbxq/getZphxq/{source_item_id}")
        return data.get("result") or {}
