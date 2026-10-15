// What the viewer shows: the view, the filters, the selection and the followed lanes.
// Actions change it with update(); main.js redraws what depends on the changed keys in the next frame.
import { CATS } from "./core/schema.js";
import { nAgents, presentLevels } from "./model.js";
import { store } from "./ui/dom.js";

export const state = initialState();

function initialState() {
  const s = {
    view: "records",
    q: "",
    agent: "",
    cats: new Set(CATS),
    levels: new Set(presentLevels),
    onlyRelated: false,
    link: null,
    sel: null,
    cursor: null,
    lanes: null,
    order: "packed",
    fit: false,
    topo: { open: true, wide: false, mode: "graph", lossy: false },
  };
  // the categories, the map settings and the view are kept between visits
  try {
    const saved = JSON.parse(store.get("mango-trace-cats") || "null");
    if (Array.isArray(saved)) s.cats = new Set(saved.filter(c => CATS.includes(c)));
  } catch (e) {
    /* storage unavailable */
  }
  try {
    const t = JSON.parse(store.get("mango-trace-topo") || "null");
    if (t && typeof t === "object") {
      if (typeof t.open === "boolean") s.topo.open = t.open;
      if (typeof t.wide === "boolean") s.topo.wide = t.wide;
      if (t.mode === "list" || t.mode === "graph") s.topo.mode = t.mode;
    }
  } catch (e) {
    /* storage unavailable */
  }
  if (store.get("mango-trace-view") === "lanes" && nAgents) s.view = "lanes";
  return s;
}

let changed = new Set();
let frame = 0;
let render = () => {};

// main.js registers the function that redraws what depends on the changed keys
export function onRender(f) {
  render = f;
}

// assigns state fields; what depends on them is redrawn in the next frame
export function update(patch) {
  for (const key of Object.keys(patch)) {
    state[key] = patch[key];
    changed.add(key);
  }
  schedule();
}

// marks keys as changed without assigning them: causes from outside the state, such as
// "width" and "height" of the view, "fonts" (once loaded) and "theme" (light or dark)
export function touch(...keys) {
  for (const key of keys) changed.add(key);
  schedule();
}

function schedule() {
  if (!frame) frame = requestAnimationFrame(flush);
}

function flush() {
  frame = 0;
  const keys = changed;
  changed = new Set();
  render(keys);
}

export function flushNow() {
  if (frame) {
    cancelAnimationFrame(frame);
    frame = 0;
  }
  flush();
}

// one-shot requests that the next redraw carries out
export const pending = { reveal: null, anchor: null, focus: null, revealWindow: true, cursorAt: null, flashLane: -1 };
