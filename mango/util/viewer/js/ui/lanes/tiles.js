import { ROW_H, TILE_H, TILE_ROWS } from "../../core/lanes-layout.js";
import { CATS, E_CANCEL, E_FAIL } from "../../core/schema.js";
import { clamp, round1 } from "../../core/util.js";
import {
  bandTime,
  dur,
  failOf,
  hasWorldRecs,
  kindOf,
  levelNames,
  levelOf,
  nAgents,
  presentCats,
  time0,
  timeHTML,
  timeLabel,
} from "../../model.js";
import { rel } from "../../selection.js";
import { state } from "../../state.js";
import { $ } from "../dom.js";
import { gutterEl, lanesBox, lanesIn, plane } from "../elements.js";
import { emit } from "../events.js";
import { html } from "../html.js";
import { charW, fontVar, geom, lonelyLabel, textW } from "./geometry.js";
import {
  ariaOf,
  ghostLabel,
  glyphClass,
  labelGen,
  labelHTML,
  labelLim,
  labelStamp,
  labelW,
  remeasureLabels,
  rowEntries,
  textLen,
} from "./labels.js";
import { hovRec, layout, selShown } from "./layout.js";
import { KO_TOP, chunked, cxGeom, knockout, pathsOf, pill, rectP, setTileSpan } from "./paths.js";
import { thread, threadGeom } from "./thread.js";
import { filtersActive } from "../../visibility.js";

let mountedCols = null;

export const mounted = new Map();

function bandsSVG(r0, r1, width) {
  let st = "";
  let gp = "";
  let ru = "";
  let run = -1;
  for (let r = r0; r <= r1; r++) {
    const b = r < r1 ? layout.bandOfRow[r] : -2;
    const odd = b >= 0 && (b & 1) === 1;
    if (odd && run < 0) run = r;
    if (!odd && run >= 0) {
      st += rectP(0, (run - r0) * ROW_H, width, (r - run) * ROW_H);
      run = -1;
    }
    if (r < r1) {
      if (b === -1) gp += rectP(0, (r - r0) * ROW_H, width, ROW_H);
      if (r > 0 && (b === -1 || layout.bandRow[b] === r || layout.bandOfRow[r - 1] === -1))
        ru += `M0 ${(r - r0) * ROW_H + 0.5}h${width}`;
    }
  }
  return { st, gp, ru };
}

function tileRecs(t, r0, r1, boxes, rboxes) {
  const g = geom;
  const recs = [];
  const lo = mountedCols ? mountedCols[0] : 0;
  const hi = mountedCols ? mountedCols[1] : layout.nC - 1;
  const hasSel = selShown();
  for (let r = r0; r < r1; r++) {
    if (layout.rowStart[r] === layout.rowStart[r + 1]) continue;
    const ents = rowEntries(r);
    const y = (r - r0) * ROW_H;
    for (let k = 0; k < ents.length; k++) {
      const [v, c] = ents[k];
      if (c < lo || c > hi) continue;
      const lim = labelLim(ents, k, r);
      const w = g.labels ? 28 + lim : 24;
      const left = g.life[c] - 12;
      const label = !g.labels ? "" : v < 0 ? ghostLabel(-1 - v) : labelHTML(v);
      if (g.labels) {
        const tw = Math.min(textLen(label) * charW, lim);
        if (v >= 0) {
          labelW[v] = tw;
          labelStamp[v] = labelGen;
        }
        if (tw > 0) {
          boxes.push(g.life[c] + 14, r * ROW_H + KO_TOP, tw + 4);
          if (v >= 0 && rel[v]) rboxes.push(g.life[c] + 14, r * ROW_H + KO_TOP, tw + 4);
        }
      }
      const pos = `top:${y}px;left:${left}px;width:${w}px`;
      if (v < 0) {
        const s = -1 - v;
        recs.push(
          html`<div class="rec g-x${s === state.sel ? " on" : ""}" aria-hidden="true" data-g="${s}" data-row="${r}" style="${pos}">${label}</div>`,
        );
        continue;
      }
      const rv = rel[v];
      const cls =
        "rec k-" +
        CATS[kindOf[v]] +
        " " +
        glyphClass(v) +
        (failOf[v] ? " fail" : levelNames[levelOf[v]] === "warning" ? " warn" : "") +
        (rv === 3 ? " sel" : rv === 1 ? " anc" : rv === 2 ? " desc" : hasSel ? " dim" : "") +
        (v === hovRec ? " hov" : "");
      recs.push(
        html`<div class="${cls}" data-i="${v}" data-row="${r}" role="button" tabindex="${v === state.cursor ? 0 : -1}"` +
          html` aria-pressed="${rv === 3}" aria-label="${ariaOf(v)}" style="${pos}">${label}</div>`,
      );
    }
  }
  return recs.join("");
}

