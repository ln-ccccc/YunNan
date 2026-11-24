import assert from 'node:assert/strict';
import test from 'node:test';

import { createValidationScheduler, matchVerdicts } from '../src/utils/classificationEditorValidation.mjs';

// matchVerdicts：geometry/validate 的响应数据（{ items }）与送审面的对接。
// 2026-11-24 的第一版把整个响应对象当成结论数组交给调度器，被静默丢弃，界面上一条提示都不出。
test('matchVerdicts returns the verdict array when it lines up with the submitted ids', () => {
  const items = [{ ok: true }, { ok: false, code: 'OUTSIDE_ROI' }];
  assert.equal(matchVerdicts({ items }, ['a', 'b']), items, '原样返回同一个数组（保持与入参同序）');
  assert.deepEqual(matchVerdicts({ items: [{ ok: true }] }, ['a']), [{ ok: true }]);
});

test('matchVerdicts treats anything that does not line up as "no verdict"', () => {
  assert.equal(matchVerdicts({ items: [{ ok: true }] }, ['a', 'b']), null, '结论数量与送审数量不一致');
  assert.equal(matchVerdicts({ items: [] }, ['a']), null, '一个结论都没有');
  assert.equal(matchVerdicts({ items: [{ ok: true }, { ok: true }] }, ['a']), null, '结论多于送审');
  assert.equal(matchVerdicts({ items: null }, ['a']), null);
  assert.equal(matchVerdicts({ items: 'ok' }, ['a']), null, 'items 不是数组（第一版的错误形态之一）');
  assert.equal(matchVerdicts({}, ['a']), null);
  assert.equal(matchVerdicts(null, ['a']), null, '请求失败时调度器拿到的是空值');
  assert.equal(matchVerdicts(undefined, ['a']), null);
  assert.equal(matchVerdicts([{ ok: true }], ['a']), null, '响应对象被当成数组（第一版的错误形态）：没有 items 就没有结论');
});

test('a malformed validate result is dropped instead of being reported with misaligned indices', () => {
  // 钉住这次的事故形态：validate 把 { items } 整个返回时，调度器静默丢弃——不弹下标错位的提示。
  // 「提示确实会出现」由 matchVerdicts 的对接 + 浏览器夹具（越界结论 → 越界提示）保证
  const seen = [];
  const scheduler = createValidationScheduler({
    delayMs: DELAY_MS,
    validate: async () => ({ ids: ['a'], items: { items: [{ ok: false, code: 'OUTSIDE_ROI' }] } }),
    onVerdicts: (verdicts) => seen.push(verdicts),
  });
  scheduler.request(['a']);
  return sleep(DELAY_MS * 3).then(() => assert.deepEqual(seen, []));
});

// 编辑器「编辑完成后的权威校验」的调度器：收集（防抖合并）+ 排序（自增序号，过期丢弃）+
// 容错（请求失败静默）。几何怎么取、请求怎么发、结论怎么提示都由调用方注入，这里用观测台代替。

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

/**
 * 观测台：记录每次 validate 收到的 (ids, seq) 与每次 onVerdicts 收到的结论。
 * validate 可替换；不替换时按 ids 原样返回「全部合法」
 */
function createHarness(validate) {
  const calls = [];
  const verdicts = [];
  const scheduler = createValidationScheduler({
    delayMs: DELAY_MS,
    validate: (ids, seq) => {
      calls.push({ ids, seq });
      return validate ? validate(ids, seq) : { ids, items: ids.map(() => ({ ok: true })) };
    },
    onVerdicts: (result) => {
      verdicts.push(result);
    },
  });
  return { scheduler, calls, verdicts };
}

/** 何时返回由测试决定的 validate：模拟响应晚到、先发后至 */
function controlledValidate() {
  const inFlight = [];
  const validate = (ids, seq) => new Promise((resolve, reject) => {
    inFlight.push({ ids, seq, resolve, reject });
  });
  return { validate, inFlight };
}

