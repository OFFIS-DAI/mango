import { ROW_H } from "../core/lanes-layout.js";
import { clamp, nf, plural } from "../core/util.js";
import { RECORDS, failOf, laneName, laneOf, nAgents, nRecords, rowOf, vrowOf } from "../model.js";
import { relList } from "../selection.js";
import { pending, state, touch } from "../state.js";
import { followChain, setFit, setOrder, setView } from "./actions.js";
import { $, isPhone, motion } from "./dom.js";
import { lanesBox, lanesPanel, plane, recBox, rowsEl } from "./elements.js";
import { on } from "./events.js";
import { hideTip, tip } from "./feedback.js";
import { html } from "./html.js";
import { geom } from "./lanes/geometry.js";
import { lanesReveal } from "./lanes/interact.js";
import { colOfRec, layout } from "./lanes/layout.js";
import { thread } from "./lanes/thread.js";
import { renderLanes } from "./lanes/tiles.js";
import { drawRailViewport } from "./rail.js";
import { TABLE_ROW_H, renderTable, tableRowsOnScreen, tableScrollTo, theadH } from "./table.js";
import { topoView } from "./topo/draw.js";
import { markLive } from "./topo/highlight.js";
import { vis, visList, visPos, visPosAtOrAfter } from "../visibility.js";

function rowsOnScreen() {
  if (state.view === "lanes" && layout && geom) {
    const a = Math.floor((lanesBox.scrollTop + 2) / ROW_H);
    const b = Math.min(layout.maxRow, Math.ceil((lanesBox.scrollTop + lanesBox.clientHeight - geom.headH - 2) / ROW_H));
    const out = [];
    for (let r = Math.max(0, a); r < b; r++)
      for (let j = layout.rowStart[r]; j < layout.rowStart[r + 1]; j++)
        if (layout.rowRecs[j] >= 0) out.push(layout.rowRecs[j]);
    return out;
  }
  const [a, b] = tableRowsOnScreen();
  return Array.from(visList.subarray(a, b));
}

function reveal(i, inWindow) {
  if (state.view === "lanes") {
    if (!layout || (rowOf[i] < 0 && vrowOf[i] < 0)) return;
    let from = -1;
    if (thread && i === state.sel)
      for (const pc of thread.pieces) if (pc.hop && (from < 0 || pc.lo > from)) from = pc.lo;
    lanesReveal(i, true, from);
    renderLanes(false);
    const top =
      lanesBox.getBoundingClientRect().top +
      geom.headH +
      (rowOf[i] >= 0 ? rowOf[i] : vrowOf[i]) * ROW_H -
      lanesBox.scrollTop;
    if (inWindow) intoWindow(top, top + ROW_H);
  } else {
    const p = visPos(i);
    if (p < 0) return;
    tableScrollTo(p, "center");
    renderTable(false);
    const tr = rowsEl.querySelector(`tr[data-i="${i}"]`);
    if (tr && inWindow) {
      const b = tr.getBoundingClientRect();
      intoWindow(b.top, b.bottom);
    }
  }
}

// the view card can reach below the fold: the page scrolls too, to show the whole card if that brings the row into view
function intoWindow(top, bottom) {
  const m = 8;
  const vh = innerHeight;
  if (top >= m && bottom <= vh - m) return;
  let dy = $("view").getBoundingClientRect().top - 12;
  if (top - dy < m || bottom - dy > vh - m) dy = top < m ? top - m - 44 : bottom - vh + m + 44;
  scrollBy({ top: dy, behavior: motion() ? "smooth" : "auto" });
}

// the anchor keeps its distance from the top of the view card: y is measured from the scroller's top, header included
const lanesRowOf = i =>
  rowOf[i] >= 0 ? rowOf[i] : vrowOf[i] >= 0 && layout.col[laneOf[i]] >= 0 && vis[i] ? vrowOf[i] : -1;

