/**
 * 把 geometry/validate 的响应数据对到送审的面上，取出逐个面的结论数组。
 *
 * 响应数据是 { items: [...] }，不是数组本身。2026-11-24 的第一个版本把整个响应对象当成数组往下传，
 * 调度器发现不是数组就静默丢弃——请求照发、后端照答，界面上却一条提示都不出，而当时的测试只数了
 * 「请求发出去了」。所以这个对接单独成函数并有单测：结论数组必须存在、且与送审的数量一一对应，
 * 否则视为没有结论（返回 null），绝不让下标错位的提示弹出来。
 *
 * @param {{ items?: object[] } | null | undefined} payload  接口返回的 data
 * @param {any[]} ids  这一批送审的画布 id
 * @returns {object[] | null}
 */
export function matchVerdicts(payload, ids) {
  const items = payload && Array.isArray(payload.items) ? payload.items : null;
  if (!items || items.length !== ids.length) return null;
  return items;
}

/**
 * 分类校验的「防抖批量校验」调度器（纯逻辑：无 Vue、无 DOM、无网络依赖，便于单测）。
 *
 * 背景（2026-11-24）：面的合法性（是否超出矿山边界、几何是否有效）改由后端几何引擎判定，
 * 与保存共用同一个判定函数；前端不再自带几何算法。draw.create / draw.update 每来一次就
 * 发一个请求，连续操作时会刷出一串；而响应回来时画布可能已经变了——同一个面又被改过、
 * 切换了成果、组件已销毁——旧结论不能再弹提示。这里只负责三件事：
 *   1. 收集：delayMs 内的多次 request 合并成一批（每次 request 重新计时）；
 *   2. 排序：每批一个自增序号，响应回来时若已有更新的一批发出、或已 cancel，就丢弃；
 *   3. 容错：validate 抛错或 reject 一律吞掉。编辑期校验只是提示，保存时后端仍会校验。
 * 几何怎么取、请求怎么发、结论怎么提示都由调用方注入（validate / onVerdicts）。
 *
 * @param {{
 *   delayMs?: number,
 *   validate: (ids: any[], seq: number) => any,
 *   onVerdicts: (verdicts: { ids: any[], items: any[], contexts: any[] }) => void,
 * }} options
 *   validate：取几何并发请求。返回（或 resolve 为）{ ids, items }，两者按下标对应；返回空
 *     （null / undefined）表示这次没有结论——请求失败、要素已不存在、会话已换——不提示。
 *   onVerdicts：只收到「仍是最新一批」且有结论的结果。contexts[i] 是 ids[i] 最近一次
 *     request 时附带的 context（没有则为 undefined）。
 * @returns {{ request: (ids: any[], context?: any) => void, cancel: () => void }}
 */
export function createValidationScheduler({ delayMs = 200, validate, onVerdicts } = {}) {
  if (typeof validate !== 'function' || typeof onVerdicts !== 'function') {
    throw new TypeError('createValidationScheduler 需要 validate 与 onVerdicts 两个函数');
  }
  const delay = Number.isFinite(delayMs) && delayMs >= 0 ? delayMs : 200;

  let timer = null;
  // 已发出的最新一批的序号。cancel() 也递增它，在途响应随之作废
  let latestSeq = 0;
  // 待校验的 id → 最近一次 request 附带的 context（同一个 id 多次 request 以最后一次为准）
  let pending = new Map();

  function deliver(batch, seq, result) {
    // 已有更新的一批发出（或已 cancel）：这份结论对应的是旧画布，丢弃
    if (seq !== latestSeq) return;
    if (!result || !Array.isArray(result.ids) || !Array.isArray(result.items)) return;
    onVerdicts({
      ids: result.ids,
      items: result.items,
      contexts: result.ids.map((id) => batch.get(id)),
    });
  }

  function fire() {
    timer = null;
    if (!pending.size) return;
    const batch = pending;
    pending = new Map();
    const seq = (latestSeq += 1);
    let outcome;
    try {
      outcome = validate([...batch.keys()], seq);
    } catch (_) {
      return;
    }
    Promise.resolve(outcome).then(
      (result) => deliver(batch, seq, result),
      () => {},
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
    /** 清掉计时器与待校验集合，并让在途响应失效（切换成果、组件销毁时调用） */
    cancel() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      pending = new Map();
      latestSeq += 1;
    },
  };
}
