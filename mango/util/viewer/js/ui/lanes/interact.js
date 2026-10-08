import { K_COMB, K_LOST, K_MSG, K_SELF, K_STUB, ROW_H, TILE_H, TILE_ROWS } from "../../core/lanes-layout.js";
import { CORE, E_EVRECV, E_RECV, E_SENT, S_FLIGHT, S_LOST } from "../../core/schema.js";
import { clamp, fmt, lowerBound } from "../../core/util.js";
import {
  AGENTS,
  RECORDS,
  bandOf,
  bandTime,
  causeIx,
  edgeOfRec,
  evOf,
  failOf,
  inferred,
  laneName,
  laneOf,
  levelNames,
  levelOf,
  nAgents,
  recTime,
  recvOf,
  rowOf,
  sendStatus,
  shortAid,
  vrowOf,
} from "../../model.js";
import { pending, state } from "../../state.js";
import { hubOrder, select } from "../actions.js";
import { $ } from "../dom.js";
import { lanesBox, plane } from "../elements.js";
import { emit, on } from "../events.js";
import { hideTip, tip } from "../feedback.js";
import { html } from "../html.js";
import { geom, lonelyLabel } from "./geometry.js";
import { moveLane } from "./header.js";
import { labelHTML } from "./labels.js";
import { colOfRec, hovRec, layout, setHovRec } from "./layout.js";
import { combPath, cxGeom, knockout, pathsOf } from "./paths.js";
import { fpill, gHov, gSel, mounted, ovl, renderLanes, xHov, xSel } from "./tiles.js";
import { vis } from "../../visibility.js";

function recTipHTML(i) {
  const r = RECORDS[i];
  const meta = [laneName(laneOf[i]), recTime(i), r.id != null ? r.id : r.cause != null ? "in " + r.cause : null]
    .filter(v => v != null)
    .join(" · ");
  const kv = [];
  for (const k in r) {
    if (kv.length >= 8 || CORE.has(k) || k.startsWith("sim_") || k.startsWith("__")) continue;
    const c = r[k];
    let v =
      k === "receiver"
        ? shortAid(c)
        : k === "content" && c && typeof c === "object"
          ? (c.repr ?? c.type ?? fmt(c))
          : fmt(c);
    v = String(v);
    if (v.length > 80) v = v.slice(0, 79) + "…";
    kv.push(html`<div><span>${k}=</span>${v}</div>`);
  }
  let status = "";
  if (evOf[i] === E_SENT)
    status =
      sendStatus[i] === S_LOST
        ? html` · <span class="lo">lost</span>`
        : sendStatus[i] === S_FLIGHT
          ? " · in flight"
          : "";
  const lvl = levelNames[levelOf[i]];
  if (failOf[i])
    status = html`${status} · <span class="lo">${lvl === "error" || lvl === "critical" ? lvl : "failed"}</span>`;
  return (
    html`<div class="t1">${r.event}</div><div class="t2">${meta}${status}${inferred[i] ? " · time inferred" : ""}</div>` +
    (kv.length ? html`<div class="kvs">${kv}</div>` : "")
  );
}

let tipTimer = 0;

function recConnectors(i) {
  const r = rowOf[i];
  if (r < 0 || !layout) return [];
  const out = [];
  for (const id of layout.tileCx[Math.floor(r / TILE_ROWS)]) {
    const k = layout.cx.kind[id];
    const a = layout.cx.a[id];
    if (a === i || layout.cx.b[id] === i) out.push([id, -1]);
    else if (k === K_COMB && layout.combs.get(a).recs.includes(i)) out.push([id, i]);
  }
  return out;
}

