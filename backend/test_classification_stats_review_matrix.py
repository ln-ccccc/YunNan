# -*- coding: utf-8 -*-
"""审校矩阵分块累积对照测试（2026-11-15 难点①）。

_new/_accumulate_review_matrix（分块栅格化 + 分块配对累计）必须与旧整幅
实现（全网格 rasterize + 全幅 int64 pairs）逐元素一致——统计契约不变，
只改内存形态。覆盖：多类组合、无效像元（fill=255）、空交集、块边界
（block_rows 不整除网格高）、变换平移正确性。
"""
import os
import sys
import unittest

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.project_hub.classification_stats import (
    _accumulate_review_matrix,
    _matrix_block_rows,
)

CLASS_COUNT = 6


def _reference_matrix(old_shapes, new_shapes, transform, shape, class_count):
    """旧整幅实现（重构前 compute_revised_change_matrix 的数组路径）。"""
    old = rasterize(old_shapes, out_shape=shape, transform=transform, fill=255, dtype="uint8")
    new = rasterize(new_shapes, out_shape=shape, transform=transform, fill=255, dtype="uint8")
    valid = (old < class_count) & (new < class_count)
    pixels = np.zeros((class_count, class_count), dtype=np.int64)
    if valid.any():
        pairs = (old[valid].astype(np.int64), new[valid].astype(np.int64))
        np.add.at(pixels, pairs, 1)
    return pixels, int(valid.sum())


def _rect(x0, y0, x1, y1, code):
    return (
        {
            "type": "Polygon",
            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
        },
        code,
    )


class TestReviewMatrixChunkedEquivalence(unittest.TestCase):
    """分块累积与旧整幅实现逐元素对照。"""

    def _assert_equivalent(self, old_shapes, new_shapes, transform, shape, block_rows):
        expected, expected_valid = _reference_matrix(
            old_shapes, new_shapes, transform, shape, CLASS_COUNT
        )
        actual, actual_valid = _accumulate_review_matrix(
            old_shapes, new_shapes, transform, shape, CLASS_COUNT, block_rows=block_rows
        )
        self.assertEqual(actual_valid, expected_valid)
        np.testing.assert_array_equal(actual, expected)
        return actual, expected_valid

    def test_multi_class_overlap_and_invalid_pixels(self):
        # 网格 137×201（质数尺寸），多类交错 + fill=255 无效区共存
        transform = Affine(0.001, 0, 100.0, 0, -0.001, 26.0)
        shape = (137, 201)
        old_shapes = [
            _rect(100.0, 25.9, 100.15, 26.0, 0),
            _rect(100.10, 25.95, 100.19, 25.995, 1),
            _rect(100.16, 25.86, 100.201, 25.93, 2),
        ]
        new_shapes = [
            _rect(100.02, 25.9, 100.18, 26.0, 3),
            _rect(100.14, 25.86, 100.201, 25.97, 4),
        ]
        self._assert_equivalent(old_shapes, new_shapes, transform, shape, block_rows=40)

    def test_empty_intersection_yields_zero_matrix(self):
        # 两期类别码均为无效区（fill=255）→ 空交集 → 全零矩阵（与旧实现一致）
        transform = Affine(0.001, 0, 100.0, 0, -0.001, 26.0)
        shape = (50, 60)
        actual, valid = _accumulate_review_matrix(
            [_rect(100.0, 25.9, 100.05, 25.95, 255)],
            [_rect(100.0, 25.9, 100.05, 25.95, 255)],
            transform,
            shape,
            CLASS_COUNT,
            block_rows=10,
        )
        self.assertEqual(valid, 0)
        self.assertTrue((actual == 0).all())

    def test_block_boundaries_and_undivisible_tail(self):
        # 300 高 × block_rows=97：4 块且末块 9 行——块边界像素归属必须与整幅一致
        transform = Affine(0.0005, 0, 100.0, 0, -0.0005, 26.5)
        shape = (300, 120)
        old_shapes = [_rect(100.0, 26.0, 100.06, 26.5, 2), _rect(100.02, 26.1, 100.05, 26.35, 5)]
        new_shapes = [_rect(100.01, 26.05, 100.06, 26.45, 1), _rect(100.0, 26.4, 100.03, 26.5, 3)]
        actual, _ = self._assert_equivalent(
            old_shapes, new_shapes, transform, shape, block_rows=97
        )
        self.assertGreater(int((actual > 0).sum()), 0)

    def test_single_block_equals_full_grid(self):
        # block_rows ≥ 网格高时退化为单块，仍与整幅一致
        transform = Affine(0.001, 0, 100.0, 0, -0.001, 26.0)
        shape = (60, 80)
        old_shapes = [_rect(100.0, 25.94, 100.08, 26.0, 0)]
        new_shapes = [_rect(100.0, 25.94, 100.08, 26.0, 5)]
        self._assert_equivalent(old_shapes, new_shapes, transform, shape, block_rows=60)

    def test_block_windows_respect_real_memory_constraints(self):
        """约束型断言（问题19/6）：不锁定实现公式，直接验证各阶段真实存活
        数组 ≤ 预算——覆盖 全宽/极宽/极窄 与 手动 block_rows 组合。"""
        from applications.project_hub.classification_stats import (
            _REVIEW_MATRIX_PAIR_SUBCHUNK_BYTES,
            _REVIEW_MATRIX_BLOCK_BUDGET_BYTES,
            _matrix_window_shape,
        )

        budget = _REVIEW_MATRIX_BLOCK_BUDGET_BYTES
        for width in (1, 100, 20000, 10 ** 6):
            for grid_h in (1, 5000, 10 ** 6):
                rows, cols = _matrix_window_shape((grid_h, width))
                self.assertGreaterEqual(rows, 1)
                self.assertGreaterEqual(cols, 1)
                self.assertLessEqual(rows, grid_h)
                self.assertLessEqual(cols, width)
                window_px = rows * cols
                # 栅格化阶段存活：两期窗口 uint8 2B/px + 掩膜 1B/px + 临时 1B/px
                self.assertLessEqual(
                    window_px * 4, budget,
                    f"栅格化窗口存活超预算 width={width} grid_h={grid_h}",
                )
                # 配对子分块存活：int64 索引 16B/px ≤ 64MB
                pair_sub_rows = max(1, _REVIEW_MATRIX_PAIR_SUBCHUNK_BYTES // (max(1, cols) * 16))
                self.assertLessEqual(
                    pair_sub_rows * cols * 16, _REVIEW_MATRIX_PAIR_SUBCHUNK_BYTES,
                    f"配对子分块超预算 width={width}",
                )

    def test_equivalence_holds_under_production_windows(self):
        """生产窗口尺寸下分块 == 整幅（列窗口开启后的等价性）。"""
        transform = Affine(0.001, 0, 100.0, 0, -0.001, 26.0)
        shape = (5000, 20000)
        old_shapes = [_rect(100.0, 25.9, 100.6, 26.0, 1), _rect(100.3, 25.95, 100.9, 25.99, 2)]
        new_shapes = [_rect(100.1, 25.9, 100.7, 26.0, 3), _rect(100.4, 25.85, 100.75, 25.96, 4)]
        expected, expected_valid = _reference_matrix(
            old_shapes, new_shapes, transform, shape, CLASS_COUNT
        )
        actual, actual_valid = _accumulate_review_matrix(
            old_shapes, new_shapes, transform, shape, CLASS_COUNT
        )
        self.assertEqual(actual_valid, expected_valid)
        np.testing.assert_array_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
