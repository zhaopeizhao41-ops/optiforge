import test from 'node:test';
import assert from 'node:assert/strict';
import { isOptionEnabled, nextEnabledIndex, resolveActiveIndex } from '../src/surface-picker-options.js';

const option = (value, extra = {}) => ({ value, label: `选项 ${value}`, ...extra });
const freeze = value => {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};

test('only an explicit boolean true disables an existing option', () => {
  const options = [option('omitted'), option('false', { disabled: false }), option('true', { disabled: true }),
    option('string', { disabled: 'true' }), option('one', { disabled: 1 }), option('null', { disabled: null })];
  assert.deepEqual(options.map((_, index) => isOptionEnabled(options, index)), [true, true, false, true, true, true]);
  for (const index of [-1, options.length, 0.5, NaN, Infinity, undefined, null, '0']) {
    assert.equal(isOptionEnabled(options, index), false);
  }
});

test('forward and reverse navigation skip disabled head, middle and tail and wrap in source order', () => {
  const options = [option('head', { disabled: true }), option('a'), option('middle', { disabled: true }),
    option('b'), option('c'), option('tail', { disabled: true })];
  assert.equal(nextEnabledIndex(options, -1, 1), 1);
  assert.equal(nextEnabledIndex(options, -1, -1), 4);
  assert.equal(nextEnabledIndex(options, 1, 1), 3);
  assert.equal(nextEnabledIndex(options, 3, 1), 4);
  assert.equal(nextEnabledIndex(options, 4, 1), 1);
  assert.equal(nextEnabledIndex(options, 1, -1), 4);
  assert.equal(nextEnabledIndex(options, 4, -1), 3);
  assert.equal(nextEnabledIndex(options, 3, -1), 1);
  assert.equal(nextEnabledIndex(options, 2, 1), 3);
  assert.equal(nextEnabledIndex(options, 2, -1), 1);
});

test('invalid navigation starts resolve to the first or last enabled option without coercion', () => {
  const options = [option('head', { disabled: true }), option('a'), option('b'), option('tail', { disabled: true })];
  for (const index of [-8, 4, 300, 0.5, NaN, Infinity, null, undefined, '1']) {
    assert.equal(nextEnabledIndex(options, index, 1), 1);
    assert.equal(nextEnabledIndex(options, index, -1), 2);
  }
  for (const direction of [0, 2, -2, NaN, null, undefined, '1']) {
    assert.equal(nextEnabledIndex(options, 1, direction), -1);
  }
});

test('one enabled option can wrap to itself while empty or all-disabled lists have no active index', () => {
  const single = [option('no', { disabled: true }), option('yes'), option('also-no', { disabled: true })];
  for (const direction of [-1, 1]) {
    assert.equal(nextEnabledIndex(single, 1, direction), 1);
    assert.equal(nextEnabledIndex([], -1, direction), -1);
    assert.equal(nextEnabledIndex([option('off', { disabled: true })], -1, direction), -1);
  }
  assert.equal(resolveActiveIndex([], null), -1);
  assert.equal(resolveActiveIndex([option('off', { disabled: true })], { value: 'off' }), -1);
});

test('active identity uses the original value and type, including zero and the empty string', () => {
  const options = [option('first'), option(0), option('0'), option(''), option(false)];
  assert.equal(resolveActiveIndex(options, { value: 0 }), 1);
  assert.equal(resolveActiveIndex(options, { value: '0' }), 2);
  assert.equal(resolveActiveIndex(options, { value: '' }), 3);
  assert.equal(resolveActiveIndex(options, { value: false }), 4);
  assert.equal(resolveActiveIndex(options, null), 0);
  assert.equal(resolveActiveIndex(options), 0);
});

test('reordering preserves the active value while removal or disabling resolves a current fallback', () => {
  const a = option('a'), b = option('b'), c = option('c');
  assert.equal(resolveActiveIndex([a, b, c], { value: 'b' }), 1);
  assert.equal(resolveActiveIndex([c, a, b], { value: 'b' }), 2);
  assert.equal(resolveActiveIndex([c, a], { value: 'b' }), 0);
  assert.equal(resolveActiveIndex([c, a, { ...b, disabled: true }], { value: 'b' }), 0);
  assert.equal(resolveActiveIndex([{ ...c, disabled: true }, a, { ...b, disabled: true }], { value: 'b' }), 1);
});

test('a resolved fallback can retain its identity when the previous disabled value becomes enabled', () => {
  const options = [option('first'), option('old', { disabled: true }), option('last')];
  const index = resolveActiveIndex(options, { value: 'old' });
  const canonicalChoice = { value: options[index].value };
  assert.equal(canonicalChoice.value, 'first');
  assert.equal(resolveActiveIndex([option('first'), option('old'), option('last')], canonicalChoice), 0);
});

test('all-enabled legacy options keep existing directional ordering and selected item resolution', () => {
  const options = [option(1), option(2), option(3)];
  assert.equal(resolveActiveIndex(options, { value: 2 }), 1);
  for (let index = 0; index < options.length; index += 1) {
    assert.equal(nextEnabledIndex(options, index, 1), (index + 1) % options.length);
    assert.equal(nextEnabledIndex(options, index, -1), (index - 1 + options.length) % options.length);
  }
});

test('navigation, reconciliation and capability checks do not mutate frozen options or choice', () => {
  const options = freeze([option(''), option(0, { disabled: false }), option('blocked', { disabled: true })]);
  const choice = freeze({ value: 0 }), before = JSON.stringify({ options, choice });
  for (const direction of [-1, 1]) {
    for (let index = -1; index <= options.length; index += 1) nextEnabledIndex(options, index, direction);
  }
  options.forEach((_, index) => isOptionEnabled(options, index));
  assert.equal(resolveActiveIndex(options, choice), 1);
  assert.equal(JSON.stringify({ options, choice }), before);
});

test('navigation has a bounded pass even when a large list has no enabled option', () => {
  const options = Array.from({ length: 1000 }, (_, index) => option(index, { disabled: true }));
  assert.equal(nextEnabledIndex(options, 500, 1), -1);
  assert.equal(nextEnabledIndex(options, 500, -1), -1);
});
