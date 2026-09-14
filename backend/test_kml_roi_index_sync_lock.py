"""index_sync Excel 并发写文件锁测试（2026-09-15 审计批次 Y2-5）。

历史缺陷：sync_miner_index_rows 直接 wb.save(target_path)，两个并发请求
同时读-改-写同一工作簿时后写者静默覆盖先写者的年份列。
修复契约：
- 保存前对目标工作簿的锁文件加跨进程排他锁（fcntl/msvcrt 条件导入）；
- 锁冲突时返回明确 warning（synced=False, reason=workbook_locked），不静默覆盖；
- 拿不到锁的等待有硬上限（lock_timeout 秒），不得长时间阻塞请求线程。

锁互斥单测依赖 POSIX flock 语义，仅在 Linux（容器内）执行，Windows 跳过。
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.kml_roi.index_sync import _WorkbookLock, _lock_path_for, sync_miner_index_rows

IS_WINDOWS = sys.platform.startswith("win")


class WorkbookLockTests(unittest.TestCase):
    def _workbook(self, miner_dir):
        return Path(miner_dir) / "NDVI_2year.xlsx"

    @unittest.skipIf(IS_WINDOWS, "文件锁互斥单测仅在 Linux 容器内执行")
    def test_second_locker_fails_fast_when_held(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = _lock_path_for(self._workbook(tmp_dir))
            first = _WorkbookLock(lock_path, timeout=0.2)
            self.assertTrue(first.acquire())
            try:
                started = time.monotonic()
                second = _WorkbookLock(lock_path, timeout=0.5)
                self.assertFalse(second.acquire())
                self.assertLess(time.monotonic() - started, 5.0)
            finally:
                first.release()

            third = _WorkbookLock(lock_path, timeout=0.5)
            self.assertTrue(third.acquire())
            third.release()

    @unittest.skipIf(IS_WINDOWS, "文件锁互斥单测仅在 Linux 容器内执行")
    def test_sync_returns_warning_when_workbook_locked(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = _lock_path_for(self._workbook(tmp_dir))
            blocker = _WorkbookLock(lock_path, timeout=0.5)
            self.assertTrue(blocker.acquire())
            try:
                started = time.monotonic()
                result = sync_miner_index_rows(
                    "NDVI",
                    "2024",
                    [{"fid": 9001, "mean": 0.5}],
                    miner_dir=Path(tmp_dir),
                    lock_timeout=0.3,
                )
                elapsed = time.monotonic() - started
            finally:
                blocker.release()

        self.assertFalse(result["synced"])
        self.assertEqual(result["reason"], "workbook_locked")
        self.assertIn("path", result)
        self.assertLess(elapsed, 5.0)

    def test_unlocked_sync_still_writes_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sync_miner_index_rows(
                "NDVI",
                "2024",
                [{"fid": 9001, "mean": 0.5}],
                miner_dir=Path(tmp_dir),
            )
            # 锁释放后，同目录再次写入必须成功（既有 upsert 语义不受锁影响）
            result = sync_miner_index_rows(
                "NDVI",
                "2024",
                [{"fid": 9001, "mean": 0.6}],
                miner_dir=Path(tmp_dir),
            )
            self.assertTrue(result["synced"])


if __name__ == "__main__":
    unittest.main()
