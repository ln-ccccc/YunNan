// 前端错误留痕（2026-11-05 生产化检查）：与 GeoView 侧 utils/errorSink.js 同款，
// key 按应用区分；排障时 JSON.parse(localStorage.getItem('yunnan_miner_errors'))
const STORAGE_KEY = 'yunnan_miner_errors'
const MAX_ENTRIES = 50

export function recordFrontendError(kind, detail) {
  // localStorage 在隐私模式/配额满时可能抛错——留痕本身不能变成新的错误源
  try {
    const list = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    list.push({
      t: new Date().toISOString(),
      kind,
      detail: String(detail || '').slice(0, 500),
      href: location.pathname,
    })
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(-MAX_ENTRIES)))
  } catch (_storageUnavailable) {
    /* 静默：console 仍会记录 */
  }
  console.error(`[frontend-error] ${kind}:`, detail)
}

export function installGlobalErrorHandlers(app) {
  if (app?.config) {
    app.config.errorHandler = (err, _instance, info) => {
      recordFrontendError('vue', `${info}: ${err?.stack || err}`)
    }
  }
  window.addEventListener('error', (event) => {
    recordFrontendError('window', event.message || 'unknown error')
  })
  window.addEventListener('unhandledrejection', (event) => {
    recordFrontendError('promise', event.reason?.stack || event.reason || 'unknown rejection')
  })
}