function drawOverlay(list) {
  if (!list.length || !geom) {
    ovl.innerHTML = "";
    return;
  }
  let lo = Infinity;
  let hi = -Infinity;
  for (const [id] of list) {
    lo = Math.min(lo, layout.cx.lo[id]);
    hi = Math.max(hi, layout.cx.hi[id]);
  }
  const y0 = (lo - 1) * ROW_H;
  const H = (hi - lo + 4) * ROW_H;
  const o = {};
  for (const [id, only] of list) {
    if (only >= 0) combPath(o, "ovl", "oh", layout.cx.a[id], [only], y0);
    else cxGeom(o, id, y0, "ov");
  }
  const boxes = [];
  for (const [t, m] of mounted)
    if ((t + 1) * TILE_H > y0 && t * TILE_H < y0 + H) for (const v of m.boxes) boxes.push(v);
  ovl.setAttribute("width", geom.width);
  ovl.setAttribute("height", H);
  ovl.style.top = y0 + "px";
  ovl.innerHTML = boxes.length
    ? knockout("ko-ov", geom.width, H, boxes, y0) + `<g clip-path="url(#ko-ov)">${pathsOf(o)}</g>`
    : pathsOf(o);
}

// in Lanes every connector points down, so the tooltip opens above the row, on the side away from an arrow coming in
function showRecTip(i, el) {
  tip.innerHTML = recTipHTML(i);
  tip.hidden = false;
  tip.classList.remove("act");
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  const r = el.getBoundingClientRect();
  const gx = r.left + 12;
  const side = j =>
    j >= 0 && rowOf[j] >= 0 && colOfRec(j) !== colOfRec(i)
      ? Math.sign(geom.life[colOfRec(j)] - geom.life[colOfRec(i)])
      : 0;
  const above = r.top - 8 - h >= 8;
  const avoid = above
    ? evOf[i] === E_RECV || evOf[i] === E_EVRECV
      ? side(causeIx[i])
      : 0
    : evOf[i] === E_SENT
      ? side(recvOf(i))
      : 0;
  const x = avoid > 0 ? gx - 14 - w : gx + 14;
  tip.style.left = clamp(x, 8, Math.max(8, innerWidth - 8 - w)) + "px";
  tip.style.top = (above ? r.top - 8 - h : Math.min(r.bottom + 8, innerHeight - 8 - h)) + "px";
}

function setHov(i, el) {
  if (i === hovRec) return;
  if (hovRec >= 0) plane.querySelector(`.rec[data-i="${hovRec}"]`)?.classList.remove("hov");
  setHovRec(i);
  clearTimeout(tipTimer);
  if (i < 0) {
    xHov.hidden = gHov.hidden = true;
    drawOverlay([]);
    hideTip();
    emit("peek", null);
    updateFpill();
    return;
  }
  el = el || plane.querySelector(`.rec[data-i="${i}"]`);
  el?.classList.add("hov");
  const r = rowOf[i];
  if (r >= 0) {
    xHov.style.top = gHov.style.top = r * ROW_H + "px";
    xHov.hidden = gHov.hidden = false;
    gHov.innerHTML = `<div class="bl">${lonelyLabel(bandTime(bandOf[i]))}</div>`;
  }
  drawOverlay(recConnectors(i));
  emit("peek", i);
  if (el)
    tipTimer = setTimeout(() => {
      if (el.isConnected) showRecTip(i, el);
    }, 120);
  updateFpill();
}

export function updateFpill() {
  if (!geom || geom.labels) {
    fpill.hidden = true;
    return;
  }
  const fe = document.activeElement;
  const fi = fe && fe.matches && fe.matches(".rec[data-i]:focus-visible") ? +fe.dataset.i : -1;
  const i = hovRec >= 0 ? hovRec : fi >= 0 ? fi : state.sel != null && rowOf[state.sel] >= 0 ? state.sel : -1;
  if (i < 0 || rowOf[i] < 0) {
    fpill.hidden = true;
    return;
  }
  fpill.innerHTML = labelHTML(i);
  fpill.hidden = false;
  const x = geom.life[colOfRec(i)] + 10;
  fpill.style.top = rowOf[i] * ROW_H + 1 + "px";
  fpill.style.left = Math.min(x, geom.width - 200) + "px";
}

export function positionSelRow() {
  const s = state.sel;
  const r = s != null && layout && vis[s] ? (rowOf[s] >= 0 ? rowOf[s] : -1) : -1;
  xSel.hidden = gSel.hidden = r < 0;
  if (r < 0) return;
  xSel.style.top = gSel.style.top = r * ROW_H + "px";
  gSel.innerHTML = `<div class="bl">${lonelyLabel(bandTime(bandOf[s]))}</div>`;
}

