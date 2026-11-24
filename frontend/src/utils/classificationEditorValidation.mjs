/**
 * 分类编辑器的编辑期校验（纯逻辑：无 Vue、无 DOM、无网络依赖，便于单测）。
 *
 * 面的合法性（是否超出矿山边界、几何是否有效）由后端几何引擎判定，与保存共用同一个判定函数；
 * 前端不再自带任何几何算法。这里负责三样东西：
 *   1. 调度器：收集 + 防抖合并 + 失败上报；
 *   2. 状态跟踪：每个面处在 Pending / Valid / Invalid / Unknown 哪一种；
 *   3. 协调器：把请求结果落到状态上，并决定界面提示什么。
 *
 * ## 五种状态
 *   Pending   正在校验
 *   Valid     后端确认合法
 *   Invalid   后端确认非法
 *   Unknown   校验失败或尚未取得可信结论——**不代表合法，也不代表非法**。
 *             普通编辑可以继续，保存始终由权威后端校验决定。
 *   Stale     一条结论的归属判定，不是面的状态：结论对应的面在送审之后又被改过（或已不在画布上），
 *             这条结论不应用。判定办法是逐值比较「送审时的几何」与「此刻画布上的几何」。
 *
 * 2026-11-24 的第一版请求失败是静默的（回归报告里的 E2）：断网、超时、后端 404 / 500、响应格式异常，
 * 界面上什么都没有，面看起来与「已确认合法」没有区别；撤销 / 重做是 draw.set()，不发事件，既不会让
 * 在途的旧结论过期，也不会把恢复出来的面重新送审。现在这几种情况各自有明确的状态与提示。
 */

export const PENDING = 'pending';
export const VALID = 'valid';
export const INVALID = 'invalid';
export const UNKNOWN = 'unknown';
export const STALE = 'stale';
const APPLIED = 'applied';

const FACE_TYPES = new Set(['Polygon', 'MultiPolygon']);
const SIMPLIFY_OUTCOMES = new Set(['changed', 'unchanged', 'rejected']);

/**
 * 一个面的几何指纹：类型加坐标的逐值序列化。只用于判断「还是不是同一个几何」，不做任何几何运算。
 * @returns {string | null}
 */
export function geometryKey(geometry) {
  if (!geometry || !FACE_TYPES.has(geometry.type) || !Array.isArray(geometry.coordinates)) return null;
  return `${geometry.type}|${JSON.stringify(geometry.coordinates)}`;
}

/**
 * 一个 FeatureCollection 里每个面的几何指纹（draw id → 指纹）。非面的要素（草稿上的点、线）不在其中。
 *
 * 放在这个模块里而不是组件里：组件顶部 `import { Map } from 'maplibre-gl'`，组件里的 `new Map()`
 * 构造的是地图库的 Map 而不是内置的 Map（2026-11-25 真实浏览器夹具抓到的回归：撤销走到一半抛异常）。
 */
export function geometryKeysOf(featureCollection) {
  const keys = new Map();
  const features = featureCollection?.features;
  if (!Array.isArray(features)) return keys;
  features.forEach((feature) => {
    const key = feature.id == null ? null : geometryKey(feature.geometry);
    if (key !== null) keys.set(feature.id, key);
  });
  return keys;
}

/**
 * 后端结论的形状：{ ok: true } 或 { ok: false, code, … }
 */
export function isVerdict(item) {
  if (!item || typeof item !== 'object') return false;
  if (item.ok === true) return true;
  return item.ok === false && typeof item.code === 'string' && item.code.length > 0;
}

/**
 * 把 geometry/validate 的响应数据对到送审的面上，取出逐个面的结论数组。
 *
 * 响应数据是 { items }，不是数组本身。2026-11-24 的第一个版本把整个响应对象当成数组往下传，
 * 调度器发现不是数组就静默丢弃——请求照发、后端照答，界面上却一条提示都不出，而当时的测试只数了
 * 「请求发出去了」。所以这个对接单独成函数并有单测：结论数组必须存在、与送审的数量一一对应、
 * 每一项都是后端结论的形状，否则视为没有结论（返回 null），绝不让下标错位的提示弹出来。
 *
 * @param {{ items?: object[] } | null | undefined} payload  接口返回的 data
 * @param {any[]} ids  这一批送审的画布 id
 * @returns {object[] | null}
 */
