import { Loading } from "element-plus/es/components/loading/src/service";
import { ElMessage } from "element-plus";

// loading框设置局部刷新，且所有请求完成后关闭loading框
let loading

// 逃生口（2026-11-05 迁移审查 P1-4，江西 2.2 同款）：后端 worker 被长请求占满时，
// 非 silent 请求可能长时间 pending——无超时 + lock 全屏遮罩 = 整站不可操作。
// 15s 仍未结束自动解除遮罩并提示；请求本身不中止，返回时按计数对称收口。
const LOADING_ESCAPE_MS = 15000
let escapeTimer = null

let needLoadingRequestCount = 0 // 声明一个对象用于存储请求个数
function startLoading (targetdq,text) {
  loading = Loading({
    lock: true,
    text: `努力${text}...`,
    background: 'rgba(255, 255, 255, 0.9)',
    target: document.querySelector(targetdq) // 设置加载动画区域
  })
  armEscapeHatch()
}
function endLoading () {
    disarmEscapeHatch()
    // 判空（2026-11-05）：遮罩可能已被逃生口先行解除，重复 close 会抛错
    if (loading) {
        loading.close()
        loading = null
    }
}
function armEscapeHatch () {
  disarmEscapeHatch()
  escapeTimer = setTimeout(() => {
    escapeTimer = null
    if (needLoadingRequestCount > 0) {
      if (loading) {
        loading.close()
        loading = null
      }
      ElMessage.warning('请求仍在处理中，已先行解除界面锁定，您可以继续操作其他功能')
    }
  }, LOADING_ESCAPE_MS)
}
function disarmEscapeHatch () {
  if (escapeTimer !== null) {
    clearTimeout(escapeTimer)
    escapeTimer = null
  }
}
export function showFullScreenLoading (targetdq,text = '加载中') {
  if (needLoadingRequestCount === 0) {
    startLoading(targetdq,text)
  }
  needLoadingRequestCount++
}
export function hideFullScreenLoading () {
  if (needLoadingRequestCount <= 0) return
  needLoadingRequestCount--
  if (needLoadingRequestCount === 0) {
    endLoading()
  }
}

export default {
  showFullScreenLoading,
  hideFullScreenLoading,
}