function captureAnchor() {
  if (state.view === "lanes") {
    if (!layout || !geom) return null;
    const top = lanesBox.scrollTop;
    const vh = lanesBox.clientHeight - geom.headH;
    const s = state.sel;
    const sr = s != null ? lanesRowOf(s) : -1;
    if (sr >= 0 && sr * ROW_H >= top && sr * ROW_H < top + vh) return { i: s, y: geom.headH + sr * ROW_H - top };
    for (let r = Math.max(0, Math.floor(top / ROW_H)); r < layout.maxRow; r++) {
      for (let j = layout.rowStart[r]; j < layout.rowStart[r + 1]; j++)
        if (layout.rowRecs[j] >= 0) return { i: layout.rowRecs[j], y: geom.headH + r * ROW_H - top };
    }
    return null;
  }
  const top = recBox.scrollTop;
  const th = theadH();
  const vh = recBox.clientHeight - th;
  const s = state.sel;
  const p = s != null ? visPos(s) : -1;
  if (p >= 0 && p * TABLE_ROW_H >= top && p * TABLE_ROW_H < top + vh) return { i: s, y: th + p * TABLE_ROW_H - top };
  const q0 = Math.min(visList.length - 1, Math.floor(top / TABLE_ROW_H));
  return q0 >= 0 ? { i: visList[q0], y: th + q0 * TABLE_ROW_H - top } : null;
}

function applyAnchor() {
  const a = pending.anchor;
  pending.anchor = null;
  if (!a) return;
  if (state.view === "lanes") {
    if (!layout) return;
    let j = a.i;
    while (j < nRecords && lanesRowOf(j) < 0) j++;
    if (j >= nRecords) return;
    lanesBox.scrollTop = lanesRowOf(j) * ROW_H - (a.y - geom.headH);
    const c = colOfRec(j);
    const vw = lanesBox.clientWidth - geom.gw;
    const sl = lanesBox.scrollLeft;
    if (geom.life[c] - 14 < sl || Math.min(geom.x[c] + geom.w[c], geom.life[c] + 160) > sl + vw)
      lanesBox.scrollLeft = Math.max(0, geom.life[c] - 40);
    renderLanes(false);
  } else {
    const p = Math.min(visList.length - 1, visPosAtOrAfter(a.i));
    if (p < 0) return;
    recBox.scrollTop = p * TABLE_ROW_H - (a.y - theadH());
    renderTable(false);
  }
}

const tabs = { records: $("tab-records"), lanes: $("tab-lanes") };

export function applyView() {
  const lanes = state.view === "lanes";
  recBox.hidden = lanes;
  lanesPanel.hidden = !lanes;
  $("ltools").hidden = !lanes;
  $("agent-pick").hidden = lanes;
  for (const [v, b] of Object.entries(tabs)) {
    b.setAttribute("aria-selected", v === state.view);
    b.tabIndex = v === state.view ? 0 : -1;
  }
  $("view").setAttribute("aria-label", lanes ? "Lanes" : "Records");
}

const statusEl = $("status");

export function updateStatus() {
  if (state.view === "lanes" && layout) {
    let s = `${plural(layout.maxRow, "row")} · ${nf(layout.placed)} shown`;
    if (layout.backwards) s += ` · ${nf(layout.backwards)} out of order`;
    if (state.onlyRelated && state.sel != null) {
      let n = 0;
      const lanesOff = new Set();
      for (const i of relList)
        if (vis[i] && layout.col[laneOf[i]] < 0) {
          n++;
          lanesOff.add(laneOf[i]);
        }
      if (n) {
        s +=
          ` · ${plural(n, "related record")} in ${plural(lanesOff.size, "lane")} not followed` +
          ` · <button data-act="follow">Follow chain</button>`;
      }
    }
    statusEl.innerHTML = s;
  } else
    statusEl.textContent =
      visList.length === nRecords
        ? plural(nRecords, "record")
        : `${nf(visList.length)} of ${plural(nRecords, "record")}`;
}

const selbar = $("selbar");

