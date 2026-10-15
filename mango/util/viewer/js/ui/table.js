import { CATS, CORE, levelColor } from "../core/schema.js";
import { clamp, fmt } from "../core/util.js";
import {
  ABSOLUTE,
  CLOCK,
  NODES,
  RECORDS,
  TMAX,
  TMIN,
  agentOf,
  bandOf,
  bandTime,
  bandTimesAll,
  dayOf,
  edgeOfRec,
  failOf,
  finer,
  inferred,
  kindOf,
  levelNames,
  levelOf,
  nRecords,
  parseTs,
  timeHTML,
  timeLabel,
  timeParts,
  wall0,
} from "../model.js";
import { rel } from "../selection.js";
import { state } from "../state.js";
import { select } from "./actions.js";
import { $, isPhone } from "./dom.js";
import { recBox, rowsEl, tableEl } from "./elements.js";
import { emit, on } from "./events.js";
import { html, raw } from "./html.js";
import { selShown } from "./lanes/layout.js";
import { vis, visList, visPos, visPosAtOrAfter } from "../visibility.js";

const SIMCOL = CLOCK === "sim";

export const TABLE_ROW_H = 30;

const OVERSCAN = 20;

const COL_CHAR_W = 7.5;

const MULTIDAY = ABSOLUTE && nRecords > 0 && dayOf(TMIN) !== dayOf(TMAX);

const COLS = (() => {
  const len = { lvl: 6, agent: 6, event: 6, id: 6, cause: 6 };
  const fit = (k, v) => {
    if (v == null) return;
    const n = String(v).length;
    if (n > len[k]) len[k] = n;
  };
  for (let i = 0; i < nRecords; i++) {
    const r = RECORDS[i];
    fit("agent", r.agent);
    fit("event", r.event);
    fit("id", r.id);
    fit("cause", r.cause);
  }
  for (const l of levelNames) fit("lvl", l);
  const cap = { lvl: 8, agent: 22, event: 26, id: 18, cause: 18 };
  // the event cell starts with a 10 px category bar
  const ch = (k, extra = 0) => ({
    w: `calc(${Math.min(len[k], cap[k])}ch + ${20 + extra}px)`,
    px: Math.min(len[k], cap[k]) * COL_CHAR_W + 20 + extra,
  });
  let stLen = 5;
  for (let k = 0; k < bandTimesAll.length; k++) {
    const t = bandTimesAll[k];
    stLen = Math.max(stLen, timeParts(t, finer(t, k ? bandTimesAll[k - 1] : null)).join("").length);
  }
  const stPx = Math.max(78, Math.ceil((stLen + 1) * COL_CHAR_W + 20 + (MULTIDAY ? 44 : 0)));
  return {
    st: { w: `${stPx}px`, px: stPx, h: `<button id="th-time" title="Go to a time (t)">Sim time</button>` },
    ms: { w: "60px", px: 60, h: "+ms" },
    lvl: { ...ch("lvl"), h: "Level" },
    agent: { ...ch("agent"), h: "Agent" },
    event: { ...ch("event", 10), h: "Event" },
    id: { ...ch("id"), h: "id" },
    cause: { ...ch("cause"), h: "cause" },
    details: { w: "auto", px: 160, h: "Details" },
  };
})();

// +ms is constant within a simulated instant, so it is the first column to go when space is short
function pickCols() {
  const time = SIMCOL ? ["st"] : ["ms"];
  if (isPhone()) return time.concat(["event", "agent", "lvl", "id", "cause", "details"]);
  const full = time.concat(SIMCOL ? ["ms"] : [], ["lvl", "agent", "event", "id", "cause", "details"]);
  const need = full.reduce((s, k) => s + COLS[k].px, 0);
  return SIMCOL && need > (recBox.clientWidth || 1200) ? full.filter(k => k !== "ms") : full;
}

let cols = [];

let colKey = "";

function applyCols() {
  const next = pickCols();
  const k = next.join();
  if (k === colKey) return false;
  cols = next;
  colKey = k;
  $("cols").innerHTML = cols.map(c => `<col style="width:${COLS[c].w}">`).join("");
  $("thead").innerHTML = cols.map(c => `<th scope="col"${c === "ms" ? ' class="t"' : ""}>${COLS[c].h}</th>`).join("");
  tableEl.style.minWidth = Math.round(cols.reduce((s, c) => s + COLS[c].px, 0)) + "px";
  return true;
}

