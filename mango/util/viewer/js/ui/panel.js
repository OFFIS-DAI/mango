import { CATS, CAT_LABEL, E_RECV, E_SENT, S_FLIGHT, S_LOST, levelColor } from "../core/schema.js";
import { fmt, nf } from "../core/util.js";
import {
  EDGES,
  RECORDS,
  TMAX,
  aIx,
  bandOf,
  bandTime,
  causeIx,
  dur,
  evOf,
  failOf,
  kindOf,
  laneName,
  laneOf,
  lastRecvOfAgent,
  levelNames,
  levelOf,
  nAgents,
  presentCats,
  recTime,
  recvOf,
  reverseEdge,
  sendStatus,
  shortAid,
  timeLabel,
  timeOf,
  typeOf,
} from "../model.js";
import { chain, descList, hopOfRec, hops, pathOfSel } from "../selection.js";
import { state } from "../state.js";
import { select } from "./actions.js";
import { edgeRange, receiverName } from "./describe.js";
import { $ } from "./dom.js";
import { panel } from "./elements.js";
import { html, raw } from "./html.js";
import { vis } from "../visibility.js";

const colorOf = i => `var(--c-${CATS[kindOf[i]]})`;

const bandTimeOf = i => bandTime(bandOf[i]);

function itemHTML(i, here, hop, dt) {
  const r = RECORDS[i];
  const badge = hop
    ? html`<span class="hop" title="Step ${hop}: the chain reaches ${laneName(laneOf[i])}">${hop}</span>`
    : "";
  return html`<li class="${here ? "here" : ""}" style="--k:${failOf[i] ? "var(--fail)" : colorOf(i)}">${badge}<button data-go="${i}">${r.event}${r.id != null ? html` <span>${r.id}</span>` : ""}<br><span class="agent">${laneName(laneOf[i])}${dt ? ` · ${dt}` : ""}</span></button></li>`;
}

function lostNote(s, to) {
  const later = (lastRecvOfAgent.get(to) ?? -1) > s;
  if (later)
    return html`<p class="note lost"><b>Lost:</b> no receipt, although <b>${to}</b> received later messages.</p>`;
  if (!aIx.has(to))
    return html`<p class="note lost"><b>Lost:</b> no receipt; <b>${to}</b> wrote no records in this trace.</p>`;
  const before = TMAX - timeOf[s] > 0 ? ` (sent ${dur(TMAX - timeOf[s]).slice(1)} before the trace ended)` : "";
  return html`<p class="note lost"><b>Lost:</b> no receipt; <b>${to}</b> received nothing afterwards${before}.</p>`;
}

function messageNote(s) {
  const r = RECORDS[s];
  if (evOf[s] === E_SENT) {
    const rv = recvOf(s);
    if (rv >= 0) {
      const d = bandTimeOf(rv) - bandTimeOf(s);
      return html`<p class="note">Delivered: <button data-go="${rv}">message.received</button> in <b>${laneName(laneOf[rv])}</b> at ${recTime(rv)}${d > 0 ? ` (${dur(d)})` : ""}</p>`;
    }
    const to = receiverName(s);
    if (sendStatus[s] === S_FLIGHT)
      return html`<p class="note">In flight when the trace ended: no receipt by <b>${to}</b>.</p>`;
    if (sendStatus[s] === S_LOST) return lostNote(s, to);
    return html`<p class="note">No receipt recorded${to !== "?" ? html` by <b>${to}</b>` : ""}.</p>`;
  }
  if (evOf[s] === E_RECV) {
    const p = causeIx[s];
    if (p >= 0 && evOf[p] === E_SENT) {
      const d = bandTimeOf(s) - bandTimeOf(p);
      return html`<p class="note">Sent by <b>${laneName(laneOf[p])}</b> at <button data-go="${p}">${recTime(p)}</button>${d > 0 ? ` (${dur(d)})` : ""}</p>`;
    }
    if (r.sender != null)
      return html`<p class="note">Sent by <b>${shortAid(r.sender)}</b>; the send is not in this trace.</p>`;
  }
  return "";
}