export function matchVerdicts(payload, ids) {
  const items = payload && Array.isArray(payload.items) ? payload.items : null;
  if (!items || items.length !== ids.length) return null;
  return items.every(isVerdict) ? items : null;
}

/**
 * 逐面的校验状态。每条记录里带着「送审时的几何指纹」：一条结论只有与它逐值相同才采纳。
 */
export function createValidationState() {
  const entries = new Map(); // draw id → { status, key, item }

  const settle = (id, key, liveKey, status, item) => {
    const entry = entries.get(id);
    // 已被更新的一次送审取代、或这个面已不再跟踪：这条结论是过期的
    if (!entry || entry.key !== key) return STALE;
    // 结论回来时面已经不在画布上：不再跟踪它（否则工具栏会一直写着一个并不存在的「校验中」）
    if (liveKey === null) {
      entries.delete(id);
      return STALE;
    }
    // 送审之后它又被改了：这条结论是过期的，等它新一次校验
    if (liveKey !== key) return STALE;
    entry.status = status;
    entry.item = item;
    return APPLIED;
  };

  return {
    statusOf: (id) => entries.get(id)?.status ?? null,
    itemOf: (id) => entries.get(id)?.item ?? null,
    setPending(id, key) {
      entries.set(id, { status: PENDING, key, item: null });
    },
    /** 应用一条后端结论。返回 'applied' 或 'stale'。 */
    apply(id, key, item, liveKey) {
      return settle(id, key, liveKey, item.ok ? VALID : INVALID, item);
    },
    /** 这一批没能取得结论。返回 'applied'（记为 Unknown）或 'stale'。 */
    setUnknown(id, key, liveKey) {
      return settle(id, key, liveKey, UNKNOWN, null);
    },
    /** 后端已经认可这个几何（保存通过、合并或简化的结果）。 */
    markValid(id, key) {
      entries.set(id, { status: VALID, key, item: null });
    },
    forget(id) {
      entries.delete(id);
    },
    retainOnly(ids) {
      for (const id of entries.keys()) if (!ids.has(id)) entries.delete(id);
    },
    clear() {
      entries.clear();
    },
    summary() {
      const counts = { pending: 0, invalid: 0, unknown: 0 };
      for (const entry of entries.values()) {
        if (entry.status in counts) counts[entry.status] += 1;
      }
      return counts;
    },
  };
}

/**
 * 编辑期校验的「防抖批量校验」调度器（纯逻辑）。
 *
 * draw.create / draw.update 每来一次就发一个请求，连续操作时会刷出一串。这里只负责两件事：
 *   1. 收集：delayMs 内的多次 request 合并成一批（每次 request 重新计时）；
 *   2. 分发：一批的结论交给 onVerdicts，失败交给 onFailure；同步抛错与 Promise 拒绝都接住。
 * 「这条结论是否已经过期」不在这里按批次判断，而是由调用方逐面比对几何指纹（见协调器）——
 * 同一批里没被改过的面，结论仍然有效，不能因为另一批更晚发出就一起丢掉。
 *
 * @param {{
 *   delayMs?: number,
 *   validate: (ids: any[]) => any,
 *   onVerdicts: (result: { ids: any[], items: any[], snapshots: any[], contexts: any[] }) => void,
 *   onFailure: (failure: { ids: any[], snapshots: any[], contexts: any[], error: any }) => void,
 * }} options
 *   validate：取几何并发请求。返回（或 resolve 为）{ ids, items, snapshots }，三者按下标对应；
 *     返回空（null / undefined）表示这次没有送审任何面。失败时抛出或 reject；若错误对象上带有
 *     validation = { ids, snapshots }，就按它上报（只有真正送审过的面才记为 Unknown）。
 * @returns {{ request: (ids: any[], context?: any) => void, cancel: () => void }}
 */
