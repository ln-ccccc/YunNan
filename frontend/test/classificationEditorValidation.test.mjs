import assert from 'node:assert/strict';
import test from 'node:test';

import {
  INVALID,
  PENDING,
  STALE,
  UNKNOWN,
  VALID,
  createSimplifiedRegistry,
  createValidationCoordinator,
  createValidationScheduler,
  createValidationState,
  describeValidationFailure,
  describeValidationSummary,
  geometryKey,
  isFaceGeometry,
  geometryKeysOf,
  matchVerdicts,
  parseSimplifyResponse,
  parseUnionResponse,
  simplifyMessages,
  summarizeSimplify,
} from '../src/utils/classificationEditorValidation.mjs';

// 防抖窗口取 80ms：比编辑器实际用的 200ms 短，又足够让「还没到点」的断言不受调度抖动影响
const DELAY_MS = 80;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 等到条件成立；超时即失败。肯定性断言不依赖精确计时 */
async function until(condition, message) {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() > deadline) assert.fail(message);
    await sleep(5);
  }
}

const square = (x) => ({ type: 'Polygon', coordinates: [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]] });
const key = (x) => geometryKey(square(x));
const OUTSIDE = { ok: false, code: 'OUTSIDE_ROI', field: 'geometry', reason: '几何必须完全位于成果 ROI 内' };
const SELF_INTERSECT = { ok: false, code: 'INVALID_GEOMETRY', field: 'geometry', reason: '几何必须是非空有效面' };
const OK = { ok: true };

// ── matchVerdicts：geometry/validate 的响应数据（{ items }）与送审面的对接 ──
// 2026-11-24 的第一版把整个响应对象当成结论数组交给调度器，被静默丢弃，界面上一条提示都不出。
test('matchVerdicts returns the verdict array when it lines up with the submitted ids', () => {
  const items = [OK, OUTSIDE];
  assert.equal(matchVerdicts({ items }, ['a', 'b']), items, '原样返回同一个数组（保持与入参同序）');
  assert.deepEqual(matchVerdicts({ items: [OK] }, ['a']), [OK]);
});

test('matchVerdicts treats anything that does not line up as "no verdict"', () => {
  assert.equal(matchVerdicts({ items: [OK] }, ['a', 'b']), null, '结论数量与送审数量不一致');
  assert.equal(matchVerdicts({ items: [] }, ['a']), null, '一个结论都没有');
  assert.equal(matchVerdicts({ items: [OK, OK] }, ['a']), null, '结论多于送审');
  assert.equal(matchVerdicts({ items: null }, ['a']), null);
  assert.equal(matchVerdicts({ items: 'ok' }, ['a']), null, 'items 不是数组（第一版的错误形态之一）');
  assert.equal(matchVerdicts({}, ['a']), null);
  assert.equal(matchVerdicts(null, ['a']), null, '请求失败时拿到的是空值');
  assert.equal(matchVerdicts(undefined, ['a']), null);
  assert.equal(matchVerdicts([OK], ['a']), null, '响应对象被当成数组（第一版的错误形态）：没有 items 就没有结论');
});

test('matchVerdicts refuses items that are not shaped like a backend verdict', () => {
  // 数量对得上但某一项不是后端结论的形状：错误页被当成 JSON、代理改写了响应……都不能当成结论
  for (const item of [null, 'ok', { ok: 'yes' }, { ok: false }, { ok: false, code: 422 }, {}]) {
    assert.equal(matchVerdicts({ items: [item] }, ['a']), null, `不认识的结论形状：${JSON.stringify(item)}`);
  }
});

// ── 调度器：收集（防抖合并）+ 分发（结论与失败各走各的）──
// 几何怎么取、请求怎么发都由调用方注入，这里用观测台代替。

/** 观测台：记录每次 validate 收到的 ids 与每次 onVerdicts / onFailure 收到的内容 */
function createHarness(validate) {
  const calls = [];
  const verdicts = [];
  const failures = [];
  const scheduler = createValidationScheduler({
    delayMs: DELAY_MS,
    validate: (ids) => {
      calls.push({ ids });
      const snapshot = (id) => `sent:${id}`;
      const result = validate ? validate(ids) : { ids, items: ids.map(() => OK), snapshots: ids.map(snapshot) };
      if (result && typeof result.then !== 'function' && result.ids) {
        return { ...result, snapshots: result.snapshots || result.ids.map(snapshot) };
      }
      return result;
    },
    onVerdicts: (result) => verdicts.push(result),
    onFailure: (failure) => failures.push(failure),
  });
  return { scheduler, calls, verdicts, failures };
}

/** 何时返回由测试决定的 validate：模拟响应晚到、先发后至 */
function controlledValidate() {
  const inFlight = [];
  const validate = (ids) => new Promise((resolve, reject) => {
    inFlight.push({ ids, resolve, reject });
  });
  return { validate, inFlight };
}

test('requests inside the debounce window are merged into one batch', async () => {
  const { scheduler, calls, verdicts } = createHarness();
  scheduler.request(['a'], 'create');
  await sleep(20);
  scheduler.request(['b'], 'update');
  await sleep(20);
  scheduler.request(['c'], 'update');
  assert.equal(calls.length, 0, '防抖窗口还没结束时不得发请求');

  await until(() => verdicts.length === 1, '防抖窗口结束后应交付一批结论');
  assert.deepEqual(calls, [{ ids: ['a', 'b', 'c'] }], '三次 request 只发一次，id 按首次进入的顺序');
  assert.deepEqual(verdicts, [{
    ids: ['a', 'b', 'c'],
    items: [OK, OK, OK],
    snapshots: ['sent:a', 'sent:b', 'sent:c'],
    contexts: ['create', 'update', 'update'],
  }], '结论与入参同序，每个 id 带着自己那次编辑的 context 与送审时的几何指纹');

  await sleep(DELAY_MS + 30);
  assert.equal(calls.length, 1, '同一批只发一次');
});

