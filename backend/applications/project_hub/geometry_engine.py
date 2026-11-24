# -*- coding: utf-8 -*-
"""统一几何引擎：成果图斑的结构解析、合法性判定、简化、合并只在这里计算。

2026-11-24 之前，同一个问题有三套互不相同的答案：后端保存用 GEOS covers，前端越界
提示先用 turf booleanWithin（没有容差）、后改成采样点检查，前端简化用 turf 的不保拓扑
Douglas-Peucker，有效性又各写了一份 JavaScript 版本（还是对着另一个 GEOS 版本核对的）。
贴边界的图斑被误报越界、简化后留下厘米级细缝直到保存才被 422 拒绝，都是这么来的。

现在编辑期校验、简化、合并接口和最终保存共用 evaluate() 这一个判定函数。权威计算核心是
后端运行时自带的 Shapely 1.8.5 / GEOS 3.10.3。本模块只做三件事：解析入参、调用 GEOS、
把结论翻译成接口数据——不自行实现任何空间关系、相交、拓扑或有效性算法。

- ROI 覆盖用完整拓扑关系 covers，不采样；
- 有效性用 GEOS is_valid；
- 简化用 GEOS 自带的拓扑保持简化，结果必须再过一遍 evaluate 才返回；不通过就降容差重试，
  仍不通过就拒绝、由调用方保持原样。运行时的 GEOS 没有 coverage_simplify（3.12 才有），
  曾实现过一版「边界链保护」的自定义简化，全量对照实验表明收益不足以抵偿复杂度，已删除，
  实验数据见 docs/geometry-engine-simplify-ablation.md。

三个容差是三种不同的东西，不能互相顶替：
- COORDINATE_QUANTUM_DEGREES   坐标精度：自动矢量化写坐标时的舍入单位，不是容差；
- NUMERIC_TOLERANCE_DEGREES    数值容差：只吸收坐标舍入与投影换算误差，是合法性判定里
                               唯一允许的容差；真实越界（像元量级，>= 1e-6 度）仍被拒绝；
- SIMPLIFY_TOLERANCE_DEGREES   简化容差：用户可见的「消除锯齿」力度，与合法性无关。

像元分辨率属于每条成果自己的 label 栅格，不参与合法性判定。

不变量（测试见 backend/test_geometry_engine.py）：
- I1  evaluate 接受 ⟺ 几何是结构合规的非空有效面，且 ROI 或其数值容差外扩多边形 covers 它；
- I2  simplify 返回 changed 的几何一定被 evaluate 接受；unchanged / rejected 时调用方保持原样；
- I3  simplify 的结果顶点全部来自原几何，坐标位置数不增加；
- I4  simplify 的面积偏差有界：被移除的顶点都在保留下来的弦的容差范围内，
      所以偏差区域不超过「简化容差 × 新边界周长」；
- I5  union 的结果覆盖全部输入（输入并集与之的差面积为 0），且被 evaluate 接受才返回。

已知特性：贴矿山边界的图斑做原生简化时，可能越过边界（被 evaluate 拒绝，保持原样），
也可能从边界上拉开不超过简化容差的距离（合法，全量数据上已部署方案同样如此）。

本模块不依赖 Flask / 数据库，可以在一次性容器里单独导入做离线回归。
"""
import math
import threading
from collections import OrderedDict

from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union
from shapely.prepared import prep


COORDINATE_QUANTUM_DEGREES = 1e-10
NUMERIC_TOLERANCE_DEGREES = 1e-9
SIMPLIFY_TOLERANCE_DEGREES = 1e-5
MAX_SIMPLIFY_TOLERANCE_DEGREES = 1e-3
SIMPLIFY_RETRY_SCALES = (1.0, 0.25, 0.0625)
# 度换算成米的近似系数，只用于提示里的量级，不参与任何判定
DEGREES_TO_METRES = 111_320.0

INVALID_GEOMETRY = "INVALID_GEOMETRY"
OUTSIDE_ROI = "OUTSIDE_ROI"

UNCHANGED = "unchanged"
CHANGED = "changed"
REJECTED = "rejected"

INVALID_FACE_MESSAGE = "几何必须是非空有效面"
OUTSIDE_ROI_MESSAGE = "几何必须完全位于成果 ROI 内"


class GeometryProblem(ValueError):
    """结构不合法（类型、坐标、闭合等）。field 与 reason 和保存接口 422 details 的口径一致。"""

    def __init__(self, field, reason):
        super().__init__(reason)
        self.field = field
        self.reason = reason


def _check_position(position, field):
    if not isinstance(position, list) or len(position) != 2:
        raise GeometryProblem(field, "坐标位置必须恰有二维 [longitude, latitude]")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in position):
        raise GeometryProblem(field, "坐标位置必须是数值")
    longitude, latitude = position
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise GeometryProblem(field, "坐标位置必须是有限数值")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise GeometryProblem(field, "坐标超出 EPSG:4326 范围")
    return 1


