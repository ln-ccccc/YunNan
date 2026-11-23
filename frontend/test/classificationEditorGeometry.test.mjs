import test from 'node:test';
import assert from 'node:assert/strict';
import simplify from '@turf/simplify';
import union from '@turf/union';

import {
  polygonGeometryProblems,
  polygonStructureProblem,
  simplifyPolygonSafely,
} from '../src/utils/classificationEditorGeometry.mjs';

// 面几何校验与安全简化（E2-b / E2-c，2026-11-23）：@turf/simplify 是不保拓扑的
// Douglas-Peucker，可能产出自相交 / 退化环，此前要到保存时才被后端 shapely 判无效
// 返回 422。这里覆盖纯函数的判定规则、重试降容差，以及用真实 turf 复现的缺陷。

const polygonOf = (...rings) => ({ type: 'Polygon', coordinates: rings });
const multiOf = (...polygons) => ({ type: 'MultiPolygon', coordinates: polygons.map((polygon) => polygon.coordinates) });
const ringOf = (...vertices) => [...vertices, vertices[0]];
const squareRing = (x, y, size) => ringOf([x, y], [x + size, y], [x + size, y + size], [x, y + size]);
const unitSquare = polygonOf(squareRing(0, 0, 1));

test('polygonGeometryProblems accepts ordinary valid polygons', () => {
  assert.deepEqual(polygonGeometryProblems(unitSquare), []);
  // 三角形、带共线中间点的环、凹多边形（C 形）都是合法面
  assert.deepEqual(polygonGeometryProblems(polygonOf(ringOf([0, 0], [1, 0], [0, 1]))), []);
  assert.deepEqual(polygonGeometryProblems(polygonOf(ringOf([0, 0], [1, 0], [2, 0], [2, 2], [0, 2]))), []);
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(ringOf([0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [4, 3], [4, 4], [0, 4]))),
    [],
  );
  // 带洞、带多个互不相交的洞
  assert.deepEqual(polygonGeometryProblems(polygonOf(squareRing(0, 0, 10), squareRing(2, 2, 2))), []);
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 10), squareRing(1, 1, 3), squareRing(6, 6, 3))),
    [],
  );
  // 月牙形的洞（凹洞）同样合法
  const crescent = ringOf([2, 2], [8, 2], [8, 5], [4, 5], [4, 8], [2, 8]);
  assert.deepEqual(polygonGeometryProblems(polygonOf(squareRing(0, 0, 10), crescent)), []);
});

test('polygonGeometryProblems rejects a bow-tie ring (self-intersection)', () => {
  const bowTie = polygonOf(ringOf([0, 0], [2, 2], [2, 0], [0, 2]));
  assert.deepEqual(polygonGeometryProblems(bowTie), ['外环自相交']);
  // Douglas-Peucker 的典型产物：马蹄形（C 形）的两条长边被拉到一起相互穿越
  const simplifiedHorseshoe = polygonOf(ringOf([0, 0], [100, 30], [0, 30], [90, 20]));
  assert.deepEqual(polygonGeometryProblems(simplifiedHorseshoe), ['外环自相交']);
});

test('polygonGeometryProblems rejects unclosed and undersized rings', () => {
  // 首尾未闭合
  assert.deepEqual(
    polygonGeometryProblems(polygonOf([[0, 0], [1, 0], [1, 1], [0, 1]])),
    ['外环首尾未闭合'],
  );
  // 坐标数不足 4（结构层先拦）
  assert.deepEqual(polygonGeometryProblems(polygonOf(ringOf([0, 0], [1, 0]))), ['外环少于 4 个坐标']);
  // 坐标数够 4、已闭合、无相邻重复点，但去闭合点后不足 3 个互异顶点
  const twoDistinct = polygonOf([[0, 0], [1, 1], [0, 0], [1, 1], [0, 0]]);
  assert.deepEqual(polygonGeometryProblems(twoDistinct), ['外环去闭合点后不足 3 个互异顶点']);
  // 三个互异顶点但全部共线（零面积环）：后端同样判无效
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(ringOf([0, 0], [1, 0], [2, 0]))),
    ['外环含折返尖刺'],
  );
  // 折返尖刺：互异顶点足够，但走到尖上又原路返回
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(ringOf([0, 0], [2, 0], [1, 0], [1, 2]))),
    ['外环含折返尖刺'],
  );
  // 非法坐标
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(ringOf([0, 0], [1, 0], [1, Number.NaN], [0, 1]))),
    ['外环含非法坐标'],
  );
  // 洞上的同类问题要点名是第几个洞
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 10), [[1, 1], [2, 1], [2, 2], [1, 2]])),
    ['第 1 个洞首尾未闭合'],
  );
});

