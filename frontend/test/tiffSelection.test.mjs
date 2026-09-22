import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createUploadItems,
  filesFromInputEvent,
  isValidTiff,
  normalizeSelectedItems,
} from '../src/utils/tiffSelection.mjs';

test('isValidTiff accepts all tif/tiff casings and rejects others', () => {
  assert.equal(isValidTiff({ name: 'a.tif' }), true);
  assert.equal(isValidTiff({ name: 'a.tiff' }), true);
  assert.equal(isValidTiff({ name: 'a.TIF' }), true);
  assert.equal(isValidTiff({ name: 'a.TIFF' }), true);
  assert.equal(isValidTiff({ name: 'a.jpg' }), false);
  assert.equal(isValidTiff({ name: 'a.png' }), false);
  assert.equal(isValidTiff({ name: 'noext' }), false);
  assert.equal(isValidTiff(undefined), false);
});

test('isValidTiff prefers relativePath so folder drops keep their full path', () => {
  // 文件夹拖拽：file.name 只是叶子名，relativePath 才是判型依据
  assert.equal(isValidTiff({ relativePath: 'dir/IMG.TIF', name: 'IMG.TIF' }), true);
  assert.equal(isValidTiff({ relativePath: 'dir/label.xml', name: 'IMG.TIF' }), false);
  // el-upload 条目 {raw} 与 DataTransfer 条目 {file} 两种包裹形态
  assert.equal(isValidTiff({ raw: { name: 'b.tiff' } }), true);
  assert.equal(isValidTiff({ file: { name: 'b.tiff' } }), true);
});

test('normalizeSelectedItems unwraps the three selection shapes', () => {
  const rawFile = { name: 'x.tif', webkitRelativePath: 'root/x.tif' };
  // 1) el-upload 形态 {raw}：原样透传
  assert.deepEqual(normalizeSelectedItems([{ raw: rawFile }]), [{ raw: rawFile }]);
  // 2) DataTransfer 形态 {file, relativePath}
  assert.deepEqual(
    normalizeSelectedItems([{ file: rawFile, relativePath: 'root/x.tif' }]),
    [{ raw: rawFile, relativePath: 'root/x.tif' }],
  );
  // 3) 裸 File：用 webkitRelativePath 兜底
  assert.deepEqual(
    normalizeSelectedItems([rawFile]),
    [{ raw: rawFile, relativePath: 'root/x.tif' }],
  );
  assert.deepEqual(normalizeSelectedItems([{ name: 'y.tif' }]), [{ raw: { name: 'y.tif' }, relativePath: 'y.tif' }]);
});

test('createUploadItems derives upload entries with stable uid and relative name', () => {
  const raw = { name: 'x.tif', lastModified: 123, size: 456, webkitRelativePath: '' };
  const [item] = createUploadItems([{ raw, relativePath: 'root/x.tif' }]);
  assert.equal(item.name, 'root/x.tif');
  assert.equal(item.size, 456);
  assert.equal(item.status, 'ready');
  assert.equal(item.uid, 'x.tif-123-0');
  assert.equal(item.raw, raw);
  // 无 relativePath 时退回 webkitRelativePath / name
  const [fallback] = createUploadItems([{ raw: { name: 'z.tif', lastModified: 1, size: 2, webkitRelativePath: 'w/z.tif' } }]);
  assert.equal(fallback.name, 'w/z.tif');
});

test('filesFromInputEvent tolerates missing target and files', () => {
  const fileA = { name: 'a.tif' };
  assert.deepEqual(filesFromInputEvent({ target: { files: [fileA] } }), [fileA]);
  assert.deepEqual(filesFromInputEvent({}), []);
  assert.deepEqual(filesFromInputEvent(null), []);
});