test('every request restarts the timer (trailing debounce)', async () => {
  const { scheduler, calls } = createHarness();
  scheduler.request(['a']);
  await sleep(DELAY_MS / 2);
  scheduler.request(['b']);
  // 距第一次 request 已超过一个窗口，距第二次还没有
  await sleep(DELAY_MS / 2 + 10);
  assert.equal(calls.length, 0, '计时必须从最后一次 request 重新开始，否则拖动中会按固定节奏发请求');
  await until(() => calls.length === 1, '最后一次 request 之后一个窗口应发出');
  assert.deepEqual(calls[0].ids, ['a', 'b']);
});

test('a face queued twice is validated once, with the context of its latest edit', async () => {
  const { scheduler, calls, verdicts } = createHarness();
  // 刚画出来（create）就拖了一个顶点（update）：同一个面只校验一次，提示按「改过顶点」给
  scheduler.request(['a'], 'create');
  scheduler.request(['a'], 'update');
  await until(() => verdicts.length === 1, '应交付一批结论');
  assert.deepEqual(calls, [{ ids: ['a'] }]);
  assert.deepEqual(verdicts[0].contexts, ['update']);
});

test('responses are delivered whatever order they arrive in', async () => {
  // 「这条结论过没过期」由协调器逐面比对几何指纹来判（见下面的协调器用例），调度器不按批次丢：
  // 同一批里没被改过的面，结论仍然有效，不能因为另一批更晚发出就一起丢掉，否则它会永远停在「校验中」
  const controlled = controlledValidate();
  const { scheduler, verdicts } = createHarness(controlled.validate);
  scheduler.request(['a', 'b'], 'create');
  await until(() => controlled.inFlight.length === 1, '第一批应发出');
  scheduler.request(['a'], 'update'); // 面在校验途中又被改了
  await until(() => controlled.inFlight.length === 2, '第二批应发出');

  // 先发后至：第二批先回，第一批后回，两批都交给协调器
  controlled.inFlight[1].resolve({ ids: ['a'], items: [OK], snapshots: ['sent:a'] });
  controlled.inFlight[0].resolve({ ids: ['a', 'b'], items: [OUTSIDE, OK], snapshots: ['sent:a', 'sent:b'] });
  await until(() => verdicts.length === 2, '两批结论都应交付');
  assert.deepEqual(verdicts.map((item) => item.ids), [['a'], ['a', 'b']]);
});

test('only the faces validate actually judged come back, each with its own context', async () => {
  // 发出时面 a 已被删除（取不到几何）：validate 只返回真正送审的 b、c，下标仍与 items 对应
  const { scheduler, verdicts } = createHarness((ids) => ({
    ids: ids.filter((id) => id !== 'a'), items: [OUTSIDE, OK],
  }));
  scheduler.request(['a', 'b', 'c'], 'create');
  await until(() => verdicts.length === 1, '应交付结论');
  assert.deepEqual(verdicts[0], {
    ids: ['b', 'c'], items: [OUTSIDE, OK], snapshots: ['sent:b', 'sent:c'], contexts: ['create', 'create'],
  });

  // 不在这批里的 id 没有 context
  const foreign = createHarness(() => ({ ids: ['zzz'], items: [OUTSIDE] }));
  foreign.scheduler.request(['a'], 'create');
  await until(() =>foreign.verdicts.length === 1, '应交付结论');
  assert.deepEqual(foreign.verdicts[0].contexts, [undefined]);
});

test('cancel() clears the timer and the pending set', async () => {
  const { scheduler, calls, verdicts } = createHarness();
  scheduler.request(['a']);
  scheduler.cancel();
  await sleep(DELAY_MS + 30);
  assert.deepEqual(calls, [], 'cancel 之后不得再把待校验的面发出去');
  assert.deepEqual(verdicts, []);
});

test('cancel() does not chase in-flight responses, and the scheduler stays usable', async () => {
  const controlled = controlledValidate();
  const { scheduler, verdicts } = createHarness(controlled.validate);
  scheduler.request(['a']);
  await until(() => controlled.inFlight.length === 1, '第一批应发出');
  scheduler.cancel(); // 切换成果 / 画布回到已保存的状态
  // 在途的响应照样交给协调器：此时画布已经换了，指纹对不上，由协调器判为过期
  controlled.inFlight[0].resolve({ ids: ['a'], items: [OUTSIDE], snapshots: ['sent:a'] });
  await until(() => verdicts.length === 1, '在途的响应回到协调器');

  scheduler.request(['b'], 'create');
  await until(() => controlled.inFlight.length === 2, 'cancel 之后新一批照常发出');
  controlled.inFlight[1].resolve({ ids: ['b'], items: [OK], snapshots: ['sent:b'] });
  await until(() => verdicts.length === 2, '新一批的结论照常交付');
  assert.deepEqual(verdicts[1], { ids: ['b'], items: [OK], snapshots: ['sent:b'], contexts: ['create'] });
});

