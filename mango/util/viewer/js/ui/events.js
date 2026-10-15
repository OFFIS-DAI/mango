// Notifications between views that do not import each other: one view emits, the others listen.
//   "leaving-view"  the view is about to switch (popovers close, the scroll anchor is taken)
//   "rows"          the rows on screen changed (the map marks the connections they use)
//   "peek"          a record is under the pointer (record index, or null)
//   "peek-lane"     a lane header is under the pointer (lane, or null)
//   "map-hover"     an agent ({ n }) or connection ({ e }) of the map is under the pointer, or null
const listeners = new Map();

export function on(name, listener) {
  if (!listeners.has(name)) listeners.set(name, []);
  listeners.get(name).push(listener);
}

export function emit(name, ...args) {
  for (const listener of listeners.get(name) || []) listener(...args);
}
