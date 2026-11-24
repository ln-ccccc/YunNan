"""编辑期几何接口（校验 / 简化 / 合并）的契约测试。

夹具沿用 test_classification_result_api（16×16 的方形矿山，一条含单个图斑的 ready 成果）。
四个状态都要有：成功、无数据（成果不属于该项目 / 不存在）、参数错误（422）、失败（合并结果不合法）；
外加只读性——三个接口都不写库、不写文件。请求体上限另见 test_classification_body_limit。

简化接口的容差是自适应的（2026-11-25）：按这条成果的 label 像元与每个图斑自己的面积定，
所以这里既测接口形状，也测像元从哪里来、读不到时怎么回退。
"""
import json
import math
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.project_hub import geometry_engine as engine
from applications.project_hub import classification_results as results_service
from applications.project_hub.classification_results import publish_classification_result, roi_context, simplify_scale
from applications.project_hub.service import create_project
from test_classification_result_api import TestClassificationResultAPI


def square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


INSIDE = square(1, 1, 5, 5)
OUTSIDE = square(1, 1, 16.5, 5)
BOW_TIE = {"type": "Polygon", "coordinates": [[[1, 1], [5, 5], [5, 1], [1, 5], [1, 1]]]}
# 右侧有 0.1 米幅值的锯齿（1e-6 度）：容差不小于它时应拉平成长方形
JAGGED = {"type": "Polygon", "coordinates": [[[6, 6], [9, 6], [9.000001, 7], [9, 7.5], [9.000001, 8], [9, 10], [6, 10], [6, 6]]]}
# 夹具矿山的中心在纬度 8 度；名义 1 度的 EPSG:4326 像元在这里的真尺寸（米），用独立的级数公式算
EARTH_A = 6378137.0
EARTH_E2 = 0.00669437999014
LAT = math.radians(8.0)
METRES_PER_DEGREE_LAT = math.pi / 180 * EARTH_A * (1 - EARTH_E2) / (1 - EARTH_E2 * math.sin(LAT) ** 2) ** 1.5
METRES_PER_DEGREE_LON = math.pi / 180 * EARTH_A * math.cos(LAT) / math.sqrt(1 - EARTH_E2 * math.sin(LAT) ** 2)