test('polygonGeometryProblems rejects adjacent duplicate vertices and fold-back spikes', () => {
  const duplicated = ringOf([0, 0], [1, 0], [1, 1], [1, 1], [0, 1]);
  assert.deepEqual(polygonGeometryProblems(polygonOf(duplicated)), ['外环含相邻重复顶点']);
  // 闭合点之前再写一次首点（…, [0, 0], [0, 0]）属于相邻重复，而不是别的毛病
  const doubledClosing = [...squareRing(0, 0, 1), [0, 0]];
  assert.deepEqual(polygonGeometryProblems(polygonOf(doubledClosing)), ['外环含相邻重复顶点']);
  // 折返尖刺：走到尖上又沿原线返回
  const spike = ringOf([0, 0], [2, 0], [2, 2], [3, 3], [2, 2], [4, 0], [4, 4], [0, 4]);
  assert.deepEqual(polygonGeometryProblems(polygonOf(spike)), ['外环含折返尖刺']);
});

test('polygonGeometryProblems rejects a ring that pinches itself at a vertex', () => {
  // 两个环圈只在 (1, 1) 处共用一个顶点：边与边只在端点相触，后端判 Ring Self-intersection
  const pinched = ringOf([0, 0], [1, 1], [2, 0], [2, 2], [1, 1], [0, 2]);
  assert.deepEqual(polygonGeometryProblems(polygonOf(pinched)), ['外环自相交']);
  // 对照：相邻边共享端点是正常情况，不得误报（任意凸多边形都如此）
  const convex = ringOf(...Array.from({ length: 40 }, (_, index) => {
    const angle = (2 * Math.PI * index) / 40;
    return [Math.cos(angle), Math.sin(angle)];
  }));
  assert.deepEqual(polygonGeometryProblems(polygonOf(convex)), []);
});

test('polygonGeometryProblems rejects holes outside the outer ring', () => {
  // 洞完全在外环外：没有边相交，只能靠代表弦判定
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 10), squareRing(20, 20, 1))),
    ['第 1 个洞不在外环内'],
  );
  // 洞反过来包住外环
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(2, 2, 6), squareRing(0, 0, 10))),
    ['第 1 个洞不在外环内'],
  );
  // 洞跨过外环边界：既相交也不在内
  const straddling = polygonOf(squareRing(0, 0, 10), squareRing(5, 5, 10));
  assert.deepEqual(polygonGeometryProblems(straddling), ['第 1 个洞与外环相交', '第 1 个洞不在外环内']);
  // 顶点顺序反过来写也一样能查出来
  const reversed = polygonOf(
    [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]],
    [[20, 20], [20, 21], [21, 21], [21, 20], [20, 20]],
  );
  assert.deepEqual(polygonGeometryProblems(reversed), ['第 1 个洞不在外环内']);
});

