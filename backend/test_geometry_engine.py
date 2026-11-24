# -*- coding: utf-8 -*-
"""统一几何引擎的不变量与属性测试（backend/applications/project_hub/geometry_engine.py）。

合成场景覆盖：凹多边形、孔洞、近共线边、超限图斑（> 5000 坐标）、贴边界图斑、厘米级细缝、
MultiPolygon、自相交。关键结论另用不依赖 GEOS 的纯 Python 实现交叉验证，
避免测试与实现共享同一个假设。
"""
import ast
import inspect
import math
import os
import random
import sys
import time
import unittest
from unittest import mock

from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, box, shape
from shapely.ops import unary_union

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.project_hub import geometry_engine as engine  # noqa: E402


X0, Y0 = 100.0, 25.0  # 真实经纬度量级：这个量级上 double 的最小间隔约 1.4e-14
K = 1e-3              # 1e-3 度约 111 米
TOL = engine.NUMERIC_TOLERANCE_DEGREES


def ring(points):
    return [[X0 + x, Y0 + y] for x, y in points] + [[X0 + points[0][0], Y0 + points[0][1]]]


def polygon(*rings):
    return {"type": "Polygon", "coordinates": [ring(points) for points in rings]}


def multipolygon(*polygons):
    return {"type": "MultiPolygon", "coordinates": [[ring(points) for points in rings] for rings in polygons]}


def square(x0, y0, x1, y1):
    return polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def context(roi):
    return engine.RoiContext(shape(roi))


def rings_of(geometry):
    if geometry["type"] == "Polygon":
        return [[(p[0], p[1]) for p in r] for r in geometry["coordinates"]]
    return [[(p[0], p[1]) for p in r] for part in geometry["coordinates"] for r in part]


def positions_of(geometry):
    return sum(len(r) for r in rings_of(geometry))


def vertex_set(geometry):
    return {(round(x, 12), round(y, 12)) for r in rings_of(geometry) for x, y in r[:-1]}


def parity_inside(x, y, rings):
    """纯 Python 射线法，独立于 GEOS。"""
    inside = False
    for r in rings:
        x0, y0 = r[-1]
        for x1, y1 in r:
            if (y0 > y) != (y1 > y):
                if x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                    inside = not inside
            x0, y0 = x1, y1
    return inside


def ring_distance(x, y, rings):
    """纯 Python 点到环的最短距离，独立于 GEOS。"""
    best = float("inf")
    for r in rings:
        x0, y0 = r[-1]
        for x1, y1 in r:
            dx, dy = x1 - x0, y1 - y0
            if dx or dy:
                t = ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            else:
                t = 0.0
            best = min(best, math.hypot(x - (x0 + t * dx), y - (y0 + t * dy)))
            x0, y0 = x1, y1
    return best


def literal_save_rule(roi, geometry_object):
    """改造前保存接口的表达式，原样照抄，不走引擎代码。"""
    return geometry_object.is_valid and (roi.covers(geometry_object) or roi.buffer(TOL).covers(geometry_object))


UNIT = square(0, 0, K, K)  # 1e-3 度见方的矿山
NOTCH_Y = K - 4e-7
# 矿山边界上有个 22 米宽、4.4 厘米深的小凹口
NOTCHED_ROI = polygon([(0, 0), (K, 0), (K, K), (6e-4, K), (6e-4, NOTCH_Y), (4e-4, NOTCH_Y), (4e-4, K), (0, K)])


def staircase_along_y(x_start, x_end, y, inward, *, amplitude=1e-6, step=2e-5):
    """沿 y=y 从 x_start 走到 x_end 的一格锯齿：边界点与往内侧（inward 方向）偏移 amplitude 的点交替。"""
    direction = 1 if x_end > x_start else -1
    points = [(x_start, y)]
    count = int(abs(x_end - x_start) / step)
    for index in range(1, count + 1):
        points.append((x_start + direction * index * step, y + (inward * amplitude if index % 2 else 0.0)))
    points.append((x_end, y))
    return points


def bottom_staircase(x0, x1):
    """沿 x0→x1 底边的一格锯齿（往内侧即 +y 偏移一个像元）。"""
    return staircase_along_y(x0, x1, 0.0, 1)


def right_staircase(x0, y0, x1, y1, *, amplitude=1e-6, step=2e-5):
    """矩形，右侧是栅格锯齿：每 step 前进一点，幅值 amplitude 来回折。"""
    points = [(x0, y0), (x1, y0)]
    y = y0
    inward = False
    while y + step < y1:
        y += step
        inward = not inward
        points.append((x1 - (amplitude if inward else 0.0), y))
    points += [(x1, y1), (x0, y1)]
    return points