function connectionHTML() {
  const lk = state.link;
  const list = [lk.e];
  const rev = reverseEdge(lk.e);
  if (lk.both && rev >= 0) list.push(rev);
  const parts = [
    html`<div class="conn"><h3>Connection</h3><h2>${EDGES[lk.e].from} ${lk.both ? "⇄" : "→"} ${EDGES[lk.e].to}</h2>` +
      html`<div class="pacts"><button class="tool" data-act="both" aria-pressed="${lk.both}" ${rev < 0 ? "disabled" : ""}>⇄ both ways</button>` +
      html`<button class="tool" data-act="clear-link">✕ clear</button></div></div>`,
  ];
  for (const e of list) {
    const E = EDGES[e];
    const lost = E.lost - E.inFlight;
    const max = Math.max(1, ...E.types.values());
    const lostRecs = E.sends.filter(s => sendStatus[s] === S_LOST);
    const fl = E.sends.filter(s => sendStatus[s] === S_FLIGHT);
    const bars = [...E.types]
      .sort((a, b) => b[1] - a[1])
      .map(
        ([t, c]) =>
          html`<span>${t ?? "?"}</span><span class="bar" style="width:${Math.max(4, (100 * c) / max)}%"></span><span>${nf(c)}</span>`,
      );
    const lostBtn = s =>
      html`<button data-go="${s}">${RECORDS[s].id} · ${typeOf(s) || "message"} · ${recTime(s)}</button>`;
    const flBtn = s => html`<button class="fl" data-go="${s}">${RECORDS[s].id} · ${typeOf(s) || "message"}</button>`;
    const counts =
      html`${nf(E.sent)} sent · ${nf(E.received)} received` +
      (lost ? html` · <span class="lo">${nf(lost)} lost</span>` : "") +
      (E.inFlight ? ` · ${nf(E.inFlight)} in flight` : "");
    parts.push(
      html`<div class="conn">${list.length > 1 ? html`<h3>${E.from} → ${E.to}</h3>` : ""}` +
        html`<p class="cnts">${raw(counts)}</p><div class="tb">${bars}</div>` +
        (edgeRange(E) ? html`<p class="hint mt8">first ${timeLabel(E.first)} · last ${timeLabel(E.last)}</p>` : "") +
        (lostRecs.length
          ? html`<h3 class="mt10">Lost</h3><div class="lostl">${lostRecs.slice(0, 40).map(lostBtn)}</div>`
          : "") +
        (fl.length ? html`<h3 class="mt10">In flight</h3><div class="lostl">${fl.map(flBtn)}</div>` : "") +
        `</div>`,
    );
  }
  return parts.join("");
}

// the notation icons are fixed markup
const ico = body => raw(`<svg width="28" height="16" viewBox="0 0 28 16" aria-hidden="true">${body}</svg>`);

const dot = (k, extra = "") => `<circle cx="14" cy="8" r="4.5" fill="var(--c-${k})"${extra}/>`;

const NOTATION = [
  [ico(dot("message")), "sent, started, finished, log"],
  [ico(`<circle cx="14" cy="8" r="3.5" fill="var(--panel)" stroke="var(--c-message)" stroke-width="2"/>`), "received"],
  [
    ico(`<rect x="10.5" y="4.5" width="7" height="7" rx="1" fill="var(--c-event)" transform="rotate(45 14 8)"/>`),
    "event emitted, run",
  ],
  [ico(`<rect x="9.5" y="3.5" width="9" height="9" rx="2" fill="var(--c-task)"/>`), "task scheduled"],
  [ico(`<circle cx="14" cy="8" r="3.5" fill="var(--c-message)"/>`), "handler call, task cycle"],
  [ico(`<path d="M10 4l8 8M18 4l-8 8" stroke="var(--lost)" stroke-width="2"/>`), "lost: where it should have arrived"],
  [
    ico(
      `<path d="M2 3H9Q12 3 12 6V10Q12 13 15 13H20" fill="none" stroke="var(--edge-msg)" stroke-width="1.4"/><path d="M26 13l-6 3v-6z" fill="var(--edge-msg)"/>`,
    ),
    "message, down to its receipt",
  ],
  [
    ico(
      `<path d="M3 2V6H25M10 6V12M18 6V12" fill="none" stroke="var(--edge-evt)" stroke-width="1.4"/><path d="M10 15l-2.4-4h4.8zM18 15l-2.4-4h4.8z" fill="var(--edge-evt)"/>`,
    ),
    "event, to its receivers",
  ],
  [ico(`<rect x="11.5" y="1" width="5" height="14" rx="2.5" fill="var(--bar-run)"/>`), "task running"],
  [
    ico(
      `<path d="M14 1.5V14.5" stroke="var(--c-wait)" stroke-width="1.6" stroke-dasharray="0 3.4" stroke-linecap="round"/>`,
    ),
    "task waiting",
  ],
  [
    ico(`<path d="M3 8H25" stroke="var(--ancestor)" stroke-width="2.25" stroke-linecap="round"/>`),
    "started by: the cause chain",
  ],
  [ico(`<path d="M3 8H25" stroke="var(--accent)" stroke-width="1.6" stroke-linecap="round"/>`), "led to"],
  [
    ico(
      `<rect x="5.5" y="0.5" width="17" height="15" rx="7.5" fill="var(--panel)" stroke="var(--ancestor)"/><text x="14" y="11.5" text-anchor="middle"` +
        ` font-size="10" font-weight="600" fill="var(--ancestor)" font-family="var(--font-data)">1</text>`,
    ),
    "the chain reaches another agent",
  ],
  [
    ico(`<rect x="0.5" y="2.5" width="27" height="11" fill="url(#hatch)" stroke="var(--rule)"/>`),
    "gap: a stretch of time without records",
  ],
];