// fromRow: an earlier row that should come into view with the record when both fit (the step into its agent)
export function lanesReveal(i, center, fromRow) {
  const r = rowOf[i] >= 0 ? rowOf[i] : vrowOf[i];
  if (r < 0 || !geom) return false;
  const vh = lanesBox.clientHeight - geom.headH;
  const top = lanesBox.scrollTop;
  const ya = (fromRow != null && fromRow >= 0 && fromRow < r && (r - fromRow + 2) * ROW_H <= vh ? fromRow : r) * ROW_H;
  const yb = (r + 1) * ROW_H;
  if (ya < top || yb > top + vh) lanesBox.scrollTop = center ? (ya + yb - vh) / 2 : ya < top ? ya : yb - vh;
  const c = layout.col[laneOf[i]];
  const vw = lanesBox.clientWidth - geom.gw;
  const sl = lanesBox.scrollLeft;
  if (c >= 0) {
    const x0 = geom.life[c] - 14;
    const x1 = Math.min(geom.x[c] + geom.w[c], geom.life[c] + 160, geom.width);
    if (x0 < sl) lanesBox.scrollLeft = x0;
    else if (x1 > sl + vw) lanesBox.scrollLeft = Math.min(x0, x1 - vw);
  }
  return true;
}

const colList = c => layout.colRecs.subarray(layout.colStart[c], layout.colStart[c + 1]);

function rowBound(list, row) {
  let lo = 0;
  let hi = list.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (rowOf[list[m]] < row) lo = m + 1;
    else hi = m;
  }
  return lo;
}

function nearestIn(list, row) {
  if (!list.length) return -1;
  const lo = rowBound(list, row);
  if (lo >= list.length) return list[list.length - 1];
  if (lo === 0) return list[0];
  return row - rowOf[list[lo - 1]] < rowOf[list[lo]] - row ? list[lo - 1] : list[lo];
}

function lanesCursorStart() {
  const c = state.cursor;
  if (c != null && rowOf[c] >= 0) return c;
  const r = Math.floor(lanesBox.scrollTop / ROW_H) + 1;
  for (let k = 0; k < layout.nC; k++) {
    const n = nearestIn(colList(k), r);
    if (n >= 0) return n;
  }
  return -1;
}

function lanesMove(k) {
  if (!layout || !layout.placed) return;
  let i = lanesCursorStart();
  if (i < 0) return;
  if (i === state.cursor || rowOf[state.cursor] >= 0) {
    const c = colOfRec(i);
    const list = colList(c);
    const at = lowerBound(list, i);
    const page = Math.max(1, Math.floor((lanesBox.clientHeight - geom.headH) / ROW_H) - 2);
    if (k === "ArrowUp") i = list[Math.max(0, at - 1)];
    else if (k === "ArrowDown") i = list[Math.min(list.length - 1, at + 1)];
    else if (k === "Home") i = list[0];
    else if (k === "End") i = list[list.length - 1];
    else if (k === "PageUp" || k === "PageDown") i = nearestIn(list, rowOf[i] + (k === "PageUp" ? -page : page));
    else if (k === "ArrowLeft" || k === "ArrowRight") {
      const d = k === "ArrowLeft" ? -1 : 1;
      const row = rowOf[i];
      for (let c2 = c + d; c2 >= 0 && c2 < layout.nC; c2 += d) {
        const l2 = colList(c2);
        if (!l2.length) continue;
        const lo = rowBound(l2, row);
        const after = lo < l2.length ? l2[lo] : -1;
        const before = lo > 0 ? l2[lo - 1] : -1;
        i = after >= 0 && (before < 0 || rowOf[after] - row <= row - rowOf[before]) ? after : before;
        break;
      }
    }
  }
  lanesCursor(i);
}

function lanesCursor(i) {
  const old = state.cursor;
  state.cursor = i;
  lanesReveal(i, false);
  renderLanes(false);
  if (old != null) plane.querySelector(`.rec[data-i="${old}"]`)?.setAttribute("tabindex", "-1");
  const el = plane.querySelector(`.rec[data-i="${i}"]`);
  if (el) {
    el.tabIndex = 0;
    el.focus({ preventScroll: true });
    showRecTip(i, el);
  }
}