test('polygonGeometryProblems rejects holes that intersect each other', () => {
  const overlapping = polygonOf(squareRing(0, 0, 10), squareRing(2, 2, 4), squareRing(4, 4, 4));
  assert.deepEqual(polygonGeometryProblems(overlapping), ['第 2 个洞与第 1 个洞相交']);
  // 两个洞部分重叠（边相互穿越）
  const partial = polygonOf(squareRing(0, 0, 10), squareRing(1, 1, 4), squareRing(3, 3, 4));
  assert.deepEqual(polygonGeometryProblems(partial), ['第 2 个洞与第 1 个洞相交']);
  // 多个洞时点名具体是哪两个
  const three = polygonOf(squareRing(0, 0, 20), squareRing(1, 1, 3), squareRing(8, 8, 3), squareRing(9, 9, 3));
  assert.deepEqual(polygonGeometryProblems(three), ['第 3 个洞与第 2 个洞相交']);
  // 洞套洞：边不相交，后端判 Holes are nested。两个方向（小洞在前 / 在后）都要点名
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 20), squareRing(2, 2, 2), squareRing(1, 1, 16))),
    ['第 1 个洞嵌套在第 2 个洞内'],
  );
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 20), squareRing(1, 1, 16), squareRing(2, 2, 2))),
    ['第 2 个洞嵌套在第 1 个洞内'],
  );
  // 互不包含的两个洞不会被代表弦判定误伤
  assert.deepEqual(
    polygonGeometryProblems(polygonOf(squareRing(0, 0, 20), squareRing(1, 1, 8), squareRing(11, 11, 8))),
    [],
  );
});

test('polygonGeometryProblems treats any hole contact as invalid, matching the backend where it matters', () => {
  // 洞与外环相触于两个点，会把面切成互不相连的两块：后端判 Interior is disconnected。
  // 只拦"穿越"的宽松规则查不出来（这里没有任何穿越，只有端点相触）——所以对洞从严。
  const touchesLeftAndRight = polygonOf(squareRing(0, 0, 10), ringOf([0, 5], [5, 8], [10, 5], [5, 2]));
  assert.deepEqual(polygonGeometryProblems(touchesLeftAndRight), ['第 1 个洞与外环相交']);
  const touchesTopAndBottom = polygonOf(squareRing(0, 0, 10), ringOf([5, 0], [8, 5], [5, 10], [2, 5]));
  assert.deepEqual(polygonGeometryProblems(touchesTopAndBottom), ['第 1 个洞与外环相交']);
  // 有意偏离（如实记录）：只相触于一个点时后端判合法，这里同样拦下。
  // 简化只保留原顶点、不会新造出点相触，从严不会让正常的面被跳过。
  const touchesOnce = polygonOf(squareRing(0, 0, 10), ringOf([0, 5], [3, 8], [6, 5], [3, 2]));
  assert.deepEqual(polygonGeometryProblems(touchesOnce), ['第 1 个洞与外环相交']);
});

test('polygonGeometryProblems handles every part of a MultiPolygon', () => {
  const clean = multiOf(unitSquare, polygonOf(squareRing(2, 2, 1), squareRing(2.2, 2.2, 0.2)));
  assert.deepEqual(polygonGeometryProblems(clean), []);
  // 各子面的问题分别点名
  const mixed = multiOf(unitSquare, polygonOf(ringOf([0, 0], [1, 1], [1, 0], [0, 1])), polygonOf(squareRing(5, 5, 1), squareRing(9, 9, 1)));
  assert.deepEqual(polygonGeometryProblems(mixed), ['第 2 个子面外环自相交', '第 3 个子面第 1 个洞不在外环内']);
  // 子面之间穿越（简化后两个相邻部分彼此越过边界）
  const crossing = multiOf(unitSquare, polygonOf(squareRing(0.5, 0.5, 2)));
  assert.deepEqual(polygonGeometryProblems(crossing), ['第 1 个子面与第 2 个子面相交']);
  // 前面的子面自身有问题时，后面"子面之间相交"仍报真实子面序号，不会顺移成 1 和 2
  const offset = multiOf(
    polygonOf(ringOf([0, 0], [1, 1], [1, 0], [0, 1])),
    polygonOf(squareRing(0, 0, 1)),
    polygonOf(squareRing(0.5, 0.5, 2)),
  );
  assert.deepEqual(polygonGeometryProblems(offset), ['第 1 个子面外环自相交', '第 2 个子面与第 3 个子面相交']);
  // 共用一条边 / 共线重叠同样无效
  assert.deepEqual(
    polygonGeometryProblems(multiOf(unitSquare, polygonOf(squareRing(1, 0, 1)))),
    ['第 1 个子面与第 2 个子面相交'],
  );
  assert.deepEqual(
    polygonGeometryProblems(multiOf(unitSquare, polygonOf(ringOf([1, 0.2], [2, 0.2], [2, 0.7], [1, 0.7])))),
    ['第 1 个子面与第 2 个子面相交'],
  );
  // 只在角点相触、或一个子面的顶点落在另一个子面的边上（T 形相触）：
  // 后端判合法——合并两个只在角点相接的面得到的正是这种 MultiPolygon，不得误拦
  assert.deepEqual(polygonGeometryProblems(multiOf(unitSquare, polygonOf(squareRing(1, 1, 1)))), []);
  assert.deepEqual(
    polygonGeometryProblems(multiOf(unitSquare, polygonOf(ringOf([0.5, 1], [0, 2], [1, 2])))),
    [],
  );
});

