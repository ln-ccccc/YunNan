import assert from 'node:assert/strict';
import test from 'node:test';

import { formatInferenceFailure } from '../src/utils/inferenceFailureText.js';

test('formatInferenceFailure 无 details 时原样返回基础文案', () => {
  const text = formatInferenceFailure({ response: { data: { msg: '数据集不存在或不属于当前项目' } } });
  assert.equal(text, '数据集不存在或不属于当前项目');
  const fallback = formatInferenceFailure(new Error('网络中断'));
  assert.equal(fallback, '网络中断');
});

test('formatInferenceFailure 门槛拒绝携带缺项中文与瓦片缺失原因', () => {
  const error = {
    response: {
      data: {
        msg: '项目当前不满足启动推理条件',
        data: {
          details: {
            failed_checks: [
              { code: 'ACTIVE_BASEMAP', reason_code: 'ACTIVE_BASEMAP_TILES_MISSING' },
              { code: 'PROJECT_PROFILE', reason_code: 'PROJECT_PROFILE_INCOMPLETE' },
            ],
          },
        },
      },
    },
  };
  const text = formatInferenceFailure(error);
  assert.match(text, /底图未激活（激活底图的瓦片文件缺失/);
  assert.match(text, /项目档案未填完整/);
  assert.match(text, /请先完成上述准备后再启动推理/);
});

test('formatInferenceFailure 非门槛类消息不追加缺项后缀', () => {
  const error = {
    response: {
      data: {
        msg: '别的错误',
        data: { details: { failed_checks: [{ code: 'ACTIVE_BASEMAP' }] } },
      },
    },
  };
  assert.equal(formatInferenceFailure(error), '别的错误');
});