export function renderLanesEmpty() {
  const el = $("lempty");
  const lanes = state.lanes || [];
  const pick = html`<button class="tool" data-act="pick">Choose lanes…</button>`;
  if (!lanes.length) {
    const sugg = hubOrder(AGENTS.map((a, l) => l)).slice(0, 5);
    el.innerHTML =
      `<h3>Choose agents to follow side by side</h3><p>Each followed agent gets a lane; records line up by time.</p>` +
      html`<div class="acts">${sugg.map(l => html`<button class="tool" data-act="lane" data-l="${l}">${laneName(l)}</button>`)}</div>` +
      html`<div class="acts"><button class="tool" data-act="follow" ${state.sel == null ? "disabled" : ""}>Follow chain</button>${pick}</div>`;
    el.hidden = false;
  } else if (layout && !layout.placed) {
    el.innerHTML =
      `<h3>No records in these lanes match the filters</h3><p>Change the search, clear the filters or follow other agents.</p>` +
      html`<div class="acts"><button class="tool" data-act="clear-filters">Clear filters</button>${pick}</div>`;
    el.hidden = false;
  } else el.hidden = true;
  let hint = $("lanes-one");
  if (nAgents === 1 && !hint) {
    hint = document.createElement("div");
    hint.id = "lanes-one";
    hint.className = "hintbar";
    hint.textContent = "Lanes compare agents side by side; this trace has one agent.";
    $("view").append(hint);
  }
  if (hint) hint.hidden = state.view !== "lanes";
}

// when the cursor's lane was dropped, the cursor goes to the nearest record of the lane in its place
export function restoreCursor() {
  const pc = pending.cursorAt;
  if (!pc) return;
  pending.cursorAt = null;
  const c0 = Math.min(pc.c, layout.nC - 1);
  let i = -1;
  for (let dist = 0; i < 0 && dist < layout.nC; dist++) {
    for (const c of dist ? [c0 - dist, c0 + dist] : [c0])
      if (i < 0 && c >= 0 && c < layout.nC) i = nearestIn(colList(c), pc.row);
  }
  if (i >= 0) {
    state.cursor = i;
    lanesReveal(i, false);
    renderLanes(false);
    if (pc.focus) pending.focus = i;
  }
}

// the messages of a connection hovered in the map are drawn over the lanes
function drawHoveredConnection(h) {
  if (state.view !== "lanes") return;
  if (!h) {
    if (hovRec < 0) drawOverlay([]);
    return;
  }
  if (h.e == null || !layout) return;
  const list = [];
  for (const t of mounted.keys()) {
    for (const id of layout.tileCx[t] || []) {
      const k = layout.cx.kind[id];
      if ((k === K_MSG || k === K_SELF || k === K_LOST || k === K_STUB) && edgeOfRec[layout.cx.a[id]] === h.e)
        list.push([id, -1]);
    }
  }
  drawOverlay(list);
}

export function initLanes() {
  on("map-hover", drawHoveredConnection);
  plane.addEventListener("pointermove", e => {
    const el = e.target.closest(".rec[data-i]");
    setHov(el ? +el.dataset.i : -1, el);
  });
  plane.addEventListener("pointerleave", () => setHov(-1));
  plane.addEventListener("click", e => {
    const el = e.target.closest(".rec[data-i]");
    if (!el) return;
    state.cursor = +el.dataset.i;
    select(+el.dataset.i);
  });
  plane.addEventListener("focusin", e => {
    const el = e.target.closest(".rec[data-i]");
    if (el && el.matches(":focus-visible")) showRecTip(+el.dataset.i, el);
    updateFpill();
  });
  plane.addEventListener("focusout", () => {
    hideTip();
    requestAnimationFrame(updateFpill);
  });
  lanesBox.addEventListener("keydown", e => {
    if (e.target.closest("button, input")) return;
    if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      moveLane(e.key === "ArrowLeft" ? -1 : 1);
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(e.key)) {
      e.preventDefault();
      lanesMove(e.key);
    } else if ((e.key === "Enter" || e.key === " ") && state.cursor != null && rowOf[state.cursor] >= 0) {
      e.preventDefault();
      select(state.cursor);
    }
  });
}