export function createValidationScheduler({ delayMs = 200, validate, onVerdicts, onFailure = () => {} } = {}) {
  if (typeof validate !== 'function' || typeof onVerdicts !== 'function') {
    throw new TypeError('createValidationScheduler 需要 validate 与 onVerdicts 两个函数');
  }
  const delay = Number.isFinite(delayMs) && delayMs >= 0 ? delayMs : 200;

  let timer = null;
  // 待校验的 id → 最近一次 request 附带的 context（同一个 id 多次 request 以最后一次为准）
  let pending = new Map();

  const contextsOf = (ids, batch) => ids.map((id) => batch.get(id));

  function deliver(batch, result) {
    if (!result || !Array.isArray(result.ids) || !Array.isArray(result.items) || !Array.isArray(result.snapshots)) return;
    onVerdicts({
      ids: result.ids,
      items: result.items,
      snapshots: result.snapshots,
      contexts: contextsOf(result.ids, batch),
    });
  }

  function fail(batch, error) {
    const sent = error && error.validation;
    const ids = sent && Array.isArray(sent.ids) ? sent.ids : [...batch.keys()];
    const snapshots = sent && Array.isArray(sent.snapshots) ? sent.snapshots : ids.map(() => undefined);
    onFailure({ ids, snapshots, contexts: contextsOf(ids, batch), error });
  }

  function fire() {
    timer = null;
    if (!pending.size) return;
    const batch = pending;
    pending = new Map();
    let outcome;
    try {
      outcome = validate([...batch.keys()]);
    } catch (error) {
      fail(batch, error);
      return;
    }
    Promise.resolve(outcome).then(
      (result) => deliver(batch, result),
      (error) => fail(batch, error),
    );
  }

  return {
    /** 把这些 id 记入待校验集合，并重新开始计时 */
    request(ids, context) {
      const list = Array.isArray(ids) ? ids : [];
      if (!list.length) return;
      list.forEach((id) => pending.set(id, context));
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(fire, delay);
    },
    /** 清掉计时器与待校验集合（切换成果、画布回到已保存的状态、组件销毁时调用）。
     * 在途的响应不用管：它们回到协调器时，几何指纹对不上，自然判为过期。 */
    cancel() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      pending = new Map();
    },
  };
}

/**
 * 校验失败的原因归类（给界面文案用）。kind 由接口客户端给出：http / network / timeout / business；
 * 响应格式异常是 protocol。拿不到 kind 时按有没有响应体推断。
 * @returns {{ kind: string, reason: string }}
 */
export function describeValidationFailure(error) {
  const status = error?.status ?? error?.response?.status ?? null;
  const hasResponse = Boolean(error?.response) || status !== null;
  let kind = error?.kind;
  if (!['http', 'network', 'timeout', 'business', 'protocol'].includes(kind)) {
    kind = hasResponse ? 'http' : 'network';
  }
  if (kind === 'protocol') return { kind, reason: '服务返回的数据与送审的面对不上' };
  if (kind === 'timeout') return { kind, reason: '请求超时' };
  if (kind === 'network') return { kind, reason: '网络连接失败' };
  if (kind === 'business') return { kind, reason: error?.message || '服务拒绝了这次请求' };
  if (status === 404) return { kind, reason: '校验接口不存在，后端可能还没有更新到配套版本' };
  if (status >= 500) return { kind, reason: '校验服务出错' };
  return { kind, reason: `校验服务返回 HTTP ${status}` };
}

function protocolError(message) {
  return Object.assign(new Error(message), { kind: 'protocol' });
}

/**
 * 面几何的结构检查（只看形状，不做几何运算）：类型是 Polygon / MultiPolygon，坐标嵌套正确，
 * 每个位置恰有两个有限数值，每个环至少四个位置且显式闭合。后端返回的几何一定过得了这一关；
 * 这里防的是被代理改写、截断或格式异常的响应——那种几何交给绘制库会抛错，或者画出一个坏面。
 */
export function isFaceGeometry(geometry) {
  if (!geometry || !FACE_TYPES.has(geometry.type) || !Array.isArray(geometry.coordinates)) return false;
  const isPosition = (position) => Array.isArray(position) && position.length === 2
    && position.every((value) => typeof value === 'number' && Number.isFinite(value));
  const isRing = (ring) => Array.isArray(ring) && ring.length >= 4 && ring.every(isPosition)
    && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1];
  if (geometry.type === 'Polygon') return geometry.coordinates.length > 0 && geometry.coordinates.every(isRing);
  return geometry.coordinates.length > 0
    && geometry.coordinates.every((polygon) => Array.isArray(polygon) && polygon.length > 0 && polygon.every(isRing));
}

