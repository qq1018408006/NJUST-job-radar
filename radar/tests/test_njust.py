from django.test import SimpleTestCase

from radar.services.njust import normalize_record, normalize_recruitment_record, parse_event_times


class NjustParserTests(SimpleTestCase):
    def test_parse_time_range(self):
        event_date, start, end = parse_event_times({"kssjd": "2026-09-20 19:00-21:00"})
        self.assertEqual(event_date.isoformat(), "2026-09-20")
        self.assertEqual(start.strftime("%H:%M"), "19:00")
        self.assertEqual(end.strftime("%H:%M"), "21:00")

    def test_parse_date_without_time(self):
        event_date, start, end = parse_event_times({"jbrq": "2026-09-21"})
        self.assertEqual(event_date.isoformat(), "2026-09-21")
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_normalize_record_strips_html_and_builds_url(self):
        result = normalize_record(
            {
                "xjhid": "123",
                "xjhmc": "<b>测试宣讲会</b>",
                "xjlbdm": "1",
                "kssjd": "2026-09-20 14:00-15:30",
                "jbdd": "A101",
                "ggnr": "<p>欢迎 <script>bad()</script>参加</p>",
            },
            "https://example.com",
            "10288",
        )
        self.assertEqual(result["title"], "测试宣讲会")
        self.assertEqual(result["mode"], "offline")
        self.assertIn("xjhid=123", result["source_url"])
        self.assertNotIn("<p>", result["description_text"])

    def test_normalize_recruitment_record_uses_separate_start_and_end(self):
        result = normalize_recruitment_record(
            {
                "zphid": "456",
                "zphmc": "<b>秋季双选会</b>",
                "zphlbdm": "1",
                "jbkssj": "2026-09-22 09:30:00",
                "jbjssj": "2026-09-22 12:00:00",
                "jbcd": "学术交流中心",
                "jbzd": "南京理工大学",
                "zphzt": "1",
                "zphsmnr": "<p>招聘详情</p>",
            },
            "https://example.com",
            "10288",
        )
        self.assertEqual(result["source_item_id"], "456")
        self.assertEqual(result["title"], "秋季双选会")
        self.assertEqual(result["event_start"].strftime("%Y-%m-%d %H:%M"), "2026-09-22 09:30")
        self.assertEqual(result["event_end"].strftime("%Y-%m-%d %H:%M"), "2026-09-22 12:00")
        self.assertIn("zphid=456", result["source_url"])
        self.assertEqual(result["description_text"], "招聘详情")
