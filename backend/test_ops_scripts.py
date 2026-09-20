# -*- coding: utf-8 -*-
"""运维脚本单元：worker 陈旧暂存目录清扫 + cleanup fid 目录守卫。

对照江西 2026-09 改进（3e82f47 启动清扫 / 6fc814a U 前缀守卫）落地到云南。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.inference.worker import sweep_stale_workdirs
from cleanup_change_outputs import _preview_output_dir, is_fid_directory


class SweepStaleWorkdirsTests(unittest.TestCase):
    def test_sweep_removes_only_stale_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "old-job-dir"
            stale.mkdir()
            (stale / "tile.tif").write_bytes(b"x" * 16)
            fresh = root / "fresh-job-dir"
            fresh.mkdir()
            stray_file = root / "notes.txt"
            stray_file.write_text("not a dir", encoding="utf-8")

            old_ts = time.time() - 25 * 3600
            os.utime(stale, (old_ts, old_ts))

            removed = sweep_stale_workdirs(root)

            self.assertEqual(removed, ["old-job-dir"])
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(stray_file.exists())

    def test_sweep_custom_age_and_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            self.assertEqual(sweep_stale_workdirs(root), [])

            root.mkdir()
            young = root / "1h-old"
            young.mkdir()
            ts = time.time() - 3600
            os.utime(young, (ts, ts))
            self.assertEqual(sweep_stale_workdirs(root, max_age_seconds=24 * 3600), [])
            self.assertEqual(sweep_stale_workdirs(root, max_age_seconds=1800), ["1h-old"])


class CleanupFidGuardTests(unittest.TestCase):
    def test_is_fid_directory_accepts_only_digits(self):
        self.assertTrue(is_fid_directory("101"))
        self.assertTrue(is_fid_directory("007"))
        self.assertFalse(is_fid_directory("U-2024"))
        self.assertFalse(is_fid_directory("backups"))
        self.assertFalse(is_fid_directory("101a"))
        self.assertFalse(is_fid_directory(".hidden"))

    def test_preview_keeps_recent_years_and_lists_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            fid_dir = Path(tmp) / "101"
            fid_dir.mkdir()
            (fid_dir / "101+2024_mask.png").write_bytes(b"x")
            (fid_dir / "101+2024_src.png").write_bytes(b"x")
            (fid_dir / "101+2020_mask.png").write_bytes(b"x")

            would_remove = _preview_output_dir("101", fid_dir, keep_last_years=1)

            self.assertIn("101+2020_mask.png", would_remove)
            self.assertNotIn("101+2024_mask.png", would_remove)


if __name__ == "__main__":
    unittest.main()