/**
 * 校验 geometry/simplify 的响应：items 必须与送审的面一一对应，每一项的形状必须是后端会给的。
 * 对不上就抛 protocol 错误——整次操作按失败处理，画布一个面都不动。
 * @returns {{ outcome: string, geometry?: object, reason?: object }[]}
 */
export function parseSimplifyResponse(payload, expectedCount) {
  const items = payload && payload.items;
  if (!Array.isArray(items) || items.length !== expectedCount) {
    throw protocolError('简化服务返回的结果与送审的面对不上');
  }
  for (const item of items) {
    if (!item || typeof item !== 'object' || !SIMPLIFY_OUTCOMES.has(item.outcome)) {
      throw protocolError('简化服务返回了不认识的结果');
    }
    if (item.outcome === 'changed' && !isFaceGeometry(item.geometry)) {
      throw protocolError('简化服务返回的几何不是有效的面');
    }
  }
  return items;
}

/** 校验 geometry/union 的响应：必须带一个结构正确的面几何。 */
export function parseUnionResponse(payload) {
  if (!payload || !isFaceGeometry(payload.geometry)) throw protocolError('合并服务没有返回有效的面');
  return payload.geometry;
}

/**
 * 简化结果的分类计数（按后端的 outcome 与拒绝原因）。
 */
export function summarizeSimplify(items) {
  const counts = { changed: 0, unchanged: 0, rejectedOutside: 0, rejectedInvalid: 0, rejectedDrift: 0, maxAreaChange: 0 };
  for (const item of items) {
    if (item.outcome === 'changed') {
      counts.changed += 1;
      counts.maxAreaChange = Math.max(counts.maxAreaChange, Math.abs(Number(item.area_change) || 0));
    } else if (item.outcome === 'unchanged') {
      counts.unchanged += 1;
    } else if (item.reason?.code === 'OUTSIDE_ROI') {
      counts.rejectedOutside += 1;
    } else if (item.reason?.code === 'INVALID_GEOMETRY') {
      counts.rejectedInvalid += 1;
    } else {
      counts.rejectedDrift += 1;
    }
  }
  return counts;
}

/**
 * 消除锯齿之后的界面提示。每条为 { type: 'success' | 'warning' | 'info', text }。
 * @param {ReturnType<typeof summarizeSimplify>} counts
 * @param {{ changedMeanwhile?: number, alreadySimplified?: number }} extras
 *   changedMeanwhile：请求期间被修改、本次没有套用的面；alreadySimplified：已是简化结果、没有重复提交的面
 */
export function simplifyMessages(counts, extras = {}) {
  const messages = [];
  const { changedMeanwhile = 0, alreadySimplified = 0 } = extras;
  if (counts.changed) {
    const drift = counts.maxAreaChange >= 0.00005 ? `，面积最大变化 ${(counts.maxAreaChange * 100).toFixed(2)}%` : '';
    messages.push({ type: 'success', text: `已简化 ${counts.changed} 个面的边缘${drift}` });
  }
  const refused = counts.rejectedOutside + counts.rejectedInvalid;
  if (refused) {
    messages.push({ type: 'warning', text: `${refused} 个面简化后会超出矿山边界或几何无效，已跳过，原面保持不变` });
  }
  if (counts.rejectedDrift) {
    messages.push({ type: 'warning', text: `${counts.rejectedDrift} 个面简化后面积或形态变化会超过精度上限，已跳过，原面保持不变` });
  }
  if (counts.unchanged) {
    messages.push({ type: 'info', text: `${counts.unchanged} 个面在精度要求内没有可安全去除的顶点，保持原样` });
  }
  if (changedMeanwhile) {
    messages.push({ type: 'info', text: `${changedMeanwhile} 个面在简化期间被修改，本次没有简化；需要的话请重新选择` });
  }
  if (alreadySimplified) {
    messages.push({ type: 'info', text: `${alreadySimplified} 个面已是消除锯齿后的结果，没有重复简化；改动过之后才能再次消除锯齿` });
  }
  if (!messages.length) messages.push({ type: 'info', text: '选中面的边缘已足够平滑' });
  return messages;
}

