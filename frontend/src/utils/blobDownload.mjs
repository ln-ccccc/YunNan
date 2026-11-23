// 把 blob 存成本地文件的统一入口：服务端导出、草稿 GeoJSON、结果图片下载都走这里。
//
// object URL 必须延迟释放。anchor.click() 只是发起下载，同步 revoke 在部分浏览器会截断
// 尚未开始的 blob 下载。Miner 的原始影像下载 2026-09-22 已因同一问题改成延迟回收，但
// 两个应用里其余入口当时都漏了；2026-11-24 回收审查统一收敛为"规则只在这一处 +
// test/blobDownload.test.mjs 扫描守护"。定时器是一次性的，到点只做资源释放，不触碰组件状态。
export const OBJECT_URL_RELEASE_DELAY_MS = 60000;

export function saveBlobAsFile(blob, filename) {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), OBJECT_URL_RELEASE_DELAY_MS);
}
