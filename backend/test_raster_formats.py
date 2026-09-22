# -*- coding: utf-8 -*-
"""S1 格式扩展契约测试：IMG(HFA)/ENVI/JP2 直读、ENVI 成对校验与共享词干落盘、
推理输入解析泛化、缺 CRS 拒绝。"""
import io
import json
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import rasterio

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.common.utils.raster_formats import (
    ENVI_DATA_EXTENSIONS,
    RasterFormatError,
    SUPPORTED_RASTER_EXTENSIONS,
    detect_raster_kind,
    is_supported_raster,
    validate_raster,
)
from applications.extensions import db
from test_large_tiff_upload import LargeTiffUploadBase


def _make_raster(path: Path, driver: str, width=64, height=48, count=3):
    """现造 IMG(HFA)/ENVI/GeoTIFF 样例（容器内驱动支持创建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    data = rng.integers(20, 235, size=(count, height, width), dtype=np.uint8)
    profile = {
        "driver": driver,
        "height": height,
        "width": width,
        "count": count,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": rasterio.transform.from_origin(100.0, 26.0, 0.001, 0.001),
    }
    if driver == "ENVI":
        profile["dtype"] = "uint8"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)


class TestRasterFormats(unittest.TestCase):
    def test_supported_extensions_and_gating(self):
        for ext in ("tif", "tiff", "img", "jp2", "dat", "bin"):
            self.assertTrue(is_supported_raster(f"x.{ext}"), ext)
        # .hdr 是伴生文本不是栅格本体（配对由 detect 负责）
        self.assertFalse(is_supported_raster("x.hdr"))
        self.assertFalse(is_supported_raster("x.jpg"))
        self.assertFalse(is_supported_raster("x.png"))
        self.assertFalse(is_supported_raster("x.tar.gz"))
        # 环境配置一致性
        self.assertEqual(ENVI_DATA_EXTENSIONS, {"dat", "bin"})
        self.assertIn("img", SUPPORTED_RASTER_EXTENSIONS)

    def test_detect_single_file_kinds(self):
        entries = detect_raster_kind(["a.tif", "b.img", "c.jp2", "d.png", "e.txt"])
        kinds = {e["filename"]: e["kind"] for e in entries}
        self.assertEqual(kinds, {"a.tif": "geotiff", "b.img": "erdas_img", "c.jp2": "jp2"})

    def test_envi_pairing_rules(self):
        # 成对（数据+头，任意顺序）→ 单条 envi 条目带 header
        entries = detect_raster_kind(["scene.hdr", "scene.dat"])
        envi = [e for e in entries if e["kind"] == "envi"]
        self.assertEqual(len(envi), 1)
        # 孤儿数据文件（无同名头）整批拒绝
        with self.assertRaises(RasterFormatError):
            detect_raster_kind(["scene.hdr", "scene.dat", "other.bin"])
        self.assertEqual(envi[0]["filename"], "scene.dat")
        self.assertEqual(envi[0]["header"], "scene.hdr")
        # 缺头
        with self.assertRaises(RasterFormatError) as ctx:
            detect_raster_kind(["scene.dat"])
        self.assertIn(".hdr", str(ctx.exception))
        # 缺数据文件
        with self.assertRaises(RasterFormatError) as ctx:
            detect_raster_kind(["scene.hdr"])
        self.assertIn(".dat/.bin", str(ctx.exception))

    def test_validate_raster_accepts_created_formats_and_rejects_bad(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, driver in (("a.img", "HFA"), ("a.tif", "GTiff")):
                target = root / name
                _make_raster(target, driver)
                summary = validate_raster(target)
                self.assertIn(summary["driver"], ("HFA", "GTiff"))
                self.assertEqual(summary["count"], 3)
            # ENVI：驱动创建 .dat（同目录自动写 .hdr）
            envi_path = root / "envi_scene.dat"
            _make_raster(envi_path, "ENVI")
            self.assertTrue((root / "envi_scene.hdr").is_file())
            summary = validate_raster(envi_path)
            self.assertEqual(summary["driver"], "ENVI")
            # 无 CRS 拒绝
            no_crs = root / "no_crs.tif"
            with rasterio.open(
                no_crs, "w", driver="GTiff", height=4, width=4, count=1, dtype="uint8"
            ) as dst:
                dst.write(np.zeros((1, 4, 4), dtype=np.uint8))
            with self.assertRaises(RasterFormatError) as ctx:
                validate_raster(no_crs)
            self.assertIn("CRS", str(ctx.exception))
            # 不可读
            junk = root / "junk.img"
            junk.write_bytes(b"not a raster")
            with self.assertRaises(RasterFormatError) as ctx:
                validate_raster(junk)
            self.assertIn("无法读取", str(ctx.exception))


class TestFormatUploadFlow(LargeTiffUploadBase):
    def setUp(self):
        super().setUp()
        import tempfile

        self.raster_tmp = tempfile.TemporaryDirectory(prefix="raster-fmt-")
        self.addCleanup(self.raster_tmp.cleanup)
        self.fmt_root = Path(self.raster_tmp.name)

    def _upload_files(self, files, type_="地物分类", keep_raw=True):
        # Werkzeug 多文件：同一键传 (stream, filename) 元组列表；
        # 地物分类默认 keepRawTiff（推理走原始影像直读）
        return self.client.post(
            "/api/file/upload",
            data={"files": files, "type": type_, "keepRawTiff": "true" if keep_raw else "false"},
            content_type="multipart/form-data",
        )

    def test_envi_data_without_header_rejected_400(self):
        self.login_as_admin()
        envi_path = self.fmt_root / "envi_scene.dat"
        _make_raster(envi_path, "ENVI")
        response = self._upload_files([
            (io.BytesIO(envi_path.read_bytes()), "envi_scene.dat"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertIn(".hdr", response.get_json()["msg"])

    def test_envi_pair_uploads_with_shared_stem_and_reads_back(self):
        self.login_as_admin()
        envi_path = self.fmt_root / "envi_scene.dat"
        _make_raster(envi_path, "ENVI")
        header_path = self.fmt_root / "envi_scene.hdr"
        response = self._upload_files([
            (io.BytesIO(envi_path.read_bytes()), "envi_scene.dat"),
            (io.BytesIO(header_path.read_bytes()), "envi_scene.hdr"),
        ])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        envi_entry = next((d for d in data if d["filename"] == "envi_scene.dat"), None)
        self.assertIsNotNone(envi_entry, data)
        raw = Path(envi_entry["raw_tiff_path"])
        self.assertTrue(raw.is_file())
        self.assertTrue(raw.with_suffix(".hdr").is_file(), "头文件必须与数据文件共享词干")
        # rasterio 直读验证（ENVI 驱动按同名约定找头）
        summary = validate_raster(raw)
        self.assertEqual(summary["driver"], "ENVI")

    def test_img_upload_enters_keep_raw_fast_path(self):
        self.login_as_admin()
        img_path = self.fmt_root / "scene.img"
        _make_raster(img_path, "HFA")
        response = self._upload_files([
            (io.BytesIO(img_path.read_bytes()), "scene.img"),
        ])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertIn("data", body, body)
        data = body["data"]
        img_entry = next((d for d in data if d["filename"] == "scene.img"), None)
        self.assertIsNotNone(img_entry, data)
        self.assertTrue(Path(img_entry["raw_tiff_path"]).is_file())
        summary = validate_raster(img_entry["raw_tiff_path"])
        self.assertEqual(summary["driver"], "HFA")

    def test_resolve_uploaded_raster_accepts_img(self):
        from applications.inference.interpretation import resolve_uploaded_tiff

        self.login_as_admin()
        img_path = self.fmt_root / "resolve.img"
        _make_raster(img_path, "HFA")
        response = self._upload_files([
            (io.BytesIO(img_path.read_bytes()), "resolve.img"),
        ])
        raw = response.get_json()["data"][0]["raw_tiff_path"]
        resolved = resolve_uploaded_tiff(raw)
        self.assertEqual(resolved.suffix, ".img")

    def test_resolve_rejects_jpg(self):
        from applications.inference.interpretation import resolve_uploaded_tiff

        self.login_as_admin()
        response = self._upload_files([
            (io.BytesIO(b"\xff\xd8\xff\xe0fake"), "plain.jpg"),
        ], type_="场景分类")
        self.assertEqual(response.status_code, 200)
        # src 是 /_uploads/photos/<uuid>.jpg；解析器要求相对 UPLOADED_PHOTOS_DEST 的路径
        saved_name = Path(response.get_json()["data"][0]["src"]).name
        with self.assertRaises(ValueError) as ctx:
            resolve_uploaded_tiff(f"{self.upload_tmp}/{saved_name}")
        self.assertIn("仅支持", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
