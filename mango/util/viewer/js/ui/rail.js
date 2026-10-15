import { S_LOST } from "../core/schema.js";
import { clamp } from "../core/util.js";
import { FAILS, SENDS, rowOf, sendStatus } from "../model.js";
import { chain, descList } from "../selection.js";
import { state, touch } from "../state.js";
import { $ } from "./dom.js";
import { lanesBox, recBox } from "./elements.js";
import { layout } from "./lanes/layout.js";
import { visList, visPos } from "../visibility.js";

const rail = $("rail");

const railMarks = document.createElement("canvas");

let railColors = null;

// the canvas needs plain colours: a token holds light-dark(...), which only an element resolves
function readRailColors() {
  const probe = document.createElement("i");
  probe.hidden = true;
  rail.after(probe);
  const v = k => {
    probe.style.color = `var(${k})`;
    return getComputedStyle(probe).color;
  };
  railColors = {
    fail: v("--fail"),
    lost: v("--lost"),
    anc: v("--ancestor"),
    acc: v("--accent"),
    view: v("--rail-view"),
  };
  probe.remove();
}

function railPos(i) {
  if (state.view === "lanes") {
    if (!layout || !layout.maxRow) return -1;
    const r = rowOf[i] >= 0 ? rowOf[i] : -1;
    return r < 0 ? -1 : (r + 0.5) / layout.maxRow;
  }
  const p = visPos(i);
  return p < 0 ? -1 : (p + 0.5) / Math.max(1, visList.length);
}

export function drawRail() {
  if (rail.offsetParent === null) return;
  if (!railColors) readRailColors();
  const dpr = window.devicePixelRatio || 1;
  const h = rail.clientHeight;
  const w = 12;
  railMarks.width = w * dpr;
  railMarks.height = Math.max(1, h * dpr);
  const c = railMarks.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  const mark = (i, mw, mh, color, alpha) => {
    const p = railPos(i);
    if (p < 0) return;
    c.globalAlpha = alpha || 1;
    c.fillStyle = color;
    c.fillRect((w - mw) / 2, Math.round(p * h - mh / 2), mw, mh);
  };
  let n = 0;
  for (const d of descList) {
    mark(d, 5, 2, railColors.acc, 0.6);
    if (++n > 4000) break;
  }
  for (const a of chain) mark(a, 5, 2, railColors.anc);
  // --lost and --fail share a hue: lost marks are fainter and narrower, failures are drawn last, on top
  for (const s of SENDS) if (sendStatus[s] === S_LOST) mark(s, 4, 2, railColors.lost, 0.45);
  for (const f of FAILS) mark(f, 8, 2, railColors.fail);
  if (state.sel != null) mark(state.sel, 12, 3, railColors.acc);
  c.globalAlpha = 1;
  drawRailViewport();
}

export function drawRailViewport() {
  if (rail.offsetParent === null) return;
  if (!railColors) readRailColors();
  const dpr = window.devicePixelRatio || 1;
  const h = rail.clientHeight;
  if (rail.width !== 12 * dpr || rail.height !== Math.max(1, h * dpr)) {
    rail.width = 12 * dpr;
    rail.height = Math.max(1, h * dpr);
  }
  const c = rail.getContext("2d");
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.clearRect(0, 0, rail.width, rail.height);
  if (railMarks.width) c.drawImage(railMarks, 0, 0);
  const box = state.view === "lanes" ? lanesBox : recBox;
  const rows = state.view === "lanes" ? (layout ? layout.maxRow : 0) : visList.length;
  if (!rows) return;
  const sh = box.scrollHeight || 1;
  const y0 = (box.scrollTop / sh) * h;
  const y1 = ((box.scrollTop + box.clientHeight) / sh) * h;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = railColors.view;
  c.fillRect(0, y0, 12, Math.max(6, y1 - y0));
}

function railScroll(e) {
  const r = rail.getBoundingClientRect();
  const f = clamp((e.clientY - r.top) / r.height, 0, 1);
  const box = state.view === "lanes" ? lanesBox : recBox;
  box.scrollTop = f * box.scrollHeight - box.clientHeight / 2;
}

const themeWatch = matchMedia("(prefers-color-scheme: dark)");

const onTheme = () => {
  railColors = null;
  touch("theme");
};

export function initRail() {
  rail.addEventListener("pointerdown", e => {
    rail.setPointerCapture(e.pointerId);
    railScroll(e);
  });
  rail.addEventListener("pointermove", e => {
    if (rail.hasPointerCapture(e.pointerId)) railScroll(e);
  });
  themeWatch.addEventListener ? themeWatch.addEventListener("change", onTheme) : themeWatch.addListener(onTheme);
  new MutationObserver(onTheme).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
}
