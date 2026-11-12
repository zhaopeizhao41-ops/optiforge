export function isOptionEnabled(options, index) {
  return Number.isInteger(index) && index >= 0 && index < options.length
    && Boolean(options[index]) && options[index].disabled !== true;
}

export function nextEnabledIndex(options, fromIndex, direction) {
  if (direction !== 1 && direction !== -1) return -1;
  const length = options.length;
  const start = Number.isInteger(fromIndex) && fromIndex >= 0 && fromIndex < length
    ? fromIndex : direction === 1 ? -1 : length;
  for (let step = 1; step <= length; step += 1) {
    const index = (start + direction * step + length) % length;
    if (isOptionEnabled(options, index)) return index;
  }
  return -1;
}

// A wrapper distinguishes no active choice from legitimate values such as '' or 0.
export function resolveActiveIndex(options, activeChoice = null) {
  const index = activeChoice === null ? -1
    : options.findIndex(option => option && option.value === activeChoice.value);
  return isOptionEnabled(options, index) ? index : nextEnabledIndex(options, -1, 1);
}