function tileHTML(t) {
  const g = geom;
  const r0 = t * TILE_ROWS;
  const r1 = Math.min(layout.maxRow, r0 + TILE_ROWS);
  const H = (r1 - r0) * ROW_H;
  const y0 = r0 * ROW_H;
  const Wd = g.width;
  const boxes = [];
  const rboxes = [];
  const recs = tileRecs(t, r0, r1, boxes, rboxes);
  const kid = "ko" + t;
  const clip = boxes.length ? ` clip-path="url(#${kid})"` : "";
  // the selection's thread passes over faded labels; only the labels of its own records cut it
  const rclip = rboxes.length ? ` clip-path="url(#kr${t})"` : "";
  const bg = bandsSVG(r0, r1, Wd);
  let sep = "";
  let ll = "";
  for (let c = 1; c < layout.nC; c++) sep += `M${g.x[c] - 0.5} 0V${H}`;
  if (g.contentW < Wd) sep += `M${g.contentW - 0.5} 0V${H}`;
  for (let c = 0; c < layout.nC; c++) ll += `M${g.life[c] + 0.5} 0V${H}`;
  const out = [
    `<svg class="bg" width="${Wd}" height="${H}" aria-hidden="true">${boxes.length ? knockout(kid, Wd, H, boxes, y0) : ""}` +
      (rboxes.length ? knockout("kr" + t, Wd, H, rboxes, y0) : "") +
      `<path class="st" d="${bg.st}"/><path class="gp" d="${bg.gp}"/><path class="ru" d="${bg.ru}"/>` +
      `<path class="ru" d="${sep}"${clip}/><path class="ll" d="${ll}"${clip}/></svg>`,
  ];

  const o = { run: "", fade: "", idle: "", cf: "", cc: "" };
  const showIdle = state.cats.has("wait");
  // bars are cut to the tile (plus a margin that hides the cut ends): a dotted idle line is rastered along its whole length
  const lo = y0 - 8;
  const hi = y0 + H + 8;
  for (const id of layout.tileBars[t]) {
    const bar = layout.bars[id];
    const x = g.life[bar.c] - 6 * bar.t + 0.5;
    const end = bar.open ? Math.max(bar.y0, bar.y1 - ROW_H) : bar.y1;
    let y = bar.y0;
    for (let k = 0; k <= bar.idle.length; k += 2) {
      const last = k >= bar.idle.length;
      const a = last ? end : Math.min(bar.idle[k], end);
      const b = last ? end : Math.min(bar.idle[k + 1], end);
      const ra = Math.max(y, lo);
      const rb = Math.min(a, hi);
      if (rb > ra) o.run += pill(x - 2.5, ra - y0, rb - ra, 5);
      if (!last && showIdle && b > a && b > lo && a < hi) {
        const ia = a < lo ? a + Math.ceil((lo - a) / 3.4) * 3.4 : a;
        const ib = Math.min(b, hi);
        if (ib > ia) o.idle += `M${x} ${round1(ia - y0)}V${round1(ib - y0)}`;
      }
      if (b > y) y = b;
      if (y > hi) break;
    }
    if (bar.open) o.fade += rectP(x - 2.5, end - y0, 5, bar.y1 - end);
    if (bar.end === E_FAIL) o.cf += rectP(x - 3.5, bar.y1 - y0 - 1, 7, 2);
    else if (bar.end === E_CANCEL) o.cc += rectP(x - 3.5, bar.y1 - y0 - 1, 7, 2);
  }
  setTileSpan([-8, H + 8]);
  const cxParts = chunked(
    {},
    layout.tileCx[t],
    id => layout.cx.lo[id],
    (p, id) => cxGeom(p, id, y0, ""),
  );
  out.push(
    `<svg class="cx" width="${Wd}" height="${H}" aria-hidden="true"><g${clip}>${pathsOf(o)}${cxParts.map(p => pathsOf(p)).join("")}</g></svg>`,
  );

  if (thread && thread.tile[t] && thread.tile[t].length) {
    const proto = {
      desc: "",
      descd: "",
      dh: "",
      anc: "",
      ancd: "",
      ah: "",
      own: "",
      oh: "",
      ownl: "",
      owns: "",
      ownsh: "",
    };
    const hParts = chunked(
      proto,
      thread.tile[t],
      pid => thread.pieces[pid].lo,
      (p, pid) => threadGeom(p, thread.pieces[pid], y0),
    );
    const draw = thread.fresh ? " draw" : "";
    const len = { anc: ' pathLength="1" style="--len:1"', desc: ' pathLength="1" style="--len:1"' };
    const wr = thread.waitRing;
    const extra = wr && wr.t === t ? `<circle class="wr" cx="${wr.x}" cy="${wr.y - y0}" r="5"/>` : "";
    const paths = hParts.map(p => pathsOf(p, len)).join("");
    out.push(
      `<svg class="hl${draw}" width="${Wd}" height="${H}" aria-hidden="true"><g${rclip}>${paths}</g>${extra}</svg>`,
    );
  }

  setTileSpan(null);
  out.push(recs);
  if (thread && thread.pills.length) {
    let p = "";
    for (const pl of thread.pills) {
      if (pl.y < y0 - 10 || pl.y >= y0 + H + 10) continue;
      const w = String(pl.n).length > 1 ? 24 : 17;
      const x = round1(pl.x);
      const y = round1(pl.y - y0);
      p +=
        `<g class="hp"><rect x="${round1(x - w / 2)}" y="${round1(y - 8.5)}" width="${w}" height="17" rx="8.5"/>` +
        `<text x="${x}" y="${round1(y + 3.8)}" text-anchor="middle">${pl.n}</text></g>`;
    }
    if (p) out.push(`<svg class="pl" width="${Wd}" height="${H}" aria-hidden="true">${p}</svg>`);
  }
  return { html: out.join(""), boxes };
}

