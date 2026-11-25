// 已部署方案 A 的逐图斑计时：turf 不保拓扑 Douglas-Peucker + 采样式接受判定 + 降容差重试（÷4、÷16）。
// 结论与几何沿用 before-verdicts.ndjson（同一套函数、同一份数据），这里只补每个图斑的耗时。
// 用法: node a-timing.mjs <production-results.ndjson> <a-timing.ndjson>
import fs from 'node:fs';
import readline from 'node:readline';
import simplify from 'file:///D:/项目/YunNan/frontend/node_modules/@turf/simplify/dist/esm/index.js';
import {
  roiViolation,
  simplifyPolygonSafely,
} from 'file:///D:/项目/YunNan/frontend/src/utils/classificationEditorGeometry.mjs';

const [input, output] = process.argv.slice(2);
const lines = readline.createInterface({ input: fs.createReadStream(input, { encoding: 'utf8' }), crlfDelay: Infinity });
const out = fs.createWriteStream(output, { encoding: 'utf8' });
let features = 0;
const started = Date.now();
for await (const line of lines) {
  if (!line.startsWith('{"result_id"')) continue;
  const result = JSON.parse(line);
  for (const item of result.features) {
    const t0 = performance.now();
    simplifyPolygonSafely(item.geometry, {
      tolerance: 0.00001,
      simplify: (geometry, tolerance) => simplify({ type: 'Feature', properties: {}, geometry }, { tolerance, highQuality: false }).geometry,
      accept: (candidate) => roiViolation(candidate, result.roi) === null,
    });
    const ms = performance.now() - t0;
    features += 1;
    out.write(`${JSON.stringify({ r: result.result_id, f: item.fid, ms: Math.round(ms * 1000) / 1000 })}\n`);
  }
}
await new Promise((resolve) => out.end(resolve));
console.log(JSON.stringify({ features, wallMs: Date.now() - started }));