const OUTSIDE = { ok: false, code: 'OUTSIDE_ROI', field: 'geometry', reason: '几何必须完全位于成果 ROI 内' };

test('requests inside the debounce window are merged into one batch', async () => {
  const { scheduler, calls, verdicts } = createHarness();
  scheduler.request(['a'], 'create');
  await sleep(20);
  scheduler.request(['b'], 'update');
  await sleep(20);
  scheduler.request(['c'], 'update');
  assert.equal(calls.length, 0, '防抖窗口还没结束时不得发请求');

  await until(() => verdicts.length === 1, '防抖窗口结束后应交付一批结论');
  assert.deepEqual(calls, [{ ids: ['a', 'b', 'c'], seq: 1 }], '三次 request 只发一次，id 按首次进入的顺序');
  assert.deepEqual(verdicts, [{
    ids: ['a', 'b', 'c'],
    items: [{ ok: true }, { ok: true }, { ok: true }],
    contexts: ['create', 'update', 'update'],
  }], '结论与入参同序，每个 id 带着自己那次编辑的 context');

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
  assert.deepEqual(calls, [{ ids: ['a'], seq: 1 }]);
  assert.deepEqual(verdicts[0].contexts, ['update']);
});

test('each batch gets the next sequence number', async () => {
  const { scheduler, calls } = createHarness();
  for (const id of ['a', 'b', 'c']) {
    scheduler.request([id]);
    await until(() => calls.length === calls.length && calls.some((call) => call.ids[0] === id), `${id} 所在的一批应发出`);
  }
  assert.deepEqual(calls.map((call) => call.seq), [1, 2, 3]);
});

test('the older batch is dropped when its response arrives while a newer batch is in flight', async () => {
  const controlled = controlledValidate();
  const { scheduler, verdicts } = createHarness(controlled.validate);
  scheduler.request(['a'], 'create');
  await until(() => controlled.inFlight.length === 1, '第一批应发出');
  // 第一批还在途，面 b 又画好了：第二批发出，第一批的结论从此对应旧画布
  scheduler.request(['b'], 'create');
  await until(() => controlled.inFlight.length === 2, '第二批应发出');
  assert.deepEqual(controlled.inFlight.map((call) => call.seq), [1, 2]);

  controlled.inFlight[0].resolve({ ids: ['a'], items: [OUTSIDE] });
  await sleep(20);
  assert.deepEqual(verdicts, [], '已有更新的一批发出时，旧一批的结论必须丢弃，不得弹过期的越界提示');
});

test('responses arriving out of order: only the latest batch is delivered', async () => {
  const controlled = controlledValidate();
  const { scheduler, verdicts } = createHarness(controlled.validate);
  scheduler.request(['a'], 'create');
  await until(() => controlled.inFlight.length === 1, '第一批应发出');
  scheduler.request(['b'], 'update');
  await until(() => controlled.inFlight.length === 2, '第二批应发出');

  // 先发后至：第二批的结论先回来，它是最新的一批，照常交付
  controlled.inFlight[1].resolve({ ids: ['b'], items: [OUTSIDE] });
  await until(() => verdicts.length === 1, '最新一批的结论应交付');
  // 第一批的结论随后才到：丢弃
  controlled.inFlight[0].resolve({ ids: ['a'], items: [{ ok: true }] });
  await sleep(20);
  assert.deepEqual(verdicts, [{ ids: ['b'], items: [OUTSIDE], contexts: ['update'] }]);
});

