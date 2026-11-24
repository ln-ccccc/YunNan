"""编辑期几何接口（校验 / 简化 / 合并）的契约测试。

夹具沿用 test_classification_result_api（16×16 的方形矿山，一条含单个图斑的 ready 成果）。
四个状态都要有：成功、无数据（成果不属于该项目 / 不存在）、参数错误（422）、失败（合并结果不合法）；
外加只读性——三个接口都不写库、不写文件。
"""
import json
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.project_hub import geometry_engine as engine
from applications.project_hub.classification_results import roi_context
from applications.project_hub.service import create_project
from shapely.geometry import shape
from test_classification_result_api import TestClassificationResultAPI


def square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


INSIDE = square(1, 1, 5, 5)
OUTSIDE = square(1, 1, 16.5, 5)
BOW_TIE = {"type": "Polygon", "coordinates": [[[1, 1], [5, 5], [5, 1], [1, 5], [1, 1]]]}
# 右侧一毫米级的锯齿（远小于简化容差 0.00001 度的 100 倍以内）：简化后应成为长方形
JAGGED = {"type": "Polygon", "coordinates": [[[6, 6], [9, 6], [9.000001, 7], [9, 7.5], [9.000001, 8], [9, 10], [6, 10], [6, 6]]]}


class TestClassificationGeometryAPI(unittest.TestCase):
    ROI = TestClassificationResultAPI.ROI
    setUp = TestClassificationResultAPI.setUp
    tearDown = TestClassificationResultAPI.tearDown
    _create_project_resources = TestClassificationResultAPI._create_project_resources
    _create_ready_result = TestClassificationResultAPI._create_ready_result
    _result_url = TestClassificationResultAPI._result_url
    _revision_state = TestClassificationResultAPI._revision_state

    def post(self, suffix, payload, client=None):
        return (client or self.client).post(self._result_url(suffix), json=payload)

    def storage_state(self):
        return {
            path.relative_to(self.root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def assertRejected(self, response, field):
        self.assertEqual(response.status_code, 422, response.data)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["data"]["details"]["field"], field)
        return payload["data"]["details"]

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
        details = self.assertRejected(self.client.post(self._result_url("/geometry/validate"), data=b"not json",
                                                      content_type="application/json"), "body")
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

    def test_validate_requires_login_and_result_in_project(self):
        unauthenticated = self.app.test_client().post(self._result_url("/geometry/validate"), json={"geometries": [INSIDE]})
        self.assertEqual(unauthenticated.status_code, 401)
        other_project = create_project({"name": "Other project", "region": "Dali"})
        base = f"/api/projects/{other_project['id']}/classification-results/{self.result.id}"
        for suffix, payload in (("/geometry/validate", {"geometries": [INSIDE]}), ("/geometry/simplify", {"geometries": [INSIDE]}),
                                ("/geometry/union", {"geometries": [INSIDE, INSIDE]})):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.post(f"{base}{suffix}", json=payload).status_code, 404)
                self.assertEqual(self.client.post(f"/api/projects/{self.project_id}/classification-results/999999{suffix}", json=payload).status_code, 404)

    # ---- simplify ----
    def test_simplify_outcomes(self):
        response = self.post("/geometry/simplify", {"geometries": [JAGGED, INSIDE, OUTSIDE, BOW_TIE]})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.get_json()["data"]
        self.assertEqual(data["tolerance"], engine.SIMPLIFY_TOLERANCE_DEGREES)
        changed, unchanged, outside, invalid = data["items"]
        self.assertEqual(changed["outcome"], "changed")
        self.assertEqual(set(changed), {"outcome", "geometry"})
        # 返回的几何一定是保存会接受的
        self.assertEqual(self.post("/geometry/validate", {"geometries": [changed["geometry"]]}).get_json()["data"]["items"], [{"ok": True}])
        self.assertEqual(shape(changed["geometry"]).area, 12)
        self.assertEqual(unchanged, {"outcome": "unchanged"})
        self.assertEqual((outside["outcome"], outside["reason"]["code"]), ("rejected", "OUTSIDE_ROI"))
        self.assertEqual((invalid["outcome"], invalid["reason"]["code"]), ("rejected", "INVALID_GEOMETRY"))

    def test_simplify_tolerance_errors(self):
        for tolerance in (0, -1, "0.00001", True, 1e-2, None):
            with self.subTest(tolerance=tolerance):
                self.assertRejected(self.post("/geometry/simplify", {"geometries": [JAGGED], "tolerance": tolerance}), "tolerance")
        self.assertEqual(self.post("/geometry/simplify", {"geometries": [JAGGED], "tolerance": 1e-7}).get_json()["data"]["tolerance"], 1e-7)

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
        self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(5, 1, 9, 5)]})
        self.post("/geometry/union", {"geometries": [square(1, 1, 5, 5), square(10, 10, 16.5, 12)]})
        db.session.expire_all()
        self.assertEqual(self._revision_state(), state_before)
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
