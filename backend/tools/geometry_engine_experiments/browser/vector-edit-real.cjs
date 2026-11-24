// 矢量编辑真实可用性测试 v2（真实浏览器 + 真实后端 + 真实保存）。
// 对象：沙盒项目的成果。编辑动作全部走真实鼠标/键盘/按钮，从 Miner 的"编辑矢量"入口进入 GeoView。
//
// 与 v1 的区别：不再依赖数据状态。
// - 点击位置不靠试错：页面内按产品修正后的命中规则（utils/classificationEditor.mjs queryDrawHits——
//   点择近在前；面：恰好一个面把点击点包含在内就选它，否则取最小的）预测"点下去会选中谁"，
//   取预测结果是目标图斑的位置再点。
// - 改类 / 删顶点 / 合并 / 删除 / 并发的目标都在运行时挑选，不写死地类与图斑。
//
// 地图句柄只用于把"哪个图斑、第几个顶点"换算成屏幕坐标和读状态，不代替用户执行任何编辑或保存。
// 用法: [ZC_PROJECT=52 ZC_FID=10353 ZC_OUT=realdata-3055] NODE_PATH=D:/项目/YunNan-acceptance/pw/node_modules node vector-edit-real.cjs <resultId>
//       ZC_PROJECT / ZC_FID  成果所在的项目与矿山（默认 51 / 990001）
//       ZC_OUT               截图与结果输出目录名（默认 vector-edit）
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const EXE = 'C:/Users/Administrator/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe';
const MINER = 'http://localhost:4000';
const API = 'http://localhost:5008';
const PROJECT = Number(process.env.ZC_PROJECT || 51);
const RESULT = Number(process.argv[2]);
const FID = Number(process.env.ZC_FID || 990001);
const OUT = `D:/YunNan-Handoff/zcode-20261123/shots/${process.env.ZC_OUT || 'vector-edit'}`;
const CLASS_NAMES = { 0: '草地', 1: '林地', 2: '建筑', 3: '道路', 4: '裸地', 5: '水体' };

if (!Number.isInteger(RESULT)) {
  console.error('用法: node vector-edit-real.cjs <resultId>');
  process.exit(2);
}

function readEnv() {
  const env = {};
  for (const line of fs.readFileSync('D:/项目/YunNan/.env', 'utf8').split(/\r?\n/)) {
    const match = /^(ADMIN_USERNAME|ADMIN_PASSWORD)=(.*)$/.exec(line);
    if (match) env[match[1]] = match[2].replace(/^["']|["']$/g, '');
  }
  return env;
}

const steps = [];
const problems = [];
let browserRef = null;
let editorRef = null;

function record(name, ok, detail) {
  steps.push({ name, ok, detail });
  console.log(`${ok ? '✓' : '✗'} ${name}${detail ? `  ${JSON.stringify(detail).slice(0, 360)}` : ''}`);
}
function check(condition, message) {
  if (!condition) throw new Error(message);
}

// ---------- 页面内助手：找到编辑器实例，只做几何换算、命中预测与状态读取 ----------
async function installHelpers(page) {
  const installed = await page.evaluate(() => {
    const seen = new Set();
    let found = null;
    const visit = (vnode) => {
      if (!vnode || found || typeof vnode !== 'object' || seen.has(vnode)) return;
      seen.add(vnode);
      if (vnode.component) {
        const state = vnode.component.proxy || {};
        if (state.map && typeof state.map.project === 'function' && state.draw && typeof state.draw.getAll === 'function') { found = state; return; }
        visit(vnode.component.subTree);
        if (found) return;
      }
      if (Array.isArray(vnode.children)) vnode.children.forEach(visit);
      if (vnode.suspense) visit(vnode.suspense.activeBranch);
    };
    visit(document.querySelector('#app')._vnode);
    if (!found) return false;
    const map = found.map;
    const draw = found.draw;
    const countVertices = (geometry) => (geometry.type === 'Polygon' ? geometry.coordinates : geometry.coordinates.flatMap((polygon) => polygon)).reduce((sum, ring) => sum + ring.length, 0);
    const outerRing = (geometry) => (geometry.type === 'Polygon' ? geometry.coordinates[0] : geometry.coordinates[0][0]);
    const canvasBox = () => {
      const rect = document.querySelector('.classification-map canvas.maplibregl-canvas').getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    };
    // 画布可能比浏览器窗口高（页面可滚动）：候选点必须落在"画布 ∩ 窗口"的可见部分里
    const visibleBox = () => {
      const rect = canvasBox();
      const left = Math.max(rect.x, 0); const top = Math.max(rect.y, 0);
      const right = Math.min(rect.x + rect.width, window.innerWidth); const bottom = Math.min(rect.y + rect.height, window.innerHeight);
      return { left, top, right, bottom, width: right - left, height: bottom - top };
    };
    const bboxOf = (feature) => {
      const rings = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates[0]] : feature.geometry.coordinates.map((polygon) => polygon[0]);
      let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
      for (const ring of rings) for (const point of ring) {
        if (point[0] < minX) minX = point[0]; if (point[0] > maxX) maxX = point[0];
        if (point[1] < minY) minY = point[1]; if (point[1] > maxY) maxY = point[1];
      }
      const topLeft = map.project([minX, maxY]);
      const bottomRight = map.project([maxX, minY]);
      return { x: topLeft.x, y: topLeft.y, width: bottomRight.x - topLeft.x, height: bottomRight.y - topLeft.y };
    };
    // 渲染结果里 draw 给用户属性加 user_ 前缀，并混入 id/meta/mode 等内部字段；字符串 ID 不会出现在渲染结果的 id 上
    const renderedFeatureId = (rendered) => {
      const props = (rendered && rendered.properties) || {};
      return props.user_feature_id !== undefined ? props.user_feature_id : props.feature_id;
    };
    const drawLayers = () => map.getStyle().layers.filter((layer) => layer.id.startsWith('gl-draw-')).map((layer) => layer.id);
    // "点击点在哪个面里"只问填充图层：描边图层的命中带容差，挨着点的边也会被算进来
    const fillLayers = () => map.getStyle().layers.filter((layer) => layer.id.startsWith('gl-draw-') && layer.type === 'fill').map((layer) => layer.id);
    const areaPx = (geometry) => {
      const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
      let total = 0;
      for (const rings of polygons) rings.forEach((ring, ringIndex) => {
        let doubled = 0;
        for (let index = 0; index + 1 < ring.length; index += 1) {
          const from = map.project(ring[index]); const to = map.project(ring[index + 1]);
          doubled += from.x * to.y - to.x * from.y;
        }
        total += ringIndex === 0 ? Math.abs(doubled / 2) : -Math.abs(doubled / 2);
      });
      return total;
    };
    // 预测"在画布坐标 (px, py) 点下去会命中谁"——复刻产品修正后的规则：
    // 同一个 ±2px 缓冲盒、同一组绘图图层；点（顶点/中点）优先；面按面积升序，
    // 恰好一个面把点击点本身包含在内就选它，否则取最小的
    const predictAt = (px, py) => {
      const layers = drawLayers();
      const hit = map.queryRenderedFeatures([[px - 2, py - 2], [px + 2, py + 2]], { layers })
        .filter((feature) => ['feature', 'midpoint', 'vertex'].includes(feature.properties.meta));
      const seen = new Set();
      const points = [];
      const polygons = [];
      for (const feature of hit) {
        const key = feature.properties.meta === 'feature' ? `f-${feature.properties.id}` : 'point';
        if (seen.has(key)) continue;
        seen.add(key);
        if (feature.properties.meta === 'feature' && ['Polygon', 'MultiPolygon'].includes(feature.geometry.type)) polygons.push(feature);
        else if (feature.properties.meta !== 'feature') points.push(feature);
      }
      let target = null;
      if (points.length) {
        target = points[0];
      } else if (polygons.length > 1) {
        const insideIds = new Set(map.queryRenderedFeatures([px, py], { layers: fillLayers() }).filter((feature) => feature.properties.meta === 'feature').map((feature) => feature.properties.id));
        const inside = polygons.filter((feature) => insideIds.has(feature.properties.id));
        const pool = inside.length === 1 ? inside : polygons.slice().sort((a, b) => areaPx(a.geometry) - areaPx(b.geometry));
        target = pool[0];
      } else if (polygons.length) {
        target = polygons[0];
      }
      return target ? { fid: String(renderedFeatureId(target)), meta: target.properties.meta } : null;
    };
    const previousInitial = window.__zc && window.__zc.initial;
    window.__zc = {
      initial: previousInitial || { center: map.getCenter(), zoom: map.getZoom() },
      count: () => draw.getAll().features.length,
      mode: () => draw.getMode(),
      zoom: () => map.getZoom(),
      selectedIds: () => draw.getSelectedIds(),
      selectedVertices: () => (draw.getSelectedPoints ? draw.getSelectedPoints().features.length : 0),
      features: () => draw.getAll().features.map((feature) => ({
        id: feature.id, fid: feature.properties.feature_id, cls: feature.properties.class_code,
        type: feature.geometry.type, vertices: countVertices(feature.geometry), box: bboxOf(feature),
      })),
      geometryOf: (id) => { const feature = draw.get(id); return feature ? { type: feature.geometry.type, coordinates: feature.geometry.coordinates } : null; },
      feature: (id) => {
        const feature = draw.get(id);
        return feature ? { id: feature.id, fid: feature.properties.feature_id, cls: feature.properties.class_code, type: feature.geometry.type, vertices: countVertices(feature.geometry) } : null;
      },
      // 草稿 = 没有地类的那个要素；GeoJSON 外环含"跟随鼠标的点"与闭合点，已放置顶点数 = 外环坐标数 − 2
      sketchPlaced: () => {
        const draft = draw.getAll().features.find((feature) => feature.properties.class_code === undefined);
        return draft ? draft.geometry.coordinates[0].length - 2 : -1;
      },
      boxCenter: (id) => { const box = bboxOf(draw.get(id)); const canvas = canvasBox(); return { x: canvas.x + box.x + box.width / 2, y: canvas.y + box.y + box.height / 2 }; },
      // 该图斑上一处"点下去必然选中它"的屏幕位置：在它的外接框里由中心向外找，先粗网格再细网格
      clickPointFor: (id) => {
        const feature = draw.get(id);
        if (!feature) return null;
        const wanted = String(feature.properties.feature_id);
        const box = bboxOf(feature);
        const canvas = canvasBox();
        const visible = visibleBox();
        const onCanvas = (x, y) => {
          if (x < visible.left + 40 || y < visible.top + 40 || x > visible.right - 40 || y > visible.bottom - 40) return false;
          const element = document.elementFromPoint(x, y);
          return Boolean(element && element.classList.contains('maplibregl-canvas'));
        };
        const cx = box.x + box.width / 2; const cy = box.y + box.height / 2;
        for (const step of [8, 3]) {
          const candidates = [];
          for (let px = box.x + step / 2; px <= box.x + box.width; px += step) for (let py = box.y + step / 2; py <= box.y + box.height; py += step) candidates.push([px, py]);
          candidates.sort((p, q) => Math.hypot(p[0] - cx, p[1] - cy) - Math.hypot(q[0] - cx, q[1] - cy));
          for (const [px, py] of candidates) {
            const x = canvas.x + px; const y = canvas.y + py;
            if (!onCanvas(x, y)) continue;
            const prediction = predictAt(px, py);
            if (!prediction || prediction.meta !== 'feature' || prediction.fid !== wanted) continue;
            // 稳健性：四周各偏 1 像素仍命中同一个图斑才算数。鼠标坐标会被取整，离两个图斑的
            // 公共边界不到 1 像素的点可能落到另一侧（真实数据上抓到）
            const steady = [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dy]) => {
              const neighbour = predictAt(px + dx, py + dy);
              return neighbour && neighbour.meta === 'feature' && neighbour.fid === wanted;
            });
            if (steady) return { x, y };
          }
        }
        return null;
      },
      vertexPoint: (id, index) => {
        const ring = outerRing(draw.get(id).geometry);
        const projected = map.project(ring[index % (ring.length - 1)]);
        const canvas = canvasBox();
        return { x: canvas.x + projected.x, y: canvas.y + projected.y };
      },
      // 屏幕坐标处的命中预测（诊断用）
      predictAtScreen: (x, y) => { const canvas = canvasBox(); return predictAt(x - canvas.x, y - canvas.y); },
      // 诊断：挂住地图的 queryRenderedFeatures，记录点击过程中库里（以及项目的命中修正）查了什么、查到了谁
      hookQueries: () => {
        window.__zcQ = [];
        const original = map.queryRenderedFeatures.bind(map);
        map.queryRenderedFeatures = (geometry, options) => {
          const result = original(geometry, options);
          window.__zcQ.push({ geometry: JSON.stringify(geometry).slice(0, 70), layers: options && options.layers ? options.layers.length : null, hits: result.map((feature) => `${feature.layer.id.replace('gl-draw-', '')}:${String(renderedFeatureId(feature)).slice(-9)}:${feature.properties.meta}`) });
          return result;
        };
      },
      unhookQueries: () => { delete map.queryRenderedFeatures; return window.__zcQ.splice(0); },
      // 那一点上两次查询的原始结果：±2px 缓冲盒、以及不带缓冲的点击点本身
      rawHitAt: (x, y) => {
        const canvas = canvasBox();
        const px = x - canvas.x; const py = y - canvas.y;
        const layers = drawLayers();
        const describe = (feature) => ({ layer: feature.layer.id, id: String(renderedFeatureId(feature)), meta: feature.properties.meta, type: feature.geometry.type, areaPx: Math.round(areaPx(feature.geometry)) });
        return {
          box: map.queryRenderedFeatures([[px - 2, py - 2], [px + 2, py + 2]], { layers }).map(describe),
          point: map.queryRenderedFeatures([px, py], { layers }).map(describe),
        };
      },
      // 第 index 个顶点与前后两个顶点在屏幕上的较小间距（像素），以及它指向下一个顶点那条边的长度
      vertexSpacing: (id, index) => {
        const ring = outerRing(draw.get(id).geometry);
        const count = ring.length - 1;
        const canvas = canvasBox();
        const at = (i) => { const point = map.project(ring[((i % count) + count) % count]); return { x: canvas.x + point.x, y: canvas.y + point.y }; };
        const here = at(index);
        const previous = Math.hypot(at(index - 1).x - here.x, at(index - 1).y - here.y);
        const next = at(index + 1);
        return { min: Math.min(previous, Math.hypot(next.x - here.x, next.y - here.y)), edge: { from: here, to: next, length: Math.hypot(next.x - here.x, next.y - here.y) }, vertexCount: count };
      },
      roiRings: (roi) => (roi.type === 'MultiPolygon' ? roi.coordinates.map((polygon) => polygon[0]) : [roi.coordinates[0]]),
      inRoi: (roi, lngLat) => window.__zc.roiRings(roi).some((ring) => {
        let inside = false;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
          const [xi, yi] = ring[i]; const [xj, yj] = ring[j];
          if ((yi > lngLat.lat) !== (yj > lngLat.lat) && lngLat.lng < ((xj - xi) * (lngLat.lat - yi)) / (yj - yi) + xi) inside = !inside;
        }
        return inside;
      }),
      // ROI（矿山边界）里能放下一个"占可见区域 w×h 比例"的矩形的位置：返回左上角与宽高的屏幕坐标，
      // 取离 ROI 质心最近的；四角与四边中点都要在界内。找不到返回 null
      roiAnchor: (roi, w, h) => {
        const canvas = canvasBox();
        const lngLatOf = (fx, fy) => { const point = window.__zc.pointAt(fx, fy); return map.unproject([point.x - canvas.x, point.y - canvas.y]); };
        const visible = visibleBox();
        const width = (visible.width - 80) * w; const height = (visible.height - 80) * h;
        const centre = window.__zc.roiCentreScreen(roi);
        let best = null;
        for (let fx = 0.03; fx + w <= 0.97; fx += 0.03) {
          for (let fy = 0.03; fy + h <= 0.97; fy += 0.03) {
            const probe = [[fx, fy], [fx + w, fy], [fx + w, fy + h], [fx, fy + h], [fx + w / 2, fy], [fx + w, fy + h / 2], [fx + w / 2, fy + h], [fx, fy + h / 2]];
            if (!probe.every(([x, y]) => window.__zc.inRoi(roi, lngLatOf(x, y)))) continue;
            const anchor = window.__zc.pointAt(fx, fy);
            const dx = anchor.x + width / 2 - centre.x; const dy = anchor.y + height / 2 - centre.y;
            if (!best || dx * dx + dy * dy < best.distance) best = { x: anchor.x, y: anchor.y, width, height, distance: dx * dx + dy * dy };
          }
        }
        return best;
      },
      // ROI 质心的屏幕坐标（取第一个外环的顶点均值）
      roiCentreScreen: (roi) => {
        const ring = window.__zc.roiRings(roi)[0];
        let lng = 0; let lat = 0;
        for (const point of ring) { lng += point[0]; lat += point[1]; }
        const projected = map.project([lng / ring.length, lat / ring.length]);
        const canvas = canvasBox();
        return { x: canvas.x + projected.x, y: canvas.y + projected.y };
      },
      // 当前视野下每像素对应的地面米数
      metresPerPixel: () => (156543.03392 * Math.cos((map.getCenter().lat * Math.PI) / 180)) / Math.pow(2, map.getZoom()),
      // 按比例取点：以"画布 ∩ 窗口"的可见部分为基准（四周留 40px），不落在窗口外
      pointAt: (fx, fy) => { const visible = visibleBox(); return { x: visible.left + 40 + (visible.width - 80) * fx, y: visible.top + 40 + (visible.height - 80) * fy }; },
      lngLatAt: (fx, fy) => { const point = window.__zc.pointAt(fx, fy); const canvas = canvasBox(); const lngLat = map.unproject([point.x - canvas.x, point.y - canvas.y]); return [lngLat.lng, lngLat.lat]; },
      onCanvas: (point, margin) => { const visible = visibleBox(); return point.x > visible.left + margin && point.y > visible.top + margin && point.x < visible.right - margin && point.y < visible.bottom - margin; },
      visible: () => visibleBox(),
      restoreView: () => { map.jumpTo({ center: window.__zc.initial.center, zoom: window.__zc.initial.zoom }); },
    };
    return true;
  });
  check(installed, '没找到编辑器的地图实例');
}