/**
 * 「已是消除锯齿的结果」登记簿：draw id → 简化返回的那个几何的指纹。
 * 接口不保证幂等（对同一个面再简化一次，约 5% 的面还会变，累计偏差会叠上去），所以编辑器对
 * 「仍是简化返回的几何、之后没被修改」的面不重复提交。指纹对不上（被拖过顶点、被撤销重做换过）
 * 就不再算数。
 */
export function createSimplifiedRegistry() {
  const entries = new Map();
  return {
    remember(id, geometry) {
      const key = geometryKey(geometry);
      if (key !== null) entries.set(id, key);
    },
    isFreshResult(feature) {
      return Boolean(feature) && entries.has(feature.id) && entries.get(feature.id) === geometryKey(feature.geometry);
    },
    retainOnly(ids) {
      for (const id of entries.keys()) if (!ids.has(id)) entries.delete(id);
    },
    clear() {
      entries.clear();
    },
  };
}

/**
 * 校验协调器：把调度器的结果落到逐面状态上，并决定界面提示什么。
 *
 * @param {{
 *   readGeometryKey: (drawId: any) => (string | null),   // 此刻画布上这个面的几何指纹；取不到返回 null
 *   requestValidation: (ids: any[], action: string) => void,  // 记入调度器
 *   onChange: (summary: { pending: number, invalid: number, unknown: number }) => void,
 *   warn: (text: string) => void,                        // 越界 / 无效的提示
 *   warnMerged: (text: string) => void,                  // 未确认的提示（同样内容的合并成一条）
 * }} deps
 */