export function updateSelbar() {
  const s = state.sel;
  selbar.hidden = !isPhone() || s == null;
  if (selbar.hidden) return;
  selbar.classList.toggle("fail", !!failOf[s]);
  selbar.innerHTML =
    html`<span class="sb-t">${RECORDS[s].event} <span class="m">${laneName(laneOf[s])}</span></span>` +
    `<button class="tool" data-act="details">Details ↓</button>`;
}

let sRaf = 0;

function scrollFrame() {
  if (sRaf) return;
  sRaf = requestAnimationFrame(() => {
    sRaf = 0;
    if (state.view === "lanes") renderLanes(false);
    else renderTable(false);
    drawRailViewport();
    const ae = document.activeElement;
    const onRec = ae && ae.closest && ae.closest(".rec");
    if (!tip.hidden && !tip.classList.contains("act") && !onRec) hideTip();
  });
}

// beside the side column the card takes the rest of the first screen, so its bottom and scrollbar are never below the fold
export function fitCard() {
  const root = document.documentElement.style;
  if (innerWidth <= 980) {
    root.removeProperty("--card-h");
    return;
  }
  const top = $("view").getBoundingClientRect().top + scrollY;
  root.setProperty("--card-h", Math.round(clamp(innerHeight - top - 12, 420, Math.min(1400, innerHeight - 24))) + "px");
}

// what actions asked for once the view is drawn: keep the scroll anchor, show a record, focus one
export function applyPending() {
  if (pending.anchor) applyAnchor();
  if (pending.reveal != null) {
    const i = pending.reveal;
    pending.reveal = null;
    reveal(i, pending.revealWindow);
  }
  pending.revealWindow = true;
  if (pending.focus != null) {
    const i = pending.focus;
    pending.focus = null;
    plane.querySelector(`.rec[data-i="${i}"]`)?.focus({ preventScroll: true });
  }
}

let viewW = 0;

let liveFrame = 0;

// once per frame, whenever the rows on screen change
function rowsChanged() {
  if (liveFrame || !topoView) return;
  liveFrame = requestAnimationFrame(() => {
    liveFrame = 0;
    markLive(rowsOnScreen());
  });
}

// the Lanes tools show the order and Fit of the state
export function syncLaneTools() {
  $("o-packed").setAttribute("aria-pressed", state.order === "packed");
  $("o-file").setAttribute("aria-pressed", state.order === "file");
  $("fit").setAttribute("aria-pressed", state.fit);
}

export function initView() {
  on("leaving-view", () => {
    pending.anchor = captureAnchor();
  });
  on("rows", rowsChanged);
  recBox.addEventListener(
    "scroll",
    () => {
      if (state.view === "records") scrollFrame();
    },
    { passive: true },
  );
  lanesBox.addEventListener(
    "scroll",
    () => {
      if (state.view === "lanes") scrollFrame();
    },
    { passive: true },
  );
  $("o-packed").addEventListener("click", () => setOrder("packed"));
  $("o-file").addEventListener("click", () => setOrder("file"));
  $("fit").addEventListener("click", () => setFit(!state.fit));
  $("follow").addEventListener("click", () => followChain());
  for (const [v, b] of Object.entries(tabs)) {
    b.addEventListener("click", () => {
      if (b.getAttribute("aria-disabled") !== "true") setView(v);
    });
    b.addEventListener("keydown", e => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const to = v === "records" ? "lanes" : "records";
      if (to === "lanes" && !nAgents) return;
      setView(to);
      tabs[to].focus();
    });
  }
  if (!nAgents) {
    tabs.lanes.setAttribute("aria-disabled", "true");
    tabs.lanes.title = "No agent records";
  }
  addEventListener("resize", fitCard);
  {
    const ro = new ResizeObserver(fitCard);
    ro.observe(document.querySelector("header"));
    ro.observe(document.querySelector(".filters"));
  }
  new ResizeObserver(() => {
    const w = $("view").clientWidth;
    if (w !== viewW) {
      viewW = w;
      touch("width");
    } else touch("height");
  }).observe($("view"));
}