class TestParseGeometry(unittest.TestCase):
    def problem(self, geometry):
        with self.assertRaises(engine.GeometryProblem) as raised:
            engine.parse_geometry(geometry)
        return raised.exception.field, raised.exception.reason

    def test_structure_rules(self):
        good = square(0.1, 0.1, 0.2, 0.2)
        bow_tie = polygon([(1e-4, 1e-4), (9e-4, 9e-4), (9e-4, 1e-4), (1e-4, 9e-4)])
        cases = [
            (None, "geometry", "几何只能包含 type 和 coordinates"),
            ({**good, "extra": 1}, "geometry", "几何只能包含 type 和 coordinates"),
            ({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "geometry.type", "仅支持 Polygon 或 MultiPolygon"),
            ({"type": "Polygon", "coordinates": []}, "geometry.coordinates", "Polygon 必须包含至少一个面环"),
            ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [1, 1]]]}, "geometry.coordinates", "面环必须显式闭合"),
            ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1]]]}, "geometry.coordinates", "面环至少需要四个闭合坐标位置"),
            ({"type": "Polygon", "coordinates": [[[0, 0, 5], [1, 0, 5], [1, 1, 5], [0, 0, 5]]]}, "geometry.coordinates", "坐标位置必须恰有二维 [longitude, latitude]"),
            ({"type": "Polygon", "coordinates": [[[0, True], [1, 0], [1, 1], [0, True]]]}, "geometry.coordinates", "坐标位置必须是数值"),
            ({"type": "Polygon", "coordinates": [[[0, "1"], [1, 0], [1, 1], [0, "1"]]]}, "geometry.coordinates", "坐标位置必须是数值"),
            ({"type": "Polygon", "coordinates": [[[0, math.nan], [1, 0], [1, 1], [0, math.nan]]]}, "geometry.coordinates", "坐标位置必须是有限数值"),
            ({"type": "Polygon", "coordinates": [[[0, math.inf], [1, 0], [1, 1], [0, math.inf]]]}, "geometry.coordinates", "坐标位置必须是有限数值"),
            ({"type": "Polygon", "coordinates": [[[181.0, 0], [182, 0], [182, 1], [181.0, 0]]]}, "geometry.coordinates", "坐标超出 EPSG:4326 范围"),
            ({"type": "Polygon", "coordinates": [[[0, -91.0], [1, -91], [1, -90], [0, -91.0]]]}, "geometry.coordinates", "坐标超出 EPSG:4326 范围"),
            ({"type": "MultiPolygon", "coordinates": []}, "geometry.coordinates", "MultiPolygon 必须包含至少一个面"),
            ({"type": "MultiPolygon", "coordinates": [[]]}, "geometry.coordinates", "MultiPolygon 中的面不能为空"),
            (bow_tie, "geometry", "几何必须是非空有效面"),
            ({"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [0.0, 0.0]]]}, "geometry", "几何必须是非空有效面"),
        ]
        for geometry, field, reason in cases:
            with self.subTest(geometry=geometry):
                self.assertEqual(self.problem(geometry), (field, reason))

    def test_valid_geometry_and_position_count(self):
        parsed, count = engine.parse_geometry(square(1e-4, 1e-4, 9e-4, 9e-4))
        self.assertEqual(count, 5)
        self.assertEqual(engine.position_count(parsed), 5)
        with_hole = polygon([(0, 0), (K, 0), (K, K), (0, K)], [(2e-4, 2e-4), (2e-4, 4e-4), (4e-4, 4e-4), (4e-4, 2e-4)])
        parsed, count = engine.parse_geometry(with_hole)
        self.assertEqual(count, 10, "闭合位置计入坐标数（保存接口的容量口径）")
        # to_geojson 往返不改变坐标
        self.assertEqual(engine.to_geojson(parsed), with_hole)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.context = context(UNIT)
        self.roi = shape(UNIT)

    def test_inside_and_touching_are_legal(self):
        for geometry in (square(1e-4, 1e-4, 9e-4, 9e-4), square(0, 0, K, K), square(0, 0, 5e-4, 5e-4)):
            self.assertTrue(engine.evaluate(self.context, geometry).ok)
        self.assertEqual(engine.evaluate(self.context, square(1e-4, 1e-4, 9e-4, 9e-4)).payload(), {"ok": True})

    def test_only_numeric_tolerance_is_absorbed(self):
        # 伸出 1e-10 度（坐标舍入量级）：合法；伸出 1e-8 度（仍不足 1 毫米）：拒绝
        self.assertTrue(engine.evaluate(self.context, square(0, 0, K + 1e-10, K)).ok)
        verdict = engine.evaluate(self.context, square(0, 0, K + 1e-8, K))
        self.assertEqual((verdict.code, verdict.message), (engine.OUTSIDE_ROI, "几何必须完全位于成果 ROI 内"))
        self.assertTrue(9e-9 < verdict.outside_distance < 1.1e-8)
        payload = verdict.payload()
        self.assertEqual(payload["ok"], False)
        self.assertAlmostEqual(payload["outside_distance_m"], 1e-8 * engine.DEGREES_TO_METRES, places=3)
        self.assertGreater(payload["outside_area_m2"], 0)

    def test_concave_roi_notch_covered_by_feature_is_rejected(self):
        # 矿山边界有个 3 厘米宽、11 米深的凹口，图斑把凹口盖住：没有采样点会正好落在凹口里
        notch = [(5.0015e-4, K), (5.0015e-4, K - 1e-4), (4.9985e-4, K - 1e-4), (4.9985e-4, K)]
        roi = polygon([(0, 0), (K, 0), (K, K), *notch, (0, K)])
        verdict = engine.evaluate(context(roi), square(0, 0, K, K))
        self.assertEqual(verdict.code, engine.OUTSIDE_ROI)
        self.assertAlmostEqual(verdict.outside_area, 3e-7 * 1e-4, delta=1e-17)

    def test_roi_hole_covered_by_feature_is_rejected(self):
        donut = polygon([(0, 0), (K, 0), (K, K), (0, K)], [(4e-4, 4e-4), (4e-4, 6e-4), (6e-4, 6e-4), (6e-4, 4e-4)])
        roi_context = context(donut)
        self.assertEqual(engine.evaluate(roi_context, square(4e-4, 4e-4, 6e-4, 6e-4)).code, engine.OUTSIDE_ROI)
        self.assertTrue(engine.evaluate(roi_context, donut).ok)

    def test_centimetre_sliver_outside_is_rejected(self):
        spike = polygon([(1e-4, 1e-4), (4e-4, 1e-4), (4e-4, -1e-5), (4.0001e-4, -1e-5), (4.0001e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (1e-4, 9e-4)])
        verdict = engine.evaluate(self.context, spike)
        self.assertEqual(verdict.code, engine.OUTSIDE_ROI)
        self.assertTrue(1.0 < verdict.payload()["outside_distance_m"] < 1.2)

    def test_sub_millimetre_sliver_is_within_numeric_tolerance(self):
        spike = polygon([(1e-4, 1e-4), (4e-4, 1e-4), (4e-4, -1e-10), (4.00000001e-4, -1e-10), (4.00000001e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (1e-4, 9e-4)])
        self.assertTrue(engine.evaluate(self.context, spike).ok)

    def test_multipolygon_feature_and_roi(self):
        left = [(1e-4, 1e-4), (3e-4, 1e-4), (3e-4, 3e-4), (1e-4, 3e-4)]
        right = [(6e-4, 6e-4), (9e-4, 6e-4), (9e-4, 9e-4), (6e-4, 9e-4)]
        self.assertTrue(engine.evaluate(self.context, multipolygon((left,), (right,))).ok)
        outside_part = [(6e-4, 6e-4), (9e-4, 6e-4), (9e-4, K + 1e-7), (6e-4, K + 1e-7)]
        self.assertEqual(engine.evaluate(self.context, multipolygon((left,), (outside_part,))).code, engine.OUTSIDE_ROI)
        roi_two = multipolygon(([(0, 0), (2e-4, 0), (2e-4, 2e-4), (0, 2e-4)],), ([(8e-4, 0), (K, 0), (K, 2e-4), (8e-4, 2e-4)],))
        bridging = square(1e-4, 1e-5, 9e-4, 1e-4)
        self.assertEqual(engine.evaluate(context(roi_two), bridging).code, engine.OUTSIDE_ROI)
        self.assertTrue(engine.evaluate(context(roi_two), square(8.2e-4, 1e-5, 9e-4, 1e-4)).ok)

    def test_self_intersecting_and_degenerate_faces_are_invalid(self):
        message = "几何必须是非空有效面"
        bow_tie = polygon([(1e-4, 1e-4), (9e-4, 9e-4), (9e-4, 1e-4), (1e-4, 9e-4)])
        verdict = engine.evaluate(self.context, bow_tie)
        self.assertEqual((verdict.code, verdict.message), (engine.INVALID_GEOMETRY, message))
        # 三个顶点精确共线（零面积）。注意：加了经纬度基点后若因浮点舍入不再精确共线，
        # GEOS 会把它当成一个极窄但有效的面——这与改造前 is_valid 的口径相同，不是引擎放宽
        collinear = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [0.0, 0.0]]]}
        self.assertEqual(engine.evaluate(self.context, collinear).code, engine.INVALID_GEOMETRY)
        hole_outside_shell = polygon([(1e-4, 1e-4), (4e-4, 1e-4), (4e-4, 4e-4), (1e-4, 4e-4)], [(6e-4, 6e-4), (8e-4, 6e-4), (8e-4, 8e-4), (6e-4, 8e-4)])
        self.assertEqual(engine.evaluate(self.context, hole_outside_shell).code, engine.INVALID_GEOMETRY)
        hole_touching_shell_along_edge = polygon([(1e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (1e-4, 9e-4)], [(1e-4, 3e-4), (5e-4, 3e-4), (5e-4, 5e-4), (1e-4, 5e-4)])
        self.assertEqual(engine.evaluate(self.context, hole_touching_shell_along_edge).code, engine.INVALID_GEOMETRY)

    def test_structural_problems_come_back_as_invalid_verdicts(self):
        unclosed = {"type": "Polygon", "coordinates": [[[X0 + 1e-4, Y0 + 1e-4], [X0 + 9e-4, Y0 + 1e-4], [X0 + 9e-4, Y0 + 9e-4]]]}
        verdict = engine.evaluate(self.context, unclosed)
        self.assertEqual((verdict.code, verdict.field, verdict.message), (engine.INVALID_GEOMETRY, "geometry.coordinates", "面环至少需要四个闭合坐标位置"))
        verdict = engine.evaluate(self.context, {"type": "Point", "coordinates": [1, 1]})
        self.assertEqual((verdict.code, verdict.field, verdict.message), (engine.INVALID_GEOMETRY, "geometry.type", "仅支持 Polygon 或 MultiPolygon"))

    def test_invalid_roi_is_refused(self):
        with self.assertRaises(ValueError):
            context(polygon([(0, 0), (K, K), (K, 0), (0, K)]))

    def test_verdict_matches_literal_save_rule(self):
        rng = random.Random(20261124)
        rois = [UNIT, NOTCHED_ROI, polygon([(0, 0), (K, 0), (K, K), (5.015e-4, K), (5.015e-4, K - 1e-4), (4.985e-4, K - 1e-4), (4.985e-4, K), (0, K)])]
        rejected = 0
        for _ in range(400):
            roi = rng.choice(rois)
            x0 = rng.uniform(-2e-5, 9e-4)
            y0 = rng.uniform(-2e-5, 9.9e-4)
            geometry = square(x0, y0, x0 + rng.uniform(1e-5, 2e-4), y0 + rng.uniform(1e-5, 2e-4))
            expected = literal_save_rule(shape(roi), shape(geometry))
            self.assertEqual(engine.evaluate(context(roi), geometry).ok, expected)
            rejected += not expected
        self.assertGreater(rejected, 40, "随机样本里应同时出现接受与拒绝两种结论")


class TestSimplify(unittest.TestCase):
    def test_staircase_is_reduced_and_stays_legal(self):
        roi_context = context(UNIT)
        feature = polygon(right_staircase(1e-4, 1e-4, 9e-4, 9e-4))
        outcome = engine.simplify(roi_context, feature)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertEqual(set(outcome), {"outcome", "geometry"})
        self.assertLess(positions_of(outcome["geometry"]), positions_of(feature) / 4)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
        self.assertTrue(vertex_set(outcome["geometry"]) <= vertex_set(feature), "I3：简化结果的顶点必须都来自原几何")
        # 锯齿被拉直：偏差只有 1 像素幅值的小三角，面积变化远小于 0.1%
        self.assertLess(abs(shape(outcome["geometry"]).area - shape(feature).area) / shape(feature).area, 0.001)

    def test_triangle_is_unchanged(self):
        triangle = polygon([(1e-4, 1e-4), (9e-4, 1e-4), (5e-4, 9e-4)])
        self.assertEqual(engine.simplify(context(UNIT), triangle), {"outcome": engine.UNCHANGED})

    def test_boundary_feature_is_simplified_and_stays_inside_roi(self):
        # 图斑贴着矿山底边，底边是一格一格的锯齿：简化后底边拉直，仍在矿山内
        roi_context = context(UNIT)
        feature = polygon(bottom_staircase(0, K) + [(K, K), (0, K)])
        self.assertGreater(positions_of(feature), 50)
        outcome = engine.simplify(roi_context, feature)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
        self.assertLessEqual(positions_of(outcome["geometry"]), 6)

    def test_simplify_rejects_geometry_that_is_already_outside(self):
        outcome = engine.simplify(context(UNIT), square(0, 0, K + 1e-7, K))
        self.assertEqual((outcome["outcome"], outcome["reason"]["code"]), (engine.REJECTED, engine.OUTSIDE_ROI))
        self.assertEqual(set(outcome), {"outcome", "reason"})

    def test_simplify_rejects_invalid_geometry(self):
        roi_context = context(UNIT)
        bow_tie = polygon([(1e-4, 1e-4), (9e-4, 9e-4), (9e-4, 1e-4), (1e-4, 9e-4)])
        self.assertEqual(engine.simplify(roi_context, bow_tie)["reason"]["code"], engine.INVALID_GEOMETRY)
        unclosed = {"type": "Polygon", "coordinates": [[[X0, Y0], [X0 + K, Y0], [X0 + K, Y0 + K]]]}
        outcome = engine.simplify(roi_context, unclosed)
        self.assertEqual((outcome["outcome"], outcome["reason"]["code"]), (engine.REJECTED, engine.INVALID_GEOMETRY))

    def test_thin_structures_are_never_returned_as_invalid_geometry(self):
        roi_context = context(UNIT)
        # 0.1 毫米宽、60 米深的缝：偏差远小于容差，会被抹平成一条直边——结果仍是有效面
        slit = polygon([(1e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (5.000005e-4, 9e-4), (5.000005e-4, 3e-4), (4.999995e-4, 3e-4), (4.999995e-4, 9e-4), (1e-4, 9e-4)])
        self.assertTrue(engine.evaluate(roi_context, slit).ok)
        outcome = engine.simplify(roi_context, slit)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
        # 0.1 毫米宽的细颈连着两块：要么给出仍被接受的有效面，要么拒绝、原面不动
        dumbbell = polygon([(1e-4, 1e-4), (4e-4, 1e-4), (4e-4, 4.9e-4), (6e-4, 4.9e-4), (6e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (6e-4, 9e-4), (6e-4, 4.90001e-4), (4e-4, 4.90001e-4), (4e-4, 9e-4), (1e-4, 9e-4)])
        self.assertTrue(engine.evaluate(roi_context, dumbbell).ok)
        for thin in (slit, dumbbell):
            outcome = engine.simplify(roi_context, thin)
            if outcome["outcome"] == engine.CHANGED:
                self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
                self.assertTrue(literal_save_rule(shape(UNIT), shape(outcome["geometry"])))
            elif outcome["outcome"] == engine.REJECTED:
                self.assertIn(outcome["reason"]["code"], (engine.INVALID_GEOMETRY, engine.OUTSIDE_ROI))
                self.assertNotIn("geometry", outcome)

    def test_retry_ladder(self):
        roi_context = context(UNIT)
        feature = polygon(right_staircase(1e-4, 1e-4, 9e-4, 9e-4))
        real = engine._simplify_candidate
        tolerances = []

        def fails_once(parsed, tolerance):
            tolerances.append(tolerance)
            return Polygon() if len(tolerances) == 1 else real(parsed, tolerance)

        with mock.patch.object(engine, "_simplify_candidate", fails_once):
            outcome = engine.simplify(roi_context, feature)
        self.assertEqual(outcome["outcome"], engine.CHANGED, "第一次结果无效时应降容差重试")
        self.assertEqual(tolerances, [1e-5, 2.5e-6])

        tolerances.clear()
        with mock.patch.object(engine, "_simplify_candidate", lambda parsed, tolerance: (tolerances.append(tolerance), Polygon())[1]):
            outcome = engine.simplify(roi_context, feature)
        self.assertEqual((outcome["outcome"], outcome["reason"]["code"]), (engine.REJECTED, engine.INVALID_GEOMETRY))
        self.assertEqual(tolerances, [1e-5, 2.5e-6, 6.25e-7], "三级容差各试一次后放弃")
    def test_boundary_feature_with_shallow_notch_is_rejected_and_kept(self):
        # 矿山边界上有个 22 米宽、4.4 厘米深的小凹口，图斑贴边经过它。原生简化会把偏差小于容差的凹口顶点
        # 去掉，新直边越过凹口、越出矿山（2026-11-24 真实数据上保存被 422 拒绝的原因）。
        # 统一校验必须拦住它：拒绝并保持原面不动。几何正确性优先于简化成功率。
        roi_context = context(NOTCHED_ROI)
        feature = polygon(bottom_staircase(0, K) + [(K, K), (6e-4, K), (6e-4, NOTCH_Y), (4e-4, NOTCH_Y), (4e-4, K), (0, K)])
        self.assertTrue(engine.evaluate(roi_context, feature).ok)
        for preserve in (False, True):
            plain = engine.to_geojson(shape(feature).simplify(engine.SIMPLIFY_TOLERANCE_DEGREES, preserve_topology=preserve))
            self.assertEqual(engine.evaluate(roi_context, plain).code, engine.OUTSIDE_ROI, "对照：不校验的原生简化会越出 ROI")
        outcome = engine.simplify(roi_context, feature)
        self.assertEqual((outcome["outcome"], outcome["reason"]["code"]), (engine.REJECTED, engine.OUTSIDE_ROI))
        self.assertNotIn("geometry", outcome)

    def test_multipolygon_parts_are_simplified_together(self):
        roi_context = context(UNIT)
        feature = multipolygon((right_staircase(1e-4, 1e-4, 3e-4, 3e-4),), (right_staircase(6e-4, 6e-4, 9e-4, 9e-4),))
        outcome = engine.simplify(roi_context, feature)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertEqual(outcome["geometry"]["type"], "MultiPolygon")
        self.assertEqual(len(outcome["geometry"]["coordinates"]), 2)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
    def test_hole_and_near_collinear_edges(self):
        roi_context = context(UNIT)
        with_hole = polygon([(1e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (1e-4, 9e-4)], right_staircase(4e-4, 4e-4, 6e-4, 6e-4))
        outcome = engine.simplify(roi_context, with_hole)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
        self.assertEqual(len(outcome["geometry"]["coordinates"]), 2, "孔洞不能被简化没了")
        self.assertEqual(len(outcome["geometry"]["coordinates"][1]), 5)
        # 近共边：偏差 3e-7（约 3 厘米）的顶点应被去掉，面积几乎不变
        near_collinear = polygon([(1e-4, 1e-4), (9e-4, 1e-4), (9e-4, 5e-4), (9e-4 - 3e-7, 5e-4 + 2e-5), (9e-4, 5e-4 + 4e-5), (9e-4, 9e-4), (1e-4, 9e-4)])
        outcome = engine.simplify(roi_context, near_collinear)
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertEqual(positions_of(outcome["geometry"]), 5)
        self.assertNotIn((round(X0 + 9e-4 - 3e-7, 12), round(Y0 + 5e-4 + 2e-5, 12)), vertex_set(outcome["geometry"]))

    def test_oversized_feature(self):
        # 一万多个坐标位置（生产上单要素最多 6422 个，超过保存接口 5000 的单要素上限）
        roi_context = context(UNIT)
        feature = polygon(right_staircase(1e-4, 1e-4, 9e-4, 9e-4, step=8e-8))
        self.assertGreater(positions_of(feature), 9000)
        started = time.perf_counter()
        outcome = engine.simplify(roi_context, feature)
        elapsed = time.perf_counter() - started
        self.assertEqual(outcome["outcome"], engine.CHANGED)
        self.assertTrue(engine.evaluate(roi_context, outcome["geometry"]).ok)
        self.assertLess(positions_of(outcome["geometry"]), positions_of(feature) / 50)
        self.assertLess(elapsed, 5.0, f"超限图斑简化耗时 {elapsed:.2f}s")

    def test_tolerance_bounds(self):
        roi_context = context(UNIT)
        feature = square(1e-4, 1e-4, 9e-4, 9e-4)
        for tolerance in (0, -1e-5, math.nan, math.inf, True, "0.00001", 1e-2):
            with self.subTest(tolerance=tolerance), self.assertRaises(engine.GeometryProblem) as raised:
                engine.simplify(roi_context, feature, tolerance)
            self.assertEqual(raised.exception.field, "tolerance")
        self.assertEqual(engine.simplify(roi_context, feature, 1e-3)["outcome"], engine.UNCHANGED)
        self.assertEqual(engine.simplify(roi_context, feature, 1e-6)["outcome"], engine.UNCHANGED)
        # 锯齿幅值 1e-6：容差比它小就不动，比它大就拉直
        staircase = polygon(right_staircase(1e-4, 1e-4, 9e-4, 9e-4))
        self.assertEqual(engine.simplify(roi_context, staircase, 5e-7)["outcome"], engine.UNCHANGED)
        self.assertEqual(engine.simplify(roi_context, staircase, 2e-6)["outcome"], engine.CHANGED)


class TestUnion(unittest.TestCase):
    def setUp(self):
        self.context = context(UNIT)

    def test_adjacent_squares_merge_into_one_polygon(self):
        left = square(1e-4, 1e-4, 5e-4, 9e-4)
        # 右侧那个的左边界上多一个共线点：合并后不应残留
        right = polygon([(5e-4, 1e-4), (9e-4, 1e-4), (9e-4, 9e-4), (5e-4, 9e-4), (5e-4, 5e-4)])
        merged, verdict = engine.union(self.context, [left, right])
        self.assertTrue(verdict.ok)
        self.assertIsInstance(merged, Polygon)
        # GEOS 的叠加运算会保留落在边上的输入顶点（共线点），所以不比坐标个数，比拓扑相等
        self.assertTrue(merged.equals(box(X0 + 1e-4, Y0 + 1e-4, X0 + 9e-4, Y0 + 9e-4)))
        self.assertTrue(engine.evaluate(self.context, engine.to_geojson(merged)).ok)
        self.assertAlmostEqual(merged.area, shape(left).area + shape(right).area, places=16)

    def test_disjoint_squares_form_multipolygon(self):
        merged, verdict = engine.union(self.context, [square(1e-4, 1e-4, 3e-4, 3e-4), square(6e-4, 6e-4, 9e-4, 9e-4)])
        self.assertTrue(verdict.ok)
        self.assertIsInstance(merged, MultiPolygon)
        self.assertEqual(engine.to_geojson(merged)["type"], "MultiPolygon")

    def test_union_covers_every_input_and_subtracts_only_the_overlap(self):
        a, b = square(1e-4, 1e-4, 6e-4, 6e-4), square(4e-4, 4e-4, 9e-4, 9e-4)
        merged, verdict = engine.union(self.context, [a, b])
        self.assertTrue(verdict.ok)
        # 独立算法：蒙特卡洛采样估计并集面积，与 GEOS 的叠加结果比对
        rng = random.Random(3)
        hits = 0
        for _ in range(40000):
            point = Point(rng.uniform(X0 + 1e-4, X0 + 9e-4), rng.uniform(Y0 + 1e-4, Y0 + 9e-4))
            hits += shape(a).contains(point) or shape(b).contains(point)
        estimated = hits / 40000 * (8e-4) ** 2
        self.assertAlmostEqual(merged.area, estimated, delta=estimated * 0.02)
        self.assertAlmostEqual(merged.area, shape(a).area + shape(b).area - shape(a).intersection(shape(b)).area, places=16)
        self.assertEqual(unary_union([merged, shape(a), shape(b)]).area, merged.area, "I5：并集必须覆盖全部输入")

    def test_union_outside_roi_is_refused(self):
        merged, verdict = engine.union(self.context, [square(1e-4, 1e-4, 3e-4, 3e-4), square(6e-4, 6e-4, 9e-4, K + 1e-6)])
        self.assertIsNone(merged)
        self.assertEqual(verdict.code, engine.OUTSIDE_ROI)

    def test_union_rejects_invalid_input(self):
        with self.assertRaises(engine.GeometryProblem):
            engine.union(self.context, [square(1e-4, 1e-4, 3e-4, 3e-4), polygon([(4e-4, 4e-4), (8e-4, 8e-4), (8e-4, 4e-4), (4e-4, 8e-4)])])


class TestRoiContext(unittest.TestCase):
    def test_covers_equals_literal_save_rule_on_every_roi_shape(self):
        # 单环、带凹口、带孔、多部分四种矿山：缓存了外扩多边形的 covers 与改造前的表达式逐一相同
        rng = random.Random(5)
        rois = [
            UNIT, NOTCHED_ROI,
            polygon([(0, 0), (K, 0), (K, K), (0, K)], [(4e-4, 4e-4), (4e-4, 6e-4), (6e-4, 6e-4), (6e-4, 4e-4)]),
            multipolygon(([(0, 0), (2e-4, 0), (2e-4, 2e-4), (0, 2e-4)],), ([(8e-4, 0), (K, 0), (K, 2e-4), (8e-4, 2e-4)],)),
        ]
        rejected = 0
        for roi_geojson in rois:
            roi = shape(roi_geojson)
            roi_context = context(roi_geojson)
            for _ in range(80):
                geometry = shape(square(rng.uniform(-2e-5, 9e-4), rng.uniform(-2e-5, 9.9e-4), rng.uniform(1e-4, 1.02e-3), rng.uniform(1e-4, 1.02e-3)))
                expected = roi.covers(geometry) or roi.buffer(TOL).covers(geometry)
                self.assertEqual(roi_context.covers(geometry), expected)
                rejected += not expected
        self.assertGreater(rejected, 100, "随机样本里应同时出现接受与拒绝两种结论")
    def test_cache_is_bounded_and_reuses_entries(self):
        cache = engine.RoiContextCache(capacity=2)
        calls = []

        def factory(name):
            calls.append(name)
            return name

        self.assertEqual(cache.get("a", lambda: factory("a")), "a")
        self.assertEqual(cache.get("a", lambda: factory("a")), "a")
        self.assertEqual(calls, ["a"])
        cache.get("b", lambda: factory("b"))
        cache.get("c", lambda: factory("c"))
        self.assertEqual(list(cache._entries), ["b", "c"], "容量 2：最久未用的 a 应被淘汰")
        cache.get("b", lambda: factory("b2"))
        cache.get("d", lambda: factory("d"))
        self.assertEqual(list(cache._entries), ["b", "d"], "刚用过的 b 不应被淘汰")
        cache.clear()
        self.assertEqual(len(cache._entries), 0)

    def test_invalid_roi_shapes_are_refused(self):
        for roi in (polygon([(0, 0), (K, K), (K, 0), (0, K)]), multipolygon(([(0, 0), (3e-4, 3e-4), (3e-4, 0), (0, 3e-4)],), ([(2e-4, 2e-4), (5e-4, 2e-4), (5e-4, 5e-4), (2e-4, 5e-4)],))):
            with self.subTest(roi=roi["type"]), self.assertRaises(ValueError):
                context(roi)


def grid_cells(region, cell, rng, keep_probability):
    """把一块区域按 cell 打成格，随机保留一些格子（模拟栅格掩膜），返回格子的并集。"""
    minx, miny, maxx, maxy = region.bounds
    cells = []
    x = minx
    while x + cell <= maxx:
        y = miny
        while y + cell <= maxy:
            if rng.random() < keep_probability:
                cells.append(box(x, y, x + cell, y + cell))
            y += cell
        x += cell
    return unary_union(cells) if cells else Polygon()


def random_roi(rng):
    """格点凸多边形；一半概率在一条真实的边上挖一个 2~8 厘米深的 V 形凹口；
    三成概率在它外面再加一个不相连的小部分（生产的矿山边界几乎全是 MultiPolygon）。"""
    points = {(rng.randrange(100, 9901) * 1e-7, rng.randrange(100, 9901) * 1e-7) for _ in range(10)}
    hull = MultiPoint([(X0 + x, Y0 + y) for x, y in points]).convex_hull
    centre = hull.centroid
    if rng.random() < 0.5:
        coordinates = list(hull.exterior.coords)
        index = rng.randrange(len(coordinates) - 1)
        start, end = coordinates[index], coordinates[index + 1]
        ux, uy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(ux, uy)
        ux, uy = ux / length, uy / length
        nx, ny = -uy, ux  # 内法线：朝向多边形中心
        middle = (start[0] + (end[0] - start[0]) / 2, start[1] + (end[1] - start[1]) / 2)
        if (centre.x - middle[0]) * nx + (centre.y - middle[1]) * ny < 0:
            nx, ny = -nx, -ny
        depth = rng.uniform(2e-7, 8e-7)
        half = rng.uniform(4e-6, 1.2e-5)
        cutter = Polygon([
            (middle[0] - ux * half + nx * depth, middle[1] - uy * half + ny * depth),
            (middle[0] + ux * half + nx * depth, middle[1] + uy * half + ny * depth),
            (middle[0] - nx * depth * 3, middle[1] - ny * depth * 3),
        ])
        hull = hull.difference(cutter)
    if rng.random() < 0.3:
        # 格点横坐标最大 9.9e-4，把小方块放在 K 以外，保证与主体不相连
        hull = MultiPolygon([hull, box(X0 + K, Y0 + 1e-5, X0 + K + 5e-5, Y0 + 6e-5)])
    return hull


class TestEngineOnlyWrapsGeos(unittest.TestCase):
    """引擎是 GEOS 的封装层：这里钉住「只有这些函数」，并禁止再引入自写的几何算法。
    合法性只能来自 GEOS 的 covers / is_valid；要加新的几何逻辑，先补对照实验，再改这个清单。"""

    FUNCTIONS = {
        "_check_position", "_check_ring", "_is_valid_face", "parse_geometry", "_rings", "position_count", "to_geojson",
        "_measure_outside", "evaluate_parsed", "evaluate", "_simplify_candidate", "simplify", "union",
    }

    def test_function_set_is_pinned(self):
        module = ast.parse(inspect.getsource(engine))
        functions = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(functions, self.FUNCTIONS)
        self.assertNotIn("STRtree", inspect.getsource(engine), "引擎不应自己建空间索引或拆边界线段")
        self.assertNotIn("prep(", inspect.getsource(engine), "不用 prepared 谓词：验收规则要与改造前的表达式逐字相同，包括数值边界上的个例")
        self.assertIn("parsed.simplify(tolerance, preserve_topology=True)", inspect.getsource(engine), "简化只能是 GEOS 拓扑保持简化")

    def test_legality_comes_only_from_geos_predicates(self):
        # evaluate_parsed 是全系统唯一的合法性判定：有效性看 GEOS is_valid，覆盖看 GEOS covers
        source = inspect.getsource(engine.evaluate_parsed)
        self.assertIn("_is_valid_face(parsed)", source)
        self.assertIn("context.covers(parsed)", source)
        self.assertEqual(inspect.getsource(engine.RoiContext.covers).count("covers("), 3)

    def test_three_tolerances_are_different_things(self):
        self.assertLess(engine.COORDINATE_QUANTUM_DEGREES * 5, engine.NUMERIC_TOLERANCE_DEGREES, "坐标精度不是容差")
        self.assertLess(engine.NUMERIC_TOLERANCE_DEGREES * 100, 1e-6, "数值容差必须远小于最细像元（0.1 米）")
        self.assertLess(engine.NUMERIC_TOLERANCE_DEGREES * 1000, engine.SIMPLIFY_TOLERANCE_DEGREES, "简化容差不能拿去做合法性判定")
        self.assertEqual(engine.SIMPLIFY_RETRY_SCALES, (1.0, 0.25, 0.0625))


class TestProperties(unittest.TestCase):
    """随机场景下的不变量：I2 简化结果必被接受、I3 顶点来自原几何、I4 面积偏差有界。

    图斑是「随机栅格掩膜 ∩ 矿山」，与自动矢量化产物同构：内部是锯齿，贴矿山的部分是边界链。
    """

    def test_simplify_invariants_on_random_raster_features(self):
        rng = random.Random(20261124)
        changed = unchanged = rejected = 0
        multi_part_rois = multi_part_features = 0
        rejected_codes = {}
        for _ in range(45):
            roi = random_roi(rng)
            self.assertTrue(roi.is_valid and not roi.is_empty)
            multi_part_rois += roi.geom_type == "MultiPolygon"
            roi_geojson = engine.to_geojson(roi)
            roi_rings = rings_of(roi_geojson)
            roi_context = context(roi_geojson)
            mask = grid_cells(roi, 2.5e-5, rng, keep_probability=rng.uniform(0.2, 0.9)).intersection(roi)
            pieces = [piece for piece in getattr(mask, "geoms", [mask]) if not piece.is_empty and piece.area > 4e-10]
            rng.shuffle(pieces)
            cases = [(piece, engine.to_geojson(piece)) for piece in pieces[:4]]
            if len(cases) >= 2 and cases[0][0].distance(cases[1][0]) > 1e-6:
                # 两个不相连的图斑合成一个 MultiPolygon 图斑
                merged_object = MultiPolygon([cases[0][0], cases[1][0]])
                if merged_object.is_valid:
                    cases[0] = (merged_object, engine.to_geojson(merged_object))
            for piece, feature in cases:
                multi_part_features += feature["type"] == "MultiPolygon"
                self.assertTrue(engine.evaluate(roi_context, feature).ok, "生成的图斑本身必须合法")
                outcome = engine.simplify(roi_context, feature)
                if outcome["outcome"] != engine.CHANGED:
                    rejected += outcome["outcome"] == engine.REJECTED
                    unchanged += outcome["outcome"] == engine.UNCHANGED
                    if outcome["outcome"] == engine.REJECTED:
                        rejected_codes[outcome["reason"]["code"]] = rejected_codes.get(outcome["reason"]["code"], 0) + 1
                        self.assertNotIn("geometry", outcome)
                    else:
                        self.assertEqual(outcome, {"outcome": engine.UNCHANGED})
                    continue
                changed += 1
                new_geometry = outcome["geometry"]
                self.assertTrue(engine.evaluate(roi_context, new_geometry).ok, "I2：changed 的结果必须被接受")
                self.assertTrue(literal_save_rule(roi, shape(new_geometry)), "I2：改造前的保存规则也必须接受")
                self.assertTrue(vertex_set(new_geometry) <= vertex_set(feature), "I3：顶点必须都来自原几何")
                self.assertLess(positions_of(new_geometry), positions_of(feature))
                self.assertEqual(new_geometry["type"], feature["type"], "Polygon / MultiPolygon 类型不变")
                # I4：面积偏差有界——被移除的顶点都在保留下来的弦的容差范围内，偏差区域落在弦的容差带里
                new_object = shape(new_geometry)
                tolerance = engine.SIMPLIFY_TOLERANCE_DEGREES
                bound = tolerance * new_object.length + math.pi * tolerance ** 2
                self.assertLessEqual(abs(new_object.area - piece.area), bound * 1.001 + 1e-18, "面积偏差超过容差带")
                # 独立交叉验证（纯 Python，不用 GEOS）：新几何每条边的 1/4、1/2、3/4 处都在矿山内或贴边界
                for r in rings_of(new_geometry):
                    for (xa, ya), (xb, yb) in zip(r, r[1:]):
                        for fraction in (0.25, 0.5, 0.75):
                            mx, my = xa + (xb - xa) * fraction, ya + (yb - ya) * fraction
                            self.assertTrue(ring_distance(mx, my, roi_rings) <= TOL or parity_inside(mx, my, roi_rings), "纯 Python 检查：简化后的边穿出了矿山边界")
        self.assertGreater(changed, 80, f"随机样本应产生足够多的成功简化（changed={changed} unchanged={unchanged} rejected={rejected} {rejected_codes}）")
        self.assertGreaterEqual(multi_part_rois, 8, "应覆盖到多部分的矿山")
        self.assertGreaterEqual(multi_part_features, 8, "应覆盖到 MultiPolygon 图斑")

    def test_evaluate_agrees_with_pure_python_when_pushed_outside(self):
        rng = random.Random(11)
        checked = 0
        for _ in range(60):
            roi = random_roi(rng)
            roi_geojson = engine.to_geojson(roi)
            roi_context = context(roi_geojson)
            roi_rings = rings_of(roi_geojson)
            mask = grid_cells(roi, 2.5e-5, rng, keep_probability=0.6).intersection(roi)
            rightmost = roi.bounds[2]
            for piece in list(getattr(mask, "geoms", [mask])):
                if piece.geom_type != "Polygon" or piece.is_empty or not piece.is_valid or len(piece.interiors) > 0:
                    continue
                checked += 1
                # 把图斑整体右移，让它的最右端越过矿山最右端 1e-6 度（像元量级）
                shift = rightmost - piece.bounds[2] + 1e-6
                moved = {"type": "Polygon", "coordinates": [[[x + shift, y] for x, y in piece.exterior.coords]]}
                verdict = engine.evaluate(roi_context, moved)
                self.assertEqual(verdict.code, engine.OUTSIDE_ROI, "越过矿山边界 1e-6 度必须拒绝")
                # 独立验证（纯 Python）：移动后最右边的那个顶点确实在矿山外 1e-9 度以上
                rightmost_vertex = max(moved["coordinates"][0][:-1], key=lambda point: point[0])
                self.assertGreater(ring_distance(rightmost_vertex[0], rightmost_vertex[1], roi_rings), 1e-7)
                self.assertFalse(parity_inside(rightmost_vertex[0], rightmost_vertex[1], roi_rings))
                # 只移动坐标舍入量级（1e-10 度）：必须接受
                nudged = {"type": "Polygon", "coordinates": [[[x + 1e-10, y] for x, y in piece.exterior.coords]]}
                self.assertTrue(engine.evaluate(roi_context, nudged).ok, "右移 1e-10 度（舍入量级）必须接受")
        self.assertGreater(checked, 20, "随机样本产生的图斑太少")


if __name__ == "__main__":
    unittest.main()
