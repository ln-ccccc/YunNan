import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildProjectRoute,
  buildProjectInferenceCards,
  buildInterpretationHistoryCards,
  groupUploadSourcesByTiff,
  readProjectId,
} from '../src/utils/interpretationContext.mjs';

test('readProjectId accepts only a positive safe integer', () => {
  assert.equal(readProjectId('?project_id=7'), 7);
  assert.equal(readProjectId('?project_id=0'), null);
  assert.equal(readProjectId('?project_id=dali'), null);
  assert.equal(readProjectId(''), null);
});

test('buildProjectRoute carries only a valid project id between tools', () => {
  assert.deepEqual(buildProjectRoute('/spectralindices', 7), {
    path: '/spectralindices',
    query: { project_id: '7' },
  });
  assert.deepEqual(buildProjectRoute('/segmentation', null), {
    path: '/segmentation',
    query: {},
  });
});

test('groupUploadSourcesByTiff keeps slices attached to their source TIFF', () => {
  const groups = groupUploadSourcesByTiff([
    { src: '/slice-a-1.png', raw_tiff_path: '/uploads/a.tif' },
    { src: '/slice-b-1.png', raw_tiff_path: '/uploads/b.tif' },
    { src: '/slice-a-2.png', raw_tiff_path: '/uploads/a.tif' },
  ]);

  assert.deepEqual([...groups.entries()], [
    ['/uploads/a.tif', ['/slice-a-1.png', '/slice-a-2.png']],
    ['/uploads/b.tif', ['/slice-b-1.png']],
  ]);
});

test('buildProjectInferenceCards uses backend result URLs', () => {
  const cards = buildProjectInferenceCards(
    [{
      fid: 101,
      year: 2022,
      before_img: '/api/projects/7/outputs/inference/101/101+2022_src.png',
      after_img: '/api/projects/7/outputs/inference/101/101+2022.png',
    }],
    'http://127.0.0.1:5008/',
  );

  assert.equal(cards[0].record_id, '101|2022');
  assert.equal(
    cards[0].after_img,
    'http://127.0.0.1:5008/api/projects/7/outputs/inference/101/101+2022.png',
  );
  assert.equal(
    cards[0].before_img,
    'http://127.0.0.1:5008/api/projects/7/outputs/inference/101/101+2022_src.png',
  );
});

test('buildInterpretationHistoryCards keeps standalone classifications visible', () => {
  const cards = buildInterpretationHistoryCards(
    [],
    [{ id: 8, before_img: '/uploads/source.png', after_img: '/generated/result.png' }],
    'http://127.0.0.1:5008/',
  );

  assert.equal(cards.length, 1);
  assert.equal(cards[0].record_source, 'standalone');
  assert.equal(cards[0].after_img, 'http://127.0.0.1:5008/generated/result.png');
});

test('buildInterpretationHistoryCards distinguishes project and legacy flash records', () => {
  const cards = buildInterpretationHistoryCards(
    [
      { id: 1, data: { mode: 'project' }, after_img: '/project.png' },
      { id: 2, data: { mode: 'flash' }, after_img: '/flash.png' },
    ],
    [],
    'http://127.0.0.1:5008/',
  );

  assert.equal(cards[0].record_source, 'project');
  assert.equal(cards[1].record_source, 'flash');
});
