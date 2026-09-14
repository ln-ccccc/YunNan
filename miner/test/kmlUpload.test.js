import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { saveKmlUpload } from '../services/kmlUpload.js';

test('saveKmlUpload stores a valid kml file and returns the bare filename', () => {
  const uploadRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'kml-upload-'));
  const result = saveKmlUpload({
    uploadRoot,
    filename: '../新增矿山.kml',
    content: '<kml><Document></Document></kml>',
  });

  // kml_path 是受控目录内的裸文件名，不得回显服务器绝对路径
  assert.equal(result.kml_path, '新增矿山.kml');
  assert.equal(path.isAbsolute(result.kml_path), false);
  assert.equal(result.kml_path_absolute, path.join(uploadRoot, '新增矿山.kml'));
  assert.equal(fs.readFileSync(result.kml_path_absolute, 'utf-8'), '<kml><Document></Document></kml>');
});

test('saveKmlUpload rejects non-kml names and blank content', () => {
  const uploadRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'kml-upload-'));

  assert.throws(
    () => saveKmlUpload({ uploadRoot, filename: 'bad.txt', content: '<kml />' }),
    /只支持 \.kml 文件/,
  );
  assert.throws(
    () => saveKmlUpload({ uploadRoot, filename: 'bad.kml', content: '   ' }),
    /KML 内容不能为空/,
  );
});