test('a rejected request is reported to onFailure with the faces that were actually sent', async () => {
  // 请求失败不再静默：哪些面送审过、送审时的几何是什么，都交给 onFailure 去记「未确认」。
  // 拒绝必须被调度器接住，不能变成未处理的 Promise 拒绝
  const unhandled = [];
  const onUnhandled = (reason) => { unhandled.push(reason); };
  process.on('unhandledRejection', onUnhandled);
  try {
    const failure = Object.assign(new Error('分类成果服务暂时不可用'), { status: null, kind: 'network' });
    const { scheduler, calls, verdicts, failures } = createHarness((ids) => Promise.reject(failure));
    scheduler.request(['a', 'b']);
    await until(() => calls.length === 1, '第一批应发出');
    await until(() => failures.length === 1, '请求失败应上报 onFailure');
    assert.deepEqual(failures[0].ids, ['a', 'b'], '发请求之前就失败的：这一批的面都算送审过');
    assert.deepEqual(failures[0].contexts, [undefined, undefined]);
    assert.equal(failures[0].error, failure);
    assert.deepEqual(verdicts, [], '请求失败不得产生结论');

    await sleep(20);
    assert.deepEqual(unhandled, [], '拒绝必须被调度器接住，不能变成未处理的 Promise 拒绝');
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});

test('a failure carrying the faces it sent is reported with exactly those faces and fingerprints', async () => {
  // 调用方取几何时跳过了已被删除的面：只有真正送审的才记「未确认」
  const error = Object.assign(new Error('HTTP 500'), { status: 500, kind: 'http' });
  error.validation = { ids: ['b'], snapshots: ['key-b'], sessionSeq: 7 };
  const { scheduler, failures } = createHarness((ids) => {
    throw error;
  });
  scheduler.request(['a', 'b'], 'create');
  await until(() => failures.length === 1, '应上报失败');
  assert.deepEqual(failures[0], { ids: ['b'], snapshots: ['key-b'], contexts: ['create'], error });
});

test('a failing request does not affect later batches', async () => {
  let offline = true;
  const { scheduler, verdicts, failures } = createHarness((ids) => (offline
    ? Promise.reject(new Error('offline'))
    : { ids, items: [OK] }));
  scheduler.request(['a']);
  await until(() => failures.length === 1, '第一批的失败应上报');

  offline = false;
  scheduler.request(['b']);
  await until(() => verdicts.length === 1, '失败之后的下一批照常交付');
  assert.deepEqual(verdicts[0].ids, ['b']);
});

test('a validate that throws synchronously is reported, not thrown into the timer', async () => {
  const { scheduler, verdicts, failures } = createHarness(() => {
    throw new TypeError('取几何时出错');
  });
  assert.doesNotThrow(() => scheduler.request(['a']));
  // 若异常从定时器回调里逃出，测试进程会在这里直接崩掉
  await until(() => failures.length === 1, '同步抛错也应上报');
  assert.equal(failures[0].error.message, '取几何时出错');
  assert.deepEqual(failures[0].ids, ['a']);
  assert.deepEqual(verdicts, []);
});

test('a result without verdicts is not delivered', async () => {
  const results = [null, undefined, { items: [] }, { ids: 'a', items: [] }, { ids: ['a'], items: [OK], snapshots: 'x' }];
  for (const [index, result] of results.entries()) {
    const { scheduler, calls, verdicts } = createHarness(() => result);
    scheduler.request(['a']);
    await until(() => calls.length === 1, `第 ${index + 1} 个返回值所在的一批应发出`);
    await sleep(20);
    assert.deepEqual(verdicts, [], `validate 返回 ${JSON.stringify(result)} 时不得调用 onVerdicts`);
  }
});

test('a validate that returns a plain value (not a promise) is delivered as is', async () => {
  const { scheduler, verdicts } = createHarness(() => ({ ids: ['a'], items: [OUTSIDE] }));
  scheduler.request(['a'], 'update');
  await until(() => verdicts.length === 1, '应交付结论');
  assert.deepEqual(verdicts, [{ ids: ['a'], items: [OUTSIDE], snapshots: ['sent:a'], contexts: ['update'] }]);
});

test('requests without ids are ignored', async () => {
  const { scheduler, calls } = createHarness();
  for (const empty of [[], null, undefined, 'a']) scheduler.request(empty, 'create');
  await sleep(DELAY_MS + 30);
  assert.deepEqual(calls, [], '没有 id 的 request 不得占用一次请求，也不得推迟已在计时的一批');

  // 在途的一批不被空 request 打断：已有 id 照常发出
  scheduler.request(['a']);
  scheduler.request([]);
  await until(() => calls.length === 1, '已有 id 的一批应发出');
  assert.deepEqual(calls[0].ids, ['a']);
});

test('missing callbacks are rejected when the scheduler is created', () => {
  const noop = () => {};
  assert.throws(() => createValidationScheduler({ onVerdicts: noop }), TypeError, '缺 validate');
  assert.throws(() => createValidationScheduler({ validate: noop }), TypeError, '缺 onVerdicts');
  assert.doesNotThrow(() => createValidationScheduler({ validate: noop, onVerdicts: noop }), 'onFailure 可以不传');
});

// ── geometryKey：几何指纹 ──
test('geometryKey distinguishes geometries value by value and refuses non-faces', () => {
  assert.equal(geometryKey(square(1)), geometryKey(square(1)), '同样的坐标得到同一个指纹');
  assert.notEqual(geometryKey(square(1)), geometryKey(square(2)));
  assert.notEqual(geometryKey(square(1)), geometryKey({ type: 'MultiPolygon', coordinates: [square(1).coordinates] }));
  const moved = square(1);
  moved.coordinates[0][0] = [1.0000001, 0]; // 动了一个顶点不足一亿分之一度
  assert.notEqual(geometryKey(square(1)), geometryKey(moved), '顶点挪动极小的一段也是另一个几何');
  for (const bad of [null, undefined, {}, { type: 'LineString', coordinates: [[0, 0], [1, 1]] }, { type: 'Polygon' }]) {
    assert.equal(geometryKey(bad), null);
  }
});

test('geometryKeysOf maps draw ids to geometry fingerprints and leaves out anything that is not a face', () => {
  const collection = {
    type: 'FeatureCollection',
    features: [
      { id: 'a', geometry: square(1) },
      { id: 'b', geometry: { type: 'MultiPolygon', coordinates: [square(2).coordinates] } },
      { id: 'draft', geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] } }, // 画到一半的草稿
      { id: 'broken', geometry: { type: 'Polygon' } },
      { geometry: square(3) }, // 没有 draw id
    ],
  };
  const keys = geometryKeysOf(collection);
  assert.deepEqual([...keys.keys()], ['a', 'b']);
  assert.equal(keys.get('a'), key(1));
  assert.ok(keys instanceof Map, '返回的是内置的 Map（这个模块里 Map 没有被地图库遮蔽）');
  assert.deepEqual([...geometryKeysOf(null).keys()], []);
  assert.deepEqual([...geometryKeysOf({ features: 'x' }).keys()], []);
});