class TestClassificationGeometryAPI(unittest.TestCase):
    ROI = TestClassificationResultAPI.ROI
    setUp = TestClassificationResultAPI.setUp
    tearDown = TestClassificationResultAPI.tearDown
    _create_project_resources = TestClassificationResultAPI._create_project_resources
    _create_ready_result = TestClassificationResultAPI._create_ready_result
    _result_url = TestClassificationResultAPI._result_url
    _revision_state = TestClassificationResultAPI._revision_state

    def setUp(self):
        # 标架与 ROI 上下文都是进程级缓存，键里含成果 id；测试之间重建数据库后 id 会重复，
        # 不清掉的话上一个用例缓存的「这条成果的 label」会被下一个用例命中
        results_service._SIMPLIFY_SCALES.clear()
        results_service._ROI_CONTEXTS.clear()
        TestClassificationResultAPI.setUp(self)

    def post(self, suffix, payload, client=None):
        return (client or self.client).post(self._result_url(suffix), json=payload)

    def storage_state(self):
        root = Path(os.environ["PROJECT_STORAGE_ROOT"])
        return {
            path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*")
            if path.is_file()
        }

    def assertRejected(self, response, field):
        self.assertEqual(response.status_code, 422, response.data)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["data"]["details"]["field"], field)
        return payload["data"]["details"]

    def publish_with_label(self, *, crs, transform, tag):
        """用指定的坐标系与仿射变换造一个 label，发布成一条新成果（同一座矿山，任务标识不同）。"""
        label_path = self.root / f"label-{tag}.tif"
        labels = np.full((16, 16), 255, dtype=np.uint8)
        labels[0:4, 0:4] = 0
        with rasterio.open(
            label_path, "w", driver="GTiff", height=16, width=16, count=1, dtype="uint8", nodata=255,
            crs=crs, transform=transform,
        ) as dataset:
            dataset.write(labels, 1)
        published = publish_classification_result(
            project_id=self.project_id, fid=101, year=2030, inference_job_id=f"job-{tag}",
            mine_resource_id=self.mine_resource.id, model_id="cc-ln/CUGRS", label_path=label_path,
        )
        return ClassificationResult.query.get(published["result_id"])

    def pixel_size_reported_for(self, result):
        response = self.client.post(
            f"/api/projects/{self.project_id}/classification-results/{result.id}/geometry/simplify",
            json={"geometries": [INSIDE]},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()["data"]["pixel_size_m"]

    # ---- validate ----
    def test_validate_success_matches_save_verdicts(self):
        response = self.post("/geometry/validate", {"geometries": [INSIDE, OUTSIDE, BOW_TIE]})
        self.assertEqual(response.status_code, 200, response.data)
        items = response.get_json()["data"]["items"]
        self.assertEqual(items[0], {"ok": True})
        self.assertEqual((items[1]["ok"], items[1]["code"]), (False, "OUTSIDE_ROI"))
        self.assertGreater(items[1]["outside_area_m2"], 0)
        self.assertEqual((items[2]["ok"], items[2]["code"]), (False, "INVALID_GEOMETRY"))

        # 与保存是同一个判定函数：保存被拒的理由与校验给出的理由逐字相同
        feature_id = json.loads(self.result.current_feature_collection_json)["features"][0]["properties"]["feature_id"]
        for geometry, item in ((OUTSIDE, items[1]), (BOW_TIE, items[2])):
            saved = self.client.post(
                self._result_url("/revisions"),
                json={
                    "base_revision_no": self.result.current_revision_no,
                    "feature_collection": {
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {"class_code": 0, "feature_id": feature_id}, "geometry": geometry}],
                    },
                },
            )
            self.assertEqual(saved.status_code, 422)
            details = saved.get_json()["data"]["details"]
            self.assertEqual((details["field"], details["reason"]), (item["field"], item["reason"]))
        # 而校验通过的面能保存
        saved = self.client.post(
            self._result_url("/revisions"),
            json={
                "base_revision_no": self.result.current_revision_no,
                "feature_collection": {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "properties": {"class_code": 0, "feature_id": feature_id}, "geometry": INSIDE}],
                },
            },
        )
        self.assertEqual(saved.status_code, 200, saved.data)

    def test_validate_parameter_errors(self):
        details = self.assertRejected(
            self.client.post(self._result_url("/geometry/validate"), data=b"not json", content_type="application/json"), "body",
        )
        self.assertEqual(details["reason"], "必须提交 JSON")
        for payload, field in (
            ({}, "body"),
            ({"geometries": [], "extra": 1}, "body"),
            ({"geometries": "x"}, "geometries"),
            ({"geometries": []}, "geometries"),
            ({"geometries": [INSIDE] * 2001}, "geometries"),
            ({"geometries": [INSIDE, {"type": "Polygon"}]}, "geometry"),
            ({"geometries": [{"type": "Polygon", "coordinates": [[[0, 0]] * 200001]}]}, "geometries"),
        ):
            with self.subTest(payload=str(payload)[:60]):
                response = self.post("/geometry/validate", payload)
                details = self.assertRejected(response, field)
                self.assertEqual(response.get_json()["msg"], "几何请求不合法")
                if field == "geometry":
                    self.assertEqual(details["feature_index"], 1)

    def test_requires_login_and_result_in_project(self):
        unauthenticated = self.app.test_client().post(self._result_url("/geometry/validate"), json={"geometries": [INSIDE]})
        self.assertEqual(unauthenticated.status_code, 401)
        other_project = create_project({"name": "Other project", "region": "Dali"})
        base = f"/api/projects/{other_project['id']}/classification-results/{self.result.id}"
        for suffix, payload in (("/geometry/validate", {"geometries": [INSIDE]}), ("/geometry/simplify", {"geometries": [JAGGED]}),
                                ("/geometry/union", {"geometries": [INSIDE, INSIDE]})):
            with self.subTest(suffix=suffix):
                # 成果不属于 URL 里的项目：404；显式传了容差也先过归属检查（不泄露另一个项目的成果是否存在）
                self.assertEqual(self.client.post(f"{base}{suffix}", json=payload).status_code, 404)
                missing = self.client.post(f"/api/projects/{self.project_id}/classification-results/999999{suffix}", json=payload)
                self.assertEqual(missing.status_code, 404)

    # ---- simplify ----
    def test_simplify_outcomes_with_adaptive_tolerance(self):
        # 夹具的 label 一个像元是 1 度：自适应容差由 1 米的上限决定，仍足以拉平 0.1 米的锯齿
        response = self.post("/geometry/simplify", {"geometries": [JAGGED, INSIDE, OUTSIDE, BOW_TIE]})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.get_json()["data"]
        self.assertEqual((data["mode"], data["tolerance"]), ("adaptive", None))
        self.assertAlmostEqual(data["pixel_size_m"], METRES_PER_DEGREE_LON, delta=METRES_PER_DEGREE_LON * 0.01)
        changed, unchanged, outside, invalid = data["items"]
        self.assertEqual(changed["outcome"], "changed")
        self.assertEqual(set(changed), {"outcome", "geometry", "tolerance", "area_change", "shape_change"})
        self.assertAlmostEqual(changed["tolerance"] * METRES_PER_DEGREE_LAT, 1.0, places=2)  # 起始容差 = 1 米的上限
        # 返回的几何一定是保存会接受的，且在精度门槛内
        self.assertEqual(self.post("/geometry/validate", {"geometries": [changed["geometry"]]}).get_json()["data"]["items"], [{"ok": True}])
        self.assertEqual(shape(changed["geometry"]).area, 12)
        self.assertLessEqual(abs(changed["area_change"]), engine.MAX_SIMPLIFY_AREA_CHANGE)
        self.assertLessEqual(changed["shape_change"], engine.MAX_SIMPLIFY_SHAPE_CHANGE)
        self.assertEqual(unchanged, {"outcome": "unchanged"})
        self.assertEqual((outside["outcome"], outside["reason"]["code"]), ("rejected", "OUTSIDE_ROI"))
        self.assertEqual((invalid["outcome"], invalid["reason"]["code"]), ("rejected", "INVALID_GEOMETRY"))

    def test_simplify_reports_shape_drift_with_its_own_code(self):
        # 带细齿的窄条：候选结果合法，但去掉齿会让面积少一成以上——拒绝原因必须是 SHAPE_DRIFT，
        # 不能记成越界或非法，也不能说成「已经足够平滑」
        top = [[2 + index * 0.05, 8.0002 + (0.00004 if index % 2 else 0)] for index in range(81)]
        comb = {"type": "Polygon", "coordinates": [top + [[x, y - 0.0004] for x, y in reversed(top)] + [top[0]]]}
        self.assertTrue(engine.evaluate(roi_context(self.result), comb).ok)
        response = self.post("/geometry/simplify", {"geometries": [comb], "tolerance": 1e-3})
        self.assertEqual(response.status_code, 200, response.data)
        item = response.get_json()["data"]["items"][0]
        self.assertEqual((item["outcome"], item["reason"]["code"]), ("rejected", engine.SHAPE_DRIFT))
        self.assertNotIn("geometry", item)

    def test_simplify_pixel_size_comes_from_the_label(self):
        # EPSG:3857 的 label：名义 1 米的像元，在纬度 16 度处的真尺寸约 0.96 米
        north = EARTH_A * math.log(math.tan(math.pi / 4 + math.radians(16) / 2))
        mercator = self.publish_with_label(crs="EPSG:3857", transform=from_origin(0.0, north, 1.0, 1.0), tag="mercator")
        self.assertAlmostEqual(self.pixel_size_reported_for(mercator), math.cos(math.radians(16)), delta=0.005)
        # EPSG:4326 的 label：0.5 米的像元，两个方向的度数不同，换算回来都是 0.5 米
        latitude = math.radians(16)
        metres_lon = math.pi / 180 * EARTH_A * math.cos(latitude) / math.sqrt(1 - EARTH_E2 * math.sin(latitude) ** 2)
        metres_lat = math.pi / 180 * EARTH_A * (1 - EARTH_E2) / (1 - EARTH_E2 * math.sin(latitude) ** 2) ** 1.5
        geographic = self.publish_with_label(
            crs="EPSG:4326", transform=from_origin(0.0, 16.0, 0.5 / metres_lon, 0.5 / metres_lat), tag="geographic",
        )
        self.assertAlmostEqual(self.pixel_size_reported_for(geographic), 0.5, delta=0.005)
        scale = simplify_scale(geographic)
        self.assertAlmostEqual(scale.pixel_metres, 0.5, delta=0.005)
        # 米换成度按保守轴：与纬向每度的米数相乘正好是原值，与经向相乘只会更小
        self.assertAlmostEqual(scale.metres_to_degrees(0.5) * scale.metres_per_degree_lat, 0.5, places=9)
        self.assertLess(scale.metres_to_degrees(0.5) * scale.metres_per_degree_lon, 0.5)

    def test_simplify_falls_back_when_the_label_cannot_be_read(self):
        # 生产里有 14 条成果的 label 文件已经不在了：简化不能因此报错，按保守的 0.1 米回退，
        # 并在响应里说明像元未知。用的是回退值而不是旧的 1.1 米：0.1 米只去掉不到 0.07 米的偏差，较大的那级锯齿还在
        label = Path(os.environ["PROJECT_STORAGE_ROOT"]) / self.result.label_path
        self.assertTrue(label.is_file())
        label.unlink()
        response = self.post("/geometry/simplify", {"geometries": [JAGGED]})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.get_json()["data"]
        self.assertEqual((data["mode"], data["pixel_size_m"]), ("adaptive", None))
        item = data["items"][0]
        scale = simplify_scale(self.result)
        self.assertIsNone(scale.pixel_metres)
        self.assertEqual(item["outcome"], "changed")
        self.assertAlmostEqual(item["tolerance"], engine.FALLBACK_SIMPLIFY_TOLERANCE_M / scale.metres_per_degree_lat, places=18)
        self.assertIn([9.000001, 7.0], item["geometry"]["coordinates"][0], "0.1 米的容差去不掉 0.11 米幅值的锯齿")
        self.assertEqual(self.post("/geometry/validate", {"geometries": [item["geometry"]]}).get_json()["data"]["items"], [{"ok": True}])
        # 同一条成果第二次请求不再重复去读那个不存在的文件（每个进程每条成果最多读一次，读不到也一样）
        self.assertIs(simplify_scale(self.result), scale)

    def test_simplify_with_explicit_tolerance(self):
        response = self.post("/geometry/simplify", {"geometries": [JAGGED], "tolerance": 1e-5})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.get_json()["data"]
        self.assertEqual((data["mode"], data["tolerance"], data["pixel_size_m"]), ("explicit", 1e-5, None))
        self.assertEqual((data["items"][0]["outcome"], data["items"][0]["tolerance"]), ("changed", 1e-5))
        self.assertEqual(shape(data["items"][0]["geometry"]).area, 12)

    def test_simplify_tolerance_errors(self):
        for tolerance in (0, -1, "0.00001", True, 1e-2, math.nan):
            with self.subTest(tolerance=tolerance):
                self.assertRejected(self.post("/geometry/simplify", {"geometries": [JAGGED], "tolerance": tolerance}), "tolerance")
        # 容差非法时整批拒绝：不会一部分按旧容差、一部分按新容差，也不碰存储
        state_before = self._revision_state()
        self.assertRejected(self.post("/geometry/simplify", {"geometries": [JAGGED, INSIDE], "tolerance": -1}), "tolerance")
        self.assertEqual(self._revision_state(), state_before)
        # 不传、或显式传 null：都是自适应
        for payload in ({"geometries": [JAGGED]}, {"geometries": [JAGGED], "tolerance": None}):
            with self.subTest(payload=payload):
                response = self.post("/geometry/simplify", payload)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.get_json()["data"]["mode"], "adaptive")

    def test_simplify_scale_is_cached_per_result(self):
        first = simplify_scale(self.result)
        self.assertIs(simplify_scale(self.result), first)
        self.assertIsNotNone(first.pixel_metres)
        # 换了 label 就是另一份标架
        self.result.label_path = "projects/1/outputs/classification-results/1/other-label.tif"
        second = simplify_scale(self.result)
        self.assertIsNot(second, first)
        self.assertIsNone(second.pixel_metres)

    # ---- union ----
    def test_union_success(self):
        response = self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(5, 1, 9, 5)]})
        self.assertEqual(response.status_code, 200, response.data)
        geometry = response.get_json()["data"]["geometry"]
        merged = shape(geometry)
        self.assertEqual(geometry["type"], "Polygon")
        self.assertEqual(merged.area, 32)
        self.assertEqual(merged.symmetric_difference(shape(square(1, 1, 9, 5))).area, 0)
        # 两个不相邻的面合并成 MultiPolygon
        geometry = self.post("/geometry/union", {"geometries": [square(1, 1, 3, 3), square(10, 10, 12, 12)]}).get_json()["data"]["geometry"]
        self.assertEqual(geometry["type"], "MultiPolygon")

    def test_union_failures(self):
        details = self.assertRejected(self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(10, 10, 16.5, 12)]}), "geometry")
        self.assertEqual((details["code"], details["reason"]), ("OUTSIDE_ROI", "几何必须完全位于成果 ROI 内"))
        self.assertGreater(details["outside_area_m2"], 0)
        details = self.assertRejected(self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), BOW_TIE]}), "geometry")
        self.assertEqual(details["reason"], "几何必须是非空有效面")
        for payload, field in (({"geometries": [square(1, 1, 5, 5)]}, "geometries"),
                               ({"geometries": [INSIDE] * 51}, "geometries"),
                               ({"geometries": [INSIDE, INSIDE], "tolerance": 1e-5}, "body")):
            with self.subTest(payload=str(payload)[:50]):
                self.assertRejected(self.post("/geometry/union", payload), field)

    # ---- 只读性 ----
    def test_geometry_endpoints_never_write(self):
        storage_before = self.storage_state()
        state_before = self._revision_state()
        self.post("/geometry/validate", {"geometries": [INSIDE, OUTSIDE]})
        self.post("/geometry/simplify", {"geometries": [JAGGED, OUTSIDE]})
        self.post("/geometry/simplify", {"geometries": [JAGGED, INSIDE]})
        self.post("/geometry/simplify", {"geometries": [JAGGED], "tolerance": 1e-5})
        self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(5, 1, 9, 5)]})
        self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(10, 10, 16.5, 12)]})
        db.session.expire_all()
        self.assertEqual(self._revision_state(), state_before)
        # 读 label 的头信息不能在存储里留下任何副作用（例如 GDAL 的 .aux.xml）
        self.assertEqual(self.storage_state(), storage_before)

    def test_roi_context_is_cached_per_result(self):
        first = roi_context(self.result)
        self.assertIs(roi_context(self.result), first)
        self.assertEqual(first.roi.wkt, shape(TestClassificationResultAPI.ROI).wkt)
        self.result.roi_geometry_json = json.dumps(square(1, 1, 9, 9))
        self.assertIsNot(roi_context(self.result), first)
        self.assertEqual(roi_context(self.result).roi.area, 64)


if __name__ == "__main__":
    unittest.main()
