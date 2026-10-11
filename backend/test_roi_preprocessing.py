import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import rasterio
from rasterio.transform import from_origin

from applications.inference.jobs import normalize_job_request
from applications.kml_roi.pipeline import run_kml_roi_pipeline
from applications.kml_roi.tiles import prepare_tiles
from applications.kml_roi.preprocessing import source_sha256
from test_inference_runner import load_worker_module


FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/roi-preprocessing-v1.json"


class TestRoiPreprocessing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "scene.tif"
        self.transform = from_origin(100, 26, 0.001, 0.001)
        rng = np.random.default_rng(51)
        pixels = rng.integers(20, 235, (3, 32, 32), dtype=np.uint8)
        pixels[:, 0, :] = 0
        with rasterio.open(
            self.source, "w", driver="GTiff", width=32, height=32, count=3,
            dtype="uint8", crs="EPSG:4326", transform=self.transform, nodata=0,
        ) as dst:
            dst.write(pixels)
        self.source_bytes = self.source.read_bytes()
        self.geometry = {
            "type": "Polygon",
            "coordinates": [[[100, 26], [100.032, 26], [100.032, 25.968], [100, 25.968], [100, 26]]],
        }
        self.vector = self.root / "mines.geojson"
        self.vector.write_text(json.dumps({
            "type": "FeatureCollection", "features": [{
                "type": "Feature", "properties": {"FID_1": 101}, "geometry": self.geometry,
            }],
        }), encoding="utf-8")
        _, self.names, _ = prepare_tiles(
            self.source, self.source, [("101", self.geometry)], self.root / "baseline", year="2024",
        )
        self.name = self.names[0]
        self.raw_png = (self.root / "baseline" / self.name).read_bytes()
        self.raw_bgr = cv2.imread(str(self.root / "baseline" / self.name))

    def _request(self, **options):
        return normalize_job_request({"old_tif_path": str(self.source), **options}, allowed_roots=[self.root])

    def test_normalization_preserves_explicit_options(self):
        normalized = self._request(prehandle=2, denoise=5)
        self.assertEqual(normalized.get("prehandle"), 2)
        self.assertEqual(normalized.get("denoise"), 5)

    def test_fixture_invalid_options_are_rejected(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for request in fixture["parameter_error"]["requests"]:
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "prehandle|denoise"):
                self._request(**request)

    def test_worker_transmits_options_and_rejects_invalid_persisted_option(self):
        worker = load_worker_module().InferenceWorker(pipeline_runner=Mock(), job_store=Mock())
        worker.resolution = SimpleNamespace(effective="cpu")
        payload = {
            "old_tif_path": str(self.source), "new_tif_path": str(self.source),
            "kml_path": str(self.vector), "output_root": str(self.root / "outputs"),
            "prehandle": 4, "denoise": 3,
        }
        worker._run_pipeline(payload, self.root / "work")
        kwargs = worker.pipeline_runner.call_args.kwargs
        self.assertEqual(kwargs.get("prehandle"), 4)
        self.assertEqual(kwargs.get("denoise"), 3)
        with self.assertRaisesRegex(ValueError, "prehandle"):
            worker._run_pipeline({**payload, "prehandle": True}, self.root / "bad")

    @staticmethod
    def _expected(bgr, prehandle, denoise):
        result = bgr.copy()
        if prehandle == 2:
            clahe = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8))
            result = cv2.merge([clahe.apply(channel) for channel in cv2.split(result)])
        elif prehandle == 4:
            result = cv2.filter2D(result, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=int))
        if denoise == 3:
            result = cv2.medianBlur(result, 3)
        elif denoise == 5:
            result = cv2.GaussianBlur(result, (3, 3), 0, 0)
        return result

    def _run(self, suffix, **options):
        self.assertIn("prehandle", inspect.signature(run_kml_roi_pipeline).parameters)
        work_dir = self.root / suffix
        observed = {}

        def runner(**kwargs):
            data_dir = Path(kwargs["data_path"])
            observed["data_path"] = data_dir
            observed["png"] = (data_dir / self.name).read_bytes()
            observed["bgr"] = cv2.imread(str(data_dir / self.name))
            out_dir = Path(kwargs["out_dir"])
            base = Path(self.name).stem
            cv2.imwrite(str(out_dir / f"pred_{base}.png"), self.raw_bgr)
            cv2.imwrite(str(out_dir / f"mask_{base}.png"), np.ones(self.raw_bgr.shape[:2], dtype=np.uint8))
            return [], {}

        summary = run_kml_roi_pipeline(
            old_tif=self.source, new_tif=self.source, kml_path=self.vector,
            output_root=self.root / f"out-{suffix}", work_dir=work_dir,
            model_id="cc-ln/CUGRS", device="cpu", year="2024", keep_workdir=True,
            tile_runner=runner, **options,
        )
        return summary, observed, work_dir

    def test_default_is_byte_identical_and_does_not_hash_source(self):
        with patch("applications.kml_roi.preprocessing.source_sha256", wraps=source_sha256) as hash_source:
            summary, observed, work_dir = self._run("default", prehandle=0, denoise=0)
        hash_source.assert_not_called()
        self.assertEqual(observed["png"], self.raw_png)
        self.assertEqual(observed["data_path"], work_dir / "tiles")
        self.assertFalse((work_dir / "preprocessed").exists())
        self.assertNotIn("preprocessing", summary)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_existing_algorithms_apply_in_order_and_preserve_original_georeference(self):
        for prehandle, denoise in ((2, 0), (4, 0), (0, 3), (0, 5), (2, 5), (4, 3)):
            with self.subTest(prehandle=prehandle, denoise=denoise):
                suffix = f"processing-{prehandle}-{denoise}"
                summary, observed, work_dir = self._run(suffix, prehandle=prehandle, denoise=denoise)
                np.testing.assert_array_equal(observed["bgr"], self._expected(self.raw_bgr, prehandle, denoise))
                self.assertEqual(observed["data_path"], work_dir / "preprocessed")
                self.assertEqual((work_dir / "tiles" / self.name).read_bytes(), self.raw_png)
                output_dir = self.root / f"out-{suffix}" / "101"
                self.assertEqual((output_dir / "101+2024_src.png").read_bytes(), self.raw_png)
                with rasterio.open(output_dir / "101+2024_label.tif") as label:
                    self.assertEqual(label.crs.to_string(), "EPSG:4326")
                    self.assertEqual(label.nodata, 255)
                    self.assertEqual(tuple(label.bounds), (100, 25.968, 100.032, 26))
                    self.assertTrue(np.all(label.read(1)[0, :] == 255))
                record = summary["preprocessing"]
                self.assertEqual(record["prehandle"], prehandle)
                self.assertEqual(record["denoise"], denoise)
                self.assertEqual(record["sources"][0]["sha256"], hashlib.sha256(self.source_bytes).hexdigest())
                tile = record["tiles"][0]
                self.assertEqual(tile["crs"], "EPSG:4326")
                self.assertEqual(tile["source_rgb_sha256"], hashlib.sha256(self.raw_bgr[:, :, ::-1].copy().tobytes()).hexdigest())
                self.assertEqual(tile["processed_rgb_sha256"], hashlib.sha256(observed["bgr"][:, :, ::-1].copy().tobytes()).hexdigest())
                with rasterio.open(work_dir / "tiles" / f"{Path(self.name).stem}.tif") as crop:
                    self.assertEqual(tile["transform"], list(crop.transform)[:6])
                    self.assertEqual((tile["width"], tile["height"]), (crop.width, crop.height))
                self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_empty_roi_records_options_without_claiming_applied_tiles(self):
        self.assertIn("prehandle", inspect.signature(run_kml_roi_pipeline).parameters)
        with patch("applications.kml_roi.pipeline.load_vector_features", return_value=[]):
            summary, _, _ = self._run("empty", prehandle=2, denoise=5)
        self.assertEqual(summary["status"], "no_features")
        self.assertEqual(summary["preprocessing"]["tiles"], [])

    def test_unreadable_processed_rgb_fails_before_inference(self):
        self.assertIn("prehandle", inspect.signature(run_kml_roi_pipeline).parameters)
        with patch("applications.image_processing.CLAHE.cv2.imread", return_value=None):
            with self.assertRaisesRegex(ValueError, "读取失败|预处理"):
                self._run("broken", prehandle=2)


if __name__ == "__main__":
    unittest.main()