// ── 逐面状态 ──
test('a face goes pending → valid / invalid, and a verdict for an older geometry is stale', () => {
  const states = createValidationState();
  assert.equal(states.statusOf('a'), null, '没被编辑过的面不在跟踪中');
  states.setPending('a', key(1));
  assert.equal(states.statusOf('a'), PENDING);
  assert.equal(states.summary().pending, 1);

  // 送审之后这个面又被改了：这条结论过期，不应用，状态仍是「校验中」（等它新一次校验）
  assert.equal(states.apply('a', key(1), OUTSIDE, key(2)), STALE);
  assert.equal(states.statusOf('a'), PENDING);
  assert.deepEqual(states.summary(), { pending: 1, invalid: 0, unknown: 0 });

  // 几何与送审时一致：采纳
  assert.equal(states.apply('a', key(1), OUTSIDE, key(1)), 'applied');
  assert.equal(states.statusOf('a'), INVALID);
  assert.deepEqual(states.itemOf('a'), OUTSIDE);
  assert.deepEqual(states.summary(), { pending: 0, invalid: 1, unknown: 0 });
  states.setPending('a', key(1));
  assert.equal(states.apply('a', key(1), OK, key(1)), 'applied');
  assert.equal(states.statusOf('a'), VALID);
  assert.deepEqual(states.summary(), { pending: 0, invalid: 0, unknown: 0 }, '合法与「没被跟踪」都不需要提示');
});

test('a verdict is stale when a newer request for the same face has superseded it', () => {
  const states = createValidationState();
  states.setPending('a', key(1)); // 第一次送审
  states.setPending('a', key(2)); // 第二次送审（新的几何）
  // 第一次的结论晚到：即便此刻画布上的几何碰巧等于第一次的，它也已经被第二次送审取代
  assert.equal(states.apply('a', key(1), OUTSIDE, key(1)), STALE);
  assert.equal(states.statusOf('a'), PENDING);
});

test('a verdict for a face that left the canvas is stale', () => {
  const states = createValidationState();
  states.setPending('a', key(1));
  states.setPending('b', key(2));
  assert.equal(states.apply('a', key(1), OUTSIDE, null), STALE, '取不到几何（已删除）');
  states.retainOnly(new Set(['b']));
  assert.equal(states.statusOf('a'), null, '不在画布上的面不再跟踪');
  assert.equal(states.apply('a', key(1), OK, key(1)), STALE);
  assert.equal(states.statusOf('b'), PENDING);
});

test('unknown is recorded only for a face still awaiting that very request', () => {
  const states = createValidationState();
  states.setPending('a', key(1));
  states.setPending('b', key(2));
  assert.equal(states.setUnknown('a', key(1), key(2)), STALE, '送审之后又被改了：不记未确认，等它的新一次校验');
  assert.equal(states.statusOf('a'), PENDING);
  assert.equal(states.setUnknown('b', key(2), key(2)), 'applied');
  assert.equal(states.statusOf('b'), UNKNOWN);
  assert.deepEqual(states.summary(), { pending: 1, invalid: 0, unknown: 1 });
  // 服务恢复后的下一次校验把它确认下来（a 还在等它新一次校验的结论）
  states.setPending('b', key(2));
  assert.equal(states.apply('b', key(2), OK, key(2)), 'applied');
  assert.deepEqual(states.summary(), { pending: 1, invalid: 0, unknown: 0 });
});

test('markValid and clear', () => {
  const states = createValidationState();
  states.setPending('a', key(1));
  states.setUnknown('a', key(1), key(1));
  states.markValid('a', key(1)); // 保存通过 / 合并简化的结果：后端已经认可
  assert.equal(states.statusOf('a'), VALID);
  states.forget('a');
  assert.equal(states.statusOf('a'), null);
  states.setPending('b', key(2));
  states.clear();
  assert.deepEqual(states.summary(), { pending: 0, invalid: 0, unknown: 0 });
});