async function waitEditorReady(page) {
  await page.locator('.classification-map canvas.maplibregl-canvas').waitFor({ timeout: 60000 });
  await page.waitForFunction(() => {
    const state = document.querySelector('.toolbar-state');
    return state && !/加载/.test(state.textContent) && document.querySelectorAll('.class-option').length >= 6;
  }, null, { timeout: 60000 });
  await page.waitForTimeout(2500);
  await installHelpers(page);
}

const stateText = async (page) => (await page.locator('.toolbar-state').innerText()).replace(/\s+/g, ' ').trim();
const toasts = async (page) => (await page.locator('.el-message__content').allInnerTexts()).map((text) => text.replace(/\s+/g, ' ').trim());
const revisionTexts = async (page) => (await page.locator('.revision-list li').allInnerTexts()).map((text) => text.replace(/\s+/g, ' ').trim());

// 提示条默认停留 3 秒：断言"操作后出现提示"之前先等它清空，否则上一步的残留提示会让断言假通过
async function waitToastsCleared(page) {
  await page.waitForFunction(() => !document.querySelector('.el-message'), null, { timeout: 15000 });
}

async function clickMap(page, point) {
  const hit = await page.evaluate(({ x, y }) => {
    const element = document.elementFromPoint(x, y);
    return element ? `${element.tagName}.${String(element.className).split(' ')[0]}` : '空';
  }, point);
  check(hit === 'CANVAS.maplibregl-canvas', `地图点击 (${Math.round(point.x)}, ${Math.round(point.y)}) 落在 ${hit} 上，不是画布`);
  await page.mouse.move(point.x - 3, point.y - 2, { steps: 3 });
  await page.mouse.move(point.x, point.y, { steps: 2 });
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(260);
}

// draw 只处理以画布为目标的键盘事件。真实用户按键前刚点过画布，焦点天然在画布上；
// 自动化里焦点可能被别的操作带走，所以按键前显式把焦点放回画布（测的是"焦点在画布上时按键生效"）
const focusCanvas = async (page) => { await page.locator('.classification-map canvas.maplibregl-canvas').focus(); };
const undo = async (page) => {
  await focusCanvas(page);
  await page.keyboard.down('Control'); await page.keyboard.press('z'); await page.keyboard.up('Control');
  await page.waitForTimeout(400);
};

// 真实用户编辑小图斑前会先放大：以某个点为中心用滚轮缩放（notches 为负=放大）
async function wheelZoom(page, point, notches) {
  await page.mouse.move(point.x, point.y);
  for (let index = 0; index < Math.abs(notches); index += 1) {
    await page.mouse.wheel(0, notches < 0 ? -240 : 240);
    await page.waitForTimeout(200);
  }
  await page.waitForTimeout(900);
}

// 放大到该图斑的外接框约占可见区域的目标比例（滚轮每格约放大 1/3 级）
async function zoomToFit(page, point, box, fraction = 0.6) {
  const visible = await page.evaluate(() => window.__zc.visible());
  const factor = Math.min((visible.width * fraction) / Math.max(box.width, 1), (visible.height * fraction) / Math.max(box.height, 1));
  const notches = Math.max(0, Math.min(30, Math.round(Math.log2(factor) / 0.333)));
  if (notches) await wheelZoom(page, point, -notches);
  await page.waitForTimeout(300);
  return notches;
}

async function drawPolygon(page, points, { finish = true } = {}) {
  await page.click('.mapbox-gl-draw_polygon');
  for (const point of points) await clickMap(page, point);
  if (finish) {
    await focusCanvas(page);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
  }
}

const saveTimings = [];
async function save(page, label) {
  const started = Date.now();
  const [response] = await Promise.all([
    page.waitForResponse((res) => res.url().includes(`/classification-results/${RESULT}/revisions`) && res.request().method() === 'POST', { timeout: 60000 }),
    page.getByRole('button', { name: '保存新版本' }).click(),
  ]);
  const request = response.request();
  saveTimings.push({ label, http: response.status(), ms: Date.now() - started, requestBytes: request.postDataBuffer()?.length || 0 });
  const body = await response.json().catch(() => ({}));
  await page.waitForTimeout(900);
  const outcome = { label, http: response.status(), revision: body?.data?.current_revision_no, msg: body?.msg, details: body?.data?.details || null, toasts: await toasts(page), state: await stateText(page) };
  console.log(`  保存 ${label}: HTTP ${outcome.http} → V${outcome.revision}  ${outcome.msg || ''} ${outcome.details ? JSON.stringify(outcome.details).slice(0, 200) : ''}  用时 ${saveTimings[saveTimings.length - 1].ms}ms`);
  return outcome;
}

// 页面内 JS 堆用量（Chromium）；不可用时为 null
const heapMb = async (page) => page.evaluate(() => {
  const memory = performance.memory;
  return memory ? Math.round(memory.usedJSHeapSize / 1048576) : null;
});