test('polygonGeometryProblems rejects structurally broken input up front', () => {
  assert.deepEqual(polygonGeometryProblems(null), ['几何缺失']);
  assert.deepEqual(polygonGeometryProblems(undefined), ['几何缺失']);
  assert.deepEqual(polygonGeometryProblems({ type: 'LineString', coordinates: [[0, 0], [1, 1]] }), ['不支持的几何类型：LineString']);
  assert.deepEqual(polygonGeometryProblems({ type: 'Polygon', coordinates: [] }), ['不包含任何环']);
  assert.deepEqual(polygonGeometryProblems({ type: 'Polygon', coordinates: 'bad' }), ['不包含任何环']);
  assert.deepEqual(polygonGeometryProblems({ type: 'MultiPolygon', coordinates: [] }), ['MultiPolygon 不包含任何子面']);
  assert.deepEqual(
    polygonGeometryProblems({ type: 'MultiPolygon', coordinates: [unitSquare.coordinates, []] }),
    ['第 2 个子面不包含任何环'],
  );
  // 结构坏了就不再往下做拓扑检查
  assert.deepEqual(polygonGeometryProblems(polygonOf([[0, 0], [1, 0], [0, 0]])), ['外环少于 4 个坐标']);
});

test('polygonStructureProblem checks only what a merge result needs', () => {
  assert.equal(polygonStructureProblem(unitSquare), null);
  assert.equal(polygonStructureProblem(multiOf(unitSquare, unitSquare)), null, '结构层不管自相交');
  assert.equal(polygonStructureProblem({ type: 'Polygon', coordinates: [] }), '不包含任何环');
  assert.equal(polygonStructureProblem(polygonOf([[0, 0], [1, 0], [0, 0]])), '外环少于 4 个坐标');
  assert.equal(polygonStructureProblem({ type: 'MultiPolygon', coordinates: [] }), 'MultiPolygon 不包含任何子面');
  assert.equal(
    polygonStructureProblem(multiOf(unitSquare, polygonOf())),
    '第 2 个子面不包含任何环',
  );
  assert.equal(
    polygonStructureProblem(multiOf(unitSquare, polygonOf([[5, 5], [6, 5], [5, 5]]))),
    '第 2 个子面外环少于 4 个坐标',
  );
  assert.equal(polygonStructureProblem(null), '几何缺失');
  assert.equal(polygonStructureProblem({ type: 'GeometryCollection', geometries: [] }), '不支持的几何类型：GeometryCollection');
});

