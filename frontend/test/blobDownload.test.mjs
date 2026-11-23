import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { OBJECT_URL_RELEASE_DELAY_MS, saveBlobAsFile } from '../src/utils/blobDownload.mjs';

// saveBlobAsFile 只认浏览器全局（URL / document / setTimeout），在它自己的模块作用域里解析，
// 所以替身必须装到 globalThis 上，用完还原。
function installBrowserFakes() {
  const calls = { created: [], revoked: [], timers: [], anchors: [], appended: [] };
  const originals = {
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
    document: globalThis.document,
    setTimeout: globalThis.setTimeout,
  };
  URL.createObjectURL = (blob) => {
    const url = `blob:test/${calls.created.length}`;
    calls.created.push({ url, blob });
    return url;
  };
  URL.revokeObjectURL = (url) => calls.revoked.push(url);
  globalThis.document = {
    body: { appendChild: (node) => calls.appended.push(node) },
    createElement: (tag) => {
      const anchor = { tag, clicks: 0, remove: () => { anchor.removed = true; } };
      anchor.click = () => { anchor.clicks += 1; };
      calls.anchors.push(anchor);
      return anchor;
    },
  };
  globalThis.setTimeout = (callback, delay) => {
    calls.timers.push({ callback, delay });
    return calls.timers.length;
  };
  calls.restore = () => {
    URL.createObjectURL = originals.createObjectURL;
    URL.revokeObjectURL = originals.revokeObjectURL;
    globalThis.document = originals.document;
    globalThis.setTimeout = originals.setTimeout;
  };
  return calls;
}

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : sourceFiles(full);
    return /\.(?:vue|[cm]?js)$/.test(entry.name) ? [full] : [];
  });
}

test('saveBlobAsFile 发起下载后不立刻释放 object URL，60 秒后释放同一个', () => {
  const fakes = installBrowserFakes();
  try {
    const blob = new Blob(['{"type":"FeatureCollection","features":[]}']);
    saveBlobAsFile(blob, 'classification-result-142-draft-v0-unsaved.geojson');
    assert.equal(fakes.created.length, 1);
    assert.equal(fakes.created[0].blob, blob, 'URL 必须由调用方给的 blob 创建');
    const [anchor] = fakes.anchors;
    assert.equal(anchor.tag, 'a');
    assert.equal(anchor.href, fakes.created[0].url);
    assert.equal(anchor.download, 'classification-result-142-draft-v0-unsaved.geojson');
    assert.deepEqual(fakes.appended, [anchor], '锚点要先挂到页面上再点击（Firefox 不下载游离锚点）');
    assert.equal(anchor.clicks, 1, '点击一次即发起下载');
    assert.equal(anchor.removed, true, '点击后立即把锚点从页面上移除');
    assert.deepEqual(fakes.revoked, [], '同步释放会截断尚未开始的下载');
    assert.equal(fakes.timers.length, 1);
    assert.equal(fakes.timers[0].delay, 60000, '下载留给浏览器的接管时间为 60 秒');
    fakes.timers[0].callback();
    assert.deepEqual(fakes.revoked, [fakes.created[0].url], '到点释放的是本次创建的那个 URL');
    assert.equal(OBJECT_URL_RELEASE_DELAY_MS, 60000);
  } finally {
    fakes.restore();
  }
});

test('frontend/src 里下载用的 object URL 只经 saveBlobAsFile 创建和释放', () => {
  // 规则只留一处的另一半：入口不得再自己 createObjectURL / revokeObjectURL。
  // 预览图的 object URL（Segmentation.vue 的 this.fileimg）只用于 <img> 显示、不发下载，
  // 换图和离开页面时同步释放是正确的，不在此规则内。
  const root = fileURLToPath(new URL('../src/', import.meta.url));
  const helper = path.join(root, 'utils', 'blobDownload.mjs');
  const previewOnly = new Set(['views/mainfun/Segmentation.vue']);
  const offenders = [];
  for (const file of sourceFiles(root)) {
    const relative = path.relative(root, file).split(path.sep).join('/');
    if (file === helper || previewOnly.has(relative)) continue;
    fs.readFileSync(file, 'utf8').split(/\r?\n/).forEach((line, index) => {
      if (/createObjectURL|revokeObjectURL/.test(line)) {
        offenders.push(`${relative}:${index + 1} ${line.trim()}`);
      }
    });
  }
  assert.deepEqual(offenders, [], '下载入口必须走 saveBlobAsFile');
});
