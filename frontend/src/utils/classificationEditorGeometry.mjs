/**
 * 分类矢量编辑器的面几何校验与安全简化（纯逻辑，无 DOM、无 turf 依赖，便于单测）。
 *
 * 背景（E2-c，2026-11-23）：「消除锯齿」用 @turf/simplify（7.4.0，普通
 * Douglas-Peucker，不保拓扑），可能产出自相交 / 退化环，此前要到保存时才被后端
 * shapely 判无效返回 422，用户无从下手。这里在客户端做前置校验，不通过则降容差
 * 重试，仍不通过就跳过该面、原面保持不变。
 */

/** 简化不通过时的容差缩小倍数：1/4、1/16 */
const SIMPLIFY_RETRY_DIVISOR = 4;

function isPosition(value) {
  return Array.isArray(value) && value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1]);
}

function ringLabel(ringIndex) {
  return ringIndex === 0 ? '外环' : `第 ${ringIndex} 个洞`;
}

function polygonRingsProblem(rings, label) {
  if (!Array.isArray(rings) || rings.length === 0) return `${label}不包含任何环`;
  if (!Array.isArray(rings[0]) || rings[0].length < 4) return `${label}外环少于 4 个坐标`;
  return null;
}

/**
 * 结构校验：Polygon / MultiPolygon、至少一个子面、每个子面至少一个环、外环 ≥4 个坐标。
 * 合并（@turf/union 结果）只做这一层校验。返回问题描述，合法返回 null。
 */
export function polygonStructureProblem(geometry) {
  if (!geometry || typeof geometry !== 'object') return '几何缺失';
  if (geometry.type === 'Polygon') {
    return polygonRingsProblem(geometry.coordinates, '');
  }
  if (geometry.type === 'MultiPolygon') {
    if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length === 0) {
      return 'MultiPolygon 不包含任何子面';
    }
    for (let index = 0; index < geometry.coordinates.length; index += 1) {
      const problem = polygonRingsProblem(geometry.coordinates[index], `第 ${index + 1} 个子面`);
      if (problem) return problem;
    }
    return null;
  }
  return `不支持的几何类型：${geometry?.type}`;
}

/**
 * 单个环自身的有效性（输入为 GeoJSON 闭合环）：坐标 ≥4、首尾闭合、无相邻重复点、
 * 无折返尖刺（A→B→A）、去闭合点后 ≥3 个互异顶点。返回问题描述或 null。
 */