test('polygonStructureProblem accepts real @turf/union results', () => {
  // E2-b：合并后的结构校验不能把 union 的正常产物误判为无效
  const asFeatures = (...polygons) => ({
    type: 'FeatureCollection',
    features: polygons.map((geometry) => ({ type: 'Feature', properties: {}, geometry })),
  });
  const overlapping = union(asFeatures(unitSquare, polygonOf(squareRing(0.5, 0.5, 1))));
  assert.equal(overlapping.type, 'Feature');
  assert.equal(overlapping.geometry.type, 'Polygon');
  assert.equal(polygonStructureProblem(overlapping.geometry), null);

  const disjoint = union(asFeatures(unitSquare, polygonOf(squareRing(5, 5, 1))));
  assert.equal(disjoint.geometry.type, 'MultiPolygon');
  assert.equal(polygonStructureProblem(disjoint.geometry), null);

  // 只在一个角点相接的两个面：union 得到两个子面相触的 MultiPolygon（后端判有效）
  const touching = union(asFeatures(unitSquare, polygonOf(squareRing(1, 1, 1))));
  assert.equal(touching.geometry.type, 'MultiPolygon');
  assert.equal(polygonStructureProblem(touching.geometry), null);
});

test('polygonGeometryProblems stays fast on rings with 2000 vertices', () => {
  // 性能底线（任务要求"毫秒~几十毫秒级"）：本机实测 2000 顶点圆环约 2ms。
  // 断言上限留出两个数量级余量，只用来拦住退化成逐对做线段判定的实现
  const circle = (count, radius, jitter = 0) => ringOf(...Array.from({ length: count }, (_, index) => {
    const angle = (2 * Math.PI * index) / count;
    const scaled = radius * (1 + (index % 2 ? jitter : -jitter));
    return [100 + scaled * Math.cos(angle), 25 + scaled * Math.sin(angle)];
  }));
  const shapes = [
    polygonOf(circle(2000, 0.01)),
    polygonOf(circle(2000, 0.01, 0.0004)),
    // 带洞的大面：外环 2000 顶点 + 洞 500 顶点
    polygonOf(circle(2000, 0.01), circle(500, 0.004)),
  ];
  for (const shape of shapes) {
    const startedAt = performance.now();
    assert.deepEqual(polygonGeometryProblems(shape), []);
    const elapsed = performance.now() - startedAt;
    assert.ok(elapsed < 250, `2000 顶点的面应在几十毫秒内完成校验，实际 ${elapsed.toFixed(1)}ms`);
  }
  // 大环里藏一处穿越同样要查出来：把凸环上相隔很远的两个顶点对调
  const vertices = circle(2000, 0.01).slice(0, -1);
  [vertices[300], vertices[900]] = [vertices[900], vertices[300]];
  assert.deepEqual(polygonGeometryProblems(polygonOf(ringOf(...vertices))), ['外环自相交']);
});

const turfSimplify = (geometry, tolerance) => simplify(
  { type: 'Feature', properties: {}, geometry },
  { tolerance, highQuality: false },
).geometry;

test('simplifyPolygonSafely returns the first valid simplification unchanged', () => {
  const tolerances = [];
  const simplified = polygonOf(ringOf([0, 0], [10, 0], [10, 10], [0, 10]));
  const outcome = simplifyPolygonSafely(unitSquare, {
    tolerance: 0.01,
    simplify: (geometry, tolerance) => {
      tolerances.push(tolerance);
      return simplified;
    },
  });
  assert.equal(outcome.geometry, simplified);
  assert.equal(outcome.attempt, 0);
  assert.equal(outcome.tolerance, 0.01);
  assert.deepEqual(tolerances, [0.01]);
});

