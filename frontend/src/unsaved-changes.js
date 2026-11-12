/**
 * Browser beforeunload guard for unsaved requirement edits.
 *
 * This is best-effort: browsers decide whether to show a native prompt. It
 * never prevents in-app page switches, saves automatically, or creates tasks.
 */
export function beforeUnload(event) {
  event.preventDefault();
  event.returnValue = "";
  return "";
}

export function guardNeeded({ dirty = false, saving = false } = {}) {
  return Boolean(dirty || saving);
}

export function createUnsavedChangesGuard(target = globalThis.window) {
  let armed = false;
  const handler = (event) => beforeUnload(event);
  return {
    get armed() {
      return armed;
    },
    update(shouldArm) {
      const next = Boolean(shouldArm);
      if (!target || next === armed) return armed;
      if (next) target.addEventListener("beforeunload", handler);
      else target.removeEventListener("beforeunload", handler);
      armed = next;
      return armed;
    },
    dispose() {
      if (target && armed) target.removeEventListener("beforeunload", handler);
      armed = false;
    },
  };
}
