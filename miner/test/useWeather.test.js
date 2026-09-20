import assert from 'node:assert/strict';
import test from 'node:test';

import { isWeatherEnabled } from '../src/composables/useWeather.js';

test('isWeatherEnabled defaults to disabled when env is missing or unset', () => {
  assert.equal(isWeatherEnabled(undefined), false);
  assert.equal(isWeatherEnabled({}), false);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: '' }), false);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: 'false' }), false);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: '0' }), false);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: 'yes' }), false);
});

test('isWeatherEnabled only enables on explicit true or 1', () => {
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: 'true' }), true);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: '1' }), true);
  assert.equal(isWeatherEnabled({ VITE_ENABLE_WEATHER: ' TRUE ' }), true);
});