const details = r => {
  let out = "";
  for (const k in r) {
    if (CORE.has(k) || k.startsWith("__") || (SIMCOL && k === "sim_time")) continue;
    out += (out ? "  " : "") + k + "=" + fmt(r[k]);
  }
  return out;
};

const ref = v => (v == null ? "" : html`<span class="ref">${v}</span>`);

let dimOn = false;

const relClass = i => {
  const v = rel[i];
  return v === 3 ? "sel" : v === 1 ? "anc" : v === 2 ? "desc" : dimOn ? "dim" : "";
};

let tWin = [-1, -1];

export const theadH = () => tableEl.tHead.offsetHeight || 32;

function simCell(i, pos) {
  const t = bandTime(bandOf[i]);
  const rep = pos > 0 && bandOf[visList[pos - 1]] === bandOf[i];
  const text = rep ? timeLabel(t) : raw(timeHTML(t, pos > 0 ? bandTime(bandOf[visList[pos - 1]]) : null));
  const title = inferred[i] ? html` title="no sim_time; placed by its effects/neighbours"` : "";
  return html`<td class="st${rep ? " rep" : ""}"${title}>${inferred[i] ? "≈" : ""}${text}</td>`;
}

function cellHTML(c, i, pos) {
  const r = RECORDS[i];
  switch (c) {
    case "st":
      return simCell(i, pos);
    case "ms": {
      const ms = (parseTs(r.timestamp) - wall0) * 1000;
      return `<td class="t">${isNaN(ms) ? "" : ms.toFixed(1)}</td>`;
    }
    case "lvl":
      return html`<td class="lvl" style="--k:${levelColor(levelNames[levelOf[i]])}">${levelNames[levelOf[i]]}</td>`;
    case "agent":
      return html`<td class="agent">${r.agent ?? ""}</td>`;
    case "event":
      return html`<td><span class="ev" style="--k:var(--c-${CATS[kindOf[i]]})">${r.event}</span></td>`;
    case "id":
      return html`<td>${ref(r.id)}</td>`;
    case "cause":
      return html`<td>${ref(r.cause)}</td>`;
    default: {
      const det = details(r);
      return html`<td class="details" title="${det}">${det}</td>`;
    }
  }
}

function rowHTML(i, pos) {
  const cls = (failOf[i] ? "fail " : "") + relClass(i);
  const attrs =
    (agentOf[i] >= 0 ? ` data-a="${agentOf[i]}"` : "") +
    (edgeOfRec[i] >= 0 ? ` data-e="${edgeOfRec[i]}"` : "") +
    ` aria-rowindex="${pos + 2}"${i === state.sel ? ' aria-current="true"' : ""} tabindex="${i === state.cursor ? 0 : -1}"`;
  return `<tr data-i="${i}"${attrs} class="${cls}">` + cols.map(c => cellHTML(c, i, pos)).join("") + "</tr>";
}

export function renderTable(force) {
  if (applyCols()) force = true;
  dimOn = selShown();
  const n = visList.length;
  const top = Math.max(0, recBox.scrollTop - theadH());
  const first = Math.max(0, Math.min(n, Math.floor(top / TABLE_ROW_H) - OVERSCAN));
  const last = Math.min(n, first + Math.ceil(recBox.clientHeight / TABLE_ROW_H) + 2 * OVERSCAN);
  if (!force && first === tWin[0] && last === tWin[1]) return;
  tWin = [first, last];
  const spacer = h =>
    `<tr class="spacer" aria-hidden="true"><td colspan="${cols.length}" style="height:${h}px"></td></tr>`;
  const rows = [first ? spacer(first * TABLE_ROW_H) : ""];
  for (let p = first; p < last; p++) rows.push(rowHTML(visList[p], p));
  if (last < n) rows.push(spacer((n - last) * TABLE_ROW_H));
  const hadFocus = rowsEl.contains(document.activeElement);
  rowsEl.innerHTML = rows.join("");
  tableEl.setAttribute("aria-rowcount", n + 1);
  const empty = $("empty");
  empty.hidden = n > 0;
  if (!n) {
    empty.innerHTML = nRecords
      ? `<h3>No records match the filters</h3><p>Change the search or clear the filters to see records again.</p>` +
        `<div class="acts"><button class="tool" data-act="clear-filters">Clear filters</button></div>`
      : `<h3>No records in this trace</h3>`;
  }
  const cur = state.cursor != null ? rowsEl.querySelector(`tr[data-i="${state.cursor}"]`) : null;
  recBox.tabIndex = cur ? -1 : 0;
  if (hadFocus) (cur || recBox).focus({ preventScroll: true });
  emit("rows");
}