// ── 协调器：状态落到面上，并决定提示什么。用一个假的画布（Map）代替绘制库 ──
function createEditorHarness() {
  const canvas = new Map(); // draw id → 几何
  const queued = [];        // 协调器要求送审的 (ids, action)
  const warnings = [];      // 普通提示
  const merged = [];        // 「未确认」提示（同样内容合并）
  const summaries = [];
  const actions = new Map();   // 每个面最近一次的编辑方式（create / update）
  const coordinator = createValidationCoordinator({
    readGeometryKey: (id) => geometryKey(canvas.get(id) || null),
    requestValidation: (ids, action) => queued.push({ ids, action }),
    onChange: (summary) => summaries.push(summary),
    warn: (text) => warnings.push(text),
    warnMerged: (text) => merged.push(text),
  });
  const edit = (id, geometry, action = 'update') => {
    canvas.set(id, geometry);
    actions.set(id, action);
    coordinator.edited([id], action);
  };
  const verdictFor = (id, item, sentKey = geometryKey(canvas.get(id))) =>
    coordinator.verdicts({ ids: [id], items: [item], snapshots: [sentKey], contexts: [actions.get(id)] });
  const failureFor = (id, error, sentKey = geometryKey(canvas.get(id))) =>
    coordinator.failed({ ids: [id], snapshots: [sentKey], error });
  return { canvas, queued, warnings, merged, summaries, coordinator, edit, verdictFor, failureFor };
}

test('an edited face is pending until the backend confirms it; a legal verdict clears silently', () => {
  const h = createEditorHarness();
  h.edit('a', square(1), 'create');
  assert.deepEqual(h.queued, [{ ids: ['a'], action: 'create' }], '编辑完就送审');
  assert.deepEqual(h.coordinator.summary(), { pending: 1, invalid: 0, unknown: 0 });
  h.verdictFor('a', OK);
  assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
  assert.deepEqual(h.coordinator.statusOf('a'), VALID);
  assert.deepEqual(h.warnings, [], '合法的面不提示');
});

test('an illegal verdict marks the face invalid and explains the next step by how it was edited', () => {
  for (const [action, item, expected] of [
    ['create', OUTSIDE, '1 个面超出成果 ROI（矿山边界），保存时将被拒绝，请拖回界内或删除'],
    ['update', OUTSIDE, '1 个面超出成果 ROI（矿山边界），保存时将被拒绝，请调整顶点位置'],
    ['create', SELF_INTERSECT, '1 个面几何无效（如自相交），保存时将被拒绝，请调整顶点位置'],
  ]) {
    const h = createEditorHarness();
    h.edit('a', square(1), action);
    h.verdictFor('a', item);
    assert.equal(h.coordinator.statusOf('a'), INVALID);
    assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 1, unknown: 0 });
    assert.deepEqual(h.warnings, [expected]);
  }
});

test('rapid edits: the verdict for the previous geometry is stale and never surfaces', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  const firstKey = key(1);
  h.edit('a', square(2)); // 后端的结论还没回来，面又被改了（改回界内）
  assert.equal(h.queued.length, 2, '两次编辑各送审一次');
  h.verdictFor('a', OUTSIDE, firstKey); // 第一次的结论此刻才到：越界
  assert.deepEqual(h.warnings, [], '过期的越界提示一个字都不能弹');
  assert.equal(h.coordinator.statusOf('a'), PENDING, '仍在等第二次的结论');
  h.verdictFor('a', OK);
  assert.equal(h.coordinator.statusOf('a'), VALID);
  assert.deepEqual(h.warnings, []);
});

test('undo while validating: the restored face is re-validated and the stale verdict stays silent', () => {
  const h = createEditorHarness();
  h.canvas.set('a', square(1)); // 已保存的面，没被编辑过，不在跟踪中
  h.edit('a', square(2));       // 拖出去
  const draggedKey = key(2);
  // 撤销：draw.set() 不发事件，由组件调用 canvasReplaced，传入替换前后的指纹
  const before = new Map([['a', draggedKey]]);
  h.canvas.set('a', square(1));
  h.coordinator.canvasReplaced(before, new Map([['a', key(1)]]));
  assert.deepEqual(h.queued[1], { ids: ['a'], action: 'update' }, '撤销恢复出来的面重新送审');
  // 拖出去那一次的结论此时才到（越界）：过期，不提示
  h.verdictFor('a', OUTSIDE, draggedKey);
  assert.deepEqual(h.warnings, [], '撤销之后才回来的越界提示不得弹出');
  assert.equal(h.coordinator.statusOf('a'), PENDING);
  h.verdictFor('a', OK); // 恢复出来的几何是合法的
  assert.equal(h.coordinator.statusOf('a'), VALID);

  // 重做：面又是拖出去的那个几何，重新校验后判为越界并提示
  h.canvas.set('a', square(2));
  h.coordinator.canvasReplaced(new Map([['a', key(1)]]), new Map([['a', draggedKey]]));
  h.verdictFor('a', OUTSIDE, draggedKey);
  assert.equal(h.coordinator.statusOf('a'), INVALID);
  assert.deepEqual(h.warnings, ['1 个面超出成果 ROI（矿山边界），保存时将被拒绝，请调整顶点位置']);
});

test('undo across several faces re-validates only the ones whose geometry changed', () => {
  const h = createEditorHarness();
  h.canvas.set('a', square(1));
  h.canvas.set('b', square(2));
  h.canvas.set('c', square(3));
  const before = new Map([['a', key(1)], ['b', key(2)], ['c', key(3)]]);
  h.canvas.set('b', square(5)); // 只有 b 变了；d 是新出现的
  h.canvas.set('d', square(4));
  h.coordinator.canvasReplaced(before, new Map([['a', key(1)], ['b', key(5)], ['d', key(4)]]));
  assert.deepEqual(h.queued, [{ ids: ['b', 'd'], action: 'update' }]);
  h.canvas.delete('a');
  h.coordinator.canvasReplaced(new Map([['a', key(1)]]), new Map());
  assert.equal(h.coordinator.statusOf('a'), null, '被撤销掉的面不再跟踪');
});