// 边做滚轮缩放边采样 3 秒的帧间隔，衡量地图在当前状态下的流畅度
async function frameStatsWhileZooming(page, point, notches = 6) {
  await page.evaluate(() => {
    window.__zcFrames = [];
    const started = performance.now();
    const tick = (now) => {
      window.__zcFrames.push(now);
      if (now - started < 3000) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await wheelZoom(page, point, notches);
  await page.waitForTimeout(600);
  const frames = await page.evaluate(() => window.__zcFrames.splice(0));
  const deltas = frames.slice(1).map((value, index) => value - frames[index]).sort((a, b) => a - b);
  if (!deltas.length) return null;
  const at = (ratio) => Math.round(deltas[Math.min(deltas.length - 1, Math.floor(deltas.length * ratio))] * 10) / 10;
  return { frames: deltas.length, medianMs: at(0.5), p95Ms: at(0.95), maxMs: Math.round(deltas[deltas.length - 1] * 10) / 10, over50ms: deltas.filter((delta) => delta > 50).length };
}

async function apiState(context) {
  const response = await context.request.get(`${API}/api/projects/${PROJECT}/classification-results/${RESULT}`);
  const data = (await response.json()).data || {};
  const features = (data.current_feature_collection || {}).features || [];
  const countVertices = (geometry) => (geometry.type === 'Polygon' ? geometry.coordinates : geometry.coordinates.flatMap((polygon) => polygon)).reduce((sum, ring) => sum + ring.length, 0);
  return {
    revision: data.current_revision_no,
    status: data.vector_status,
    effective: data.effective,
    features: features.map((feature) => ({
      fid: feature.properties.feature_id, cls: feature.properties.class_code, type: feature.geometry.type,
      vertices: countVertices(feature.geometry), shape: JSON.stringify(feature.geometry.coordinates),
    })),
    roi: data.roi_geometry,
  };
}

// 独立于编辑器的越界核对：对几何的顶点与每条边的中点、四分点做奇偶判定，判为界外的点再量它到
// ROI 边界的距离，返回最大伸出距离（度）。删掉矿山边界拐点上的顶点后，新的直边中点会真的伸到界外
// ——只在顶点上看不出来，所以边内也要取点
function maxOutsideDistance(geometry, roi) {
  const roiRings = roi.type === 'MultiPolygon' ? roi.coordinates.flat() : roi.coordinates;
  const polygons = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
  const insideByParity = (x, y) => {
    let inside = false;
    for (const ring of roiRings) {
      for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
        const [xi, yi] = ring[index]; const [xj, yj] = ring[previous];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
      }
    }
    return inside;
  };
  const distanceToBoundary = (x, y) => {
    let best = Infinity;
    for (const ring of roiRings) {
      for (let index = 0; index + 1 < ring.length; index += 1) {
        const [ax, ay] = ring[index]; const [bx, by] = ring[index + 1];
        const dx = bx - ax; const dy = by - ay;
        const lengthSquared = dx * dx + dy * dy;
        const ratio = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / lengthSquared));
        best = Math.min(best, Math.hypot(x - (ax + ratio * dx), y - (ay + ratio * dy)));
      }
    }
    return best;
  };
  let worst = 0;
  for (const rings of polygons) {
    for (const ring of rings) {
      for (let index = 0; index + 1 < ring.length; index += 1) {
        const [ax, ay] = ring[index]; const [bx, by] = ring[index + 1];
        for (const ratio of [0, 0.25, 0.5, 0.75]) {
          const x = ax + (bx - ax) * ratio; const y = ay + (by - ay) * ratio;
          if (!insideByParity(x, y)) worst = Math.max(worst, distanceToBoundary(x, y));
        }
      }
    }
  }
  return worst;
}

// 图斑是否贴着矿山边界：它有顶点落在 ROI 的某条边上（1e-8 度以内）。
// 自动矢量化会把图斑裁在边界上，坐标舍入后沿边界伸出约 1e-10 度——后端用 1e-9 度的容差接受，
// 编辑器此前用的 turf booleanWithin 没有容差，一动这种图斑就误报"超出 ROI"（真实成果里占 10% ~ 25%）
function touchesBoundary(feature, roi) {
  const rings = roi.type === 'MultiPolygon' ? roi.coordinates.flat() : [roi.coordinates[0]];
  const vertices = feature.type === 'Polygon' ? JSON.parse(feature.shape)[0] : JSON.parse(feature.shape)[0][0];
  for (const [x, y] of vertices) {
    for (const ring of rings) {
      for (let index = 0; index + 1 < ring.length; index += 1) {
        const [ax, ay] = ring[index]; const [bx, by] = ring[index + 1];
        const dx = bx - ax; const dy = by - ay;
        const lengthSquared = dx * dx + dy * dy;
        const ratio = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / lengthSquared));
        if (Math.hypot(x - (ax + ratio * dx), y - (ay + ratio * dy)) <= 1e-8) return true;
      }
    }
  }
  return false;
}

// 射线法：点是否在多边形外环内
function pointInPolygon(lng, lat, polygon) {
  const ring = polygon.coordinates[0];
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i]; const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// 退出顶点模式（Esc），回到普通选择并保留选中。每次点图斑之前都先退出：在顶点模式下，
// 点落在顶点或中点标记上会改动几何
async function leaveVertexMode(page) {
  if ((await page.evaluate(() => window.__zc.mode())) === 'direct_select') {
    await focusCanvas(page);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
  }
}

// 找一个"点下去必然选中"的图斑。默认视野下点不中的（在屏幕上太小），与真实用户一样放大后重试。
const byBoxDesc = (a, b) => b.box.width * b.box.height - a.box.width * a.box.height;
async function findSelectable(page, predicate, { allowZoom = true } = {}) {
  const matching = async () => (await page.evaluate(() => window.__zc.features())).filter((feature) => predicate(feature));
  const attempt = async () => {
    for (const feature of (await matching()).sort(byBoxDesc)) {
      const point = await page.evaluate((id) => window.__zc.clickPointFor(id), feature.id);
      if (point) return { feature, point };
    }
    return null;
  };
  const found = await attempt();
  if (found) return { ...found, zoomed: false };
  if (!allowZoom) return null;
  const smallest = (await matching()).sort((a, b) => byBoxDesc(b, a))[0];
  if (!smallest) return null;
  await zoomToFit(page, await page.evaluate((id) => window.__zc.boxCenter(id), smallest.id), smallest.box, 0.3);
  const zoomed = await attempt();
  return zoomed ? { ...zoomed, zoomed: true, boxBefore: smallest.box, boxAfter: zoomed.feature.box } : null;
}

async function selectFeature(page, target) {
  await leaveVertexMode(page);
  const point = await page.evaluate((id) => window.__zc.clickPointFor(id), target.feature.id);
  check(point, `找不到能点中图斑 ${target.feature.fid} 的位置`);
  const predicted = await page.evaluate(([x, y]) => window.__zc.predictAtScreen(x, y), [point.x, point.y]);
  check(predicted && predicted.meta === 'feature' && predicted.fid === String(target.feature.fid), `点击位置的命中预测不是目标图斑: ${JSON.stringify(predicted)}`);
  if (process.env.ZC_HIT_DEBUG) await page.evaluate(() => window.__zc.hookQueries());
  await clickMap(page, point);
  if (process.env.ZC_HIT_DEBUG) {
    const log = await page.evaluate(() => window.__zc.unhookQueries());
    console.log(`  [命中诊断] 点击 (${Math.round(point.x)}, ${Math.round(point.y)}) 期间的查询:\n` + log.map((entry) => `    ${entry.geometry} layers=${entry.layers} → ${entry.hits.join(' | ')}`).join('\n'));
  }
  await page.waitForTimeout(350);
  await leaveVertexMode(page);
  const ids = (await page.evaluate(() => window.__zc.selectedIds())).map(String);
  if (!(ids.length === 1 && ids[0] === String(target.feature.id))) {
    const actual = await page.evaluate((selected) => selected.map((id) => window.__zc.feature(id)), ids);
    const after = await page.evaluate(([x, y]) => window.__zc.predictAtScreen(x, y), [point.x, point.y]);
    const wanted = await page.evaluate((id) => window.__zc.feature(id), target.feature.id);
    const raw = await page.evaluate(([x, y]) => window.__zc.rawHitAt(x, y), [point.x, point.y]);
    check(false, `点击后应只选中目标图斑 ${target.feature.fid}，实际选中: ${JSON.stringify(actual)}；目标: ${JSON.stringify(wanted)}；点击 (${Math.round(point.x)}, ${Math.round(point.y)}) 前的预测 ${JSON.stringify(predicted)}、后的预测 ${JSON.stringify(after)}；原始查询 ${JSON.stringify(raw)}`);
  }
  return point;
}

// 进入顶点模式：读当前模式，不是顶点模式就点一下（已选中的）面
// 进入顶点模式：先保证已选中（普通选择模式），再点一下已选中的面
async function enterVertexMode(page, target) {
  await selectFeature(page, target);
  const point = await page.evaluate((id) => window.__zc.clickPointFor(id), target.feature.id);
  check(point, `找不到能点中图斑 ${target.feature.fid} 主体的位置`);
  await clickMap(page, point);
  await page.waitForTimeout(450);
  check((await page.evaluate(() => window.__zc.mode())) === 'direct_select', `点击已选中的面应进入顶点模式（图斑 ${target.feature.fid}）`);
}

async function restoreView(page) {
  await page.evaluate(() => window.__zc.restoreView());
  await page.waitForTimeout(900);
}

