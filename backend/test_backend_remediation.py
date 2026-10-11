"""深度审计回归：真实栅格、受控路径、快照及删除失败补偿。"""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.warp import transform as project_coordinates
from shapely.geometry import box
from shapely.ops import transform

import test_project_api as fixture
from applications.extensions import db
from applications.models.project import Project, ProjectDataset
from applications.project_hub import service
from applications.project_hub.inference_inputs import _resolve_registered_tif
from applications.project_hub.snapshots import _project_manifest, import_backup_manifest


class TestBackendRemediation(unittest.TestCase):
    setUp = fixture.TestProjectAPI.setUp
    _restore_storage_root = fixture.TestProjectAPI._restore_storage_root
    sync_admin = fixture.TestProjectAPI.sync_admin
    login_as_admin = fixture.TestProjectAPI.login_as_admin
    _create_project = fixture.TestProjectAPI._create_project
    _json = fixture.TestProjectAPI._json

    def _source(self, *, nodata=None, mask=False, crs="EPSG:4326", legacy=False):
        self.login_as_admin()
        pid = self._create_project()
        key = "incoming/audit.tif"
        path = self.storage_root / key
        path.parent.mkdir(exist_ok=True)
        affine = rasterio.transform.from_origin(102.44, 24.80, .00001, .00001)
        if crs != "EPSG:4326":
            affine = rasterio.transform.from_origin(500000, 2800000, 10, 10)
        profile = dict(driver="GTiff", width=512, height=512, count=3,
                       dtype="uint8", crs=crs, transform=affine, nodata=nodata)
        pixels = np.full((3, 512, 512), 70, dtype="uint8")
        if nodata is not None:
            pixels[:, 10:20, 10:20] = nodata
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(pixels)
            if mask:
                valid = np.full((512, 512), 255, dtype="uint8")
                valid[25:30, 25:30] = 0
                dst.write_mask(valid)
        response = self.client.post(f"/api/projects/{pid}/datasets", json={
            "dataset_kind": "imagery", "display_name": "真实回归栅格",
            "storage_key": key, "year_start": 2022, "year_end": 2022,
        })
        self.assertEqual(response.status_code, 200)
        did = response.get_json()["data"]["id"]
        if legacy:
            db.session.get(ProjectDataset, did).file_path = str(path)
            db.session.commit()
        return pid, did, path

    def _clip(self, pid, did, buffer=0, geometry=None):
        geometry = geometry or box(102.44, 24.795, 102.445, 24.80).__geo_interface__
        response = self.client.post(f"/api/projects/{pid}/imagery/clip", json={
            "source": did, "geometry": geometry, "buffer_meters": buffer,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]

    def test_registered_incoming_candidates_read_dimensions_and_bounds(self):
        pid, did, path = self._source()
        data = self.client.get(f"/api/projects/{pid}/imagery/candidates").get_json()["data"]
        candidate = next(x for x in data["items"] if x["dataset_id"] == did)
        self.assertEqual(candidate["width"], 512)
        with rasterio.open(path) as src:
            np.testing.assert_allclose(candidate["bounds"], list(src.bounds))

    def test_candidates_projected_bounds_are_wgs84(self):
        pid, did, path = self._source(crs="EPSG:32648")
        data = self.client.get(f"/api/projects/{pid}/imagery/candidates").get_json()["data"]
        candidate = next(x for x in data["items"] if x["dataset_id"] == did)
        self.assertTrue(100 < candidate["bounds"][0] < 110)
        self.assertTrue(20 < candidate["bounds"][1] < 30)

    def test_candidates_unreadable_has_null_bounds_and_no_host_path(self):
        pid, did, path = self._source()
        path.write_bytes(b"not a raster")
        response = self.client.get(f"/api/projects/{pid}/imagery/candidates")
        item = response.get_json()["data"]["items"][0]
        self.assertIsNone(item["bounds"])
        self.assertNotIn(str(self.storage_root), response.get_data(as_text=True))

    def test_clip_incoming_stores_relative_key_and_inherits_year(self):
        pid, did, _ = self._source()
        result = self._clip(pid, did)
        derived = db.session.get(ProjectDataset, result["dataset_id"])
        self.assertEqual(derived.file_path, result["storage_key"])
        self.assertEqual(derived.year_start, 2022)
        self.assertEqual(derived.year_end, 2022)

    def test_clip_retains_nodata_and_invalid_pixels(self):
        pid, did, _ = self._source(nodata=255, legacy=True)
        result = self._clip(pid, did)
        with rasterio.open(self.storage_root / result["storage_key"]) as out:
            self.assertEqual(out.nodata, 255)
            self.assertEqual(np.count_nonzero(out.dataset_mask() == 0), 100)

    def test_clip_retains_explicit_mask_without_nodata(self):
        pid, did, _ = self._source(mask=True, legacy=True)
        result = self._clip(pid, did)
        with rasterio.open(self.storage_root / result["storage_key"]) as out:
            self.assertIsNone(out.nodata)
            self.assertEqual(np.count_nonzero(out.dataset_mask() == 0), 25)

    def test_slice_retains_nodata_mask_key_and_year(self):
        pid, did, _ = self._source(nodata=255, legacy=True)
        response = self.client.post(f"/api/projects/{pid}/imagery/slice", json={
            "source": did, "mode": "grid_pixels", "tile_pixels": 256,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        item = response.get_json()["data"]["items"][0]
        with rasterio.open(self.storage_root / item["storage_key"]) as out:
            self.assertEqual(out.nodata, 255)
            self.assertEqual(np.count_nonzero(out.dataset_mask() == 0), 100)
        derived = db.session.get(ProjectDataset, item["dataset_id"])
        self.assertEqual(derived.file_path, item["storage_key"])
        self.assertEqual(derived.year_start, 2022)

    def test_clip_outward_rounds_fractional_window(self):
        pid, did, _ = self._source(legacy=True)
        geometry = box(102.441001, 24.796009, 102.443009, 24.798001)
        result = self._clip(pid, did, geometry=geometry.__geo_interface__)
        with rasterio.open(self.storage_root / result["storage_key"]) as out:
            self.assertTrue(box(*out.bounds).covers(geometry))

    def test_buffer_encloses_independent_metric_buffer(self):
        pid, did, _ = self._source(legacy=True)
        # 50m 外扩完全位于源影像内；独立 AEQD 校验，不重复实现算法。
        geometry = box(102.4415, 24.7965, 102.4435, 24.7985)
        metric = "+proj=aeqd +lat_0=24.7975 +lon_0=102.4425 +datum=WGS84 +units=m"
        forward = lambda x, y: project_coordinates("EPSG:4326", metric, x, y)
        inverse = lambda x, y: project_coordinates(metric, "EPSG:4326", x, y)
        expected = transform(inverse, transform(forward, geometry).buffer(50, resolution=64))
        result = self._clip(pid, did, 50, geometry.__geo_interface__)
        with rasterio.open(self.storage_root / result["storage_key"]) as out:
            self.assertTrue(box(*out.bounds).covers(expected), expected.difference(box(*out.bounds)).area)

    def test_derived_relative_path_is_valid_inference_input(self):
        pid, did, _ = self._source()
        derived_key = f"projects/{pid}/inputs/imagery/derived.tif"
        derived = self.storage_root / derived_key
        derived.parent.mkdir(parents=True)
        derived.write_bytes(b"II*\x00")
        self.assertEqual(_resolve_registered_tif(self.storage_root, derived_key, pid), derived)

    def test_external_and_other_project_dataset_sources_are_rejected(self):
        pid, did, path = self._source()
        outside = Path(self.temp_dir.name) / "outside.tif"
        outside.write_bytes(path.read_bytes())
        other = self._create_project("另一项目")
        other_path = self.storage_root / f"projects/{other}/inputs/imagery/other.tif"
        other_path.parent.mkdir(parents=True)
        other_path.write_bytes(path.read_bytes())
        for raw in (str(outside), str(other_path), f"incoming/../projects/{other}/inputs/imagery/other.tif"):
            dataset = db.session.get(ProjectDataset, did)
            dataset.file_path = raw
            db.session.commit()
            response = self.client.post(f"/api/projects/{pid}/imagery/clip", json={
                "source": did, "geometry": box(102.44,24.795,102.445,24.80).__geo_interface__,
            })
            self.assertEqual(response.status_code, 400, raw)

    def test_legacy_detail_never_exposes_physical_dataset_paths(self):
        pid, did, path = self._source()
        dataset = db.session.get(ProjectDataset, did)
        dataset.file_path = str(path)
        db.session.commit()
        response = self.client.get(f"/api/projects/{pid}")
        self.assertNotIn(str(self.storage_root), response.get_data(as_text=True))
        self.assertEqual(response.get_json()["data"]["datasets"][0]["file_path"], "incoming/audit.tif")

    def test_snapshot_roundtrip_incoming_keeps_relative_reference(self):
        pid, did, _ = self._source()
        manifest = _project_manifest(db.session.get(Project, pid), 1)
        import_backup_manifest(pid, manifest)
        self.assertEqual(ProjectDataset.query.filter_by(project_id=pid).one().file_path, "incoming/audit.tif")

    def test_snapshot_project_reference_keeps_relative_key(self):
        pid, did, _ = self._source()
        dataset = db.session.get(ProjectDataset, did)
        dataset.file_path = f"projects/{pid}/inputs/imagery/from-snapshot.tif"
        db.session.commit()
        manifest = _project_manifest(db.session.get(Project, pid), 1)
        import_backup_manifest(pid, manifest)
        self.assertEqual(ProjectDataset.query.filter_by(project_id=pid).one().file_path, manifest["datasets"][0]["file_path"])

    def test_delete_commit_failure_restores_directory_and_database(self):
        self.login_as_admin()
        pid = self._create_project()
        root = self.storage_root / "projects" / str(pid)
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve")
        service.archive_project(pid)
        with patch.object(db.session, "commit", side_effect=RuntimeError("injected commit failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                service.delete_project(pid)
        db.session.remove()
        self.assertIsNone(db.session.get(Project, pid).deleted_at)
        self.assertEqual((root / "sentinel.txt").read_text(), "preserve")
        self.assertEqual(list((self.storage_root / "trash").glob(f"{pid}_*")), [])


if __name__ == "__main__":
    unittest.main()