test('network failure, timeout, 404, 500 and malformed responses all become "unconfirmed" with a specific reason', () => {
  const cases = [
    [{ status: null, kind: 'network' }, '网络连接失败'],
    [{ status: null, kind: 'timeout' }, '请求超时'],
    [{ status: 404, kind: 'http' }, '校验接口不存在，后端可能还没有更新到配套版本'],
    [{ status: 500, kind: 'http' }, '校验服务出错'],
    [{ status: 502, kind: 'http' }, '校验服务出错'],
    [{ status: 422, kind: 'http' }, '校验服务返回 HTTP 422'],
    [{ kind: 'protocol' }, '服务返回的数据与送审的面对不上'],
    [{ status: 422, kind: 'business', message: '请求 JSON 超过 10 MiB' }, '请求 JSON 超过 10 MiB'],
  ];
  for (const [error, reason] of cases) {
    const h = createEditorHarness();
    h.edit('a', square(1));
    h.failureFor('a', Object.assign(new Error('x'), error));
    assert.equal(h.coordinator.statusOf('a'), UNKNOWN, `未确认：${JSON.stringify(error)}`);
    assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 1 }, '不是合法（没有 invalid），也不是「校验中」');
    assert.equal(h.merged.length, 1);
    assert.equal(h.merged[0], `1 个面的合法性未能确认（${reason}）；可以继续编辑，保存时以后端校验为准`);
    assert.deepEqual(h.warnings, [], '未确认不冒充越界 / 无效的提示');
  }
});

test('describeValidationFailure infers the kind when the client did not set one', () => {
  assert.deepEqual(describeValidationFailure(new Error('boom')), { kind: 'network', reason: '网络连接失败' });
  assert.deepEqual(describeValidationFailure({ response: { status: 404 } }), { kind: 'http', reason: '校验接口不存在，后端可能还没有更新到配套版本' });
  assert.deepEqual(describeValidationFailure({ status: 503 }), { kind: 'http', reason: '校验服务出错' });
});

test('a failure for a geometry that has since changed is stale: the face stays pending, nothing is reported', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  const firstKey = key(1);
  h.edit('a', square(2));
  h.failureFor('a', Object.assign(new Error('offline'), { kind: 'network' }), firstKey);
  assert.equal(h.coordinator.statusOf('a'), PENDING, '等它新一次校验的结论，不记未确认');
  assert.deepEqual(h.merged, []);
});

test('editing continues after a failure, and the next successful check clears "unconfirmed"', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  h.failureFor('a', Object.assign(new Error('offline'), { kind: 'network' }));
  assert.equal(h.coordinator.statusOf('a'), UNKNOWN);
  h.edit('a', square(2)); // 未确认不妨碍继续编辑
  assert.equal(h.coordinator.statusOf('a'), PENDING);
  h.verdictFor('a', OK);
  assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
});

test('a face that left the canvas while its request was failing is not reported', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  const sentKey = key(1);
  h.canvas.delete('a');
  h.failureFor('a', Object.assign(new Error('offline'), { kind: 'network' }), sentKey);
  assert.deepEqual(h.merged, [], '面都已经删了，没什么可确认的');
  assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
});

test('undoing the creation of a face clears its record and refreshes the summary', () => {
  // 2026-11-25 浏览器夹具抓到：撤销「新建面」时这个面直接消失，跟踪表里删掉了它，却没有通知界面，
  // 状态条上残留着「1 个面未确认」。几何变了的面会经「重新送审」顺带刷新；没有面需要送审时也必须刷新
  for (const settle of [
    (h) => h.failureFor('a', Object.assign(new Error('offline'), { kind: 'network' })),
    (h) => h.verdictFor('a', OUTSIDE),
    () => {},
  ]) {
    const h = createEditorHarness();
    h.edit('a', square(1), 'create');
    settle(h);
    const summariesBefore = h.summaries.length;
    h.canvas.delete('a');
    h.coordinator.canvasReplaced(new Map([['a', key(1)]]), new Map());
    assert.equal(h.coordinator.statusOf('a'), null, '被撤销掉的面不再跟踪');
    assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
    assert.equal(h.summaries.length, summariesBefore + 1, '没有面需要重新送审时，也必须通知界面刷新汇总');
    assert.deepEqual(h.queued.slice(1), [], '没有面需要送审');
  }
});

test('merging marks the new face valid and drops tracking of the faces it replaced', () => {
  const h = createEditorHarness();
  h.canvas.set('a', square(1));
  h.canvas.set('b', square(2));
  h.edit('a', square(3));
  h.edit('b', square(4));
  h.verdictFor('b', OUTSIDE);
  assert.deepEqual(h.coordinator.summary(), { pending: 1, invalid: 1, unknown: 0 });
  // 合并：两个源面离开画布，出现一个后端校验过的新面
  h.canvas.delete('a');
  h.canvas.delete('b');
  h.canvas.set('merged', { type: 'MultiPolygon', coordinates: [square(3).coordinates, square(4).coordinates] });
  h.coordinator.removed(['a', 'b']);
  h.coordinator.backendValidated(['merged']);
  assert.deepEqual(h.queued.slice(2), [], '合并出来的面后端校验过，不再送审');
  assert.equal(h.coordinator.statusOf('merged'), VALID);
  assert.equal(h.coordinator.statusOf('a'), null);
  assert.equal(h.coordinator.statusOf('b'), null);
  assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 }, '被合并掉的面的越界记录随之消失');
});