export function createValidationCoordinator({
  readGeometryKey, requestValidation = () => {}, onChange = () => {}, warn = () => {}, warnMerged = warn,
} = {}) {
  if (typeof readGeometryKey !== 'function') throw new TypeError('校验协调器需要 readGeometryKey');
  const states = createValidationState();
  const changed = () => onChange(states.summary());

  /** 把一批结论或失败落到状态上。汇总有变化（包括因为面已离开画布而不再跟踪）就通知界面 */
  function settleEach(ids, snapshots, apply) {
    const before = JSON.stringify(states.summary());
    ids.forEach((id, index) => apply(id, snapshots[index], index));
    if (before !== JSON.stringify(states.summary())) changed();
  }

  /** 画完 / 改完一个面：记为校验中并送审 */
  function edited(ids, action) {
    const live = ids.filter((id) => {
      const key = readGeometryKey(id);
      if (key === null) return false;
      states.setPending(id, key);
      return true;
    });
    if (!live.length) return;
    changed();
    requestValidation(live, action);
  }

  return {
    edited,
    /** 只对「此刻采纳的、现在仍不合法的」面提示；过期的结论一个字都不提 */
    verdicts({ ids, items, snapshots, contexts }) {
      const outsideActions = [];
      let invalidCount = 0;
      let applied = 0;
      settleEach(ids, snapshots, (id, key, index) => {
        if (states.apply(id, key, items[index], readGeometryKey(id)) !== APPLIED) return;
        applied += 1;
        const item = items[index];
        if (item.ok) return;
        if (item.code === 'OUTSIDE_ROI') outsideActions.push(contexts[index]);
        else invalidCount += 1;
      });
      if (!applied) return;
      if (outsideActions.length) {
        // 刚画出来的面：拖回界内或删除；改顶点改出去的：调整顶点位置。同一批里两种都有时取后者
        const allCreated = outsideActions.every((action) => action === 'create');
        const hint = allCreated ? '，请拖回界内或删除' : '，请调整顶点位置';
        warn(`${outsideActions.length} 个面超出成果 ROI（矿山边界），保存时将被拒绝${hint}`);
      }
      if (invalidCount) {
        warn(`${invalidCount} 个面几何无效（如自相交），保存时将被拒绝，请调整顶点位置`);
      }
    },
    /** 这一批没能取得结论：仍然在跟踪、且送审之后没被改过的面记为「未确认」 */
    failed({ ids, snapshots, error }) {
      let unknown = 0;
      settleEach(ids, snapshots, (id, key) => {
        if (states.setUnknown(id, key, readGeometryKey(id)) === APPLIED) unknown += 1;
      });
      if (!unknown) return;
      const { reason } = describeValidationFailure(error);
      warnMerged(`${unknown} 个面的合法性未能确认（${reason}）；可以继续编辑，保存时以后端校验为准`);
    },
    /**
     * 画布被程序化替换之后（撤销 / 重做是 draw.set()，不发 draw 事件）：几何变了的面重新送审，
     * 已经不在画布上的面不再跟踪。beforeKeys / afterKeys 是替换前、替换后每个面的几何指纹（Map）。
     * 替换后新出现的 draw id 也算「变了」——绘制库若没有保留 id，所有面都会被重新送审一遍。
     */
    canvasReplaced(beforeKeys, afterKeys = null) {
      const moved = [];
      let forgotten = false;
      for (const [id, before] of beforeKeys) {
        const live = afterKeys ? (afterKeys.has(id) ? afterKeys.get(id) : null) : readGeometryKey(id);
        if (live === null) {
          states.forget(id);
          forgotten = true;
        } else if (live !== before) moved.push(id);
      }
      if (afterKeys) {
        for (const id of afterKeys.keys()) if (!beforeKeys.has(id)) moved.push(id);
      }
      // 撤销「新建面」时这个面直接消失：没有面需要重新送审，但跟踪表变了，界面上的汇总要跟着刷新
      if (moved.length) edited(moved, 'update');
      else if (forgotten) changed();
    },
    /** 面离开了画布（被删除、被合并或简化掉）：不再跟踪。否则用户删掉一个越界的面之后，
     * 工具栏还会一直写着「1 个面未通过校验」 */
    removed(ids) {
      let forgotten = 0;
      ids.forEach((id) => {
        if (states.statusOf(id) !== null) {
          states.forget(id);
          forgotten += 1;
        }
      });
      if (forgotten) changed();
    },
    /**
     * 合并或简化改过画布：新出现的面来自后端已校验的结果，记为合法；其余跟踪状态不变。
     * verifiedNewIds 是这次新增的 draw id。
     */
    backendValidated(verifiedNewIds) {
      verifiedNewIds.forEach((id) => {
        const key = readGeometryKey(id);
        if (key !== null) states.markValid(id, key);
      });
      changed();
    },
    /**
     * 保存成功且保存期间又有新编辑：提交之后没再改过的面，后端已经在保存时认可了它们——
     * 还在「校验中」或「未确认」的，可以据此记为合法。submittedKeys 是提交那一刻每个面的指纹。
     */
    savedUnchanged(submittedKeys) {
      for (const [id, submitted] of submittedKeys) {
        const status = states.statusOf(id);
        if ((status === PENDING || status === UNKNOWN) && readGeometryKey(id) === submitted) {
          states.markValid(id, submitted);
        }
      }
      changed();
    },
    /** 画布回到已保存的状态、或切换了成果：全部不再跟踪 */
    reset() {
      states.clear();
      changed();
    },
    statusOf: (id) => states.statusOf(id),
    summary: () => states.summary(),
  };
}

/**
 * 工具区里那一句校验状态（空文本表示没有任何需要用户知道的状态）。
 * @returns {{ text: string, tone: '' | 'pending' | 'invalid' | 'unknown', hint: string }}
 */
export function describeValidationSummary(summary) {
  const { pending = 0, invalid = 0, unknown = 0 } = summary || {};
  const parts = [];
  const hints = [];
  if (pending) {
    parts.push(`校验中 ${pending} 个面`);
    hints.push('这些面的合法性正在由后端校验');
  }
  if (invalid) {
    parts.push(`${invalid} 个面未通过校验`);
    hints.push('未通过校验的面保存时会被拒绝');
  }
  if (unknown) {
    parts.push(`${unknown} 个面未确认`);
    hints.push('校验服务暂时不可用，这些面的合法性尚未确认；可以继续编辑，保存时以后端校验为准');
  }
  const tone = unknown ? 'unknown' : invalid ? 'invalid' : pending ? 'pending' : '';
  return { text: parts.join('，'), tone, hint: hints.join('；') };
}
