# -*- coding: utf-8 -*-
"""重计算内存预算与产物清理（2026-11-14 审查问题25 子项）。

单窗预算计入 mask/有效掩膜/alpha 副本等同时存活数组并按并发分摊；
clip/slice 数据库提交失败时清理已写产物，不残留孤儿文件。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.project_hub.imagery_processing import (
    MAX_WINDOW_BYTES,
    ImageryProcessingError,
    _estimate_window_bytes,
    _guard_window_bytes,
)

from test_imagery_processing import ImageryProcessingBase


class _FakeSrc:
    def __init__(self, count, dtype):
        self.count = count
        self.dtypes = [dtype]


class TestWindowBudget(unittest.TestCase):
    def test_estimate_counts_mask_and_copy_arrays(self):
        # 1000×1000×4 波段 uint16：data 4×2B + masks 4×1B + valid 1B + alpha 1B = 14B/像素
        src = _FakeSrc(4, "uint16")
        estimated = _estimate_window_bytes(src, MagicMock(width=1000, height=1000))
        self.assertEqual(estimated, 1000 * 1000 * 14)

    def test_guard_rejects_window_over_budget(self):
        # 20000×20000×4 波段 uint8：4 亿像素 × 10B/像素 = 4GB，超出单请求预算
        src = _FakeSrc(4, "uint8")
        with self.assertRaises(ImageryProcessingError):
            _guard_window_bytes(src, MagicMock(width=20000, height=20000))

    def test_budget_fits_container_limit_with_concurrency(self):
        # backend 容器 mem_limit 2048m、并发 2 路：单路峰值预算须 ≤ 1GB
        self.assertLessEqual(MAX_WINDOW_BYTES, 1024 ** 3)
        self.assertGreaterEqual(MAX_WINDOW_BYTES, 256 * 1024 ** 2)


class TestRecomputeSemaphore(unittest.TestCase):
    def test_semaphore_bounds_concurrent_recompute(self):
        from applications.project_hub.imagery_processing import _recompute_semaphore

        acquired = _recompute_semaphore.acquire(blocking=False)
        second = _recompute_semaphore.acquire(blocking=False)
        try:
            self.assertTrue(acquired)
            self.assertTrue(second)
            self.assertFalse(_recompute_semaphore.acquire(blocking=False))
        finally:
            _recompute_semaphore.release()
            _recompute_semaphore.release()
        self.assertTrue(_recompute_semaphore.acquire(blocking=False))
        _recompute_semaphore.release()


class TestArtifactCleanupOnCommitFailure(ImageryProcessingBase):
    def _tifacts(self):
        return sorted(
            p.name for p in self.inputs_dir.glob("clip_*.tif")
        ) + sorted(p.name for p in self.inputs_dir.glob("slice_*.tif"))

    def test_clip_cleans_output_when_commit_fails(self):
        with patch.object(
            db.session.__class__, "commit", side_effect=RuntimeError("commit boom")
        ):
            response = self._clip({
                "source": self.source_key,
                "mine_fid": 42,
                "buffer_meters": 100,
            })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._tifacts(), [])
        self.assertTrue((self.inputs_dir / "source_full.tif").is_file())

    def test_slice_cleans_outputs_when_commit_fails(self):
        with patch.object(
            db.session.__class__, "commit", side_effect=RuntimeError("commit boom")
        ):
            response = self._slice({
                "source": self.source_key,
                "mode": "grid_pixels",
                "tile_pixels": 320,
            })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._tifacts(), [])
        self.assertTrue((self.inputs_dir / "source_full.tif").is_file())

    def test_clip_and_slice_still_succeed_without_patch(self):
        clip = self._clip({"source": self.source_key, "mine_fid": 42, "buffer_meters": 100})
        self.assertEqual(clip.status_code, 200, clip.get_data(as_text=True))
        sliced = self._slice({
            "source": self.source_key,
            "mode": "grid_pixels",
            "tile_pixels": 320,
        })
        self.assertEqual(sliced.status_code, 200, sliced.get_data(as_text=True))
        self.assertGreater(len(self._tifacts()), 0)


if __name__ == "__main__":
    unittest.main()