test('deleting a face drops its validation record, including an "unconfirmed" one', () => {
  for (const settle of [
    (h) => h.verdictFor('a', OUTSIDE),
    (h) => h.failureFor('a', Object.assign(new Error('offline'), { kind: 'network' })),
    (h) => {},
  ]) {
    const h = createEditorHarness();
    h.edit('a', square(1));
    settle(h);
    assert.notDeepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
    h.coordinator.removed(['a', 'never-tracked']);
    assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 }, '用户删掉这个面之后，工具栏不该再提它');
  }
});

test('saving while checks are pending: faces unchanged since submission become valid, edited ones keep waiting', () => {
  const h = createEditorHarness();
  h.canvas.set('base', square(9));
  h.edit('a', square(1));                     // 校验中
  h.edit('b', square(2));
  h.failureFor('b', Object.assign(new Error('offline'), { kind: 'network' })); // 未确认
  const submitted = new Map([['a', key(1)], ['b', key(2)], ['base', key(9)]]);
  h.edit('a', square(5));                     // 保存期间 a 又被改了
  h.coordinator.savedUnchanged(submitted);
  assert.equal(h.coordinator.statusOf('a'), PENDING, '保存之后又改过的面不等同于后端认可');
  assert.equal(h.coordinator.statusOf('b'), VALID, '提交后没再改过的面：后端在保存时认可了它');
  assert.deepEqual(h.coordinator.summary(), { pending: 1, invalid: 0, unknown: 0 });
});

test('an invalid face is not excused by a save that was submitted before it went bad', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  h.verdictFor('a', OK);
  h.edit('a', square(2));
  h.verdictFor('a', OUTSIDE);
  h.coordinator.savedUnchanged(new Map([['a', key(1)]])); // 提交的是改坏之前的几何
  assert.equal(h.coordinator.statusOf('a'), INVALID, '已确认非法的面不因为一次旧提交而变合法');
});

test('reset clears everything', () => {
  const h = createEditorHarness();
  h.edit('a', square(1));
  h.verdictFor('a', OUTSIDE);
  h.edit('b', square(2));
  h.coordinator.reset();
  assert.deepEqual(h.coordinator.summary(), { pending: 0, invalid: 0, unknown: 0 });
  assert.equal(h.coordinator.statusOf('a'), null);
  // 重置之后才回来的结论：面已不在跟踪中，过期
  const warningsBefore = h.warnings.length;
  h.verdictFor('b', OUTSIDE);
  assert.equal(h.warnings.length, warningsBefore, '重置之后才回来的越界提示不得弹出');
  assert.deepEqual(h.merged, []);
});

test('the coordinator refuses to start without a way to read the canvas', () => {
  assert.throws(() => createValidationCoordinator({}), TypeError);
  assert.throws(() => createValidationCoordinator({ readGeometryKey: 'x' }), TypeError);
});

test('the toolbar sentence names each state and ranks the most serious one', () => {
  assert.deepEqual(describeValidationSummary({ pending: 0, invalid: 0, unknown: 0 }), { text: '', tone: '', hint: '' });
  assert.equal(describeValidationSummary(null).text, '', '还没有任何汇总时什么都不显示');
  const pending = describeValidationSummary({ pending: 3, invalid: 0, unknown: 0 });
  assert.deepEqual([pending.text, pending.tone], ['校验中 3 个面', 'pending']);
  const invalid = describeValidationSummary({ pending: 1, invalid: 2, unknown: 0 });
  assert.deepEqual([invalid.text, invalid.tone], ['校验中 1 个面，2 个面未通过校验', 'invalid']);
  assert.match(invalid.hint, /保存时会被拒绝/);
  const unknown = describeValidationSummary({ pending: 0, invalid: 1, unknown: 5 });
  assert.deepEqual([unknown.text, unknown.tone], ['1 个面未通过校验，5 个面未确认', 'unknown']);
  assert.match(unknown.hint, /尚未确认.*保存时以后端校验为准/);
});

// ── 合并 / 简化的响应校验：响应格式异常按失败处理，不往画布上套 ──
test('isFaceGeometry accepts well-formed faces and refuses everything else', () => {
  assert.equal(isFaceGeometry(square(1)), true);
  assert.equal(isFaceGeometry({ type: 'MultiPolygon', coordinates: [square(1).coordinates, square(2).coordinates] }), true);
  const holed = { type: 'Polygon', coordinates: [square(1).coordinates[0], [[1.2, 0.2], [1.2, 0.4], [1.4, 0.4], [1.4, 0.2], [1.2, 0.2]]] };
  assert.equal(isFaceGeometry(holed), true, '带孔的面');
  for (const bad of [
    null, undefined, {}, { type: 'Polygon' }, { type: 'Polygon', coordinates: [] },
    { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1]]] },              // 没有闭合
    { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1, 3], [0, 0]]] }, // 多出一维
    { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, NaN], [0, 1], [0, 0]]] },    // 非有限数值
    { type: 'Polygon', coordinates: [[[0, 0], [1, 0], ['1', 1], [0, 1], [0, 0]]] },    // 字符串坐标
    { type: 'MultiPolygon', coordinates: [] },
    { type: 'GeometryCollection', geometries: [square(1)] },
    { type: 'Feature', geometry: square(1) },
  ]) {
    assert.equal(isFaceGeometry(bad), false, `应拒绝：${JSON.stringify(bad)}`);
  }
});

test('parseUnionResponse returns the geometry or throws a protocol error', () => {
  assert.deepEqual(parseUnionResponse({ geometry: square(1) }), square(1));
  for (const bad of [null, undefined, {}, { geometry: null }, { geometry: { type: 'Polygon' } }, [{ type: 'Polygon' }], 'ok']) {
    assert.throws(() => parseUnionResponse(bad), (error) => error.kind === 'protocol', `应拒绝：${JSON.stringify(bad)}`);
  }
});

