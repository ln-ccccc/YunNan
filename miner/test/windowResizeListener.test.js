import test from 'node:test';
import assert from 'node:assert/strict';

import { createWindowResizeListener } from '../src/composables/windowResizeListener.js';

function createStubTarget() {
  const listeners = new Map();
  return {
    addEventListener(name, fn) {
      const named = listeners.get(name) || new Set();
      named.add(fn);
      listeners.set(name, named);
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
    emit(name) {
      for (const fn of listeners.get(name) || []) {
        fn({ type: name });
      }
    },
    count(name) {
      return (listeners.get(name) || new Set()).size;
    },
  };
}

test('attach delivers resize events to the handler exactly once per event', () => {
  const target = createStubTarget();
  const received = [];
  const listener = createWindowResizeListener((event) => received.push(event.type));

  listener.attach(target);
  target.emit('resize');
  target.emit('resize');

  assert.deepEqual(received, ['resize', 'resize']);
});

test('detach stops delivery and removes the listener from the target', () => {
  const target = createStubTarget();
  const received = [];
  const listener = createWindowResizeListener(() => received.push('resized'));

  listener.attach(target);
  listener.detach(target);
  target.emit('resize');

  assert.equal(received.length, 0);
  assert.equal(target.count('resize'), 0);
});

test('repeated mount and unmount cycles do not accumulate resize listeners', () => {
  const target = createStubTarget();

  for (let cycle = 1; cycle <= 3; cycle += 1) {
    const listener = createWindowResizeListener(() => {});
    listener.attach(target);
    assert.equal(target.count('resize'), 1, `cycle ${cycle}: exactly one live listener while mounted`);
    listener.detach(target);
    assert.equal(target.count('resize'), 0, `cycle ${cycle}: no listener left after unmount`);
  }

  assert.equal(target.count('resize'), 0);
});
