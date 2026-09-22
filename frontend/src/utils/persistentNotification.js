import { ElNotification } from 'element-plus';

// 常驻通知（duration:0）的可关闭包装。
// element-plus@2.1.10 函数式 API 返回的 close() 写 vm.component.proxy.visible，
// 在当前 Vue 版本组合下实测不生效（2026-09-22 GUI 实测：上传/推理两个常驻通知
// 调 close() 后滞留屏幕）。这里包装：记录本次插入的 DOM 节点，close 时依次
// 句柄.close → 组件 exposed.close（走 Element 自身的过渡与销毁记账）→ 直接摘除 DOM 兜底。
export function persistentNotification(options) {
  const known = new Set(document.querySelectorAll('.el-notification'));
  const handle = ElNotification(options);
  let el = null;
  for (const node of document.querySelectorAll('.el-notification')) {
    if (!known.has(node)) el = node;
  }
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    try { handle.close(); } catch (_) { /* 兜底路径继续 */ }
    const vm = el?.__vueParentComponent;
    if (vm && vm.exposed && typeof vm.exposed.close === 'function') {
      try { vm.exposed.close(); return; } catch (_) { /* 继续兜底 */ }
    }
    if (el && el.isConnected) {
      el.parentNode?.removeChild(el);
    }
  };
  return { close };
}