export function tableRowsOnScreen() {
  const top = recBox.scrollTop;
  // a row counts once more than a sliver of it shows
  const a = Math.max(0, Math.floor((top + 2) / TABLE_ROW_H));
  const b = Math.min(visList.length, Math.ceil((top + recBox.clientHeight - theadH() - 2) / TABLE_ROW_H));
  return [a, b];
}

export function tableScrollTo(pos, mode) {
  const h = recBox.clientHeight - theadH();
  const y = pos * TABLE_ROW_H;
  const cur = recBox.scrollTop;
  if (mode === "center") {
    if (y < cur || y + TABLE_ROW_H > cur + h) recBox.scrollTop = y - (h - TABLE_ROW_H) / 2;
  } else if (y < cur) recBox.scrollTop = y;
  else if (y + TABLE_ROW_H > cur + h) recBox.scrollTop = y + TABLE_ROW_H - h;
}

function moveTableCursor(k) {
  const n = visList.length;
  if (!n) return;
  let p = state.cursor != null ? visPos(state.cursor) : -1;
  if (p < 0)
    p =
      state.cursor != null
        ? Math.min(n - 1, visPosAtOrAfter(state.cursor))
        : Math.floor(recBox.scrollTop / TABLE_ROW_H);
  else {
    const page = Math.max(1, Math.floor((recBox.clientHeight - theadH()) / TABLE_ROW_H) - 1);
    if (k === "ArrowUp") p--;
    else if (k === "ArrowDown") p++;
    else if (k === "Home") p = 0;
    else if (k === "End") p = n - 1;
    else p += k === "PageUp" ? -page : page;
  }
  p = clamp(p, 0, n - 1);
  state.cursor = visList[p];
  tableScrollTo(p);
  renderTable(true);
  rowsEl.querySelector(`tr[data-i="${state.cursor}"]`)?.focus({ preventScroll: true });
}

// rows of an agent or a connection hovered in the map are marked by a style rule
const hoverRule = document.createElement("style");

function markHoveredRows(h) {
  if (!h) hoverRule.textContent = "";
  else if (h.e != null) hoverRule.textContent = `#rows tr[data-e="${h.e}"] td { background: var(--hover-row); }`;
  else {
    const lane = NODES[h.n].lane;
    hoverRule.textContent =
      lane >= 0 ? `#rows tr[data-a="${lane}"] td.agent { color: var(--accent); font-weight: 600; }` : "";
  }
}

export function initTable() {
  hoverRule.id = "hover-rule";
  document.head.append(hoverRule);
  on("map-hover", markHoveredRows);
  rowsEl.addEventListener("click", e => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    state.cursor = +tr.dataset.i;
    select(+tr.dataset.i);
  });
  rowsEl.addEventListener("pointerover", e => {
    const tr = e.target.closest("tr[data-i]");
    emit("peek", tr ? +tr.dataset.i : null);
  });
  rowsEl.addEventListener("pointerleave", () => emit("peek", null));
  recBox.addEventListener("keydown", e => {
    if (e.target.closest("input, button, select")) return;
    if (
      ["ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(e.key) &&
      !e.altKey &&
      !e.ctrlKey &&
      !e.metaKey
    ) {
      e.preventDefault();
      moveTableCursor(e.key);
    } else if ((e.key === "Enter" || e.key === " ") && state.cursor != null && vis[state.cursor]) {
      e.preventDefault();
      select(state.cursor);
    }
  });
}