test('simplifyPolygonSafely retries at a quarter of the tolerance and gives up after two retries', () => {
  const bowTie = polygonOf(ringOf([0, 0], [2, 2], [2, 0], [0, 2]));
  const tolerances = [];
  const outcome = simplifyPolygonSafely(unitSquare, {
    tolerance: 0.01,
    simplify: (geometry, tolerance) => {
      tolerances.push(tolerance);
      return bowTie;
    },
  });
  assert.equal(outcome, null, '三次都不通过 → 返回 null，由调用方跳过该面');
  assert.deepEqual(tolerances, [0.01, 0.0025, 0.000625], '依次为原容差、1/4、1/16');

  // 第一次不行、第二次通过 → 停在 1/4
  let attempt = 0;
  const secondTry = simplifyPolygonSafely(unitSquare, {
    tolerance: 0.01,
    simplify: () => {
      attempt += 1;
      return attempt === 1 ? bowTie : unitSquare;
    },
  });
  assert.equal(secondTry.attempt, 1);
  assert.equal(secondTry.tolerance, 0.0025);

  // 第三次才通过 → 停在 1/16
  attempt = 0;
  const thirdTry = simplifyPolygonSafely(unitSquare, {
    tolerance: 0.01,
    simplify: () => {
      attempt += 1;
      return attempt < 3 ? bowTie : unitSquare;
    },
  });
  assert.equal(thirdTry.attempt, 2);
  assert.equal(thirdTry.tolerance, 0.000625);

  // 洞出问题同样触发重试：洞跑到外环外
  const misplacedHole = polygonOf(squareRing(0, 0, 1), squareRing(2, 2, 1));
  const holeTolerances = [];
  assert.equal(simplifyPolygonSafely(unitSquare, {
    tolerance: 0.01,
    simplify: (geometry, tolerance) => {
      holeTolerances.push(tolerance);
      return misplacedHole;
    },
  }), null);
  assert.deepEqual(holeTolerances, [0.01, 0.0025, 0.000625]);
});

test('simplifyPolygonSafely passes simplify failures through and never touches the source', () => {
  const source = polygonOf(squareRing(0, 0, 1), squareRing(0.2, 0.2, 0.1));
  const snapshot = JSON.stringify(source);
  // turf 对退化环会抛 "invalid polygon"：原样抛出，由调用方计入"已跳过"
  assert.throws(
    () => simplifyPolygonSafely(source, { tolerance: 0.01, simplify: () => { throw new Error('invalid polygon'); } }),
    /invalid polygon/,
  );
  assert.equal(JSON.stringify(source), snapshot);
  // simplify 返回不了几何（null / undefined）时按"不通过"处理：三次都不行 → null，不抛错
  for (const broken of [null, undefined, { type: 'Polygon', coordinates: [] }]) {
    assert.equal(simplifyPolygonSafely(source, { tolerance: 0.01, simplify: () => broken }), null);
  }
});

test('@turf/simplify really produces an invalid ring here, and simplifyPolygonSafely recovers', () => {
  // 真实缺陷复现（@turf/simplify 7.4.0，默认容差 0.00001°）：一个合法的五顶点环，
  // Douglas-Peucker 在 1e-5 下丢掉右下角的顶点，剩下的四边形自相交——
  // 此前这样的结果会直接替换原面，直到保存时才被后端判无效返回 422。
  const unit = 0.000004;
  const source = polygonOf(ringOf(...[
    [10.13, 6],
    [7, 6.29],
    [6.82, 11.67],
    [1.33, 7.37],
    [11.46, 4.4],
  ].map(([x, y]) => [100 + x * unit, 25 + y * unit])));
  assert.deepEqual(polygonGeometryProblems(source), []);

  const reckless = turfSimplify(source, 0.00001);
  assert.deepEqual(polygonGeometryProblems(reckless), ['外环自相交']);
  assert.deepEqual(polygonGeometryProblems(turfSimplify(source, 0.0000025)), []);

  const outcome = simplifyPolygonSafely(source, { tolerance: 0.00001, simplify: turfSimplify });
  assert.equal(outcome.attempt, 1, '默认容差不通过，退到 1/4 容差');
  assert.equal(outcome.tolerance, 0.0000025);
  assert.deepEqual(polygonGeometryProblems(outcome.geometry), []);
  // 这个面在安全容差下没有可去掉的顶点：结果与原坐标一致，调用方据此不计入"已简化"
  assert.deepEqual(outcome.geometry.coordinates, source.coordinates);
});
