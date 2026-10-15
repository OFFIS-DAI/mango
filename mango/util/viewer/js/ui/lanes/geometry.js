import { clamp } from "../../core/util.js";
import { ABSOLUTE, UNIT_IX, WORLD, bandTime, timeBits, timeHTML, timeParts } from "../../model.js";
import { state, touch } from "../../state.js";
import { isPhone } from "../dom.js";
import { lanesBox, recBox } from "../elements.js";
import { layout } from "./layout.js";

export const gutterWidth = () => (isPhone() ? [52, 60, 88, 112] : [64, 72, 100, 124])[UNIT_IX] + (ABSOLUTE ? 0 : 6);

export let geom = null;

export function updateGeometry() {
  geom = geometry();
}

const measureCtx = document.createElement("canvas").getContext("2d");

export const textW = (text, font) => {
  if (!measureCtx) return text.length * 6.6;
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
};

export const fontVar = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim() || "monospace";

function gutterFit(phone) {
  const base = gutterWidth();
  const ck = phone + ":" + charW;
  if (!layout || !layout.bandKey.length) return { gw: base, elide: false };
  if (layout.gutter && layout.gutter.ck === ck) return layout.gutter;
  const mono = "11px " + fontVar("--font-data");
  const ui = "10.5px " + fontVar("--font-ui");
  let full = 0;
  let short = 0;
  for (let b = 0; b < layout.bandKey.length; b++) {
    const t = bandTime(layout.bandKey[b]);
    const x = timeBits(t, b ? bandTime(layout.bandKey[b - 1]) : null);
    const dw = x.date ? textW(x.date, ui) + 5 : 0;
    full = Math.max(full, dw + textW(x.same + x.rest, mono));
    if (b) short = Math.max(short, dw + textW((x.same ? "…" : "") + x.rest, mono));
  }
  const pad = 22;
  const elide = phone && full + pad > base;
  const gw = Math.ceil(Math.max(base, (elide ? short : full) + pad));
  layout.gutter = { ck, gw, elide, first: elide ? tailLabel(bandTime(layout.bandKey[0]), gw - pad, mono) : null };
  return layout.gutter;
}

function tailLabel(t, room, mono) {
  const parts = timeParts(t);
  const n = parts.length;
  while (parts.length > 1 && textW(parts.join(""), mono) > room) parts.shift();
  return (parts.length < n ? `<span class="m">…</span>` : "") + parts.join("");
}

export const lonelyLabel = t =>
  geom && geom.elide ? tailLabel(t, geom.gw - 22, "11px " + fontVar("--font-data")) : timeHTML(t, null);

function geometry() {
  const lanes = state.lanes || [];
  const phone = isPhone();
  const gf = gutterFit(phone);
  const gw = gf.gw;
  const ww = phone ? 40 : 56;
  const box = lanesBox.clientWidth || recBox.clientWidth || 800;
  const hasW = lanes.includes(WORLD);
  const nA = lanes.length - (hasW ? 1 : 0);
  const avail = Math.max(0, box - gw - (hasW ? ww : 0));
  // on a phone two lanes share the screen exactly, so revealing a record never scrolls the no-agent lane under the time column
  let W = 0;
  if (nA && state.fit) W = Math.max(16, avail / nA);
  else if (nA && phone) W = clamp(avail / Math.min(nA, 2), 100, 320);
  // lanes that would overflow the box by a little shrink to 120 px first, so no lane opens cut in half
  else if (nA) W = avail / nA >= 120 ? Math.min(320, avail / nA) : 148;
  W = Math.floor(W);
  const x = [];
  const w = [];
  const life = [];
  let cx = 0;
  for (const l of lanes) {
    const lw = l === WORLD ? ww : W;
    x.push(cx);
    w.push(lw);
    life.push(Math.round(l === WORLD || state.fit ? cx + lw / 2 : cx + 24));
    cx += lw;
  }
  const labels = !(state.fit && W < 100);
  return {
    gw,
    ww,
    x,
    w,
    life,
    contentW: cx,
    width: Math.max(cx, box - gw),
    W,
    labels,
    headH: labels ? 52 : 112,
    box,
    elide: gf.elide,
    first: gf.first,
  };
}

export let charW = 6.9;

function measureCW() {
  const s = document.createElement("span");
  s.style.cssText = "position:absolute;visibility:hidden;white-space:pre;font:11.5px var(--font-data)";
  s.textContent = "0".repeat(40);
  document.body.append(s);
  const w = s.getBoundingClientRect().width / 40;
  s.remove();
  return w || 6.9;
}

export function initGeometry() {
  charW = measureCW();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      const w = measureCW();
      if (Math.abs(w - charW) > 0.05) {
        charW = w;
        touch("fonts");
      }
    });
  }
}
