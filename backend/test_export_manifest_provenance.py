# -*- coding: utf-8 -*-
"""导出 manifest 来源追溯回归（2026-11-14 审查问题31）。

此前 manifest 只识别 imagery 数据集且版本一律钉 1：XLSX 台账实际读取
分类成果与当前图斑修订、geojson/shp/dxf 依赖激活矿山边界资源版本，
清单均无法解释。验收口径：修订/换版前后分别导出，文件内容与 manifest
同步变化；旧导出仍可追溯到原版本。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.project import (
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.models.project_spatial import ProjectSpatialResource


def _mine_feature(fid, name):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]
            ],
        },
        "properties": {"FID": fid, "name": name},
    }


class TestExportManifestProvenance(unittest.TestCase):
    def setUp(self):
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="export-manifest-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.addCleanup(self._restore_storage_root)
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))

    def _login_as_admin(self, password="Secret123!"):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = password
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = password
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        self.assertEqual(response.status_code, 200)

    def _create_project(self, name="追溯回归项目"):
        response = self.client.post(
            "/api/projects",
            json={
                "name": name,
                "region": "昆明市",
                "manager": "测试管理员",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self._json(response)["data"]["id"]

    def _export(self, project_id, payload):
        response = self.client.post(f"/api/projects/{project_id}/exports", json=payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self._json(response)["code"], 0)
        record = (
            ProjectExportRecord.query.filter_by(
                project_id=project_id, format=payload["format"]
            )
            .order_by(ProjectExportRecord.id.desc())
            .first()
        )
        self.assertEqual(record.status, "completed")
        return record

    def _read_manifest(self, record):
        path = (
            self.storage_root
            / Path(record.file_path).parent
            / "manifest.json"
        )
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _add_dataset(self, project_id, kind, mine_fid=101):
        dataset = ProjectDataset(
            project_id=project_id,
            dataset_kind=kind,
            display_name=f"{kind} 数据",
            file_path=f"projects/{project_id}/datasets/{kind}.tif",
            source_format="tif",
            mine_fid=mine_fid,
            year_start=2024,
            year_end=2024,
            slice_config_json="{}",
        )
        db.session.add(dataset)
        db.session.commit()
        return dataset

    def _add_classification_result(self, project_id, mine_fid=101, year=2024, revision_no=1, mine_resource_version=2):
        result = ClassificationResult(
            project_id=project_id,
            mine_fid=mine_fid,
            year=year,
            inference_job_id=f"job-{mine_fid}-{year}",
            model_id="cc-ln/CUGRS",
            mine_resource_id=11,
            mine_resource_version=mine_resource_version,
            vector_status="ready",
            current_revision_no=revision_no,
            current_feature_collection_json=json.dumps(
                {"type": "FeatureCollection", "features": []}
            ),
        )
        db.session.add(result)
        db.session.commit()
        return result

    def _activate_mine_vector(self, project_id, version, fid=101, name="矿山甲"):
        """落盘一份矿山 GeoJSON 并登记为激活 mine_vector 资源（version 递增换版）。"""
        rel = Path("projects") / str(project_id) / "vectors" / f"mines-v{version}.geojson"
        abs_path = self.storage_root / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": [_mine_feature(fid, name)]}),
            encoding="utf-8",
        )
        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="mine_vector",
            version=version,
            status="active",
            source_path=str(rel),
            normalized_path=rel.as_posix(),
            source_format="geojson",
        )
        db.session.add(resource)
        db.session.commit()
        return resource

    def test_xlsx_manifest_tracks_classification_revisions(self):
        """台账 manifest 记录成果 ID/修订号/边界版本；修订后再导出版本同步变化，
        旧导出仍指向原修订。"""
        self._login_as_admin()
        project_id = self._create_project()
        result = self._add_classification_result(revision_no=1, mine_resource_version=2, project_id=project_id)

        first = self._export(project_id, {"format": "xlsx"})
        first_manifest = self._read_manifest(first)
        self.assertEqual(
            first_manifest["classification_results"],
            [
                {
                    "result_id": result.id,
                    "mine_fid": 101,
                    "year": 2024,
                    "vector_status": "ready",
                    "revision_no": 1,
                    "mine_resource_version": 2,
                }
            ],
        )

        result.current_revision_no = 3
        result.mine_resource_version = 4
        db.session.commit()

        second = self._export(project_id, {"format": "xlsx"})
        second_manifest = self._read_manifest(second)
        self.assertEqual(second_manifest["classification_results"][0]["revision_no"], 3)
        self.assertEqual(second_manifest["classification_results"][0]["mine_resource_version"], 4)

        # 旧导出可追溯：第一个导出的 manifest 不随后续修订变化
        self.assertEqual(self._read_manifest(first)["classification_results"][0]["revision_no"], 1)

    def test_geojson_manifest_records_active_mine_vector_version(self):
        """矿山矢量导出记录激活边界资源版本；换版后再导出 manifest 同步变化。"""
        self._login_as_admin()
        project_id = self._create_project()
        db.session.add(ProjectMineBinding(project_id=project_id, mine_fid=101, mine_name_snapshot="矿山甲", sort_order=0))
        db.session.commit()
        first_resource = self._activate_mine_vector(project_id, version=2)

        first = self._export(project_id, {"format": "geojson"})
        first_manifest = self._read_manifest(first)
        self.assertEqual(
            first_manifest["mine_vector"],
            {"resource_id": first_resource.id, "version": 2},
        )

        second_resource = self._activate_mine_vector(project_id, version=3)
        second = self._export(project_id, {"format": "geojson"})
        second_manifest = self._read_manifest(second)
        self.assertEqual(
            second_manifest["mine_vector"],
            {"resource_id": second_resource.id, "version": 3},
        )
        self.assertEqual(self._read_manifest(first)["mine_vector"]["version"], 2)

    def test_csv_manifest_includes_non_imagery_dataset(self):
        """CSV 写出全部数据集行，清单须覆盖非 imagery kind（此前只识别 imagery）。"""
        self._login_as_admin()
        project_id = self._create_project()
        imagery = self._add_dataset(project_id, "imagery")
        inference = self._add_dataset(project_id, "inference_result")

        record = self._export(project_id, {"format": "csv"})
        manifest = self._read_manifest(record)
        self.assertEqual(
            manifest["source_asset_ids"],
            [f"imagery:{imagery.id}", f"inference_result:{inference.id}"],
        )
        self.assertEqual(
            manifest["source_versions"],
            {f"imagery:{imagery.id}": 1, f"inference_result:{inference.id}": 1},
        )

    def test_geojson_with_explicit_features_has_no_mine_vector_entry(self):
        """显式传入 features 不读矿山矢量，manifest 边界来源为空（不虚构来源）。"""
        self._login_as_admin()
        project_id = self._create_project()
        feature = _mine_feature(101, "外部要素")

        record = self._export(
            project_id,
            {"format": "geojson", "features": [feature]},
        )
        manifest = self._read_manifest(record)
        self.assertIsNone(manifest.get("mine_vector"))


if __name__ == "__main__":
    unittest.main()
