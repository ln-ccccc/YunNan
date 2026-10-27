// 推理失败文案本地化：门槛拒绝（400 details.failed_checks 语义码）→ 中文缺项。
// AGENTS §5：语义码由前端映射中文，不透出英文蛇形码；纯函数独立成模块便于测试。
// 门槛失败检查码 → 中文（后端 400 details.failed_checks 携带语义码，
// AGENTS §5：语义码由前端映射中文，不透出英文蛇形码）
const READINESS_CHECK_TEXT = {
  PROJECT_PROFILE: '项目档案未填完整（监测期起止年份）',
  MINE_BOUNDARY: '尚未导入矿山边界',
  ACTIVE_BASEMAP: '底图未激活',
  INFERENCE_INPUT: '缺少可用的推理影像',
};
const READINESS_REASON_TEXT = {
  ACTIVE_BASEMAP_TILES_MISSING: '激活底图的瓦片文件缺失，请重新切片或激活其他底图',
};

export function formatInferenceFailure(error) {
  const base = error?.response?.data?.msg || error?.response?.data?.error || error?.message || '推理任务执行失败';
  const details = error?.response?.data?.data?.details;
  const failedChecks = Array.isArray(details?.failed_checks) ? details.failed_checks : [];
  if (!failedChecks.length || !String(base).includes('不满足')) return base;
  const reasons = failedChecks.map((item) => {
    const reasonText = item?.reason_code ? READINESS_REASON_TEXT[item.reason_code] : '';
    const checkText = READINESS_CHECK_TEXT[item?.code] || item?.code || '未知条件未满足';
    return reasonText ? `${checkText}（${reasonText}）` : checkText;
  });
  return `${base}：${reasons.join('；')}。请先完成上述准备后再启动推理。`;
}

// M5.3：项目推理编排（发起/轮询/取消/就绪影像加载）从 useMineData 抽出，
// 地图工作台与智能解译页共用同一实现，避免双份漂移。
// projectId 支持数值、ref 或 getter，均由调用方保证项目上下文有效。
