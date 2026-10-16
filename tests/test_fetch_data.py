#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_data.py 单元测试：全部离线，mock _get，不发任何网络请求（CI 安全）"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_data as fd  # noqa: E402

# 2019-01-01T00:00:00Z 毫秒时间戳；每行 +1 天
_TS0 = 1546300800000
_DAY = 86400000


def _fake_nav_js(n=60, bad_block=False):
    rows = []
    for i in range(n):
        val = None if (bad_block and i == 5) else round(1.0 + i * 0.001, 4)
        rows.append([_TS0 + i * _DAY, val])
    return "var fS_name = '测试基金';\nData_ACWorthTrend = %s;\n/* trailing */" % json.dumps(rows)


def _fake_pe_payload(n=4000, start_ymd=(2010, 1, 1)):
    import datetime
    d = datetime.date(*start_ymd)
    data = []
    for i in range(n):
        data.append({"tradeDate": d.strftime("%Y%m%d"), "peg": round(10.0 + (i % 50) * 0.1, 4)})
        d += datetime.timedelta(days=1)
    return json.dumps({"code": "200", "data": data})


class TestFetchNav(unittest.TestCase):
    def test_parse_and_filter(self):
        with mock.patch.object(fd, "_get", return_value=_fake_nav_js(90, bad_block=True)):
            rows = fd.fetch_nav("999999", start="2019-01-01")
        self.assertEqual(len(rows), 89)  # 1 条 None 被跳过
        self.assertEqual(rows[0], ["2019-01-01", 1.0])
        self.assertEqual(rows[-1][0], "2019-03-31")
        with mock.patch.object(fd, "_get", return_value=_fake_nav_js(90)):
            late = fd.fetch_nav("999999", start="2019-02-01")
        self.assertTrue(all(r[0] >= "2019-02-01" for r in late))
        self.assertEqual(late[0][0], "2019-02-01")

    def test_missing_block_raises(self):
        with mock.patch.object(fd, "_get", return_value="var other = 1;"):
            with self.assertRaises(RuntimeError):
                fd.fetch_nav("000000")


class TestRollingPercentile(unittest.TestCase):
    def test_window_and_extremes(self):
        values = [10.0] * (fd.PE_WINDOW - 1) + [20.0, 5.0]
        pct = fd._rolling_percentile(values)
        self.assertIsNone(pct[0])
        self.assertEqual(pct[fd.PE_WINDOW - 1], 100.0)  # 20.0 严格大于其余 243 个
        self.assertEqual(pct[fd.PE_WINDOW], 0.0)        # 5.0 为窗口最小

    def test_none_propagates(self):
        values = [None] * fd.PE_WINDOW
        self.assertTrue(all(p is None for p in fd._rolling_percentile(values)))

    def test_none_inside_window(self):
        # 窗口内含 None：用有效值排位；有效值不足 min_obs 返回 None
        values = [None, None] + [10.0] * (fd.PE_WINDOW - 2)
        pct = fd._rolling_percentile(values)
        self.assertEqual(pct[-1], 0.0)  # 10.0 与全部有效值持平，严格小于=0
        sparse = [None] * (fd.PE_WINDOW - 50) + [10.0] * 50
        self.assertIsNone(fd._rolling_percentile(sparse)[-1])  # 仅 50 个有效值 <120


class TestFetchIndexPe(unittest.TestCase):
    def test_parse_filter_and_window(self):
        with mock.patch.object(fd, "_get", return_value=_fake_pe_payload()):
            rows = fd.fetch_index_pe_pct("000300", start="2019-01-01")
        self.assertGreater(len(rows), 30)
        self.assertTrue(all(r[0] >= "2019-01-01" for r in rows))
        # 首条起即有分位（内部已多取数据喂满 244 日窗口，不依赖 2019 当年数据）
        self.assertEqual(rows[0][0], "2019-01-01")
        self.assertTrue(0.0 <= rows[0][1] <= 100.0)

    def test_bad_code_raises(self):
        with mock.patch.object(fd, "_get", return_value=json.dumps({"code": "400", "data": None})):
            with self.assertRaises(RuntimeError):
                fd.fetch_index_pe_pct("999999")

    def test_url_carries_preload_window(self):
        # 必须从 2010 起取数，即使用户 start 很晚
        captured = {}

        def fake_get(url, referer=None):
            captured["url"] = url
            captured["referer"] = referer
            return _fake_pe_payload()  # 2010 起足够长，过滤后仍 >=30 条

        with mock.patch.object(fd, "_get", side_effect=fake_get):
            fd.fetch_index_pe_pct("000300", start="2019-06-01")
        self.assertIn("startDate=20100101", captured["url"])
        self.assertEqual(captured["referer"], "https://www.csindex.com.cn/")


if __name__ == "__main__":
    unittest.main()
