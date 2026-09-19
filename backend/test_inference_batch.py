import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.interface.mmseg_segmentation import run_inference_with_model


class _FakePlane:
    def __init__(self, mask):
        self._mask = mask

    def cpu(self):
        return self

    def numpy(self):
        return self._mask


class _FakeTensor:
    def __init__(self, mask):
        self._mask = mask

    def __getitem__(self, index):
        return _FakePlane(self._mask[index])

    def cpu(self):
        return self

    def numpy(self):
        return self._mask


class _FakeSemSeg:
    def __init__(self, mask):
        self.data = _FakeTensor(mask)


class FakePred:
    def __init__(self, mask):
        self.pred_sem_seg = _FakeSemSeg(mask)


class FakeForward:
    """记录每次前向的批大小；可配置对指定尺寸的批整批抛异常。"""

    def __init__(self, fail_batch_sizes=()):
        self.batch_log = []
        self.fail_batch_sizes = fail_batch_sizes

    def __call__(self, model, imgs):
        single = not isinstance(imgs, (list, tuple))
        tiles = [imgs] if single else imgs
        self.batch_log.append(1 if single else len(tiles))
        if len(tiles) in self.fail_batch_sizes and not single:
            raise RuntimeError("模拟整批前向失败")
        outputs = []
        for index, img in enumerate(tiles):
            mask = np.full((1, img.shape[0], img.shape[1]), index % 6, dtype=np.int64)
            outputs.append(FakePred(mask))
        return outputs[0] if single else outputs


class _FakeModel:
    pass


def _write_tiles(directory, count):
    """用 rasterio 写 GTiff 瓦片：worker 镜像内确定可用。"""
    import rasterio
    from rasterio.transform import from_bounds

    Path(directory).mkdir(parents=True, exist_ok=True)
    names = []
    for index in range(count):
        name = f"tile_{index}.tif"
        path = str(Path(directory) / name)
        data = np.full((3, 64, 64), index, dtype=np.uint8)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=64,
            width=64,
            count=3,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_bounds(100.0, 25.0, 100.1, 25.1, 64, 64),
        ) as dst:
            dst.write(data)
        names.append(name)
    return names


class TestInferenceBatching(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.input_dir = Path(self.temp_dir.name) / "tiles"
        self.output_dir = Path(self.temp_dir.name) / "out"
        self.input_dir.mkdir()
        self.addCleanup(self.temp_dir.cleanup)

    def test_batch_size_two_groups_five_tiles_into_three_calls(self):
        names = _write_tiles(self.input_dir, 5)
        forward = FakeForward()
        summary = run_inference_with_model(
            _FakeModel(),
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            file_names=names,
            batch_size=2,
            forward_fn=forward,
        )
        self.assertEqual(forward.batch_log, [2, 2, 1])
        self.assertEqual(summary["success"], 5)
        self.assertEqual(summary["total"], 5)
        self.assertEqual([r["input_name"] for r in summary["results"]], names)

    def test_batch_size_one_keeps_per_tile_calls(self):
        names = _write_tiles(self.input_dir, 3)
        forward = FakeForward()
        summary = run_inference_with_model(
            _FakeModel(),
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            file_names=names,
            batch_size=1,
            forward_fn=forward,
        )
        self.assertEqual(forward.batch_log, [1, 1, 1])
        self.assertEqual(summary["success"], 3)

    def test_unloadable_tile_is_isolated_without_breaking_batch(self):
        good_names = _write_tiles(self.input_dir, 3)
        bad_name = "broken.png"
        (Path(self.input_dir) / bad_name).write_text("not an image", encoding="utf-8")
        forward = FakeForward()
        summary = run_inference_with_model(
            _FakeModel(),
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            file_names=good_names[:1] + [bad_name] + good_names[1:],
            batch_size=2,
            forward_fn=forward,
        )
        errors = [r for r in summary["results"] if r["status"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to load image", errors[0]["error"])
        self.assertEqual(summary["success"], 3)
        # 坏瓦片在加载阶段即被剔除：chunk1 只有 good0 单张前向，chunk2 两张
        self.assertEqual(forward.batch_log, [1, 2])

    def test_batch_failure_degrades_to_per_tile_retry(self):
        names = _write_tiles(self.input_dir, 4)
        forward = FakeForward(fail_batch_sizes=(2,))
        summary = run_inference_with_model(
            _FakeModel(),
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            file_names=names,
            batch_size=2,
            forward_fn=forward,
        )
        self.assertEqual(summary["success"], 4)
        # 两个 chunk 的批前向各失败一次，各自降级为 2 张逐张重试
        self.assertEqual(forward.batch_log, [2, 1, 1, 2, 1, 1])

    def test_cancel_raises_before_finishing_all_tiles(self):
        names = _write_tiles(self.input_dir, 4)
        calls = {"count": 0}

        def should_cancel():
            calls["count"] += 1
            return calls["count"] > 2

        with self.assertRaises(RuntimeError) as ctx:
            run_inference_with_model(
                _FakeModel(),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                file_names=names,
                batch_size=1,
                forward_fn=FakeForward(),
                should_cancel=should_cancel,
            )
        self.assertIn("INFERENCE_CANCELLED", str(ctx.exception))


class TestGpuCacheRelease(unittest.TestCase):
    """每 chunk 推理完成后释放 GPU 缓存块（移植江西 2026-09-18 验收反馈）。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.input_dir = Path(self.temp_dir.name) / "tiles"
        self.output_dir = Path(self.temp_dir.name) / "out"
        self.input_dir.mkdir()
        self.addCleanup(self.temp_dir.cleanup)

    def _fake_torch(self):
        torch = types.ModuleType("torch")
        torch.cuda = Mock()
        torch.cuda.is_available = Mock(return_value=True)
        torch.cuda.empty_cache = Mock()
        return torch

    def test_empty_cache_called_once_per_chunk(self):
        names = _write_tiles(self.input_dir, 5)
        fake_torch = self._fake_torch()
        with patch.dict(sys.modules, {"torch": fake_torch}):
            summary = run_inference_with_model(
                _FakeModel(),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                file_names=names,
                batch_size=2,
                forward_fn=FakeForward(),
            )
        self.assertEqual(summary["success"], 5)
        # 5 张瓦片、批大小 2 → 3 个 chunk，每 chunk 收尾各释放一次
        self.assertEqual(fake_torch.cuda.empty_cache.call_count, 3)

    def test_no_cuda_available_skips_empty_cache(self):
        names = _write_tiles(self.input_dir, 2)
        fake_torch = self._fake_torch()
        fake_torch.cuda.is_available = Mock(return_value=False)
        with patch.dict(sys.modules, {"torch": fake_torch}):
            summary = run_inference_with_model(
                _FakeModel(),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                file_names=names,
                batch_size=1,
                forward_fn=FakeForward(),
            )
        self.assertEqual(summary["success"], 2)
        fake_torch.cuda.empty_cache.assert_not_called()

    def test_missing_torch_module_does_not_break_inference(self):
        names = _write_tiles(self.input_dir, 2)
        # sys.modules 中置 None 使 `import torch` 抛 ImportError（无 Torch 环境）
        with patch.dict(sys.modules, {"torch": None}):
            summary = run_inference_with_model(
                _FakeModel(),
                input_dir=str(self.input_dir),
                output_dir=str(self.output_dir),
                file_names=names,
                batch_size=1,
                forward_fn=FakeForward(),
            )
        self.assertEqual(summary["success"], 2)


if __name__ == "__main__":
    unittest.main()
