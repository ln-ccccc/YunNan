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
- 简化用 GEOS 自带的拓扑保持简化，结果必须先过 evaluate、再过精度门槛才返回；不过就降容差重试，
  仍不过就拒绝、由调用方保持原样。运行时的 GEOS 没有 coverage_simplify（3.12 才有），曾实现过一版
  「边界链保护」的自定义简化，全量对照实验表明收益不足以抵偿复杂度，已删除，实验数据见
  docs/geometry-engine-simplify-ablation.md。

简化（「消除锯齿」）的容差不是固定值，按每个图斑自己定。选型与数值来自全量生产成果的对照实验，
见 docs/geometry-engine-adaptive-tolerance-20261125.md：
- 起始容差 = min(1 个 label 像元, 0.05 × √最小部件的面积, 1 米)，以米计。大图斑按影像分辨率去掉
  像元级的锯齿，小图斑按自身尺寸封顶，很小的图斑基本不动。此前的固定容差 1e-5 度（约 1.1 米）
  相对图斑过粗：被简化的图斑 85% 不到 100 平方米，面积变化中位 13%、P95 34%；
- 候选结果要逐部件过精度门槛：面积相对变化 ≤ 3%、对称差占该部件面积 ≤ 5%，部件数与孔数不变；
  不过就把容差减半重试（共 4 级），仍不过就保持原样。无法安全简化的图斑可以合法跳过。
调用方也可以显式给一个容差（度）：它只替换起始容差，精度门槛照样生效。

两个容差是两种不同的东西，不能互相顶替：
- COORDINATE_QUANTUM_DEGREES   坐标精度：自动矢量化写坐标时的舍入单位，不是容差；
- NUMERIC_TOLERANCE_DEGREES    数值容差：只吸收坐标舍入与投影换算误差，是合法性判定里
                               唯一允许的容差；真实越界（像元量级，>= 1e-6 度）仍被拒绝。
简化容差以米计，由 label 像元与图斑面积推出，与合法性判定无关；像元分辨率本身不参与合法性判定。

物理单位：label 的坐标系有 EPSG:4326（度）与 EPSG:3857（米）两种，像元的真米尺寸与当地的
「每度多少米」由 geometry_metric.py 交给 PROJ 换算。简化仍在 EPSG:4326 里做，顶点全部原样保留，
不为了改用米制坐标引入新的几何误差；面积与对称差的比值也在度空间算——它与米制比值等价，
样本上与 PROJ 等积投影的最大相对偏差是 3.8e-5。只有「容差取多少米」「图斑面积是多少平方米」
用到标架。米换成度时按保守轴取（纬向每度的米数更多，换出的度值更小），所以两个方向上
实际偏离都不超过给定的米数。

不变量（测试见 backend/test_geometry_engine.py）：
- I1  evaluate 接受 ⟺ 几何是结构合规的非空有效面，且 ROI 或其数值容差外扩多边形 covers 它；
- I2  simplify 返回 changed 的几何一定被 evaluate 接受，且每个部件的面积相对变化 ≤ 3%、
      对称差占比 ≤ 5%，部件数与孔数不变；unchanged / rejected 时调用方保持原样；
- I3  simplify 的结果顶点全部来自原几何，坐标位置数不增加；
- I4  simplify 的面积偏差有界：被移除的顶点都在保留下来的弦的容差范围内，所以偏差区域不超过
      「实际使用的容差 × 新边界周长」；在此基础上再叠加 I2 的精度门槛；
- I5  union 的结果覆盖全部输入（输入并集与之的差面积为 0），且被 evaluate 接受才返回。

已知特性：贴矿山边界的图斑做原生简化时，可能越过边界（被 evaluate 拒绝，保持原样），
也可能从边界上拉开不超过所用容差的距离（合法，全量数据上已部署方案同样如此）。