async function discardAll(page) {
  await page.getByRole('button', { name: '放弃全部修改' }).click();
  await page.locator('.el-message-box').waitFor({ timeout: 5000 });
  await page.locator('.el-message-box__btns button', { hasText: '放弃修改' }).click();
  await page.waitForTimeout(600);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const env = readEnv();
  const browser = await chromium.launch({ executablePath: EXE, headless: true, args: ['--use-gl=angle', '--use-angle=d3d11', '--ignore-gpu-blocklist'] });
  browserRef = browser;
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true, locale: 'zh-CN' });
  const shot = async (page, name) => { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); };
  const watch = (page, tag) => {
    page.on('pageerror', (error) => problems.push(`${tag} pageerror: ${String(error).slice(0, 220)}`));
    page.on('console', (msg) => { if (msg.type() === 'error' && !/the server responded with a status of (409|422)/.test(msg.text())) problems.push(`${tag} console: ${msg.text().slice(0, 220)}`); });
    page.on('response', (res) => { if (res.status() >= 500) problems.push(`${tag} HTTP ${res.status()} ${res.request().method()} ${res.url().slice(0, 140)}`); });
  };

  // ---------- 从 Miner 的真实入口进入 ----------
  const miner = await context.newPage();
  watch(miner, 'miner');
  await miner.goto(`${MINER}/`, { waitUntil: 'domcontentloaded' });
  await miner.locator('input[autocomplete="username"]').fill(env.ADMIN_USERNAME);
  await miner.locator('input[autocomplete="current-password"]').fill(env.ADMIN_PASSWORD);
  await miner.locator('.login-btn').click();
  await miner.locator('.side-nav').waitFor({ timeout: 20000 });
  await miner.goto(`${MINER}/#/interpretation`, { waitUntil: 'domcontentloaded' });
  await miner.locator('#interpretation-project').waitFor({ timeout: 30000 });
  await miner.selectOption('#interpretation-project', String(PROJECT));

  // 后端清单：本条成果必须是有效成果，同年其它成果应标为已被取代
  const listResponse = await context.request.get(`${API}/api/projects/${PROJECT}/classification-results`);
  const items = ((await listResponse.json()).data || {}).items || [];
  const recordOfResult = items.find((item) => item.result_id === RESULT);
  check(Boolean(recordOfResult), `项目的成果列表里找不到成果 ${RESULT}`);
  check(recordOfResult.effective === true, `成果 ${RESULT} 应是有效成果（effective=${recordOfResult.effective}）：请在全新推理出的成果上跑`);
  const year = recordOfResult.year;
  const supersededItems = items.filter((item) => item.mine_fid === FID && item.year === year && item.result_id !== RESULT && item.effective === false);
  check(items.filter((item) => item.mine_fid === FID && item.year === year && item.effective === true).length === 1, '这座矿山这一年应恰好有一条有效成果');

  const rows = miner.locator('.record-row', { hasText: `矿山 ${FID} · ${year}` });
  await rows.first().waitFor({ timeout: 30000 });
  await miner.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
  const rowCount = await rows.count();
  const taggedRows = await rows.locator('.superseded-tag').count();
  // 解译记录页每年只列最新一条记录（实测沙盒里 3 年 3 行），指向旧成果的行只在"最新一次推理
  // 矢量化失败、记录仍指向更早那条"等少数情形才出现。所以这里只硬断言"我们要点的那一行不带标记"；
  // "已被取代"标记的硬断言放在列出全部成果的图斑编辑清单上（见文件末尾）
  check(await rows.first().locator('.superseded-tag').count() === 0, '解译记录第一行（最新一次推理）不应带"已被取代"标记');
  console.log(`  Miner 解译记录：矿山 ${FID} · ${year} 共 ${rowCount} 行，其中 ${taggedRows} 行标有"已被取代"；后端清单里同期已被取代的成果 ${supersededItems.length} 条`);
  await shot(miner, 'v00-miner-interpretation-entry');
  const row = rows.first();
  const [page] = await Promise.all([context.waitForEvent('page'), row.locator('.edit-btn', { hasText: '编辑矢量' }).click()]);
  watch(page, 'editor');
  editorRef = page;
  await page.waitForLoadState('domcontentloaded');
  check(page.url().includes(`result_id=${RESULT}`), `入口打开的地址不对: ${page.url()}（记录页第一行应对应最新成果）`);
  await waitEditorReady(page);

  const initial = await apiState(context);
  const detailStarted = Date.now();
  const detailResponse = await context.request.get(`${API}/api/projects/${PROJECT}/classification-results/${RESULT}`);
  const detailMs = Date.now() - detailStarted;
  const detailBytes = (await detailResponse.body()).length;
  const heapAfterLoad = await heapMb(page);
  const loaded = await page.evaluate(() => window.__zc.count());
  check(loaded === initial.features.length, `编辑器加载的图斑数 ${loaded} 与接口 ${initial.features.length} 不一致`);
  check(/当前版本已保存/.test(await stateText(page)), '初始应为已保存状态');
  check(initial.features.length >= 12, `成果图斑太少（${initial.features.length} 个），覆盖不了后面的场景`);
  check(await page.locator('.editor-alert', { hasText: '已被同矿山同年更新的成果取代' }).count() === 0, '有效成果不应显示"已被取代"告警');
  check(await page.locator('.key-hints summary').isVisible(), '面板应有"键盘操作"说明入口');
  const initialFids = new Set(initial.features.map((feature) => feature.fid));
  record('从 Miner"编辑矢量"入口打开真实成果（有效成果，无"已被取代"告警）', true, {
    result: RESULT, features: loaded, positions: initial.features.reduce((sum, feature) => sum + feature.vertices, 0),
    largestFeatureVertices: Math.max(...initial.features.map((feature) => feature.vertices)),
    detailApiMs: detailMs, detailApiKB: Math.round(detailBytes / 1024), heapMb: heapAfterLoad, revision: initial.revision,
    minerRows: rowCount, supersededRows: taggedRows,
  });
  await shot(page, 'v01-editor-initial');
  let expectedRevision = initial.revision;

  // 被本测试改动过的图斑（完整性比对时排除），以及已被场景占用的初始图斑
  const touched = new Map();
  const note = (fid, reason) => touched.set(String(fid), reason);
  const used = new Set();
  const isAuto = (feature) => String(feature.fid).startsWith('auto-') && initialFids.has(String(feature.fid));
  const take = async (predicate, reason, options = { allowZoom: false }) => {
    const target = await findSelectable(page, (feature) => !used.has(String(feature.fid)) && predicate(feature), options);
    check(Boolean(target), `找不到符合"${reason}"的可点选图斑`);
    used.add(String(target.feature.fid));
    note(target.feature.fid, reason);
    return target;
  };
  const pointAt = (fx, fy) => page.evaluate(([x, y]) => window.__zc.pointAt(x, y), [fx, fy]);

  // ---------- 画面 + 改类 + 保存 ----------
  const before = await page.evaluate(() => window.__zc.count());
  // 新面必须画在矿山边界内：位置在 ROI 里找（离质心最近的一块），四角与四边中点都在界内
  let quad = null;
  for (const size of [0.06, 0.04, 0.025, 0.015]) {
    quad = await page.evaluate(([roi, fraction]) => window.__zc.roiAnchor(roi, fraction, fraction), [initial.roi, size]);
    if (quad) break;
  }
  check(Boolean(quad), '矿山边界内找不到能放下一个新面的位置');
  await drawPolygon(page, [{ x: quad.x, y: quad.y }, { x: quad.x + quad.width, y: quad.y }, { x: quad.x + quad.width, y: quad.y + quad.height }, { x: quad.x, y: quad.y + quad.height }]);
  check(await page.evaluate(() => window.__zc.count()) === before + 1, '画面后图斑数应 +1');
  const drawnId = (await page.evaluate(() => window.__zc.selectedIds()))[0];
  check(Boolean(drawnId), '新画的面应处于选中态');
  await page.locator('.class-option', { hasText: '建筑' }).click();
  await page.waitForTimeout(300);
  check((await page.evaluate((id) => window.__zc.feature(id), drawnId)).cls === 2, '新画的面改类后应为建筑');
  check(/存在未保存编辑/.test(await stateText(page)), '改动后应为未保存状态');
  await shot(page, 'v02-drawn-reclassified-unsaved');
  let outcome = await save(page, '画面+改类');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存应成功并出版本: ${JSON.stringify(outcome)}`);
  check(outcome.toasts.some((text) => text.includes(`已保存为 V${expectedRevision + 1}`)), '应有"已保存为 Vn"提示');
  check(/当前版本已保存/.test(outcome.state), '保存后应回到已保存状态');
  let api = await apiState(context);
  // 保存后服务端给新画的面分配 manual-<成果>-<uuid> 形式的 ID，不保留客户端临时 ID：按"初始没有的 ID"找
  const added = api.features.filter((feature) => !initialFids.has(feature.fid)).find((feature) => feature.cls === 2);
  check(api.features.length === before + 1 && added, '接口应看到图斑数 +1、新面为建筑');
  check(/^manual-\d+-[0-9a-f]{32}$/.test(added.fid), `新面的 ID 应由服务端分配: ${added.fid}`);
  expectedRevision += 1;
  note(added.fid, '新画的面');
  record('画面、改类、保存 → 新版本', true, { revision: api.revision, features: api.features.length, newFeature: added.fid });

  // ---------- 刷新后仍在 ----------
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitEditorReady(page);
  check((await page.evaluate(() => window.__zc.count())) === before + 1, '刷新后图斑数应不变');
  check(/当前版本已保存/.test(await stateText(page)), '刷新后应为已保存状态');
  const revisions = await revisionTexts(page);
  check(revisions.some((text) => text.startsWith(`V${expectedRevision}`) && text.includes('人工编辑')), `版本记录应有 V${expectedRevision} 人工编辑: ${JSON.stringify(revisions)}`);
  await shot(page, 'v03-reloaded');
  record('刷新页面后编辑结果仍在，版本记录出现人工编辑版本', true, { features: before + 1, revisions: revisions.slice(0, 3) });

  // ---------- 修改已有图斑的地类（目标：任意一个自动基线里的图斑） ----------
  const reclassify = await take(isAuto, '改类');
  // 改类前的几何（接口当前版本的指纹），保存后逐字节比对
  const shapeBefore = api.features.find((feature) => feature.fid === reclassify.feature.fid).shape;
  await selectFeature(page, reclassify);
  const fromClass = reclassify.feature.cls; const toClass = (fromClass + 1) % 6;
  await page.locator('.class-option', { hasText: CLASS_NAMES[toClass] }).first().click();
  await page.waitForTimeout(300);
  check((await page.evaluate((id) => window.__zc.feature(id), reclassify.feature.id)).cls === toClass, '已有图斑改类后应为新地类');
  await shot(page, 'v04a-reclassified-unsaved');
  outcome = await save(page, '改类');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
  api = await apiState(context);
  const changed = api.features.find((feature) => feature.fid === reclassify.feature.fid);
  check(changed && changed.cls === toClass, `接口应看到该图斑已改为${CLASS_NAMES[toClass]}: ${JSON.stringify(changed && { cls: changed.cls })}`);
  check(changed && changed.shape === shapeBefore, '只改类时几何必须逐字节不变');
  expectedRevision += 1;
  record('修改已有图斑的地类并保存', true, { revision: api.revision, feature: reclassify.feature.fid, from: fromClass, to: toClass });
  await shot(page, 'v04b-reclassified-saved');

  // ---------- 超限面：单要素坐标数超过 5000 的面（生产 3036 条成果里有 10 条）原先连原样保存都被拒 ----------
  api = await apiState(context);
  const oversized = api.features.filter((feature) => feature.vertices > 5000).sort((a, b) => b.vertices - a.vertices)[0];
  if (oversized) {
    // 超限面可能在上一步"改类"里已经被用过：这里直接找，不经 take 的"未用过"筛选
    const target = await findSelectable(page, (feature) => String(feature.fid) === String(oversized.fid), { allowZoom: true });
    check(Boolean(target), `超限面 ${oversized.fid} 点不中`);
    used.add(String(oversized.fid));
    note(oversized.fid, '超限面');
    const oversizedShape = api.features.find((feature) => String(feature.fid) === String(oversized.fid)).shape;
    await selectFeature(page, target);
    const oversizedTo = (oversized.cls + 1) % 6;
    await page.locator('.class-option', { hasText: CLASS_NAMES[oversizedTo] }).first().click();
    await page.waitForTimeout(300);
    outcome = await save(page, '超限面原样几何');
    check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `超限面（${oversized.vertices} 个坐标）只改类、几何不变时保存必须成功: ${JSON.stringify(outcome)}`);
    api = await apiState(context);
    const oversizedSaved = api.features.find((feature) => String(feature.fid) === String(oversized.fid));
    check(oversizedSaved && oversizedSaved.cls === oversizedTo && oversizedSaved.shape === oversizedShape && oversizedSaved.vertices === oversized.vertices, '接口应看到超限面已改类、几何逐字节不变');
    expectedRevision += 1;
    // 再删它的一个顶点（坐标变少）：同样必须能保存
    const oversizedNow = await findSelectable(page, (feature) => String(feature.fid) === String(oversized.fid), { allowZoom: false });
    check(Boolean(oversizedNow), '超限面应可点选');
    await enterVertexMode(page, oversizedNow);
    const oversizedVertex = await page.evaluate((id) => window.__zc.vertexPoint(id, 0), oversizedNow.feature.id);
    await clickMap(page, oversizedVertex);
    await page.waitForTimeout(250);
    check((await page.evaluate(() => window.__zc.selectedVertices())) === 1, '超限面上应选中了一个顶点');
    await focusCanvas(page);
    await page.keyboard.press('Delete');
    await page.waitForTimeout(500);
    check((await page.evaluate((id) => window.__zc.feature(id).vertices, oversizedNow.feature.id)) === oversized.vertices - 1, '超限面删一个顶点后顶点数应减 1');
    outcome = await save(page, '超限面删顶点');
    check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `超限面删一个顶点后保存必须成功: ${JSON.stringify(outcome)}`);
    expectedRevision += 1;
    await leaveVertexMode(page);
    record('超限面：只改类保存成功（几何逐字节不变），删一个顶点后保存成功', true, { feature: oversized.fid, vertices: oversized.vertices, requestKB: Math.round(saveTimings[saveTimings.length - 1].requestBytes / 1024) });
  } else {
    record('成果里没有单要素超过 5000 坐标的面，超限面一步不适用', true, { largest: Math.max(...api.features.map((feature) => feature.vertices)) });
  }

  // ---------- 顶点：相邻顶点在屏幕上挨得很近时，点顶点必须恰好选中它、不多出顶点 ----------
  // 库原先按"渲染在最上面的那个点要素"取目标：间距 ≤10px 时点顶点会被判成点中点、按下即多出
  // 一个顶点；几个顶点圆重叠时选中的是绘制顺序靠后的那个。现在按"离点击最近"取目标。
  // 优先挑贴矿山边界的图斑：真实数据上它一动就被误报"超出 ROI"，这一步要盯住的正是它
  api = await apiState(context);
  const boundaryFids = new Set(api.features.filter((feature) => touchesBoundary(feature, api.roi)).map((feature) => String(feature.fid)));
  console.log(`  成果 ${RESULT}：${api.features.length} 个图斑里贴矿山边界的有 ${boundaryFids.size} 个`);
  const complex = await take((feature) => isAuto(feature) && feature.vertices >= 20 && feature.vertices <= 800
    && (boundaryFids.size === 0 || boundaryFids.has(String(feature.fid))), '删顶点');
  const complexTouchesBoundary = boundaryFids.has(String(complex.feature.fid));
  const zoomAtStart = await page.evaluate(() => window.__zc.zoom());
  // 找一个"与相邻顶点在屏幕上相距不超过 14px、且在画面内"的顶点；顶点都分得很开就缩小一点再看
  // （与真实用户缩小看全局时一致）
  const findDenseVertex = async (target) => {
    for (let index = 0; index < Math.min(target.feature.vertices - 1, 200); index += 1) {
      const vertex = await page.evaluate(([id, i]) => window.__zc.vertexPoint(id, i), [target.feature.id, index]);
      if (!(await page.evaluate((point) => window.__zc.onCanvas(point, 30), vertex))) continue;
      const spacing = await page.evaluate(([id, i]) => window.__zc.vertexSpacing(id, i), [target.feature.id, index]);
      if (spacing.min <= 14) return { index, spacing: spacing.min };
    }
    return null;
  };
  let zoomed = null;
  let dense = null;
  let zoomNotches = 0;
  for (let round = 0; round < 8 && !dense; round += 1) {
    zoomed = await findSelectable(page, (feature) => feature.fid === complex.feature.fid, { allowZoom: false });
    check(Boolean(zoomed), `目标图斑应可点选（第 ${round + 1} 轮，缩放了 ${zoomNotches} 格）`);
    dense = await findDenseVertex(zoomed);
    if (!dense) {
      const centre = await page.evaluate((id) => window.__zc.boxCenter(id), zoomed.feature.id);
      await wheelZoom(page, centre, 2);
      zoomNotches += 2;
    }
  }
  check(Boolean(dense), `找不到相邻顶点挨得够近的顶点（缩放了 ${zoomNotches} 格）`);
  const zoomTarget = await page.evaluate((id) => window.__zc.boxCenter(id), zoomed.feature.id);
  const framesDefault = await frameStatsWhileZooming(page, zoomTarget, -6);
  if (framesDefault) await wheelZoom(page, zoomTarget, 6);
  const heapBeforeVertexMode = await heapMb(page);
  const zoomAtTest = await page.evaluate(() => window.__zc.zoom());
  await selectFeature(page, zoomed);
  await enterVertexMode(page, zoomed);
  await shot(page, 'v05a-vertex-mode');
  const framesVertexMode = await frameStatsWhileZooming(page, zoomTarget, -6);
  if (framesVertexMode) await wheelZoom(page, zoomTarget, 6);
  const heapVertexMode = await heapMb(page);
  const baseVertices = zoomed.feature.vertices;
  // 连续点三个不同的顶点：每一次都恰好选中 1 个、顶点总数不变
  let picked = null;
  let tried = 0;
  for (let index = dense.index; index < Math.min(baseVertices - 1, dense.index + 60) && tried < 3; index += 1) {
    const vertex = await page.evaluate(([id, i]) => window.__zc.vertexPoint(id, i), [zoomed.feature.id, index]);
    if (!(await page.evaluate((point) => window.__zc.onCanvas(point, 30), vertex))) continue;
    const spacing = await page.evaluate(([id, i]) => window.__zc.vertexSpacing(id, i), [zoomed.feature.id, index]);
    if (spacing.min > 14) continue;
    await clickMap(page, vertex);
    const info = await page.evaluate((id) => ({ selected: window.__zc.selectedVertices(), vertices: window.__zc.feature(id).vertices, picked: window.__zc.geometryOf(id) }), zoomed.feature.id);
    check(info.vertices === baseVertices, `点第 ${index + 1} 个顶点不得改变几何（顶点数 ${baseVertices} → ${info.vertices}）`);
    check(info.selected === 1, `点第 ${index + 1} 个顶点应恰好选中 1 个顶点，实际 ${info.selected}`);
    tried += 1;
    picked = { index, spacing: spacing.min };
  }
  check(Boolean(picked), '没有点到任何顶点');
  await shot(page, 'v05b-vertex-selected');
  // 删一个顶点。真实图斑贴着矿山边界：删掉凹角上的顶点，新的直边会伸到边界外——编辑器当场提示
  // "超出成果 ROI"（保存时后端也会拒）。这是正确的保护，遇到这种顶点就撤销、换下一个
  let deletedVertex = null;
  let outOfRoiDeletions = 0;
  let trueRoiWarnings = 0;
  let falseRoiWarnings = 0;
  for (let index = 0; index < Math.min(baseVertices - 1, 200) && !deletedVertex; index += 1) {
    const vertex = await page.evaluate(([id, i]) => window.__zc.vertexPoint(id, i), [zoomed.feature.id, index]);
    if (!(await page.evaluate((point) => window.__zc.onCanvas(point, 30), vertex))) continue;
    await waitToastsCleared(page);
    await clickMap(page, vertex);
    if ((await page.evaluate(() => window.__zc.selectedVertices())) !== 1) continue;
    await focusCanvas(page);
    await page.keyboard.press('Delete');
    await page.waitForTimeout(500);
    const verticesAfter = await page.evaluate((id) => window.__zc.feature(id).vertices, zoomed.feature.id);
    const messagesAfter = await toasts(page);
    const roiWarned = messagesAfter.some((text) => text.includes('超出成果 ROI'));
    if (roiWarned) {
      // 提示是真是假用独立算法核对：几何确实伸出矿山边界（远大于 1e-9 度的容差）才是真越界
      const geometryAfter = await page.evaluate((id) => window.__zc.geometryOf(id), zoomed.feature.id);
      const distance = maxOutsideDistance(geometryAfter, api.roi);
      if (distance > 1e-7) trueRoiWarnings += 1; else falseRoiWarnings += 1;
    }
    if (verticesAfter !== baseVertices - 1 || roiWarned) {
      await undo(page);
      outOfRoiDeletions += 1;
      continue;
    }
    check(messagesAfter.length === 0, `合法删除不应有提示: ${JSON.stringify(messagesAfter)}`);
    deletedVertex = { index };
  }
  check(Boolean(deletedVertex), `没有找到删掉后仍在矿山边界内的顶点（删后越界 ${outOfRoiDeletions} 次）`);
  check(falseRoiWarnings === 0, `删顶点后的"超出成果 ROI"提示经独立核对属于误报 ${falseRoiWarnings} 次（编辑器判定应与后端 1e-9 度容差一致）`);
  check(trueRoiWarnings === outOfRoiDeletions, `每次撤销都应对应一次真实的越界提示（真越界 ${trueRoiWarnings}，撤销 ${outOfRoiDeletions}）`);
  const afterVertexDelete = await page.evaluate((id) => window.__zc.feature(id), zoomed.feature.id);
  check(afterVertexDelete && afterVertexDelete.vertices === baseVertices - 1, `删一个顶点后顶点数应减 1: ${baseVertices} → ${afterVertexDelete && afterVertexDelete.vertices}`);
  await shot(page, 'v05c-vertex-deleted-unsaved');
  outcome = await save(page, '删顶点');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
  api = await apiState(context);
  const fewer = api.features.find((feature) => feature.fid === complex.feature.fid);
  check(fewer && fewer.vertices === baseVertices - 1, `接口应看到顶点数减 1: ${baseVertices} → ${fewer && fewer.vertices}`);
  expectedRevision += 1;
  record('相邻顶点挨得很近时连续点三个顶点都恰好选中它（不多出顶点），删除后保存', true, { feature: complex.feature.fid, touchesMineBoundary: complexTouchesBoundary, featuresOnBoundary: boundaryFids.size, verticesOfFeature: baseVertices, clicks: tried, closestSpacingPx: picked.spacing.toFixed(1), trueRoiWarnings, outOfRoiDeletions, zoom: `${zoomAtStart.toFixed(2)} → ${zoomAtTest.toFixed(2)}（缩小 ${zoomNotches} 格）`, vertices: `${baseVertices} → ${fewer.vertices}`,
      framesZooming: framesDefault, framesZoomingWithVertexMarkers: framesVertexMode, heapMb: { beforeVertexMode: heapBeforeVertexMode, vertexMode: heapVertexMode } });

  // 加顶点：在一条足够长的边上点中点标记，新增的顶点直接处于选中态，按 Delete 即可移除
  // 保存后画布上的要素已换成服务端返回的新对象：按业务 ID 重新找，再进顶点模式
  const afterSave = await findSelectable(page, (feature) => feature.fid === complex.feature.fid, { allowZoom: false });
  check(Boolean(afterSave), '保存后应仍能找到这个图斑');
  check(afterSave.feature.vertices === baseVertices - 1, '保存后画布上的顶点数应是少一个之后的');
  await enterVertexMode(page, afterSave);
  // 真实影像的边界是一个像元一级的台阶，默认视野下每条边只有一两个像素：先放大，让边长到可以点中点
  await wheelZoom(page, await page.evaluate((id) => window.__zc.boxCenter(id), afterSave.feature.id), -6);
  const ringNow = afterSave.feature.vertices;
  let edge = null;
  for (let index = 0; index < Math.min(ringNow - 1, 400) && !edge; index += 1) {
    const spacing = await page.evaluate(([id, i]) => window.__zc.vertexSpacing(id, i), [afterSave.feature.id, index]);
    if (spacing.edge.length >= 26 && await page.evaluate((point) => window.__zc.onCanvas(point, 30), spacing.edge.from) && await page.evaluate((point) => window.__zc.onCanvas(point, 30), spacing.edge.to)) edge = { index, ...spacing.edge };
  }
  check(Boolean(edge), '找不到一条足够长、且两端都在画面内的边');
  const midpoint = { x: (edge.from.x + edge.to.x) / 2, y: (edge.from.y + edge.to.y) / 2 };
  await waitToastsCleared(page);
  await clickMap(page, midpoint);
  await page.waitForTimeout(300);
  const addedVertex = await page.evaluate((id) => ({ selected: window.__zc.selectedVertices(), vertices: window.__zc.feature(id).vertices }), afterSave.feature.id);
  check(addedVertex.vertices === ringNow + 1, `点边上的小圆点应新增一个顶点: ${ringNow} → ${addedVertex.vertices}`);
  check(!(await toasts(page)).some((text) => text.includes('超出成果 ROI')), `加顶点后不应被提示"超出成果 ROI": ${JSON.stringify(await toasts(page))}`);
  check(addedVertex.selected === 1, '新增的顶点应处于选中态（库内选中坐标已同步）');
  await shot(page, 'v05d-midpoint-vertex-added');
  await focusCanvas(page);
  await page.keyboard.press('Delete');
  await page.waitForTimeout(500);
  check((await page.evaluate((id) => window.__zc.feature(id).vertices, afterSave.feature.id)) === ringNow, '新增的顶点按 Delete 即移除');
  check((await toasts(page)).length === 0, `移除新增顶点不应出现"请先选中顶点"的引导: ${JSON.stringify(await toasts(page))}`);
  check(/当前版本已保存/.test(await stateText(page)), '加一个再删一个，状态应仍是已保存');
  record('点边上的小圆点新增顶点（选中态同步），Delete 即移除', true, { edgePx: edge.length.toFixed(1) });
  await leaveVertexMode(page);
  await restoreView(page);

  // ---------- 小图斑：默认视野下在屏幕上很小的图斑也要能单独点中 ----------
  const atDefault = await page.evaluate(() => window.__zc.features());
  const smallOnes = atDefault.filter((feature) => initialFids.has(String(feature.fid)) && !used.has(String(feature.fid)) && feature.box.width * feature.box.height <= 900);
  let notClickableAtDefault = 0; let sample = null;
  for (const feature of smallOnes) {
    const point = await page.evaluate((id) => window.__zc.clickPointFor(id), feature.id);
    if (!point) { notClickableAtDefault += 1; if (!sample) sample = feature; }
  }
  if (sample) {
    const found = await findSelectable(page, (feature) => feature.fid === sample.fid, { allowZoom: true });
    check(Boolean(found) && found.zoomed, `小图斑放大后应能选中: ${sample.fid}`);
    await selectFeature(page, found);
    await shot(page, 'v05e-small-parcel-selected-after-zoom');
    record('屏幕上小到点不中的图斑，放大后可以选中', true, { feature: sample.fid, sizeAtDefault: `${Math.round(found.boxBefore.width)}×${Math.round(found.boxBefore.height)}px`, sizeZoomed: `${Math.round(found.boxAfter.width)}×${Math.round(found.boxAfter.height)}px`, small: smallOnes.length, notClickable: notClickableAtDefault });
    await restoreView(page);
    check(/当前版本已保存/.test(await stateText(page)), '只选中、不改动，状态应仍是已保存');
  } else {
    record('成果里在屏幕上很小的图斑在默认视野下都能单独点中', true, { small: smallOnes.length });
  }

  // ---------- 三角面删顶点必须被拦 ----------
  const countBeforeTriangle = await page.evaluate(() => window.__zc.count());
  await drawPolygon(page, [await pointAt(0.62, 0.66), await pointAt(0.68, 0.66), await pointAt(0.65, 0.72)]);
  const triangleId = (await page.evaluate(() => window.__zc.selectedIds()))[0];
  check((await page.evaluate((id) => window.__zc.feature(id).vertices, triangleId)) === 4, '新画的应是三角面（4 个坐标含闭合点）');
  const trianglePoint = await page.evaluate((id) => window.__zc.clickPointFor(id), triangleId);
  check(Boolean(trianglePoint), '找不到能点中三角面主体的位置');
  await clickMap(page, trianglePoint);            // 已选中的面再点一次 → 顶点模式
  await page.waitForTimeout(400);
  check((await page.evaluate(() => window.__zc.mode())) === 'direct_select', '三角面应进入顶点模式');
  await clickMap(page, await page.evaluate((id) => window.__zc.vertexPoint(id, 1), triangleId));
  await page.waitForTimeout(300);
  check((await page.evaluate(() => window.__zc.selectedVertices())) >= 1, '三角面上应有顶点被选中');
  await waitToastsCleared(page);
  await focusCanvas(page);
  await page.keyboard.press('Delete');
  await page.waitForTimeout(500);
  let messages = await toasts(page);
  check(messages.some((text) => text.includes('只剩 2 个顶点')), `三角面删顶点应被拦并说明: ${JSON.stringify(messages)}`);
  check((await page.evaluate((id) => window.__zc.feature(id), triangleId)).vertices === 4, '三角面不得少顶点');
  await shot(page, 'v06-triangle-vertex-blocked');
  record('三角面删顶点被拦且有说明', true, { toasts: messages });
  await focusCanvas(page);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  await undo(page);
  check((await page.evaluate(() => window.__zc.count())) === countBeforeTriangle && /当前版本已保存/.test(await stateText(page)), '撤销后应回到已保存状态');

  // ---------- 合并同类的两个面（目标：初始图斑里数量最多的地类中任意两个） ----------
  const histogram = new Map();
  api = await apiState(context);
  for (const feature of api.features) {
    if (!initialFids.has(feature.fid) || used.has(feature.fid)) continue;
    histogram.set(feature.cls, (histogram.get(feature.cls) || 0) + 1);
  }
  const mergeClass = [...histogram.entries()].filter(([, count]) => count >= 2).sort((a, b) => b[1] - a[1])[0];
  check(Boolean(mergeClass), '找不到两个同地类、还没被用过的图斑');
  const first = await take((feature) => feature.cls === mergeClass[0], '合并后消失');
  const second = await take((feature) => feature.cls === mergeClass[0], '合并后消失');
  const countBeforeMerge = await page.evaluate(() => window.__zc.count());
  await selectFeature(page, first);
  const secondPoint = await page.evaluate((id) => window.__zc.clickPointFor(id), second.feature.id);
  check(Boolean(secondPoint), `找不到能点中图斑 ${second.feature.fid} 的位置`);
  await page.keyboard.down('Shift');
  try { await clickMap(page, secondPoint); } finally { await page.keyboard.up('Shift'); }
  await page.waitForTimeout(400);
  await leaveVertexMode(page);
  const ids = (await page.evaluate(() => window.__zc.selectedIds())).map(String);
  check(ids.includes(String(first.feature.id)) && ids.includes(String(second.feature.id)) && ids.length === 2, `Shift 加选后应恰好选中这两个面（选中: ${JSON.stringify(ids)}）`);
  const mergeButton = page.getByRole('button', { name: '合并选中' });
  check(await mergeButton.isEnabled(), '同类选中时合并应可用');
  await shot(page, 'v07a-two-selected');
  await mergeButton.click();
  await page.waitForTimeout(800);
  messages = await toasts(page);
  check(messages.some((text) => text.includes('已合并为 1 个面')), `合并应有成功提示: ${JSON.stringify(messages)}`);
  check((await page.evaluate(() => window.__zc.count())) === countBeforeMerge - 1, '合并后图斑数应减 1');
  await shot(page, 'v07b-merged-unsaved');
  outcome = await save(page, '合并');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
  api = await apiState(context);
  check(api.features.length === countBeforeMerge - 1 && !api.features.some((feature) => feature.fid === first.feature.fid) && !api.features.some((feature) => feature.fid === second.feature.fid), '接口应看到两个旧面消失、总数减 1');
  const mergedFeature = api.features.find((feature) => !initialFids.has(feature.fid) && !touched.has(String(feature.fid)));
  check(Boolean(mergedFeature), '接口应看到合并产生的新面');
  note(mergedFeature.fid, '合并产生');
  expectedRevision += 1;
  record('合并两个同类的面并保存', true, { revision: api.revision, class: CLASS_NAMES[mergeClass[0]], merged: { type: mergedFeature.type, vertices: mergedFeature.vertices } });

  // ---------- 消除锯齿 ----------
  // 能不能简化取决于数据：用编辑器自己的简化函数（含内部的合法性校验）先把成果里的面算一遍。
  // 栅格边界是"一个像元一级的台阶"，像元比容差大得多时每个顶点都必须保留，简化不会改变几何。
  const { simplifyPolygonSafely } = await import('file:///D:/项目/YunNan/frontend/src/utils/classificationEditorGeometry.mjs');
  const { default: turfSimplify } = await import('file:///D:/项目/YunNan/frontend/node_modules/@turf/simplify/dist/esm/index.js');
  const simplifyLikeEditor = (geometry) => simplifyPolygonSafely(geometry, {
    tolerance: 0.00001,
    simplify: (current, tolerance) => turfSimplify({ type: 'Feature', properties: {}, geometry: current }, { tolerance, highQuality: false }).geometry,
  });
  api = await apiState(context);
  const reducibleOriginals = api.features.filter((feature) => {
    const outcome = simplifyLikeEditor({ type: feature.type, coordinates: JSON.parse(feature.shape) });
    return Boolean(outcome) && JSON.stringify(outcome.geometry.coordinates) !== feature.shape;
  });
  if (reducibleOriginals.length) {
    const wanted = reducibleOriginals.sort((a, b) => b.vertices - a.vertices)[0];
    const ragged = await findSelectable(page, (feature) => feature.fid === wanted.fid);
    check(Boolean(ragged), `可简化的面 ${wanted.fid} 点不中`);
    await selectFeature(page, ragged);
    note(ragged.feature.fid, '简化');
    await page.getByRole('button', { name: '消除锯齿' }).click();
    await page.waitForTimeout(900);
    messages = await toasts(page);
    const simplified = (await page.evaluate(() => window.__zc.features())).find((feature) => feature.fid === ragged.feature.fid && feature.vertices < ragged.feature.vertices);
    check(messages.some((text) => text.includes('已简化')), `简化应有提示: ${JSON.stringify(messages)}`);
    check(Boolean(simplified), '应出现顶点更少的面');
    await shot(page, 'v08a-original-simplified-unsaved');
    outcome = await save(page, '简化原有图斑');
    check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
    expectedRevision += 1;
    record('对原有图斑消除锯齿（简化）并保存', true, { feature: ragged.feature.fid, vertices: `${ragged.feature.vertices} → ${simplified.vertices}`, reducibleOriginals: reducibleOriginals.length });
  } else {
    // 没有可简化的原有图斑：对最大的面点一次，确认给出的是"已足够平滑"的如实提示而不是别的
    const biggest = await take(() => true, '仅选中');
    await selectFeature(page, biggest);
    await waitToastsCleared(page);
    await page.getByRole('button', { name: '消除锯齿' }).click();
    await page.waitForTimeout(900);
    messages = await toasts(page);
    // 简化不改变几何 → "已足够平滑"；简化后几何无效 → "已跳过"。两种都是如实的无改动提示
    check(messages.some((text) => text.includes('已足够平滑') || text.includes('已跳过')), `不可简化时应如实提示，实际: ${JSON.stringify(messages)}`);
    check(/当前版本已保存/.test(await stateText(page)), '不可简化时不应产生编辑');
    record(`成果里 ${api.features.length} 个面在默认容差（约 1.1 米）下都不能简化，提示"边缘已足够平滑"且不产生编辑`, true, { note: '测试影像像元 4.4 米，边界台阶远大于容差' });
  }
  // 另画一个带细小锯齿的面，真实验证"简化 → 保存"这条路：放大后 1 像素约 0.25 米（容差约 4.5 像素）
  // 放大到约 0.25 米/像素（滚轮每格约 1/3 级），以矿山边界质心为缩放中心；锯齿面按地面尺寸画：
  // 幅度 0.6 米、周期 5 米，都小于约 1.1 米的简化容差或与之同量级——与影像像元大小无关
  const roiCentre = await page.evaluate((roi) => window.__zc.roiCentreScreen(roi), api.roi);
  const zoomNow = await page.evaluate(() => window.__zc.zoom());
  const targetZoom = 19.1;
  const jaggedNotches = Math.round((targetZoom - zoomNow) * 3);
  if (jaggedNotches) await wheelZoom(page, roiCentre, -jaggedNotches);   // 负数=放大
  const zoomJagged = await page.evaluate(() => window.__zc.zoom());
  const metresPerPixel = await page.evaluate(() => window.__zc.metresPerPixel());
  const periodPx = 5 / metresPerPixel; const amplitudePx = 0.6 / metresPerPixel;
  check(periodPx >= 12 && amplitudePx >= 1.5, `当前比例尺下锯齿画不出可辨识的形状（每像素 ${metresPerPixel.toFixed(3)} 米）`);
  const jaggedWidthPx = periodPx * 10; const jaggedHeightPx = Math.max(amplitudePx * 2, periodPx * 2);
  const origin = { x: roiCentre.x - jaggedWidthPx / 2, y: roiCentre.y - jaggedHeightPx / 2 };
  const jaggedPoints = [{ x: origin.x, y: origin.y }];
  for (let index = 1; index <= 10; index += 1) jaggedPoints.push({ x: origin.x + index * periodPx, y: origin.y - (index % 2 ? amplitudePx : 0) });
  jaggedPoints.push({ x: origin.x + jaggedWidthPx, y: origin.y + jaggedHeightPx }, { x: origin.x, y: origin.y + jaggedHeightPx });
  await drawPolygon(page, jaggedPoints);
  const jaggedId = (await page.evaluate(() => window.__zc.selectedIds()))[0];
  const jaggedBefore = await page.evaluate((id) => window.__zc.feature(id), jaggedId);
  check(Boolean(jaggedBefore) && jaggedBefore.vertices === jaggedPoints.length + 1, `新画的锯齿面应有 ${jaggedPoints.length + 1} 个坐标: ${JSON.stringify(jaggedBefore)}`);
  await shot(page, 'v08b-jagged-drawn-unsaved');
  await waitToastsCleared(page);
  await page.getByRole('button', { name: '消除锯齿' }).click();
  await page.waitForTimeout(900);
  messages = await toasts(page);
  // 消除锯齿是"加一个新面、删掉旧面"（内部 ID 会变）：按业务 ID 重新找
  const jaggedAfter = (await page.evaluate(() => window.__zc.features())).find((feature) => String(feature.fid) === String(jaggedBefore.fid));
  check(messages.some((text) => text.includes('已简化 1 个面')), `简化锯齿面应有提示: ${JSON.stringify(messages)}`);
  check(Boolean(jaggedAfter) && jaggedAfter.vertices < jaggedBefore.vertices, `简化后顶点数应减少: ${JSON.stringify(jaggedBefore)} → ${JSON.stringify(jaggedAfter)}`);
  await shot(page, 'v08c-jagged-simplified-unsaved');
  outcome = await save(page, '简化新画的锯齿面');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
  api = await apiState(context);
  // 新画面沿用"当前选中的地类"：按"初始没有、不是第一步画的那个、坐标数相同"找
  const jaggedSaved = api.features.find((feature) => !initialFids.has(feature.fid) && !touched.has(String(feature.fid)) && feature.vertices === jaggedAfter.vertices);
  check(Boolean(jaggedSaved), `接口应看到简化后的锯齿面（${jaggedAfter.vertices} 个坐标）`);
  expectedRevision += 1;
  note(jaggedSaved.fid, '简化');
  record('新画一个带细小锯齿的面 → 消除锯齿 → 保存', true, { zoom: zoomJagged.toFixed(1), vertices: `${jaggedBefore.vertices} → ${jaggedAfter.vertices}`, fid: jaggedSaved.fid });
  await restoreView(page);

  // ---------- 绘制进行中：退回上一个点、保存被拦截、撤销后仍可继续绘制 ----------
  // 草稿在绘制期间本身就算在图布的要素里（没有地类的那个），基准要在开始绘制之前取
  const countBeforeSketch = await page.evaluate(() => window.__zc.count());
  await page.click('.mapbox-gl-draw_polygon');
  const sketchOrigin = await pointAt(0.70, 0.40);
  const sketchSquare = [sketchOrigin, { x: sketchOrigin.x + 70, y: sketchOrigin.y }, { x: sketchOrigin.x + 70, y: sketchOrigin.y + 70 }, { x: sketchOrigin.x, y: sketchOrigin.y + 70 }];
  for (const point of sketchSquare) await clickMap(page, point);
  check((await page.evaluate(() => window.__zc.mode())) === 'draw_polygon', '点绘制按钮后应处于绘制模式');
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === 4, '点 4 下后应放置 4 个顶点');
  const savePosts = [];
  page.on('request', (request) => { if (request.method() === 'POST' && request.url().includes(`/classification-results/${RESULT}/revisions`)) savePosts.push(1); });
  await focusCanvas(page);
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(300);
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === 3, '绘制中 Backspace 应退回一个点');
  await undo(page);
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === 2, '绘制中 Ctrl+Z 应退回一个点，而不是把草稿变成幽灵');
  check((await page.evaluate(() => window.__zc.mode())) === 'draw_polygon', '撤销后应仍在绘制模式');
  // 撤销过的草稿接着画，回车完成
  for (const point of [{ x: sketchOrigin.x + 70, y: sketchOrigin.y + 70 }, { x: sketchOrigin.x, y: sketchOrigin.y + 70 }]) await clickMap(page, point);
  await focusCanvas(page);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(500);
  check((await page.evaluate(() => window.__zc.mode())) === 'simple_select', '回车应完成绘制');
  check((await page.evaluate(() => window.__zc.count())) === countBeforeSketch + 1, '撤销过的草稿仍能完成绘制');
  // 再画到一半去保存：拦截并指引，不发请求、草稿原样保留
  await page.click('.mapbox-gl-draw_polygon');
  for (const point of [{ x: sketchOrigin.x + 110, y: sketchOrigin.y }, { x: sketchOrigin.x + 170, y: sketchOrigin.y }, { x: sketchOrigin.x + 170, y: sketchOrigin.y + 60 }]) await clickMap(page, point);
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === 3, '应画到一半');
  const postsBefore = savePosts.length;
  await page.getByRole('button', { name: '保存新版本' }).click();
  await page.waitForTimeout(600);
  messages = await toasts(page);
  check(messages.some((text) => text.includes('有正在绘制、还没完成的面')), `未完成绘制时保存应被拦截并指引: ${JSON.stringify(messages)}`);
  check(savePosts.length === postsBefore, '未完成绘制时不得发出保存请求');
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === 3, '拦截后草稿原样保留');
  await shot(page, 'v08d-sketch-save-blocked');
  await focusCanvas(page);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  check((await page.evaluate(() => window.__zc.sketchPlaced())) === -1, 'Esc 应取消绘制');
  check((await page.evaluate(() => window.__zc.count())) === countBeforeSketch + 1, '取消的草稿不留下要素');
  await discardAll(page);
  check((await page.evaluate(() => window.__zc.count())) === countBeforeSketch && /当前版本已保存/.test(await stateText(page)), '放弃后应回到已保存状态');
  record('绘制中 Backspace / Ctrl+Z 退回上一个点，撤销过的草稿仍可完成；未完成时保存被拦截；Esc 取消绘制', true);

  // ---------- 删除整个图斑（Delete 键） ----------
  const victim = await take(() => true, '删除');
  const countBeforeDelete = await page.evaluate(() => window.__zc.count());
  await selectFeature(page, victim);
  await waitToastsCleared(page);
  await focusCanvas(page);
  await page.keyboard.press('Delete');
  await page.waitForTimeout(500);
  check((await page.evaluate(() => window.__zc.count())) === countBeforeDelete - 1, '按 Delete 删除整个图斑后图斑数应减 1');
  outcome = await save(page, '删图斑');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `保存失败: ${JSON.stringify(outcome)}`);
  api = await apiState(context);
  check(api.features.length === countBeforeDelete - 1 && !api.features.some((feature) => feature.fid === victim.feature.fid), '接口应看到该图斑已删除');
  expectedRevision += 1;
  record('选中后按 Delete 删除整个图斑并保存', true, { revision: api.revision, features: api.features.length });
  await shot(page, 'v09-after-delete-saved');

  // ---------- 撤销 / 重做 / 放弃 ----------
  const cleanCount = await page.evaluate(() => window.__zc.count());
  await drawPolygon(page, [await pointAt(0.40, 0.70), await pointAt(0.46, 0.70), await pointAt(0.46, 0.76), await pointAt(0.40, 0.76)]);
  check((await page.evaluate(() => window.__zc.count())) === cleanCount + 1, '画面后应 +1');
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await page.waitForTimeout(400);
  check((await page.evaluate(() => window.__zc.count())) === cleanCount, '撤销后应回到原数');
  await page.getByRole('button', { name: '重做', exact: true }).click();
  await page.waitForTimeout(400);
  check((await page.evaluate(() => window.__zc.count())) === cleanCount + 1, '重做后应再次 +1');
  await discardAll(page);
  check((await page.evaluate(() => window.__zc.count())) === cleanCount && /当前版本已保存/.test(await stateText(page)), '放弃后应回到已保存状态');
  record('撤销 / 重做 / 放弃全部修改', true, { features: cleanCount });

  // ---------- 越界：ROI 外的面保存必须被拒 ----------
  await wheelZoom(page, await page.evaluate(() => window.__zc.pointAt(0.5, 0.5)), 7);
  let outside = null;
  for (const [fx, fy] of [[0.06, 0.06], [0.94, 0.06], [0.06, 0.94], [0.94, 0.94], [0.03, 0.5], [0.97, 0.5], [0.5, 0.03], [0.5, 0.97]]) {
    const lngLat = await page.evaluate(([x, y]) => window.__zc.lngLatAt(x, y), [fx, fy]);
    if (!pointInPolygon(lngLat[0], lngLat[1], api.roi)) { outside = { fx, fy }; break; }
  }
  check(Boolean(outside), '缩小视野后找不到 ROI 外的位置');
  const dx = outside.fx < 0.5 ? 0.05 : -0.05; const dy = outside.fy < 0.5 ? 0.05 : -0.05;
  await drawPolygon(page, [
    await page.evaluate(([x, y]) => window.__zc.pointAt(x, y), [outside.fx, outside.fy]),
    await page.evaluate(([x, y]) => window.__zc.pointAt(x, y), [outside.fx + dx, outside.fy]),
    await page.evaluate(([x, y]) => window.__zc.pointAt(x, y), [outside.fx + dx, outside.fy + dy]),
    await page.evaluate(([x, y]) => window.__zc.pointAt(x, y), [outside.fx, outside.fy + dy]),
  ]);
  await shot(page, 'v10-outside-roi-unsaved');
  outcome = await save(page, '越界面');
  check(outcome.http === 422, `越界面保存应 422: ${JSON.stringify(outcome)}`);
  check(outcome.toasts.some((text) => /无法保存：第 \d+ 个面/.test(text) && text.includes('ROI')), `422 提示应指明第几个面与原因: ${JSON.stringify(outcome.toasts)}`);
  api = await apiState(context);
  check(api.revision === expectedRevision, '越界面不得出版本');
  check(/存在未保存编辑/.test(await stateText(page)), '被拒后编辑应保留');
  await shot(page, 'v11-outside-roi-rejected');
  record('ROI 外的面保存被拒（422，指明第几个面），不出版本、编辑保留', true, { details: outcome.details, toasts: outcome.toasts });
  await discardAll(page);
  await restoreView(page);

  // ---------- 并发：两个标签页各自保存（目标：还没被用过的任意两个图斑） ----------
  const editorUrl = page.url();
  const other = await context.newPage();
  watch(other, 'editor-B');
  await other.goto(editorUrl, { waitUntil: 'domcontentloaded' });
  await waitEditorReady(other);
  const targetA = await take(() => true, '并发测试改类');
  // 改类必须真的产生修改：图斑本来就是"道路"时改到"建筑"（同类的第二次赋值不产生修改，保存按钮是禁用的）
  const classForA = targetA.feature.cls === 3 ? 2 : 3;
  await selectFeature(page, targetA);
  await page.locator('.class-option', { hasText: CLASS_NAMES[classForA] }).first().click();
  await page.waitForTimeout(300);
  outcome = await save(page, '标签页 A');
  check(outcome.http === 200 && outcome.revision === expectedRevision + 1, `A 保存失败: ${JSON.stringify(outcome)}`);
  expectedRevision += 1;
  // B 标签页里挑一个不同于 A 的图斑
  const targetB = await findSelectable(other, (feature) => String(feature.fid) !== String(targetA.feature.fid), { allowZoom: false });
  check(Boolean(targetB), '并发测试找不到第二个可点选的图斑');
  const classBeforeB = targetB.feature.cls;
  const classForB = classBeforeB === 3 ? 2 : 3;
  await selectFeature(other, targetB);
  await other.locator('.class-option', { hasText: CLASS_NAMES[classForB] }).first().click();
  await other.waitForTimeout(300);
  const [responseB] = await Promise.all([
    other.waitForResponse((res) => res.url().includes(`/classification-results/${RESULT}/revisions`) && res.request().method() === 'POST', { timeout: 60000 }),
    other.getByRole('button', { name: '保存新版本' }).click(),
  ]);
  const bodyB = await responseB.json().catch(() => ({}));
  await other.waitForTimeout(900);
  const toastsB = await toasts(other);
  await shot(other, 'v12-conflict-in-stale-tab');
  check(responseB.status() === 409, `过期标签页保存应 409，实际 ${responseB.status()} ${JSON.stringify(bodyB).slice(0, 200)}`);
  check(toastsB.some((text) => text.includes('已有新版本') || text.includes('不会自动覆盖')), `应有冲突提示: ${JSON.stringify(toastsB)}`);
  api = await apiState(context);
  const featureB = api.features.find((feature) => feature.fid === targetB.feature.fid);
  check(api.revision === expectedRevision && featureB && featureB.cls === classBeforeB, 'B 的改动不得落库');
  check(/存在未保存编辑/.test(await stateText(other)), '冲突后 B 的编辑应保留');
  await other.reload({ waitUntil: 'domcontentloaded' });
  await waitEditorReady(other);
  const reloadedB = (await other.evaluate(() => window.__zc.features())).find((feature) => String(feature.fid) === String(targetA.feature.fid));
  check(reloadedB && reloadedB.cls === classForA, 'B 重新加载后应看到 A 的改动');
  record('并发保存：过期标签页被拒（409）、不覆盖、编辑保留，重新加载后看到对方改动', true, { revision: api.revision, httpB: responseB.status(), toastsB });
  await other.close();

  // ---------- 未保存时刷新：离开确认 ----------
  const dialogs = [];
  page.on('dialog', async (dialog) => { dialogs.push(`${dialog.type()}: ${dialog.message().slice(0, 60)}`); await dialog.dismiss(); });
  await drawPolygon(page, [await pointAt(0.52, 0.74), await pointAt(0.58, 0.74), await pointAt(0.58, 0.80), await pointAt(0.52, 0.80)]);
  await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForTimeout(1500);
  check(dialogs.some((text) => text.startsWith('beforeunload')), `未保存时刷新应弹出离开确认: ${JSON.stringify(dialogs)}`);
  check(/存在未保存编辑/.test(await stateText(page)), '取消离开后编辑应保留');
  record('未保存时刷新会先确认，取消后编辑保留', true, { dialogs });
  await discardAll(page);
  await installHelpers(page);

  // ---------- 导出已保存版本 ----------
  api = await apiState(context);
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 60000 }),
    page.locator('.toolbar-actions button', { hasText: '导出已保存 GeoJSON' }).click(),
  ]);
  const target = path.join(OUT, `export-${RESULT}-${download.suggestedFilename()}`);
  await download.saveAs(target);
  const exported = JSON.parse(fs.readFileSync(target, 'utf8'));
  check(Array.isArray(exported.features) && exported.features.length === api.features.length, `导出的图斑数 ${exported.features && exported.features.length} 应等于当前 ${api.features.length}`);
  check(exported.features.some((feature) => feature.properties.feature_id === targetA.feature.fid && feature.properties.class_code === classForA), '导出内容应包含编辑后的改动');
  check(exported.features.every((feature) => feature.properties.source === 'manual' && feature.properties.revision_no === api.revision), '导出的每个要素都应是人工修订、版本号等于当前版本');
  record('导出已保存 GeoJSON 与当前版本一致', true, { file: download.suggestedFilename(), features: exported.features.length });

  // ---------- 完整性：没有被本测试编辑过的图斑，几何与地类都必须与初始版本逐一相同 ----------
  api = await apiState(context);
  const initialByFid = new Map(initial.features.map((feature) => [String(feature.fid), feature]));
  const finalByFid = new Map(api.features.map((feature) => [String(feature.fid), feature]));
  const collateral = [];
  for (const [fid, beforeFeature] of initialByFid) {
    if (touched.has(fid)) continue;
    const afterFeature = finalByFid.get(fid);
    if (!afterFeature) collateral.push(`${fid}: 不见了`);
    else if (afterFeature.cls !== beforeFeature.cls || afterFeature.shape !== beforeFeature.shape) collateral.push(`${fid}: 被改动（顶点 ${beforeFeature.vertices} → ${afterFeature.vertices}）`);
  }
  for (const fid of finalByFid.keys()) if (!initialByFid.has(fid) && !touched.has(fid)) collateral.push(`${fid}: 多出来的面`);
  check(collateral.length === 0, `未被编辑的图斑发生了变化: ${JSON.stringify(collateral)}`);
  const untouched = [...initialByFid.keys()].filter((fid) => !touched.has(fid)).length;
  record('未被编辑的图斑与初始版本逐一相同（没有连带改动）', true, { compared: untouched, touched: [...touched.values()] });
  await shot(page, 'v13-final-editor');

  // ---------- 被取代的成果：真实数据上的告警 ----------
  const older = items.filter((item) => item.mine_fid === FID && item.year === year && item.result_id !== RESULT && item.vector_status !== 'vector_failed').sort((a, b) => b.result_id - a.result_id)[0];
  check(Boolean(older) || process.env.ZC_ALLOW_NO_SUPERSEDED === '1', '找不到同一矿山同年的旧成果（无法验证"已被取代"告警）');
  if (!older) {
    record('同一矿山同年没有旧成果，"已被取代"告警一步不适用', true, null);
    await page.close();
  } else {
  await page.goto(editorUrl.replace(`result_id=${RESULT}`, `result_id=${older.result_id}`), { waitUntil: 'domcontentloaded' });
  await waitEditorReady(page);
  const alertText = await page.locator('.editor-alert', { hasText: '已被同矿山同年更新的成果取代' }).innerText().catch(() => '');
  check(/不会体现在概览、台账、变化矩阵与溯源中/.test(alertText), `旧成果 ${older.result_id} 应显示"已被取代"告警，实际：${alertText}`);
  check(await page.getByRole('button', { name: '保存新版本' }).count() === 1, '告警不得挤掉工具栏');
  await shot(page, 'v14-superseded-result-in-editor');
  record('被取代的旧成果在编辑器里有常驻告警', true, { result: older.result_id });
  }

  // ---------- Miner 侧：智能复核入口 + 图斑编辑清单的标记 ----------
  await miner.reload({ waitUntil: 'domcontentloaded' });
  await miner.locator('#interpretation-project').waitFor({ timeout: 30000 });
  await miner.selectOption('#interpretation-project', String(PROJECT));
  const rowAfter = miner.locator('.record-row', { hasText: `矿山 ${FID} · ${year}` }).first();
  await rowAfter.waitFor({ timeout: 30000 });
  await rowAfter.locator('.edit-btn', { hasText: '智能复核' }).click();
  await miner.waitForTimeout(1800);
  await shot(miner, 'v15-miner-smart-review');
  record('Miner 智能复核入口可打开', true, null);

  await miner.goto(`${MINER}/#/editing`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  await miner.locator('#editing-project').waitFor({ timeout: 15000 }).catch(() => {});
  if (await miner.locator('#editing-project').count()) {
    await miner.selectOption('#editing-project', String(PROJECT));
    const editRows = miner.locator('.result-row', { hasText: `矿山 ${FID} · ${year}` });
    await editRows.first().waitFor({ timeout: 20000 });
    await miner.waitForTimeout(600);
    const editRowCount = await editRows.count();
    const editTagged = await editRows.locator('.superseded-tag').count();
    const expectedRows = items.filter((item) => item.mine_fid === FID && item.year === year).length;
    check(editRowCount === expectedRows && editRowCount >= 1 && editTagged === editRowCount - 1, `图斑编辑清单里矿山 ${FID} · ${year} 应只有最新一条不带标记（共 ${editRowCount} 行，带标记 ${editTagged} 行，接口 ${expectedRows} 条）`);
    const firstRowTag = await editRows.first().locator('.superseded-tag').count();
    check(firstRowTag === 0, '清单第一行（最新成果）不应带"已被取代"标记');
    await shot(miner, 'v16-miner-editing-list-superseded-tags');
    record('Miner 图斑编辑清单标出"已被取代"的成果，最新一条不带标记', true, { rows: editRowCount, tagged: editTagged });
  } else {
    record('Miner 图斑编辑清单页未找到（#/editing 路由不可用），未验证清单标记', false);
  }

  if (!page.isClosed()) await page.close();
  await miner.close();
  record('保存请求耗时', true, { saves: saveTimings.map((timing) => `${timing.label} ${timing.ms}ms/${timing.requestBytes > 1024 ? Math.round(timing.requestBytes / 1024) + 'KB' : timing.requestBytes + 'B'}`), maxMs: Math.max(...saveTimings.map((timing) => timing.ms)) });

  fs.writeFileSync(path.join(OUT, `result-${RESULT}.json`), JSON.stringify({ ok: true, projectId: PROJECT, mineFid: FID, resultId: RESULT, finalRevision: expectedRevision, finalFeatureCount: api.features.length, steps, problems, saveTimings }, null, 2));
  console.log(`\n通过 ${steps.length} 步；最终版本 V${expectedRevision}（${api.features.length} 个图斑）；问题 ${problems.length} 条；截图在 ${OUT}`);
  await browser.close();
}

main().catch(async (error) => {
  console.error('FAILED', String(error.stack || error).slice(0, 1600));
  if (editorRef) await editorRef.screenshot({ path: path.join(OUT, '99-failure.png') }).catch(() => {});
  fs.writeFileSync(path.join(OUT, `result-${RESULT}.json`), JSON.stringify({ ok: false, steps, problems, error: String(error) }, null, 2));
  if (browserRef) await browserRef.close().catch(() => {});
  process.exit(1);
});