def _check_ring(ring, field):
    if not isinstance(ring, list) or len(ring) < 4:
        raise GeometryProblem(field, "面环至少需要四个闭合坐标位置")
    count = sum(_check_position(position, field) for position in ring)
    if ring[0] != ring[-1]:
        raise GeometryProblem(field, "面环必须显式闭合")
    return count


def _is_valid_face(parsed):
    return not parsed.is_empty and parsed.is_valid and isinstance(parsed, (Polygon, MultiPolygon))


def parse_geometry(geometry):
    """GeoJSON 面几何 → (shapely 几何, 坐标位置数)。结构规则与保存接口逐条相同。

    坐标位置数含每个环末端的闭合位置，保存接口的容量上限用的就是它。
    """
    if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates"}:
        raise GeometryProblem("geometry", "几何只能包含 type 和 coordinates")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    field = "geometry.coordinates"
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise GeometryProblem(field, "Polygon 必须包含至少一个面环")
        position_count = sum(_check_ring(ring, field) for ring in coordinates)
    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise GeometryProblem(field, "MultiPolygon 必须包含至少一个面")
        position_count = 0
        for polygon in coordinates:
            if not isinstance(polygon, list) or not polygon:
                raise GeometryProblem(field, "MultiPolygon 中的面不能为空")
            position_count += sum(_check_ring(ring, field) for ring in polygon)
    else:
        raise GeometryProblem("geometry.type", "仅支持 Polygon 或 MultiPolygon")

    try:
        parsed = shape(geometry)
    except Exception:
        raise GeometryProblem("geometry", "几何无法解析")
    if not _is_valid_face(parsed):
        raise GeometryProblem("geometry", INVALID_FACE_MESSAGE)
    return parsed, position_count


def _rings(parsed):
    parts = [parsed] if isinstance(parsed, Polygon) else list(parsed.geoms)
    return [[part.exterior, *part.interiors] for part in parts]


def position_count(parsed):
    """shapely 面的坐标位置数，口径同 parse_geometry（闭合环末端位置计入）。"""
    return sum(len(ring.coords) for part in _rings(parsed) for ring in part)


def to_geojson(parsed):
    """shapely 面 → GeoJSON（纯 list），给接口返回与测试比对用。坐标原样输出，不做舍入。"""
    parts = [[[list(position) for position in ring.coords] for ring in part] for part in _rings(parsed)]
    if isinstance(parsed, Polygon):
        return {"type": "Polygon", "coordinates": parts[0]}
    return {"type": "MultiPolygon", "coordinates": parts}


class Verdict:
    """一次合法性判定的结论。code 为 None 表示合法。

    OUTSIDE_ROI 附带伸出边界的面积（平方度）和最远伸出距离（度）：只用于提示量级，
    判定本身只看 covers。最远距离取 ROI 之外那部分的顶点（最多 200 个），是近似值。
    """

    __slots__ = ("code", "message", "field", "outside_area", "outside_distance")

    def __init__(self, code=None, message="", field="geometry", outside_area=0.0, outside_distance=0.0):
        self.code = code
        self.message = message
        self.field = field
        self.outside_area = outside_area
        self.outside_distance = outside_distance

    @property
    def ok(self):
        return self.code is None

    def payload(self):
        item = {"ok": self.ok}
        if self.ok:
            return item
        item["code"] = self.code
        item["field"] = self.field
        item["reason"] = self.message
        item["outside_area_m2"] = round(self.outside_area * DEGREES_TO_METRES ** 2, 3)
        item["outside_distance_m"] = round(self.outside_distance * DEGREES_TO_METRES, 4)
        return item

    def __repr__(self):
        return f"Verdict(code={self.code!r}, message={self.message!r})"


LEGAL = Verdict()


class RoiContext:
    """一条成果的矿山边界（ROI）及其 prepared 索引。ROI 在成果矢量化后不再变化，所以按成果缓存。

    covers() 与改造前保存接口的表达式逐字等价：
        roi.covers(g) or roi.buffer(NUMERIC_TOLERANCE_DEGREES).covers(g)
    区别只是外扩多边形与 prepared 索引只算一次。
    """

    def __init__(self, roi):
        if not isinstance(roi, (Polygon, MultiPolygon)) or roi.is_empty or not roi.is_valid:
            raise ValueError("成果 ROI 几何无效")
        self.roi = roi
        self._strict = prep(roi)
        self._tolerant = prep(roi.buffer(NUMERIC_TOLERANCE_DEGREES))

    def covers(self, geometry):
        return bool(self._strict.covers(geometry) or self._tolerant.covers(geometry))


