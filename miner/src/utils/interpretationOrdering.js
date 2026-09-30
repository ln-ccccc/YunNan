/**
 * 解译历史展示排序（年份降序 → 矿山 FID 升序 → 产物名 zh 序）。
 * 原在 BFF interpretation.js 塑形层，M3 收口移到前端消费方。
 */
export function sortInterpretationRecords(records) {
  return [...(records || [])].sort((a, b) => {
    const ya = Number(a?.data?.year) || 0;
    const yb = Number(b?.data?.year) || 0;
    if (yb !== ya) return yb - ya;
    const fa = Number(a?.data?.fid) || 0;
    const fb = Number(b?.data?.fid) || 0;
    if (fb !== fa) return fa - fb;
    return String(a?.data?.file || '').localeCompare(String(b?.data?.file || ''), 'zh-CN');
  });
}