test('only the faces validate actually judged come back, each with its own context', async () => {
  // 发出时面 a 已被删除（取不到几何）：validate 只返回真正送审的 b、c，下标仍与 items 对应
  const { scheduler, verdicts } = createHarness((ids) => ({
    ids: ids.filter((id) => id !== 'a'),
    items: [OUTSIDE, { ok: true }],
  }));
  scheduler.request(['a', 'b', 'c'], 'create');
  await until(() => verdicts.length === 1, '应交付结论');
  assert.deepEqual(verdicts[0], { ids: ['b', 'c'], items: [OUTSIDE, { ok: true }], contexts: ['create', 'create'] });

  // 不在这批里的 id 没有 context
  const foreign = createHarness(() => ({ ids: ['zzz'], items: [OUTSIDE] }));
  foreign.scheduler.request(['a'], 'create');
  await until(() => foreign.verdicts.length === 1, '应交付结论');
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

test('cancel() invalidates a response that is already in flight, and the scheduler stays usable', async () => {
  const controlled = controlledValidate();
  const { scheduler, verdicts } = createHarness(controlled.validate);
  scheduler.request(['a']);
  await until(() => controlled.inFlight.length === 1, '第一批应发出');
  scheduler.cancel(); // 切换成果 / 组件销毁
  controlled.inFlight[0].resolve({ ids: ['a'], items: [OUTSIDE] });
  await sleep(20);
  assert.deepEqual(verdicts, [], 'cancel 之后在途响应不得再提示：旧成果的越界提示不能弹到新成果上');

  scheduler.request(['b'], 'create');
  await until(() => controlled.inFlight.length === 2, 'cancel 之后新一批照常发出');
  controlled.inFlight[1].resolve({ ids: ['b'], items: [{ ok: true }] });
  await until(() => verdicts.length === 1, 'cancel 之后新一批的结论照常交付');
  assert.deepEqual(verdicts[0], { ids: ['b'], items: [{ ok: true }], contexts: ['create'] });
  // cancel 本身占掉一个序号（2）：在途的那一批（1）从此小于最新序号，响应回来即被丢弃；
  // 下一批拿到 3，不会被它之前的任何一批顶掉
  assert.deepEqual(controlled.inFlight.map((call) => call.seq), [1, 3]);
});

test('a failing request is swallowed: no verdict, no unhandled rejection, later batches unaffected', async () => {
  // 请求失败静默是明确的约定：编辑期校验只是提示，保存时后端仍会校验
  const unhandled = [];
  const onUnhandled = (reason) => { unhandled.push(reason); };
  process.on('unhandledRejection', onUnhandled);
  try {
    let offline = true;
    const { scheduler, calls, verdicts } = createHarness((ids) => (offline
      ? Promise.reject(Object.assign(new Error('分类成果服务暂时不可用'), { status: null }))
      : { ids, items: [{ ok: true }] }));
    scheduler.request(['a']);
    await until(() => calls.length === 1, '第一批应发出');
    await sleep(20);
    assert.deepEqual(verdicts, [], '请求失败不得产生结论');

    offline = false;
    scheduler.request(['b']);
    await until(() => verdicts.length === 1, '失败之后的下一批照常交付');
    assert.deepEqual(verdicts[0].ids, ['b']);

    await sleep(20);
    assert.deepEqual(unhandled, [], '拒绝必须被调度器接住，不能变成未处理的 Promise 拒绝');
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});

test('a validate that throws synchronously surfaces neither to the caller nor to the timer', async () => {
  const { scheduler, verdicts } = createHarness(() => {
    throw new TypeError('取几何时出错');
  });
  assert.doesNotThrow(() => scheduler.request(['a']));
  // 若异常从定时器回调里逃出，测试进程会在这里直接崩掉
  await sleep(DELAY_MS + 30);
  assert.deepEqual(verdicts, []);
});

test('a result without verdicts is not delivered', async () => {
  const results = [null, undefined, { items: [] }, { ids: 'a', items: [] }];
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
  assert.deepEqual(verdicts, [{ ids: ['a'], items: [OUTSIDE], contexts: ['update'] }]);
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
  assert.doesNotThrow(() => createValidationScheduler({ validate: noop, onVerdicts: noop }));
});
