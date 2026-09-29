# -*- coding: utf-8 -*-
"""to_utc_z 单元守护：datetime 是 date 子类，分支顺序错会把全部时间截成午夜
（2026-09-29 实测回归：overview/timeline created_at 恒为 00:00:00Z）。"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.common.utils.utc_time import to_utc_z


class TestToUtcZ(unittest.TestCase):
    def test_naive_datetime_keeps_clock(self):
        value = datetime(2026, 9, 1, 8, 40, 0)
        self.assertEqual(to_utc_z(value), "2026-09-01T08:40:00Z")

    def test_aware_non_utc_converts(self):
        value = datetime(2026, 9, 1, 8, 40, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(to_utc_z(value), "2026-09-01T00:40:00Z")

    def test_date_becomes_midnight(self):
        self.assertEqual(to_utc_z(date(2026, 9, 1)), "2026-09-01T00:00:00Z")

    def test_iso_string_and_z_suffix(self):
        self.assertEqual(to_utc_z("2026-09-01T08:40:00"), "2026-09-01T08:40:00Z")
        self.assertEqual(to_utc_z("2026-09-01T16:40:00+08:00"), "2026-09-01T08:40:00Z")

    def test_unparseable_and_scalars_passthrough(self):
        self.assertEqual(to_utc_z("not-a-time"), "not-a-time")
        self.assertIsNone(to_utc_z(None))
        self.assertEqual(to_utc_z(5), 5)


if __name__ == "__main__":
    unittest.main()