function ringProblem(ring) {
  if (!Array.isArray(ring) || ring.length === 0) return '不是坐标数组';
  if (ring.length < 4) return `只有 ${ring.length} 个坐标（至少 4 个）`;
  if (!ring.every(isPosition)) return '含非法坐标';
  const [first] = ring;
  const last = ring[ring.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) return '首尾未闭合';
  const vertices = ring.slice(0, -1);
  const count = vertices.length;
  // 互异顶点数先判：只有 2 个互异顶点的环必然形如 A,B,A,B…，否则会先被下面的
  // 折返尖刺规则以另一条文案拦下，这条规则就永远不触发
  if (new Set(vertices.map(([x, y]) => `${x},${y}`)).size < 3) return '去闭合点后不足 3 个互异顶点';
  for (let index = 0; index < count; index += 1) {
    const a = vertices[index];
    const b = vertices[(index + 1) % count];
    const c = vertices[(index + 2) % count];
    if (a[0] === b[0] && a[1] === b[1]) return '含相邻重复顶点';
    // 折返尖刺：a→b→c 三点共线且在 b 处原路返回（后端同样判无效）
    if (orient(a, b, c) === 0 && (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0) {
      return '含折返尖刺';
    }
  }
  return null;
}

function orient(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function onSegment(a, b, point) {
  return Math.min(a[0], b[0]) <= point[0] && point[0] <= Math.max(a[0], b[0])
    && Math.min(a[1], b[1]) <= point[1] && point[1] <= Math.max(a[1], b[1]);
}

/** 共线线段的接触类型：投影到跨度不为 0 的坐标轴上比较区间 */
function collinearContact(p1, p2, p3, p4) {
  const axis = Math.max(p1[0], p2[0]) - Math.min(p1[0], p2[0]) > 0 ? 0 : 1;
  const low = Math.max(Math.min(p1[axis], p2[axis]), Math.min(p3[axis], p4[axis]));
  const high = Math.min(Math.max(p1[axis], p2[axis]), Math.max(p3[axis], p4[axis]));
  if (low < high) return 'overlap';
  return low === high ? 'point' : 'none';
}

/**
 * 两线段的接触类型：
 * - 'none'：没有公共点；
 * - 'point'：只在端点处相触（端点对端点、T 形相触、共线但只碰一个端点）；
 * - 'cross'：严格穿越，交点在两条线段内部；
 * - 'overlap'：共线且有一段正长度的重叠。
 * 四次方向判定：两条线段各自把另一条的端点严格分到两侧即穿越；叉积为 0 时再用
 * 包围盒判断该点是否落在线段上。
 */
function segmentContact(p1, p2, p3, p4) {
  const d1 = orient(p3, p4, p1);
  const d2 = orient(p3, p4, p2);
  const d3 = orient(p1, p2, p3);
  const d4 = orient(p1, p2, p4);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) {
    return 'cross';
  }
  if (d1 === 0 && d2 === 0 && d3 === 0 && d4 === 0) {
    return collinearContact(p1, p2, p3, p4);
  }
  const touches = (d1 === 0 && onSegment(p3, p4, p1))
    || (d2 === 0 && onSegment(p3, p4, p2))
    || (d3 === 0 && onSegment(p1, p2, p3))
    || (d4 === 0 && onSegment(p1, p2, p4));
  return touches ? 'point' : 'none';
}

/** 接触类型的集合简写：任意接触 / 仅穿越与共线重叠 */
const ANY_CONTACT = new Set(['point', 'cross', 'overlap']);
const CROSS_OR_OVERLAP = new Set(['cross', 'overlap']);

function buildEdges(vertices, ring) {
  /* vertices 为去掉闭合重复点的顶点序列；每条边带自身包围盒与所属环标记 */
  const count = vertices.length;
  const edges = [];
  for (let index = 0; index < count; index += 1) {
    const a = vertices[index];
    const b = vertices[(index + 1) % count];
    edges.push({
      ring,
      index,
      a,
      b,
      minX: Math.min(a[0], b[0]),
      maxX: Math.max(a[0], b[0]),
      minY: Math.min(a[1], b[1]),
      maxY: Math.max(a[1], b[1]),
    });
  }
  return edges;
}

function boundsOverlap(left, right) {
  return left.minX <= right.maxX && right.minX <= left.maxX && left.minY <= right.maxY && right.minY <= left.maxY;
}

/**
 * 扫描线求相交边对：边按最小 x 排序，依次作为"入边"，只与 x 区间仍重叠
 * （minX ≤ 入边 maxX）的后续边比较，再用 y 包围盒排除，最后做线段接触判定。
 * 同一环内的相邻边（共享端点，含首尾两条）天然有公共点，由 skipPair 排除。
 *
 * @param {Set<string>} contacts 判为"相交"的接触类型
 */
function edgesCross(edges, skipPair, contacts) {
  edges.sort((left, right) => left.minX - right.minX);
  for (let i = 0; i < edges.length; i += 1) {
    const edge = edges[i];
    for (let j = i + 1; j < edges.length && edges[j].minX <= edge.maxX; j += 1) {
      const other = edges[j];
      if (!boundsOverlap(edge, other)) continue;
      if (skipPair(edge, other)) continue;
      if (contacts.has(segmentContact(edge.a, edge.b, other.a, other.b))) return true;
    }
  }
  return false;
}

function ringBounds(vertices) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  vertices.forEach(([x, y]) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  });
  return { minX, minY, maxX, maxY };
}

/** 环是否自相交（vertices 不含闭合重复点，且已通过 ringProblem） */
function ringSelfCrosses(vertices) {
  const count = vertices.length;
  if (count < 4) return false;
  return edgesCross(
    buildEdges(vertices, 0),
    (edge, other) => {
      const distance = Math.abs(edge.index - other.index);
      return distance === 1 || distance === count - 1;
    },
    ANY_CONTACT,
  );
}