const notationHTML = () =>
  html`<div class="notation">${NOTATION.map(([i, t]) => html`<div>${i}<span>${t}</span></div>`)}</div>`;

const keysNote = document.createElement("section");

function idleHTML() {
  const lanes = state.view === "lanes";
  const swatches = presentCats.map(c => html`<span style="--k: var(--c-${c})">${CAT_LABEL[c]}</span>`);
  return (
    html`<div><h3>Cause chain</h3><p class="hint">Select a record to see what started it and everything it led to.` +
    html` Every record names its <code>cause</code>: the message, event or task it happened in.</p></div>` +
    (lanes ? html`<div><h3>Reading the lanes</h3>${notationHTML()}</div>` : "") +
    (presentCats.length ? html`<div><h3>Categories</h3><div class="swatches">${swatches}</div></div>` : "") +
    html`<p class="hint">Press <kbd>?</kbd> for keyboard shortcuts${lanes ? " and this notation" : ""}.</p>`
  );
}

function selectionHTML(s) {
  const r = RECORDS[s];
  const path = pathOfSel();
  const lvl = levelNames[levelOf[s]];
  let h =
    html`<div><h2>${r.event}</h2><p class="sub">${laneName(laneOf[s])} · ${recTime(s)}` +
    html` · <span class="lv" style="--k:${levelColor(lvl)}">${lvl}</span></p></div>` +
    html`<div class="pacts"><button class="tool" data-act="follow" ${nAgents ? "" : "disabled"}>Follow chain in Lanes <kbd>f</kbd></button></div>`;
  if (!vis[s]) h += html`<p class="note">The selected record is hidden by the filters.</p>`;
  else if (state.view === "lanes" && state.lanes && !state.lanes.includes(laneOf[s])) {
    const name = laneName(laneOf[s]);
    h += html`<p class="note">Its lane, <b>${name}</b>, is not followed. <button data-act="add-lane">Follow ${name}</button></p>`;
  }
  h += messageNote(s);
  h += `<div><h3>Started by</h3>`;
  if (chain.length) {
    const steps = path.map((i, k) => {
      const d = k ? bandTimeOf(i) - bandTimeOf(path[k - 1]) : 0;
      return itemHTML(i, i === s, hopOfRec.get(i) || "", d > 0 ? dur(d) : "");
    });
    h += html`<ol class="chain${hops.length ? " hops" : ""}">${steps}</ol>`;
  } else h += `<p class="hint">Nothing recorded: this record was not caused by a traced message, event or task.</p>`;
  h += html`</div><div><h3>Led to · ${nf(descList.length)}</h3>`;
  if (descList.length) {
    const tally = {};
    for (const d of descList) tally[RECORDS[d].event] = (tally[RECORDS[d].event] || 0) + 1;
    const where = state.view === "lanes" ? "lanes" : "table";
    h +=
      html`<div class="tally">${Object.entries(tally).map(([e, n]) => html`<span>${e} ×${nf(n)}</span>`)}</div>` +
      html`<ol class="chain mt8">${descList.slice(0, 60).map(d => itemHTML(d))}</ol>` +
      (descList.length > 60
        ? html`<p class="hint">and ${nf(descList.length - 60)} more, highlighted in the ${where}</p>`
        : "");
  } else {
    const why =
      r.id == null
        ? "Log lines have no id, so nothing points back to them."
        : "No later record names this one as its cause.";
    h += html`<p class="hint">${why}</p>`;
  }
  const fields = Object.keys(r).filter(k => !k.startsWith("__"));
  h += html`</div><div><h3>Fields</h3><dl class="kv">${fields.map(k => html`<dt>${k}</dt><dd>${fmt(r[k])}</dd>`)}</dl></div>`;
  return h;
}

// the selection with what started it and what it led to; without one the connection filter, or how to read the view
export function renderPanel() {
  if (state.sel != null) panel.innerHTML = selectionHTML(state.sel);
  else if (state.link) panel.innerHTML = connectionHTML();
  else panel.innerHTML = idleHTML();
}

export function initPanel() {
  keysNote.className = "knote";
  keysNote.innerHTML = html`<h3>Lanes notation</h3>${notationHTML()}`;
  $("keys").querySelector(".kc").after(keysNote);
  panel.addEventListener("click", e => {
    const b = e.target.closest("[data-go]");
    if (b) select(+b.dataset.go, { reveal: true });
  });
}