class RoiContextCache:
    """按 (成果 id, ROI 内容) 缓存 RoiContext，进程内有界（LRU）。"""

    def __init__(self, capacity=64):
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries = OrderedDict()

    def get(self, key, factory):
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry
        # 构建不加锁：不阻塞其它请求；两个请求同时未命中时重复构建一次无害
        entry = factory()
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return entry

    def clear(self):
        with self._lock:
            self._entries.clear()


def _measure_outside(context, parsed):
    try:
        outside = parsed.difference(context.roi)
    except Exception:
        return 0.0, 0.0
    distance = 0.0
    for part in getattr(outside, "geoms", [outside]):
        if isinstance(part, Polygon):
            coordinates = list(part.exterior.coords)[:-1]
            # 只取量级：伸出部分的顶点很多时均匀抽到 200 个以内
            step = max(1, len(coordinates) // 200)
            for x, y in coordinates[::step]:
                distance = max(distance, context.roi.boundary.distance(Point(x, y)))
    return outside.area, distance


def evaluate_parsed(context, parsed, *, measure=True):
    """已结构合规的 shapely 面的合法性：有效 + 完整位于 ROI。保存、校验、简化、合并共用。

    measure=False 时跳过越界量级的计算（叠加运算较慢），只给结论；简化重试过程中用它。
    """
    if not _is_valid_face(parsed):
        return Verdict(INVALID_GEOMETRY, INVALID_FACE_MESSAGE)
    if context.covers(parsed):
        return LEGAL
    if not measure:
        return Verdict(OUTSIDE_ROI, OUTSIDE_ROI_MESSAGE)
    outside_area, outside_distance = _measure_outside(context, parsed)
    return Verdict(OUTSIDE_ROI, OUTSIDE_ROI_MESSAGE, outside_area=outside_area, outside_distance=outside_distance)


def evaluate(context, geometry):
    """GeoJSON 几何的合法性：结构规则 + 有效性 + 完整位于 ROI。全系统唯一的判定函数。"""
    try:
        parsed, _ = parse_geometry(geometry)
    except GeometryProblem as problem:
        return Verdict(INVALID_GEOMETRY, problem.reason, field=problem.field)
    return evaluate_parsed(context, parsed)


def _simplify_candidate(parsed, tolerance):
    """GEOS 拓扑保持简化。它的输出顶点都来自输入几何。"""
    return parsed.simplify(tolerance, preserve_topology=True)


def simplify(context, geometry, tolerance=SIMPLIFY_TOLERANCE_DEGREES):
    """消除图斑边缘的栅格锯齿。GEOS 原生拓扑保持简化 → 统一校验 → 不通过则降容差重试。

    返回 {"outcome": unchanged | changed | rejected}；changed 附 "geometry"（GeoJSON），
    rejected 附 "reason"（与 validate 的结论同构）。几何本身不合法时直接 rejected，不替用户修正；
    三级容差都不过也 rejected，由调用方保持原几何不变——几何正确性优先于简化成功率。
    tolerance 不合法属于请求错误，抛 GeometryProblem。
    """
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not math.isfinite(tolerance):
        raise GeometryProblem("tolerance", "简化容差必须是有限正数")
    if tolerance <= 0 or tolerance > MAX_SIMPLIFY_TOLERANCE_DEGREES:
        raise GeometryProblem("tolerance", f"简化容差必须在 (0, {MAX_SIMPLIFY_TOLERANCE_DEGREES:g}] 度之间")
    try:
        parsed, _ = parse_geometry(geometry)
    except GeometryProblem as problem:
        return {"outcome": REJECTED, "reason": Verdict(INVALID_GEOMETRY, problem.reason, field=problem.field).payload()}
    verdict = evaluate_parsed(context, parsed)
    if not verdict.ok:
        return {"outcome": REJECTED, "reason": verdict.payload()}
    before = position_count(parsed)
    for scale in SIMPLIFY_RETRY_SCALES:
        candidate = _simplify_candidate(parsed, tolerance * scale)
        if position_count(candidate) == before:
            return {"outcome": UNCHANGED}
        verdict = evaluate_parsed(context, candidate, measure=False)
        if verdict.ok:
            return {"outcome": CHANGED, "geometry": to_geojson(candidate)}
    return {"outcome": REJECTED, "reason": evaluate_parsed(context, candidate).payload()}


def union(context, geometries):
    """合并若干面。返回 (合并后的 shapely 面 | None, Verdict)：只有 evaluate 接受才给出几何。"""
    parsed = [parse_geometry(geometry)[0] for geometry in geometries]
    merged = unary_union(parsed)
    verdict = evaluate_parsed(context, merged)
    return (merged if verdict.ok else None), verdict