/**
 * 两个环（各自不含闭合重复点，且各自不自相交）是否有指定类型的接触。
 * 默认任意接触都算：洞与外环、洞与洞相触会让后端给出 "Hole lies outside shell" /
 * "Interiors disconnected" 一类的无效结论，简化门禁从严。
 */
function ringsCross(left, right, contacts = ANY_CONTACT) {
  if (!boundsOverlap(ringBounds(left), ringBounds(right))) return false;
  return edgesCross(
    [...buildEdges(left, 0), ...buildEdges(right, 1)],
    (edge, other) => edge.ring === other.ring,
    contacts,
  );
}

/** 环与水平线 y 的交点横坐标（升序）。半开区间规则：只计恰有一个端点严格高于 y 的边，
 *  顶点落在扫描线上时仍得到偶数个交点、两两配对成环内区间 */
function scanlineCrossings(vertices, y) {
  const xs = [];
  for (let position = 0; position < vertices.length; position += 1) {
    const p = vertices[position];
    const q = vertices[(position + 1) % vertices.length];
    if ((p[1] > y) !== (q[1] > y)) xs.push(p[0] + ((q[0] - p[0]) * (y - p[1])) / (q[1] - p[1]));
  }
  xs.sort((left, right) => left - right);
  return xs;
}

/**
 * 环内代表弦：取一条不经过任何顶点的水平扫描线，环与它的交点两两配对，
 * 最左一对之间必在环内（奇偶规则）。返回 { y, x0, x1 }，环退化时返回 null。
 */
function interiorChord(vertices) {
  const ys = new Set(vertices.map(([, y]) => y));
  for (let index = 0; index < vertices.length; index += 1) {
    const a = vertices[index];
    const b = vertices[(index + 1) % vertices.length];
    if (a[1] === b[1]) continue;
    const y = (a[1] + b[1]) / 2;
    if (ys.has(y)) continue;
    const xs = scanlineCrossings(vertices, y);
    if (xs.length >= 2 && xs[1] > xs[0]) return { y, x0: xs[0], x1: xs[1] };
  }
  return null;
}

/** 环沿水平线 y 的某个环内区间是否完整覆盖 [x0, x1] */
function ringCoversChord(vertices, { y, x0, x1 }) {
  const xs = scanlineCrossings(vertices, y);
  for (let index = 0; index + 1 < xs.length; index += 2) {
    if (xs[index] <= x0 && x1 <= xs[index + 1]) return true;
  }
  return false;
}

/**
 * inner 环是否落在 outer 环内部：取 inner 的代表弦，要求它被 outer 沿同一条扫描线
 * 的环内区间完整覆盖。只判一个点会漏掉"反过来包住"（代表点恰好落在对方内部），
 * 判整条弦则能拦住。用于"洞在外环内"（含洞包住外环）与"洞套洞"。
 */
function ringNests(inner, outer) {
  const chord = interiorChord(inner);
  return chord !== null && ringCoversChord(outer, chord);
}

/**
 * 面几何有效性校验，返回问题字符串数组（空数组 = 有效）。检查项与后端 shapely
 * 判无效的主要口径对齐：
 * - 结构合法（polygonStructureProblem）；
 * - 每个环：≥4 个坐标、首尾闭合、去闭合点后 ≥3 个互异顶点、无相邻重复点 / 折返尖刺；
 * - 环内非相邻边没有公共点（穿越 = 领结，端点触碰 = 环自切，后端都判 Ring Self-intersection）；
 * - 同一子面内洞与外环、洞与洞没有公共点（从严：后端允许单点相触，但两点相触
 *   即内部不连通而无效；简化只保留原顶点、不会新造点相触，从严不增加误拦）；
 * - 洞的代表弦被外环覆盖（即洞在外环内，含"洞包住外环"的反向嵌套）；
 * - 洞不嵌套在另一个洞内（后端判 Holes are nested）；
 * - MultiPolygon 的各子面外环彼此不穿越、不共线重叠（相触于有限个点合法）。
 *
 * 未覆盖（如实说明）：MultiPolygon 的子面嵌套在另一个子面内但不在其洞中
 * （后端判 Nested shells）——简化只保留原顶点，不会把原本分离的子面变成嵌套。
 *
 * 上述口径已用 shapely 2.1.2 / GEOS 3.13.1 逐类核对（后端锁定的是 1.8.5）。
 *
 * @param {object} geometry GeoJSON Polygon / MultiPolygon
 * @returns {string[]}
 */
