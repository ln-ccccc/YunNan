// 回归基线（“改造前”）：用当前工作树里尚未替换的前端几何模块，对导出的全部生产图斑离线计算三项旧判定。
//   b2 = 已部署的采样式 ROI 检查（roiViolation === null）
//   b3 = 最初的 turf booleanWithin（无容差）
//   oldS = 已部署的简化流程结果：a=接受且有变化 / u=无变化 / s=跳过；接受的几何一并落盘供 GEOS 复核
// 只读输入，输出 NDJSON。用法: node before-verdicts.mjs <production-results.ndjson> <before-verdicts.ndjson>
import fs from 'node:fs';
import readline from 'node:readline';
import booleanWithin from 'file:///D:/项目/YunNan/frontend/node_modules/@turf/boolean-within/dist/esm/index.js';
import simplify from 'file:///D:/项目/YunNan/frontend/node_modules/@turf/simplify/dist/esm/index.js';
import {
  roiViolation,
  simplifyPolygonSafely,
} from 'file:///D:/项目/YunNan/frontend/src/utils/classificationEditorGeometry.mjs';

const [input, output] = process.argv.slice(2);
const lines = readline.createInterface({ input: fs.createReadStream(input, { encoding: 'utf8' }), crlfDelay: Infinity });
const out = fs.createWriteStream(output, { encoding: 'utf8' });
const summary = { results: 0, features: 0, b2Outside: 0, b3Outside: 0, oldS: { a: 0, u: 0, s: 0 }, ms: 0 };
const started = Date.now();
for await (const line of lines) {
  if (!line.startsWith('{"result_id"')) continue;
  const result = JSON.parse(line);
  summary.results += 1;
  for (const item of result.features) {
    const t0 = performance.now();
    let b2 = 1;
    try {
      b2 = roiViolation(item.geometry, result.roi) === null ? 1 : 0;
    } catch (_) {
      b2 = -1;
    }
    let b3 = 1;
    try {
      b3 = booleanWithin({ type: 'Feature', properties: {}, geometry: item.geometry }, { type: 'Feature', properties: {}, geometry: result.roi }) ? 1 : 0;
    } catch (_) {
      b3 = -1;
    }
    let oldS = 's';
    let oldGeometry;
    try {
      const outcome = simplifyPolygonSafely(item.geometry, {
        tolerance: 0.00001,
        simplify: (geometry, tolerance) => simplify({ type: 'Feature', properties: {}, geometry }, { tolerance, highQuality: false }).geometry,
        accept: (candidate) => roiViolation(candidate, result.roi) === null,
      });
      if (!outcome) {
        oldS = 's';
      } else if (JSON.stringify(outcome.geometry.coordinates) === JSON.stringify(item.geometry.coordinates)) {
        oldS = 'u';
      } else {
        oldS = 'a';
        oldGeometry = outcome.geometry;
      }
    } catch (_) {
      oldS = 's';
    }
    summary.features += 1;
    summary.b2Outside += b2 === 0 ? 1 : 0;
    summary.b3Outside += b3 === 0 ? 1 : 0;
    summary.oldS[oldS] += 1;
    summary.ms += performance.now() - t0;
    out.write(`${JSON.stringify({ r: result.result_id, f: item.fid, b2, b3, oldS, ...(oldGeometry ? { g: oldGeometry } : {}) })}\n`);
  }
}
await new Promise((resolve) => out.end(resolve));
console.log(JSON.stringify({ ...summary, wallMs: Date.now() - started }));