// gaps are measured between the rows on screen, so with filters or unfollowed lanes they only mean "nothing shown here"
const gapsComplete = () =>
  !filtersActive && presentCats.every(c => state.cats.has(c)) && layout.nC >= nAgents + (hasWorldRecs ? 1 : 0);

function gtileHTML(t) {
  const g = geom;
  const r0 = t * TILE_ROWS;
  const r1 = Math.min(layout.maxRow, r0 + TILE_ROWS);
  const H = (r1 - r0) * ROW_H;
  const uiFont = fontVar("--font-ui");
  const bg = bandsSVG(r0, r1, g.gw);
  const complete = gapsComplete();
  let h =
    `<svg class="bg" width="${g.gw}" height="${H}" aria-hidden="true">` +
    `<path class="st" d="${bg.st}"/><path class="gp" d="${bg.gp}"/><path class="ru" d="${bg.ru}"/></svg>`;
  for (let r = r0; r < r1; r++) {
    const b = layout.bandOfRow[r];
    const top = (r - r0) * ROW_H;
    if (b >= 0 && layout.bandRow[b] === r) {
      const lab =
        b === 0 && g.first
          ? g.first
          : timeHTML(bandTime(layout.bandKey[b]), b ? bandTime(layout.bandKey[b - 1]) : null, g.elide);
      h += `<div class="bl" data-b="${b}" style="top:${top}px">${lab}</div>`;
    } else if (b === -1 && layout.gapAt.has(r)) {
      const d = layout.gapAt.get(r);
      const full = dur(d);
      const title = `${full.slice(1)} without ${complete ? "records" : "matching records in these lanes"}`;
      const label = textW(full, "500 10.5px " + uiFont) + 10 > g.gw - 14 ? dur(d, true) : full;
      h += html`<div class="gl${complete ? "" : " part"}" style="top:${top}px" title="${title}">${label}</div>`;
    }
  }
  return h;
}

export function sizeLanes() {
  const g = geom;
  const H = layout.maxRow * ROW_H;
  lanesIn.style.width = g.gw + g.width + "px";
  gutterEl.style.width = g.gw + "px";
  gutterEl.style.height = H + "px";
  plane.style.width = g.width + "px";
  plane.style.height = H + "px";
  lanesBox.style.setProperty("--gw", g.gw + "px");
  lanesBox.classList.toggle("nolabel", !g.labels);
  lanesBox.classList.toggle("has-sel", selShown());
  pinEl.style.top = g.headH + "px";
}

const pinEl = document.createElement("div");

export const xHov = document.createElement("div");

export const xSel = document.createElement("div");

export const gHov = document.createElement("div");

export const gSel = document.createElement("div");

export const ovl = document.createElementNS("http://www.w3.org/2000/svg", "svg");

export const fpill = document.createElement("div");

function removeTiles() {
  for (const m of mounted.values()) {
    m.el.remove();
    m.gel.remove();
  }
  mounted.clear();
}