test('parseSimplifyResponse requires one well-formed item per submitted face', () => {
  const changed = { outcome: 'changed', geometry: square(1), tolerance: 1e-6, area_change: 0.001, shape_change: 0.01 };
  const items = [changed, { outcome: 'unchanged' }, { outcome: 'rejected', reason: { ok: false, code: 'SHAPE_DRIFT' } }];
  assert.deepEqual(parseSimplifyResponse({ mode: 'adaptive', tolerance: null, items }, 3), items);
  for (const [payload, count] of [
    [null, 1], [{}, 1], [{ items: items }, 2],                    // 数量对不上
    [{ items: { items } }, 2],                                    // 旧版前端曾犯的错：items 不是数组
    [{ items: [{ outcome: 'ok' }] }, 1],                          // 不认识的 outcome
    [{ items: [null] }, 1],
    [{ items: [{ outcome: 'changed' }] }, 1],                     // changed 却没有几何
    [{ items: [{ outcome: 'changed', geometry: { type: 'Polygon' } }] }, 1],
  ]) {
    assert.throws(() => parseSimplifyResponse(payload, count), (error) => error.kind === 'protocol', `应拒绝：${JSON.stringify(payload)}`);
  }
});

test('simplify outcomes are counted by outcome and refusal reason', () => {
  const counts = summarizeSimplify([
    { outcome: 'changed', area_change: 0.031 },
    { outcome: 'changed', area_change: -0.002 },
    { outcome: 'unchanged' },
    { outcome: 'rejected', reason: { code: 'OUTSIDE_ROI' } },
    { outcome: 'rejected', reason: { code: 'INVALID_GEOMETRY' } },
    { outcome: 'rejected', reason: { code: 'SHAPE_DRIFT' } },
    { outcome: 'rejected', reason: { code: 'SHAPE_DRIFT' } },
  ]);
  assert.deepEqual(counts, { changed: 2, unchanged: 1, rejectedOutside: 1, rejectedInvalid: 1, rejectedDrift: 2, maxAreaChange: 0.031 });
});

test('simplify messages tell apart simplified, refused (and why), untouched, and not re-submitted faces', () => {
  assert.deepEqual(simplifyMessages(summarizeSimplify([])), [{ type: 'info', text: '选中面的边缘已足够平滑' }]);
  assert.deepEqual(simplifyMessages(summarizeSimplify([{ outcome: 'changed', area_change: 0.00001 }])),
    [{ type: 'success', text: '已简化 1 个面的边缘' }], '面积变化不足 0.005% 时不写数字');
  assert.deepEqual(simplifyMessages(summarizeSimplify([{ outcome: 'changed', area_change: 0.00314 }, { outcome: 'unchanged' }])), [
    { type: 'success', text: '已简化 1 个面的边缘，面积最大变化 0.31%' },
    { type: 'info', text: '1 个面在精度要求内没有可安全去除的顶点，保持原样' },
  ]);
  const refused = summarizeSimplify([
    { outcome: 'rejected', reason: { code: 'OUTSIDE_ROI' } },
    { outcome: 'rejected', reason: { code: 'SHAPE_DRIFT' } },
  ]);
  assert.deepEqual(simplifyMessages(refused, { changedMeanwhile: 1, alreadySimplified: 2 }), [
    { type: 'warning', text: '1 个面简化后会超出矿山边界或几何无效，已跳过，原面保持不变' },
    { type: 'warning', text: '1 个面简化后面积或形态变化会超过精度上限，已跳过，原面保持不变' },
    { type: 'info', text: '1 个面在简化期间被修改，本次没有简化；需要的话请重新选择' },
    { type: 'info', text: '2 个面已是消除锯齿后的结果，没有重复简化；改动过之后才能再次消除锯齿' },
  ]);
});

test('a face is a fresh simplify result only while its geometry is exactly what simplify returned', () => {
  const registry = createSimplifiedRegistry();
  const feature = { id: 'a', geometry: square(1) };
  assert.equal(registry.isFreshResult(feature), false, '没简化过的面可以提交');
  registry.remember('a', square(1));
  assert.equal(registry.isFreshResult(feature), true, '简化返回的几何原样未动：不重复提交');
  assert.equal(registry.isFreshResult({ ...feature, geometry: square(2) }), false, '拖过顶点：可以再次简化');
  assert.equal(registry.isFreshResult(null), false);
  assert.equal(registry.isFreshResult({ id: 'zzz', geometry: square(1) }), false, '别的面即使几何相同也不算');
  registry.retainOnly(new Set(['b']));
  assert.equal(registry.isFreshResult(feature), false, '面离开画布后不再登记');
  registry.remember('a', { type: 'Polygon' }); // 不是面：不登记
  assert.equal(registry.isFreshResult(feature), false);
  registry.remember('a', square(1));
  registry.clear();
  assert.equal(registry.isFreshResult(feature), false);
});

// 守卫：五个状态名各不相同，过期不是状态而是结论的归属判定
test('the five state names are distinct and STALE is never a stored status', () => {
  assert.equal(new Set([PENDING, VALID, INVALID, UNKNOWN, STALE]).size, 5);
  const states = createValidationState();
  states.setPending('a', key(1));
  states.apply('a', key(1), OUTSIDE, key(2));
  states.setUnknown('a', key(1), key(2));
  for (const status of [states.statusOf('a'), ...Object.keys(states.summary())]) {
    assert.notEqual(status, STALE);
  }
});