本模块不依赖 Flask / 数据库 / 栅格库，可以在一次性容器里单独导入做离线回归。
"""
import math
import threading
from collections import OrderedDict

from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union


COORDINATE_QUANTUM_DEGREES = 1e-10
NUMERIC_TOLERANCE_DEGREES = 1e-9
# 简化容差换算成度之后的上限；调用方显式给容差（度）时也是它的上限
MAX_SIMPLIFY_TOLERANCE_DEGREES = 1e-3
# 自适应起始容差（米）= min(像元系数 × label 像元, 尺寸系数 × √最小部件面积, 上限)。
# 像元系数取 1：偏离不超过影像自身的分辨率，去不掉任何大于一个像元的地物。
# 尺寸系数取 0.05：容差不超过图斑特征尺寸的 5%，小图斑不被过度简化。
# 对照实验（12,547 个样本图斑，门槛 3% / 5%）：像元系数取 1.5~2，同等精度下压缩率只多约 6 个
# 百分点；尺寸系数取 0.02，有变化的图斑从 69% 掉到 38%。
SIMPLIFY_PIXEL_FACTOR = 1.0
SIMPLIFY_SIZE_FACTOR = 0.05
MAX_INITIAL_SIMPLIFY_TOLERANCE_M = 1.0
# label 读不到、像元未知时的保守回退：固定 0.1 米（生产像元的 P10 是 0.086 米）
FALLBACK_SIMPLIFY_TOLERANCE_M = 0.1
# 候选结果不过门槛时，容差逐级减半重试。此前是 ÷4、÷16 两级，样本上比逐级减半少简化 2.7 个百分点的图斑
SIMPLIFY_RETRY_SCALES = (1.0, 0.5, 0.25, 0.125)
# 候选结果的精度门槛：部件面积的相对变化、对称差占该部件面积的比例。
# 对照实验里从 3% / 5% 放宽到 5% / 10%，有变化的图斑只多 0.4 个百分点；
# 收紧到 1% / 2% 则少 8 个百分点。这是工程取值，不是行业标准
MAX_SIMPLIFY_AREA_CHANGE = 0.03
MAX_SIMPLIFY_SHAPE_CHANGE = 0.05
# 度换算成米的近似系数，只用于提示里的量级，不参与任何判定
DEGREES_TO_METRES = 111_320.0

INVALID_GEOMETRY = "INVALID_GEOMETRY"
OUTSIDE_ROI = "OUTSIDE_ROI"
# 简化独有的拒绝原因：候选结果合法，但面积或形态变化超过精度门槛
SHAPE_DRIFT = "SHAPE_DRIFT"

UNCHANGED = "unchanged"
CHANGED = "changed"
REJECTED = "rejected"

INVALID_FACE_MESSAGE = "几何必须是非空有效面"
OUTSIDE_ROI_MESSAGE = "几何必须完全位于成果 ROI 内"
SHAPE_DRIFT_MESSAGE = "简化后的面积或形态变化会超过精度上限"


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


def _parts(face):
    return [face] if isinstance(face, Polygon) else list(face.geoms)


def _rings(parsed):
    return [[part.exterior, *part.interiors] for part in _parts(parsed)]


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
    """一条成果的矿山边界（ROI）及其数值容差外扩多边形。ROI 在成果矢量化后不再变化，所以按成果缓存。

    covers() 与改造前保存接口的表达式逐字相同：
        roi.covers(g) or roi.buffer(NUMERIC_TOLERANCE_DEGREES).covers(g)
    区别只是外扩多边形只算一次（改造前每校验一个图斑重算一次）。
    这里刻意不用 prepared 谓词：它只是加速手段，P99 也只快 1.5 毫秒，而验收规则要的是与改造前
    逐字相同，包括数值边界上的个例。
    """

    def __init__(self, roi):
        if not isinstance(roi, (Polygon, MultiPolygon)) or roi.is_empty or not roi.is_valid:
            raise ValueError("成果 ROI 几何无效")
        self.roi = roi
        self._tolerant = roi.buffer(NUMERIC_TOLERANCE_DEGREES)

    def covers(self, geometry):
        return bool(self.roi.covers(geometry) or self._tolerant.covers(geometry))


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


class SimplifyScale:
    """一条成果的物理标架：当地的米 / 度换算，以及 label 像元的真米尺寸（读不到时为 None）。

    面积与对称差的比值都在度空间算（见模块说明），这里只提供两样东西：某个面是多少平方米
    （尺寸封顶项要用），以及多少米等于多少度（GEOS 的容差以度计）。
    """

    __slots__ = ("pixel_metres", "metres_per_degree_lon", "metres_per_degree_lat")

    def __init__(self, pixel_metres, metres_per_degree_lon, metres_per_degree_lat):
        if not (metres_per_degree_lon > 0 and metres_per_degree_lat > 0):
            raise ValueError("米 / 度换算系数必须为正")
        if pixel_metres is not None and not pixel_metres > 0:
            raise ValueError("像元尺寸必须为正")
        self.pixel_metres = pixel_metres
        self.metres_per_degree_lon = metres_per_degree_lon
        self.metres_per_degree_lat = metres_per_degree_lat

    def area_m2(self, face):
        """面（或面的一个部件）的面积，平方米。"""
        return face.area * self.metres_per_degree_lon * self.metres_per_degree_lat

    def metres_to_degrees(self, metres):
        """米 → 度，按保守轴：换出的度值在两个方向上的实际偏离都不超过给定的米数。"""
        return metres / max(self.metres_per_degree_lon, self.metres_per_degree_lat)


def check_tolerance(tolerance):
    """调用方显式给的简化容差（度）：None 表示用自适应容差，其余必须是 (0, 上限] 内的有限数。"""
    if tolerance is None:
        return None
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not math.isfinite(tolerance):
        raise GeometryProblem("tolerance", "简化容差必须是有限正数")
    if tolerance <= 0 or tolerance > MAX_SIMPLIFY_TOLERANCE_DEGREES:
        raise GeometryProblem("tolerance", f"简化容差必须在 (0, {MAX_SIMPLIFY_TOLERANCE_DEGREES:g}] 度之间")
    return float(tolerance)


def initial_tolerance(parsed, scale):
    """自适应起始容差（度）：min(1 个 label 像元, 0.05 × √最小部件面积, 1 米)。像元未知时按保守回退值。

    尺寸封顶按最小的部件算：一个 MultiPolygon 的所有部件共用一个容差，按整体面积算的话，
    大部件会把小部件的容差顶上去（1000 平方米带一个 2 平方米的小部分，容差会是 1 米）。
    """
    pixel = scale.pixel_metres if scale.pixel_metres is not None else FALLBACK_SIMPLIFY_TOLERANCE_M
    metres = min(
        SIMPLIFY_PIXEL_FACTOR * pixel,
        SIMPLIFY_SIZE_FACTOR * math.sqrt(min(scale.area_m2(part) for part in _parts(parsed))),
        MAX_INITIAL_SIMPLIFY_TOLERANCE_M,
    )
    return min(scale.metres_to_degrees(metres), MAX_SIMPLIFY_TOLERANCE_DEGREES)


def _same_structure(original, candidate):
    """几何类型、部件数、孔数都不变。GEOS 的拓扑保持简化本来就保证这一点（样本上 32.9 万个候选
    零例外），这里数个数再核对一遍，成本可以忽略。"""
    if original.geom_type != candidate.geom_type:
        return False
    old_parts, new_parts = _parts(original), _parts(candidate)
    return len(old_parts) == len(new_parts) and (
        sum(len(part.interiors) for part in old_parts) == sum(len(part.interiors) for part in new_parts)
    )


def _shape_drift(original, candidate):
    """逐部件量精度。返回 (整体面积相对变化（带符号）, 各部件对称差占比的最大值)；
    任一部件超限或无法量度时返回 None。

    调用前 candidate 必须已通过 evaluate（有效）：对无效几何做叠加会失败，也没有意义。
    叠加本身失败（GEOS 的数值鲁棒性，shapely 抛出的任何异常）同样返回 None——证明不了精度就不接受。
    """
    if original.area <= 0:
        return None
    area_change = (candidate.area - original.area) / original.area
    worst_shape = 0.0
    for old_part, new_part in zip(_parts(original), _parts(candidate)):
        if old_part.area <= 0:
            return None
        if abs(new_part.area - old_part.area) / old_part.area > MAX_SIMPLIFY_AREA_CHANGE:
            return None
        try:
            displaced = old_part.symmetric_difference(new_part).area
        except ShapelyError:
            return None
        shape_change = displaced / old_part.area
        if shape_change > MAX_SIMPLIFY_SHAPE_CHANGE:
            return None
        worst_shape = max(worst_shape, shape_change)
    if abs(area_change) > MAX_SIMPLIFY_AREA_CHANGE:
        return None
    return area_change, worst_shape


def simplify(context, geometry, *, tolerance=None, scale=None):
    """消除图斑边缘的栅格锯齿。GEOS 原生拓扑保持简化 → 统一校验 → 精度门槛 → 不过则降容差重试。

    返回 {"outcome": unchanged | changed | rejected}；changed 附 "geometry"（GeoJSON）、实际使用的
    "tolerance"（度）、"area_change"（带符号的面积相对变化）、"shape_change"（部件对称差占比的最大值）；
    rejected 附 "reason"——code 为 OUTSIDE_ROI / INVALID_GEOMETRY 时与 validate 的结论同构，
    为 SHAPE_DRIFT 时表示候选合法但面积或形态变化超限。
    几何本身不合法时直接 rejected，不替用户修正；各级容差都不过也 rejected，由调用方保持原几何
    不变——几何正确性优先于简化成功率。unchanged 只在「起始容差下就没有可去的顶点」时出现：
    前面某级的候选被拒、而更小的容差已经去不动了，算「没法安全简化」，按被拒的原因报 rejected。

    tolerance：调用方显式给的起始容差（度），None 表示自适应（此时必须给 scale）。
    tolerance 不合法属于请求错误，抛 GeometryProblem。
    """
    tolerance = check_tolerance(tolerance)
    try:
        parsed, _ = parse_geometry(geometry)
    except GeometryProblem as problem:
        return {"outcome": REJECTED, "reason": Verdict(INVALID_GEOMETRY, problem.reason, field=problem.field).payload()}
    verdict = evaluate_parsed(context, parsed)
    if not verdict.ok:
        return {"outcome": REJECTED, "reason": verdict.payload()}
    if tolerance is None:
        if scale is None:
            raise ValueError("自适应简化需要成果的物理标架")
        tolerance = initial_tolerance(parsed, scale)
    before = position_count(parsed)
    last_illegal = None
    for level, retry_scale in enumerate(SIMPLIFY_RETRY_SCALES):
        used = tolerance * retry_scale
        candidate = _simplify_candidate(parsed, used)
        if position_count(candidate) == before:
            if level == 0:
                # 起始容差下就没有可去的顶点：这个面不需要简化
                return {"outcome": UNCHANGED}
            # 前面各级给出的候选都被拒了，而更小的容差只会去掉更少的顶点：到此为止。
            # 这是「没法安全简化」，不是「已经足够平滑」——按前面的失败原因拒绝，不报 unchanged
            break
        if _same_structure(parsed, candidate):
            verdict = evaluate_parsed(context, candidate, measure=False)
            if verdict.ok:
                drift = _shape_drift(parsed, candidate)
                if drift is not None:
                    return {
                        "outcome": CHANGED,
                        "geometry": to_geojson(candidate),
                        "tolerance": used,
                        "area_change": round(drift[0], 6),
                        "shape_change": round(drift[1], 6),
                    }
                # 合法，但面积或形态变化超限（或者量不出来）
                last_illegal = None
            else:
                # 越界的面积与距离（叠加运算较慢）留到确定要拒绝时再量
                last_illegal = candidate
        else:
            # 部件或孔数变了：这是最严重的形态变化，按形态超限处理
            last_illegal = None
    if last_illegal is not None:
        return {"outcome": REJECTED, "reason": evaluate_parsed(context, last_illegal).payload()}
    return {"outcome": REJECTED, "reason": Verdict(SHAPE_DRIFT, SHAPE_DRIFT_MESSAGE).payload()}


def union(context, geometries):
    """合并若干面。返回 (合并后的 shapely 面 | None, Verdict)：只有 evaluate 接受才给出几何。"""
    parsed = [parse_geometry(geometry)[0] for geometry in geometries]
    merged = unary_union(parsed)
    verdict = evaluate_parsed(context, merged)
    return (merged if verdict.ok else None), verdict