export function polygonGeometryProblems(geometry) {
  const problems = [];
  const structure = polygonStructureProblem(geometry);
  if (structure) return [structure];
  const isMulti = geometry.type === 'MultiPolygon';
  const polygons = isMulti ? geometry.coordinates : [geometry.coordinates];
  const shells = [];

  polygons.forEach((rings, polygonIndex) => {
    const label = isMulti ? `第 ${polygonIndex + 1} 个子面` : '';
    const before = problems.length;
    rings.forEach((ring, ringIndex) => {
      const problem = ringProblem(ring);
      if (problem) problems.push(`${label}${ringLabel(ringIndex)}${problem}`);
    });
    if (problems.length > before) return;
    const vertices = rings.map((ring) => ring.slice(0, -1));
    vertices.forEach((ring, ringIndex) => {
      if (ringSelfCrosses(ring)) problems.push(`${label}${ringLabel(ringIndex)}自相交`);
    });
    if (problems.length > before) return;
    // 记住子面的真实序号：前面有子面出问题时它会缺席 shells，不能拿数组下标当子面序号
    shells.push({ polygonIndex, shell: vertices[0] });
    const shell = vertices[0];
    vertices.slice(1).forEach((hole, holeOffset) => {
      const holeName = `${label}${ringLabel(holeOffset + 1)}`;
      if (ringsCross(hole, shell)) problems.push(`${holeName}与外环相交`);
      if (!ringNests(hole, shell)) problems.push(`${holeName}不在外环内`);
      for (let other = 0; other < holeOffset; other += 1) {
        const otherHole = vertices[other + 1];
        const otherName = `${label}${ringLabel(other + 1)}`;
        if (ringsCross(hole, otherHole)) {
          problems.push(`${holeName}与${ringLabel(other + 1)}相交`);
        } else if (ringNests(hole, otherHole)) {
          // 洞套洞：边不相交，只能靠代表弦判定（后端判 "Holes are nested"）
          problems.push(`${holeName}嵌套在${ringLabel(other + 1)}内`);
        } else if (ringNests(otherHole, hole)) {
          problems.push(`${otherName}嵌套在${ringLabel(holeOffset + 1)}内`);
        }
      }
    });
  });

  // 子面之间：外环彼此穿越或共线重叠才无效——只在有限个点上相触的 MultiPolygon
  // 后端判合法（合并两个只在角点相接的面得到的正是这种结果），不能误拦
  for (let left = 0; left < shells.length; left += 1) {
    for (let right = left + 1; right < shells.length; right += 1) {
      if (ringsCross(shells[left].shell, shells[right].shell, CROSS_OR_OVERLAP)) {
        problems.push(`第 ${shells[left].polygonIndex + 1} 个子面与第 ${shells[right].polygonIndex + 1} 个子面相交`);
      }
    }
  }
  return problems;
}

/**
 * 带有效性校验的简化：依次用 tolerance、tolerance/4、tolerance/16 调用 simplify，
 * 返回第一个通过 polygonGeometryProblems 的结果；全部不通过返回 null
 * （调用方计入"已跳过"，原面保持不变）。
 *
 * @param {object} geometry GeoJSON Polygon / MultiPolygon
 * @param {{ tolerance: number, simplify: (geometry: object, tolerance: number) => object, maxRetries?: number }} options
 * @returns {{ geometry: object, tolerance: number, attempt: number } | null}
 */
export function simplifyPolygonSafely(geometry, { tolerance, simplify, maxRetries = 2 } = {}) {
  let currentTolerance = tolerance;
  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    const simplified = simplify(geometry, currentTolerance);
    if (polygonGeometryProblems(simplified).length === 0) {
      return { geometry: simplified, tolerance: currentTolerance, attempt };
    }
    currentTolerance /= SIMPLIFY_RETRY_DIVISOR;
  }
  return null;
}
