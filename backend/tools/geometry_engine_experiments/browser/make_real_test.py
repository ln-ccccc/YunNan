# -*- coding: utf-8 -*-
"""把真实数据浏览器测试 v2 复制成「指向隔离沙盒栈」的版本：
  ZC_API       后端地址（隔离后端 http://localhost:5018）
  ZC_GEOVIEW   GeoView 预览地址；设置后，Miner 打开生产 GeoView（localhost:3000）的请求在浏览器内改写到它
  ZC_SHOTS     截图输出根目录
并把「消除锯齿」一步里对已删除的前端几何模块的依赖，换成直接询问后端简化接口
（界面点击走的也是这个接口）；界面结果仍用真实点击与保存来验证。用法: python make_real_test.py"""
from pathlib import Path

src = Path(__file__).with_name("vector-edit-real.cjs").read_text(encoding="utf-8")
pairs = [
    ("const API = 'http://localhost:5008';", "const API = process.env.ZC_API || 'http://localhost:5008';"),
    ("const OUT = `D:/YunNan-Handoff/zcode-20261123/shots/${process.env.ZC_OUT || 'vector-edit'}`;",
     "const OUT = `${process.env.ZC_SHOTS || 'D:/YunNan-Handoff/zcode-20261123/shots'}/${process.env.ZC_OUT || 'vector-edit'}`;"),
    ("  const row = rows.first();\n  const [page] = await Promise.all([context.waitForEvent('page'), row.locator('.edit-btn', { hasText: '编辑矢量' }).click()]);",
     "  const row = rows.first();\n"
     "  if (process.env.ZC_GEOVIEW) {\n"
     "    // 隔离沙盒：生产 Miner 配置的 GeoView 地址（localhost:3000）在浏览器内 302 到隔离预览，生产前端不收到任何请求\n"
     "    await context.route('http://localhost:3000/**', (route) => route.fulfill({\n"
     "      status: 302, headers: { location: route.request().url().replace('http://localhost:3000', process.env.ZC_GEOVIEW) }, body: '',\n"
     "    }));\n"
     "  }\n"
     "  const [page] = await Promise.all([context.waitForEvent('page'), row.locator('.edit-btn', { hasText: '编辑矢量' }).click()]);"),
]
for old, new in pairs:
    assert src.count(old) == 1, old[:60]
    src = src.replace(old, new)

# 编辑完成后的即时校验（2026-11-24：由后端几何引擎判定）：界内的面不应有合法性提示，越界的面在保存前就应提示
extra = [
    ("  await shot(page, 'v02-drawn-reclassified-unsaved');\n",
     "  await page.waitForTimeout(700);\n"
     "  const insideToasts = await toasts(page);\n"
     "  check(!insideToasts.some((text) => text.includes('超出成果 ROI') || text.includes('几何无效')), `界内的面不应出现合法性提示: ${JSON.stringify(insideToasts)}`);\n"
     "  await shot(page, 'v02-drawn-reclassified-unsaved');\n"),
    ("  await shot(page, 'v10-outside-roi-unsaved');\n",
     "  const outsideDrawnAt = Date.now();\n"
     "  let outsideToastMs = null; const outsideTimeline = [];\n"
     "  for (let tick = 0; tick < 30 && outsideToastMs === null; tick += 1) {\n"
     "    await page.waitForTimeout(100);\n"
     "    const current = await toasts(page);\n"
     "    if (current.length) outsideTimeline.push(`+${Date.now() - outsideDrawnAt}ms ${JSON.stringify(current).slice(0, 160)}`);\n"
     "    if (current.some((text) => text.includes('超出成果 ROI') && text.includes('保存时将被拒绝'))) outsideToastMs = Date.now() - outsideDrawnAt;\n"
     "  }\n"
     "  console.log('OUTSIDE-TOAST', JSON.stringify({ outsideToastMs, outsideTimeline }));\n"
     "  check(outsideToastMs !== null, `画面后、保存前就应提示越界: ${JSON.stringify(outsideTimeline)}`);\n"
     "  await shot(page, 'v10-outside-roi-unsaved');\n"),
]
for old, new in extra:
    assert src.count(old) == 1, old[:60]
    src = src.replace(old, new)

# 记录编辑器发出的每个几何接口请求与响应（调试与取证用）
watch_anchor = "  watch(page, 'editor');\n"
assert src.count(watch_anchor) == 1
src = src.replace(watch_anchor, watch_anchor +
    "  page.on('response', async (res) => {\n"
    "    if (!res.url().includes('/geometry/')) return;\n"
    "    let body = ''; try { body = (await res.text()).slice(0, 260); } catch (_) { body = '<no body>'; }\n"
    "    console.log('GEOMETRY-API', Date.now(), res.request().method(), new URL(res.url()).pathname.replace(/.*classification-results\\//, ''), res.status(), body);\n"
    "  });\n")

start_anchor = "  // 能不能简化取决于数据：用编辑器自己的简化函数"
end_anchor = "  if (reducibleOriginals.length) {\n    const wanted = reducibleOriginals"
assert src.count(start_anchor) == 1 and src.count(end_anchor) == 1
start = src.index(start_anchor)
end = src.index(end_anchor)
new_block = (
    "  // 能不能简化由后端几何引擎决定（2026-11-24 起）：界面点「消除锯齿」调的就是这个接口。\n"
    "  // 这里先直接问接口拿到每个面的结论，再用真实点击验证界面结果与之一致、并且能保存。\n"
    "  api = await apiState(context);\n"
    "  const simplifyProbe = await context.request.post(`${API}/api/projects/${PROJECT}/classification-results/${RESULT}/geometry/simplify`, {\n"
    "    data: { geometries: api.features.map((feature) => ({ type: feature.type, coordinates: JSON.parse(feature.shape) })) },\n"
    "  });\n"
    "  check(simplifyProbe.status() === 200, `简化接口应可用: HTTP ${simplifyProbe.status()} ${(await simplifyProbe.text()).slice(0, 200)}`);\n"
    "  const simplifyItems = ((await simplifyProbe.json()).data || {}).items || [];\n"
    "  check(simplifyItems.length === api.features.length, '简化接口应逐个面给出结论');\n"
    "  check(simplifyItems.every((item, index) => item.outcome !== 'changed'\n"
    "    || enginePositions(item.geometry) < enginePositions({ type: api.features[index].type, coordinates: JSON.parse(api.features[index].shape) })),\n"
    "  '简化接口返回 changed 的几何，坐标数必须比原来少');\n"
    "  console.log(`  简化接口（真实成果 ${api.features.length} 个面）：changed ${simplifyItems.filter((item) => item.outcome === 'changed').length}、`\n"
    "    + `unchanged ${simplifyItems.filter((item) => item.outcome === 'unchanged').length}、rejected ${simplifyItems.filter((item) => item.outcome === 'rejected').length}`);\n"
    "  const reducibleOriginals = api.features.filter((feature, index) => simplifyItems[index] && simplifyItems[index].outcome === 'changed');\n"
)
src = src[:start] + new_block + src[end:]
helper_anchor = "  // ---------- 消除锯齿 ----------\n"
assert src.count(helper_anchor) == 1
src = src.replace(helper_anchor, helper_anchor + "  const enginePositions = (geometry) => (geometry.type === 'Polygon' ? geometry.coordinates : geometry.coordinates.flat()).reduce((sum, ring) => sum + ring.length, 0);\n")
target = Path(__file__).with_name("vector-edit-real.sandbox.cjs")
target.write_text(src, encoding="utf-8")
print("written", target)
