import copy
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
    ClassificationRevision,
)
from applications.models.project import ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.classification_results import publish_classification_result
from applications.project_hub.service import create_project


class TestClassificationResultAPI(unittest.TestCase):
    ROI = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [16.0, 0.0], [16.0, 16.0], [0.0, 16.0], [0.0, 0.0]]
        ],
    }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.root / "project_storage")
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_user_id"] = 1
        self.project_id, self.mine_resource, self.basemap_resource = self._create_project_resources()
        self.result = self._create_ready_result()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
        self.temp_dir.cleanup()

    def _create_project_resources(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(ProjectMineBinding(project_id=project["id"], mine_fid=101, sort_order=0))
        db.session.add(ProjectMineBinding(project_id=project["id"], mine_fid=102, sort_order=1))
        mine_relative = Path("projects") / str(project["id"]) / "resources" / "mine-vector-1" / "mines.geojson"
        mine_resource = ProjectSpatialResource(
            project_id=project["id"],
            resource_type="mine_vector",
            version=1,
            status="active",
            source_path=mine_relative.as_posix(),
            normalized_path=mine_relative.as_posix(),
            source_format="geojson",
            feature_count=1,
            crs="EPSG:4326",
            bounds_json=json.dumps([0.0, 0.0, 16.0, 16.0]),
        )
        db.session.add(mine_resource)
        db.session.flush()
        tile_relative = Path("projects") / str(project["id"]) / "tiles" / "basemap-1"
        basemap_resource = ProjectSpatialResource(
            project_id=project["id"],
            resource_type="basemap",
            version=1,
            status="active",
            source_path="incoming/source.tif",
            tile_path=tile_relative.as_posix(),
            source_format="tif",
            crs="EPSG:4326",
            bounds_json=json.dumps([0.0, 0.0, 16.0, 16.0]),
            min_zoom=0,
            max_zoom=4,
        )
        db.session.add(basemap_resource)
        db.session.commit()
        mine_path = Path(os.environ["PROJECT_STORAGE_ROOT"]) / mine_relative
        mine_path.parent.mkdir(parents=True, exist_ok=True)
        mine_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"FID_1": 101},
                            "geometry": self.ROI,
                        },
                        {
                            "type": "Feature",
                            "properties": {"FID_1": 102},
                            "geometry": self.ROI,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        tile_path = Path(os.environ["PROJECT_STORAGE_ROOT"]) / tile_relative / "1" / "2"
        tile_path.mkdir(parents=True, exist_ok=True)
        (tile_path / "3.png").write_bytes(b"tile-png")
        (tile_path.parent.parent / ".active").write_text("basemap-ready", encoding="ascii")
        return project["id"], mine_resource, basemap_resource

    def _create_ready_result(self, *, fid=101, year=2024, inference_job_id="job-api-ready"):
        label_path = self.root / "source-label.tif"
        labels = np.full((16, 16), 255, dtype=np.uint8)
        labels[0:4, 0:4] = 0
        with rasterio.open(
            label_path,
            "w",
            driver="GTiff",
            height=16,
            width=16,
            count=1,
            dtype="uint8",
            nodata=255,
            crs="EPSG:4326",
            transform=from_origin(0.0, 16.0, 1.0, 1.0),
        ) as dataset:
            dataset.write(labels, 1)
        published = publish_classification_result(
            project_id=self.project_id,
            fid=fid,
            year=year,
            inference_job_id=inference_job_id,
            mine_resource_id=self.mine_resource.id,
            model_id="cc-ln/CUGRS",
            label_path=label_path,
        )
        return ClassificationResult.query.get(published["result_id"])

    def _create_empty_result(self, *, fid=102, year=2025, inference_job_id="job-api-empty"):
        label_path = self.root / "empty-label.tif"
        with rasterio.open(
            label_path,
            "w",
            driver="GTiff",
            height=16,
            width=16,
            count=1,
            dtype="uint8",
            nodata=255,
            crs="EPSG:4326",
            transform=from_origin(0.0, 16.0, 1.0, 1.0),
        ) as dataset:
            dataset.write(np.full((16, 16), 255, dtype=np.uint8), 1)
        published = publish_classification_result(
            project_id=self.project_id,
            fid=fid,
            year=year,
            inference_job_id=inference_job_id,
            mine_resource_id=self.mine_resource.id,
            model_id="cc-ln/CUGRS",
            label_path=label_path,
        )
        return ClassificationResult.query.get(published["result_id"])

    def _editable_feature_collection(self):
        collection = json.loads(self.result.current_feature_collection_json)
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "feature_id": feature["properties"]["feature_id"],
                        "class_code": feature["properties"]["class_code"],
                    },
                    "geometry": copy.deepcopy(feature["geometry"]),
                }
                for feature in collection["features"]
            ],
        }

    def _result_url(self, suffix=""):
        return f"/api/projects/{self.project_id}/classification-results/{self.result.id}{suffix}"

    def _revision_snapshot_path(self, revision_no):
        return (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / "projects"
            / str(self.project_id)
            / "outputs"
            / "classification-results"
            / str(self.result.id)
            / "revisions"
            / f"{revision_no}.geojson"
        )

    def _revision_state(self):
        db.session.refresh(self.result)
        return {
            "current_revision_no": self.result.current_revision_no,
            "revision_count": ClassificationRevision.query.filter_by(result_id=self.result.id).count(),
            "audit_count": ClassificationEditAudit.query.filter_by(result_id=self.result.id).count(),
        }

    def _assert_rejected_without_writes(self, body, *, expected_reason=None):
        before = self._revision_state()
        response = self.client.post(self._result_url("/revisions"), json=body)
        self.assertEqual(response.status_code, 422)
        if expected_reason is not None:
            self.assertEqual(response.get_json()["data"]["details"]["reason"], expected_reason)
        self.assertEqual(self._revision_state(), before)

    @staticmethod
    def _ring_with_positions(position_count):
        unique_positions = position_count - 1
        ring = [
            [
                8.0 + math.cos(2 * math.pi * index / unique_positions),
                8.0 + math.sin(2 * math.pi * index / unique_positions),
            ]
            for index in range(unique_positions)
        ]
        return ring + [ring[0]]

    @staticmethod
    def _new_feature(geometry, *, class_code=2, feature_id=None):
        properties = {"class_code": class_code}
        if feature_id is not None:
            properties["feature_id"] = feature_id
        return {
            "type": "Feature",
            "properties": properties,
            "geometry": geometry,
        }

    def test_read_returns_full_class_table_current_snapshot_and_secure_map_manifest(self):
        response = self.client.get(self._result_url())

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["result_id"], self.result.id)
        self.assertEqual(data["vector_status"], "ready")
        self.assertEqual([item["class_code"] for item in data["classes"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual(data["current_revision_no"], 0)
        self.assertEqual(len(data["auto_feature_collection"]["features"]), 1)
        # 编辑器绘制时越 ROI 即时提示依赖此字段（2026-11-13）：只读几何下发
        roi = data["roi_geometry"]
        self.assertIn(roi["type"], ("Polygon", "MultiPolygon"))
        self.assertTrue(roi["coordinates"])
        self.assertEqual(
            data["map_manifest"]["api_tile_url"],
            f"/api/projects/{self.project_id}/map-resources/{self.basemap_resource.id}/tiles/{{z}}/{{x}}/{{y}}.png",
        )

    def test_revision_save_creates_manual_snapshot_and_stale_base_returns_409(self):
        feature_collection = self._editable_feature_collection()
        feature_collection["features"].append(
            {
                "type": "Feature",
                "properties": {"feature_id": "client-new-1", "class_code": 2},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]],
                },
            }
        )
        body = {"base_revision_no": 0, "feature_collection": feature_collection}

        response = self.client.post(self._result_url("/revisions"), json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["revision_no"], 1)
        self.assertEqual(data["current_revision_no"], 1)
        saved_features = data["feature_collection"]["features"]
        self.assertTrue(all(item["properties"]["source"] == "manual" for item in saved_features))
        self.assertTrue(any(item["properties"]["feature_id"].startswith(f"manual-{self.result.id}-") for item in saved_features))
        self.assertEqual(ClassificationRevision.query.filter_by(result_id=self.result.id).count(), 2)

        stale = self.client.post(self._result_url("/revisions"), json=body)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["data"]["details"], {
            "base_revision_no": 0,
            "current_revision_no": 1,
        })

    def test_saved_revision_snapshot_is_a_geojson_object_on_disk(self):
        """修订快照文件本身必须是 GeoJSON 对象。

        2026-11-24 的回归（48e3c64）：保存路径把 JSON 文本交给了写盘函数，文件被再编码一层，
        顶层成了字符串。库里是对的、运行时也没有代码读这些快照，功能不受影响，直到真实编辑
        测试后逐个核对存储才发现——此前的测试没有一处读回磁盘上的快照。
        """
        from applications.project_hub.classification_results import _write_json_temporary

        feature_collection = self._editable_feature_collection()
        response = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": feature_collection},
        )
        self.assertEqual(response.status_code, 200)
        snapshot_path = self._revision_snapshot_path(1)
        raw = snapshot_path.read_text(encoding="utf-8")
        self.assertTrue(raw.lstrip().startswith("{"), "快照文件顶层必须是 JSON 对象，不能是被引号包住的字符串")
        snapshot = json.loads(raw)
        self.assertEqual(snapshot["type"], "FeatureCollection")
        self.assertEqual(snapshot, response.get_json()["data"]["feature_collection"])
        revision = ClassificationRevision.query.filter_by(result_id=self.result.id, revision_no=1).first()
        self.assertEqual(raw, revision.feature_collection_json, "磁盘快照必须与库里存的修订快照逐字节相同")

        # 自动基线同样必须是对象（发布路径一直是对的，这里只是守护）
        auto_raw = (Path(os.environ["PROJECT_STORAGE_ROOT"]) / self.result.auto_geojson_path).read_text(encoding="utf-8")
        self.assertEqual(json.loads(auto_raw)["type"], "FeatureCollection")

        # 把 JSON 文本交给写盘函数会被明确拒绝，而不是悄悄多编码一层
        with self.assertRaises(TypeError):
            _write_json_temporary(self.root / "x.geojson", json.dumps({"type": "FeatureCollection", "features": []}))

    def test_revision_list_returns_only_summary_fields(self):
        response = self.client.get(self._result_url("/revisions"))

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["result_id"], self.result.id)
        self.assertEqual(data["current_revision_no"], 0)
        self.assertEqual(data["revisions"], [
            {
                "revision_no": 0,
                "source": "auto",
                "author": "system",
                "created_at": data["revisions"][0]["created_at"],
                "feature_count": 1,
            }
        ])

    def test_save_rejects_extra_server_properties_invalid_dimension_and_keeps_current_revision(self):
        collection = self._editable_feature_collection()
        collection["features"][0]["properties"]["source"] = "auto"
        rejected = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": collection},
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.result.current_revision_no, 0)

        collection = self._editable_feature_collection()
        collection["features"][0]["geometry"]["coordinates"][0][0] = [0.0, 16.0, 9.0]
        rejected = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": collection},
        )
        self.assertEqual(rejected.status_code, 422)
        db.session.refresh(self.result)
        self.assertEqual(self.result.current_revision_no, 0)
        self.assertEqual(ClassificationRevision.query.filter_by(result_id=self.result.id).count(), 1)

    def test_save_rejects_empty_feature_id_instead_of_treating_it_as_omitted(self):
        collection = self._editable_feature_collection()
        collection["features"].append(
            self._new_feature(
                {
                    "type": "Polygon",
                    "coordinates": [[[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]],
                },
                feature_id="",
            )
        )

        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="新要素必须省略 feature_id 或使用 client- 前缀 ID",
        )

    def test_save_rejects_capacity_boundaries_without_partial_writes(self):
        base_body = {"base_revision_no": 0, "feature_collection": {"type": "FeatureCollection", "features": []}}
        oversized_body = json.dumps(base_body).encode("utf-8") + b" " * (10 * 1024 * 1024)
        before = self._revision_state()
        response = self.client.post(
            self._result_url("/revisions"),
            data=oversized_body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["data"]["details"]["reason"], "请求 JSON 超过 10 MiB")
        self.assertEqual(self._revision_state(), before)

        feature = self._new_feature(
            {
                "type": "Polygon",
                "coordinates": [[[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]],
            }
        )
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {"type": "FeatureCollection", "features": [feature] * 2001},
            },
            expected_reason="要素数量不能超过 2000",
        )

        oversized_ring = self._ring_with_positions(5_001)
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature({"type": "Polygon", "coordinates": [oversized_ring]})
                    ],
                },
            },
            expected_reason="单要素坐标位置不能超过 5000",
        )

        maximum_ring = self._ring_with_positions(5_000)
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature({"type": "Polygon", "coordinates": [maximum_ring]})
                        for _ in range(20)
                    ]
                    + [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [
                                    [[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]
                                ],
                            }
                        )
                    ],
                },
            },
            expected_reason="总坐标位置不能超过 100000",
        )

    def test_save_rejects_nonfinite_outside_roi_invalid_and_nonpolygon_geometry(self):
        collection = self._editable_feature_collection()
        collection["features"][0]["geometry"]["coordinates"][0][0] = [float("nan"), 16.0]
        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="坐标位置必须是有限数值",
        )

        collection = self._editable_feature_collection()
        collection["features"][0]["geometry"]["coordinates"][0][0] = [181.0, 16.0]
        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="坐标超出 EPSG:4326 范围",
        )

        collection = self._editable_feature_collection()
        collection["features"][0]["geometry"]["coordinates"][0][0] = [float("inf"), 16.0]
        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="坐标位置必须是有限数值",
        )

        collection = self._editable_feature_collection()
        collection["features"][0]["geometry"]["coordinates"][0][0] = [float("-inf"), 16.0]
        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="坐标位置必须是有限数值",
        )

        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "LineString",
                                "coordinates": [[4.0, 4.0], [8.0, 8.0]],
                            }
                        )
                    ],
                },
            },
            expected_reason="仅支持 Polygon 或 MultiPolygon",
        )

        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [
                                    [[4.0, 4.0], [8.0, 8.0], [4.0, 8.0], [8.0, 4.0], [4.0, 4.0]]
                                ],
                            }
                        )
                    ],
                },
            },
            expected_reason="几何必须是非空有效面",
        )

        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [
                                    [[15.0, 15.0], [17.0, 15.0], [17.0, 17.0], [15.0, 17.0], [15.0, 15.0]]
                                ],
                            }
                        )
                    ],
                },
            },
            expected_reason="几何必须完全位于成果 ROI 内",
        )

    def test_save_accepts_submillimeter_roi_boundary_sliver_from_auto_baseline(self):
        """自动矢量化产物与 ROI 边界浮点相切（~1e-10 度越界）应可保存——真实栈
        H-2 发现 R4（2026-10-25）：成果 62 共 146 要素中 40 个被严格 covers 拒绝，
        未改动的自动基线整体无法保存，且全库无任何 manual 修订佐证此链从未走通。
        1e-9 度（亚毫米）容差只吸收浮点误差，像素级真实越界仍拒（上方用例）。"""
        roi = shape(json.loads(self.result.roi_geometry_json))
        min_x, min_y = roi.bounds[0], roi.bounds[1]
        sliver = 1e-10
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"class_code": 2},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [min_x - sliver, min_y],
                            [min_x + 1.0, min_y],
                            [min_x + 1.0, min_y + 1.0],
                            [min_x - sliver, min_y + 1.0],
                            [min_x - sliver, min_y],
                        ]],
                    },
                }
            ],
        }
        response = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": collection},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["revision_no"], 1)

    def test_save_rejects_duplicate_cross_result_and_cross_base_feature_ids(self):
        collection = self._editable_feature_collection()
        collection["features"].append(copy.deepcopy(collection["features"][0]))
        self._assert_rejected_without_writes(
            {"base_revision_no": 0, "feature_collection": collection},
            expected_reason="请求内 feature_id 必须唯一",
        )

        duplicate_client_features = [
            self._new_feature(
                {
                    "type": "Polygon",
                    "coordinates": [[[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0], [1.0, 1.0]]],
                },
                feature_id="client-duplicate",
            ),
            self._new_feature(
                {
                    "type": "Polygon",
                    "coordinates": [[[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]],
                },
                feature_id="client-duplicate",
            ),
        ]
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {"type": "FeatureCollection", "features": duplicate_client_features},
            },
            expected_reason="请求内 feature_id 必须唯一",
        )

        other_result = self._create_ready_result(
            fid=102,
            year=2025,
            inference_job_id="job-api-other-result",
        )
        other_feature_id = json.loads(other_result.current_feature_collection_json)["features"][0]["properties"]["feature_id"]
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [[[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]],
                            },
                            feature_id=other_feature_id,
                        )
                    ],
                },
            },
            expected_reason="既有 ID 不属于基准版本",
        )

        baseline_id = self._editable_feature_collection()["features"][0]["properties"]["feature_id"]
        saved = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": {"type": "FeatureCollection", "features": []}},
        )
        self.assertEqual(saved.status_code, 200)
        self._assert_rejected_without_writes(
            {
                "base_revision_no": 1,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [[[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]],
                            },
                            feature_id=baseline_id,
                        )
                    ],
                },
            },
            expected_reason="既有 ID 不属于基准版本",
        )

    def test_save_conflict_preserves_existing_winner_snapshot(self):
        winner_snapshot = self._revision_snapshot_path(1)
        winner_snapshot.parent.mkdir(parents=True, exist_ok=True)
        winner_collection = {"type": "FeatureCollection", "features": []}
        winner_snapshot.write_text(json.dumps(winner_collection), encoding="utf-8")
        db.session.add(
            ClassificationRevision(
                result_id=self.result.id,
                revision_no=1,
                source="manual",
                author="other-admin",
                feature_count=0,
                snapshot_path=(
                    Path("projects")
                    / str(self.project_id)
                    / "outputs"
                    / "classification-results"
                    / str(self.result.id)
                    / "revisions"
                    / "1.geojson"
                ).as_posix(),
                feature_collection_json=json.dumps(winner_collection),
            )
        )
        db.session.commit()

        response = self.client.post(
            self._result_url("/revisions"),
            json={"base_revision_no": 0, "feature_collection": self._editable_feature_collection()},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(winner_snapshot.read_text(encoding="utf-8")), winner_collection)
        db.session.refresh(self.result)
        self.assertEqual(self.result.current_revision_no, 0)

    def test_result_cannot_be_read_through_another_project_url(self):
        other_project = create_project({"name": "Other project", "region": "Dali"})

        response = self.client.get(
            f"/api/projects/{other_project['id']}/classification-results/{self.result.id}"
        )

        self.assertEqual(response.status_code, 404)

    def test_ready_empty_result_returns_empty_baseline_and_accepts_manual_save(self):
        result = self._create_empty_result()
        url = f"/api/projects/{self.project_id}/classification-results/{result.id}"

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["vector_status"], "ready_empty")
        self.assertEqual(data["auto_feature_collection"], {"type": "FeatureCollection", "features": []})
        self.assertEqual(data["current_feature_collection"], {"type": "FeatureCollection", "features": []})
        self.assertEqual(data["current_revision_no"], 0)

        saved = self.client.post(
            f"{url}/revisions",
            json={
                "base_revision_no": 0,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {
                                "type": "Polygon",
                                "coordinates": [
                                    [[4.0, 4.0], [8.0, 4.0], [8.0, 8.0], [4.0, 8.0], [4.0, 4.0]]
                                ],
                            }
                        )
                    ],
                },
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"]["current_revision_no"], 1)

    def test_vector_failed_save_returns_contract_409_and_export_uses_server_current_snapshot(self):
        missing = publish_classification_result(
            project_id=self.project_id,
            fid=101,
            year=2025,
            inference_job_id="job-api-missing",
            mine_resource_id=self.mine_resource.id,
            model_id="cc-ln/CUGRS",
            label_path=self.root / "missing-label.tif",
        )
        failed_result_id = missing["result_id"]
        rejected = self.client.post(
            f"/api/projects/{self.project_id}/classification-results/{failed_result_id}/revisions",
            json={"base_revision_no": 0, "feature_collection": {"type": "FeatureCollection", "features": []}},
        )
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.get_json()["data"]["details"]["error"], "vector_failed")
        self.assertFalse(rejected.get_json()["data"]["details"]["save_allowed"])

        failed_read = self.client.get(
            f"/api/projects/{self.project_id}/classification-results/{failed_result_id}"
        )
        self.assertEqual(failed_read.status_code, 200)
        failed_data = failed_read.get_json()["data"]
        self.assertEqual(failed_data["vector_status"], "vector_failed")
        self.assertIsNone(failed_data["auto_feature_collection"])
        self.assertIsNone(failed_data["current_feature_collection"])
        self.assertIsNone(failed_data["current_revision_no"])

        export = self.client.post(self._result_url("/export"))
        self.assertEqual(export.status_code, 200)
        exported = json.loads(export.data)
        self.assertEqual(exported, json.loads(self.result.current_feature_collection_json))

    def test_revision_and_export_do_not_leak_result_through_another_project(self):
        other_project = create_project({"name": "Other project", "region": "Dali"})
        base = f"/api/projects/{other_project['id']}/classification-results/{self.result.id}"

        self.assertEqual(self.client.get(f"{base}/revisions").status_code, 404)
        self.assertEqual(self.client.post(f"{base}/export").status_code, 404)

    def test_export_rejects_a_request_body(self):
        response = self.client.post(self._result_url("/export"), json={"features": []})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["data"]["details"]["field"], "body")

    def _stored_feature(self, geometry, *, class_code, feature_id):
        return {
            "type": "Feature",
            "properties": {
                "feature_id": feature_id,
                "class_code": class_code,
                "class_name": {0: "grassland", 1: "forest"}[class_code],
                "source": "auto",
                "result_id": self.result.id,
                "revision_no": 0,
            },
            "geometry": geometry,
        }

    def _replace_stored_baseline(self, collection):
        """把 Revision 0 与成果的自动/当前集合改写成给定集合。

        模拟"自动矢量化发布出超过保存上限的要素"：发布路径不经过保存校验，
        生产上确有 10 条成果的单要素坐标数在 5020–6422 之间（上限 5000）。
        """
        payload = json.dumps(collection, ensure_ascii=False)
        revision = ClassificationRevision.query.filter_by(result_id=self.result.id, revision_no=0).first()
        revision.feature_collection_json = payload
        revision.feature_count = len(collection["features"])
        self.result.auto_feature_collection_json = payload
        self.result.current_feature_collection_json = payload
        self.result.feature_count = len(collection["features"])
        db.session.commit()
        db.session.expire(self.result)

    def test_save_keeps_oversized_baseline_feature_that_did_not_grow(self):
        oversized_id = "auto-oversized"
        oversized_ring = self._ring_with_positions(5_001)
        self._replace_stored_baseline({
            "type": "FeatureCollection",
            "features": [
                self._stored_feature(
                    {"type": "Polygon", "coordinates": [oversized_ring]},
                    class_code=0,
                    feature_id=oversized_id,
                )
            ],
        })

        def body(geometry, *, class_code=0):
            return {
                "base_revision_no": self.result.current_revision_no,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(geometry, class_code=class_code, feature_id=oversized_id),
                    ],
                },
            }

        # 原样保存并改类：超限要素是从基准修订继承来的，必须能保存
        saved = self.client.post(
            self._result_url("/revisions"),
            json=body({"type": "Polygon", "coordinates": [oversized_ring]}, class_code=1),
        )
        self.assertEqual(saved.status_code, 200)
        saved_data = saved.get_json()["data"]
        self.assertEqual(saved_data["revision_no"], 1)
        self.assertEqual(saved_data["feature_collection"]["features"][0]["properties"]["class_code"], 1)

        # 多出一个坐标：既有要素变大，仍然拒绝
        grown = oversized_ring[:-1] + [[8.0, 8.0], oversized_ring[0]]
        self._assert_rejected_without_writes(
            body({"type": "Polygon", "coordinates": [grown]}),
            expected_reason="单要素坐标位置不能超过 5000",
        )

        # 变少：放行
        shrunk = self._ring_with_positions(5_000)
        saved = self.client.post(
            self._result_url("/revisions"),
            json=body({"type": "Polygon", "coordinates": [shrunk]}, class_code=1),
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"]["revision_no"], 2)

        # 新画的超限要素（临时 ID）不属于继承，一律受限
        self._assert_rejected_without_writes(
            {
                "base_revision_no": self.result.current_revision_no,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(
                            {"type": "Polygon", "coordinates": [oversized_ring]},
                            feature_id="client-new",
                        )
                    ],
                },
            },
            expected_reason="单要素坐标位置不能超过 5000",
        )

    def test_save_capacity_limits_constrain_growth_not_inheritance(self):
        """要素数与总坐标数同单要素坐标数：只约束相对基准修订的增长。"""
        from unittest import mock

        from applications.project_hub import classification_results

        def square(x, y):
            return {
                "type": "Polygon",
                "coordinates": [[[x, y], [x + 2.0, y], [x + 2.0, y + 2.0], [x, y + 2.0], [x, y]]],
            }

        def pentagon(x, y):
            return {
                "type": "Polygon",
                "coordinates": [
                    [[x, y], [x + 2.0, y], [x + 2.0, y + 2.0], [x + 1.0, y + 3.0], [x, y + 2.0], [x, y]]
                ],
            }

        shapes = {
            "auto-a-pentagon": pentagon(1.0, 1.0),
            "auto-b-square": square(5.0, 1.0),
            "auto-c-square": square(9.0, 1.0),
            "auto-d-square": square(13.0, 1.0),
            "auto-e-square": square(1.0, 9.0),
        }
        self._replace_stored_baseline({
            "type": "FeatureCollection",
            "features": [
                self._stored_feature(geometry, class_code=0, feature_id=feature_id)
                for feature_id, geometry in shapes.items()
            ],
        })

        def body(feature_ids, *, geometries=None, extra=()):
            geometries = geometries or shapes
            return {
                "base_revision_no": self.result.current_revision_no,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [
                        self._new_feature(geometries[feature_id], class_code=0, feature_id=feature_id)
                        for feature_id in feature_ids
                    ] + list(extra),
                },
            }

        # 四边形含闭合点是 5 个坐标、五边形是 6 个：单要素上限卡在两者之间
        patches = [
            mock.patch.object(classification_results, "MAX_FEATURES", 10),
            mock.patch.object(classification_results, "MAX_POSITIONS_PER_FEATURE", 5),
            mock.patch.object(classification_results, "MAX_POSITIONS_TOTAL", 9),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        everything = list(shapes)
        without_e = everything[:-1]
        # 5 个要素（上限 10）、26 个坐标（上限 9）、A 有 6 个坐标（上限 5）：全部是继承，原样保存放行
        saved = self.client.post(self._result_url("/revisions"), json=body(everything))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"]["revision_no"], 1)

        # 删掉一个：更小，放行
        saved = self.client.post(self._result_url("/revisions"), json=body(without_e))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"]["revision_no"], 2)

        # 把 E 加回来：总量 21 → 26，超过基准修订的总量，拒绝
        self._assert_rejected_without_writes(
            body(everything), expected_reason="总坐标位置不能超过 100000"
        )

        # A 退成 5 个坐标的面：放行
        small_a = dict(shapes, **{"auto-a-pentagon": square(1.0, 1.0)})
        saved = self.client.post(self._result_url("/revisions"), json=body(without_e, geometries=small_a))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"]["revision_no"], 3)

        # A 再变回 6 个坐标：比基准修订里的 A（5 个）大，拒绝
        self._assert_rejected_without_writes(
            body(without_e), expected_reason="单要素坐标位置不能超过 5000"
        )

        # 新画 7 个面：4 → 11 个，超过上限 10 也超过基准修订的 4 个，拒绝
        self._assert_rejected_without_writes(
            body(
                without_e,
                geometries=small_a,
                extra=[
                    self._new_feature(square(5.0 + 2.0 * index, 9.0), feature_id=f"client-new-{index}")
                    for index in range(7)
                ],
            ),
            expected_reason="要素数量不能超过 2000",
        )

    def test_effective_flag_marks_superseded_results_in_detail_and_list(self):
        """概览、台账、溯源只认有效成果；详情与编辑导航清单必须标出哪一条有效。"""
        list_url = f"/api/projects/{self.project_id}/classification-results"
        older_id = self.result.id

        self.assertTrue(self.client.get(self._result_url()).get_json()["data"]["effective"])
        newer = self._create_ready_result(fid=101, year=2024, inference_job_id="job-api-newer")

        # 同矿山同年再次推理：新成果（id 更大）成为有效成果，旧成果被取代
        self.assertFalse(self.client.get(self._result_url()).get_json()["data"]["effective"])
        self.assertTrue(
            self.client.get(
                f"/api/projects/{self.project_id}/classification-results/{newer.id}"
            ).get_json()["data"]["effective"]
        )

        # 更新的一次矢量化失败：不得抢走有效位（与统计口径同一条规则）
        failed = publish_classification_result(
            project_id=self.project_id,
            fid=101,
            year=2024,
            inference_job_id="job-api-failed",
            mine_resource_id=self.mine_resource.id,
            model_id="cc-ln/CUGRS",
            label_path=self.root / "missing-label.tif",
        )
        self.assertEqual(failed["vector_status"], "vector_failed")
        failed_detail = self.client.get(
            f"/api/projects/{self.project_id}/classification-results/{failed['result_id']}"
        ).get_json()["data"]
        self.assertFalse(failed_detail["effective"])
        self.assertTrue(
            self.client.get(
                f"/api/projects/{self.project_id}/classification-results/{newer.id}"
            ).get_json()["data"]["effective"]
        )

        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        items = response.get_json()["data"]["items"]
        flags = {(item["result_id"]): item["effective"] for item in items}
        self.assertEqual(flags[older_id], False)
        self.assertEqual(flags[newer.id], True)
        self.assertEqual(flags[failed["result_id"]], False)
        # 每个矿山每年恰好一条有效成果
        effective_by_mine_year = {}
        for item in items:
            key = (item["mine_fid"], item["year"])
            effective_by_mine_year[key] = effective_by_mine_year.get(key, 0) + (1 if item["effective"] else 0)
        self.assertTrue(effective_by_mine_year.values())
        self.assertTrue(all(count == 1 for count in effective_by_mine_year.values()), effective_by_mine_year)

    def test_secure_tile_route_requires_login_and_project_owned_active_resource(self):
        unauthenticated = self.app.test_client().get(
            f"/api/projects/{self.project_id}/map-resources/{self.basemap_resource.id}/tiles/1/2/3.png"
        )
        self.assertEqual(unauthenticated.status_code, 401)

        response = self.client.get(
            f"/api/projects/{self.project_id}/map-resources/{self.basemap_resource.id}/tiles/1/2/3.png"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"tile-png")
        response.close()

        # 超金字塔 maxZoom：200 透明 PNG 兜底（2026-11-05 生产化检查，对齐 BFF
        # miner/routes/tiles.js 的 J-1 语义——GeoView 直连本路由，逐格 404 刷屏）
        over_zoom = self.client.get(
            f"/api/projects/{self.project_id}/map-resources/{self.basemap_resource.id}/tiles/5/2/3.png"
        )
        self.assertEqual(over_zoom.status_code, 200)
        self.assertTrue(over_zoom.data.startswith(b"\x89PNG"))

        self.basemap_resource.status = "retired"
        db.session.commit()
        self.assertEqual(
            self.client.get(
                f"/api/projects/{self.project_id}/map-resources/{self.basemap_resource.id}/tiles/1/2/3.png"
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