export function renderLanes(rebuild) {
  if (!layout || !geom) return;
  const g = geom;
  const ae = document.activeElement;
  const hadFocus = !!ae && plane.contains(ae);
  if (rebuild) {
    removeTiles();
    remeasureLabels();
  }
  if (layout.nC > 24 && !state.fit) {
    const x0 = lanesBox.scrollLeft;
    const x1 = x0 + lanesBox.clientWidth - g.gw;
    let f = 0;
    while (f < layout.nC - 1 && g.x[f] + g.w[f] < x0) f++;
    let l = f;
    while (l < layout.nC - 1 && g.x[l + 1] < x1) l++;
    const leftOut = f - 2 < mountedCols?.[0] && mountedCols[0] > 0;
    const rightOut = l + 2 > mountedCols?.[1] && mountedCols[1] < layout.nC - 1;
    if (!mountedCols || leftOut || rightOut || rebuild) {
      const nc = [Math.max(0, f - 4), Math.min(layout.nC - 1, l + 4)];
      if (!mountedCols || nc[0] !== mountedCols[0] || nc[1] !== mountedCols[1]) {
        mountedCols = nc;
        removeTiles();
      }
    }
  } else mountedCols = null;
  const top = lanesBox.scrollTop;
  const vh = Math.max(0, lanesBox.clientHeight - g.headH);
  const a = Math.max(0, Math.floor((top - 704) / TILE_H));
  const b = Math.min(layout.nTiles - 1, Math.floor((top + vh + 704) / TILE_H));
  for (const [t, m] of mounted)
    if (t < a || t > b) {
      m.el.remove();
      m.gel.remove();
      mounted.delete(t);
    }
  for (let t = a; t <= b; t++) {
    if (mounted.has(t)) continue;
    const el = document.createElement("div");
    const gel = document.createElement("div");
    const h = (Math.min(layout.maxRow, (t + 1) * TILE_ROWS) - t * TILE_ROWS) * ROW_H;
    el.className = "tile";
    el.style.cssText = `top:${t * TILE_H}px;height:${h}px;width:${g.width}px`;
    gel.className = "gtile";
    gel.style.cssText = `top:${t * TILE_H}px;height:${h}px;width:${g.gw}px`;
    const tile = tileHTML(t);
    el.innerHTML = tile.html;
    gel.innerHTML = gtileHTML(t);
    plane.append(el);
    gutterEl.append(gel);
    mounted.set(t, { el, gel, boxes: tile.boxes });
  }
  if (thread) thread.fresh = false;
  if (hadFocus && !plane.contains(document.activeElement)) {
    const el = state.cursor != null ? plane.querySelector(`.rec[data-i="${state.cursor}"]`) : null;
    if (el) {
      el.tabIndex = 0;
      el.focus({ preventScroll: true });
    } else lanesBox.focus({ preventScroll: true });
  }
  updatePinned();
  emit("rows");
}

function bandAtRow(r) {
  if (!layout.maxRow) return -1;
  r = clamp(r, 0, layout.maxRow - 1);
  let b = layout.bandOfRow[r];
  for (let k = r; b < 0 && k < layout.maxRow; k++) b = layout.bandOfRow[k];
  for (let k = r; b < 0 && k >= 0; k--) b = layout.bandOfRow[k];
  return b;
}

function updatePinned() {
  const ct = $("corner-t");
  const cd = $("corner-d");
  const b = layout && layout.maxRow ? bandAtRow(Math.floor(lanesBox.scrollTop / ROW_H)) : -1;
  if (b < 0) {
    pinEl.innerHTML = "";
    pinEl.style.visibility = "hidden";
    if (ct) {
      ct.textContent = "";
      cd.textContent = "";
    }
    return;
  }
  const t = bandTime(layout.bandKey[b]);
  if (ct) {
    ct.textContent = timeLabel(t);
    cd.textContent = t - time0 > 0 ? dur(t - time0, true) : "start";
    cd.title = t - time0 > 0 ? dur(t - time0).slice(1) + " after the first record" : "";
  }
  const show = layout.bandRow[b] * ROW_H < lanesBox.scrollTop - 2;
  pinEl.innerHTML = show ? `<div class="bl">${lonelyLabel(t)}</div>` : "";
  pinEl.style.visibility = show ? "visible" : "hidden";
  let next = layout.bandRow[b] + 1;
  while (next < layout.maxRow && layout.bandOfRow[next] === b) next++;
  const push = Math.min(0, next * ROW_H - lanesBox.scrollTop - ROW_H);
  pinEl.style.transform = push ? `translateY(${push}px)` : "";
}

export function initTiles() {
  pinEl.className = "pin";
  pinEl.setAttribute("aria-hidden", "true");
  xHov.className = "xrow hov";
  xSel.className = "xrow sel";
  gHov.className = "gxrow hov";
  gSel.className = "gxrow sel";
  ovl.setAttribute("class", "ov");
  ovl.setAttribute("aria-hidden", "true");
  fpill.className = "fpill";
  for (const el of [xHov, xSel, gHov, gSel, fpill]) el.hidden = true;
  plane.append(xHov, xSel, ovl, fpill);
  gutterEl.append(pinEl, gHov, gSel);
}
