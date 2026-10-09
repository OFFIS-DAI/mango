import { E_RECV, E_SENT } from "../../core/schema.js";
import { nf, plural, round1 } from "../../core/util.js";
import {
  EDGES,
  IN_FLIGHT,
  LOSSY,
  NODES,
  WORLD,
  aIx,
  edgeOfRec,
  evOf,
  laneName,
  laneOf,
  nAgents,
  nRecv,
  reverseEdge,
} from "../../model.js";
import { chain, descList, hops, pathOfSel } from "../../selection.js";
import { state } from "../../state.js";
import { $ } from "../dom.js";
import { html } from "../html.js";
import {
  BULK,
  badgeShift,
  countPill,
  countPillWidth,
  drawTopology,
  mapSets,
  topoLayout,
  topoSvg,
  topoView,
} from "./draw.js";
import { placeLabels } from "./labels.js";
import { filtersActive, linkMatch } from "../../visibility.js";

export const nodeOfLane = l => (l >= 0 && l < nAgents ? l : -1);

let labelsKey = "";

export function mapState(forceLabels) {
  if (!topoView) return;
  const s = state.sel;
  const fol = new Set();
  if (state.view === "lanes") {
    for (const l of state.lanes || []) if (l < nAgents) fol.add(l);
  } else if (state.agent !== "" && aIx.has(state.agent)) fol.add(aIx.get(state.agent));
  const chainSet = new Set();
  const dag = new Set();
  for (const c of chain) {
    const n = nodeOfLane(laneOf[c]);
    if (n >= 0) chainSet.add(n);
  }
  for (const d of descList) {
    const n = nodeOfLane(laneOf[d]);
    if (n >= 0) dag.add(n);
  }
  const selN = s != null ? nodeOfLane(laneOf[s]) : -1;
  mapSets.fol = fol;
  mapSets.chain = chainSet;
  mapSets.dag = dag;
  mapSets.sel = selN;
  mapSets.hops = new Set(hops.filter(h => h.edge >= 0).map(h => h.edge));
  const key = [selN, [...fol], [...chainSet], [...dag], hops.map(h => h.edge + ">" + h.to)].join("|");
  const labelsDirty = forceLabels || key !== labelsKey;
  labelsKey = key;
  // with every agent followed the mark would be on every node and say nothing
  const folShown = fol.size < nAgents ? fol : new Set();
  topoSvg.classList.toggle("has-sel", s != null);
  topoSvg.classList.toggle("has-fol", folShown.size > 0);
  topoSvg.classList.toggle("lossy", state.topo.lossy);
  const ghosting = filtersActive;
  topoSvg.classList.toggle("ghosting", ghosting);
  topoView.nodeEls.forEach((el, i) => {
    if (!el) return;
    el.classList.toggle("fol", folShown.has(i));
    el.classList.toggle("sel", i === selN);
    el.classList.toggle("anc", i !== selN && chainSet.has(i));
    el.classList.toggle("dag", i !== selN && !chainSet.has(i) && dag.has(i));
    el.setAttribute("aria-pressed", fol.has(i));
    const bd = el.querySelector(".bdg");
    if (bd) {
      const out = badgeShift(i, i === selN || chainSet.has(i) || dag.has(i));
      bd.setAttribute("transform", out ? `translate(${round1(out.x)},${round1(out.y)})` : "");
    }
  });
  topoView.linkEls.forEach((el, e) => {
    if (!el) return;
    el.classList.toggle("ghost", ghosting && !linkMatch[e] && !mapSets.hops.has(e));
    el.setAttribute(
      "aria-pressed",
      !!state.link && (state.link.e === e || (state.link.both && reverseEdge(state.link.e) === e)),
    );
  });
  if (BULK) drawBulkLayers();
  drawHot();
  if (labelsDirty) placeLabels();
  rovingNode();
  renderTopoMeta();
  renderRoute();
  if (state.topo.mode === "list") syncTopoList();
}

export function bulkD(set) {
  let d = "";
  for (const e of set) d += topoView.geo[e].straight;
  return d;
}

function drawBulkLayers() {
  const m = topoSvg.querySelector(".bulk-match");
  const lv = topoSvg.querySelector(".bulk-live");
  if (!m) return;
  m.innerHTML = filtersActive
    ? `<path class="lk-b on" d="${bulkD(EDGES.filter(e => linkMatch[e.k] > 0).map(e => e.k))}"/>`
    : "";
  lv.innerHTML =
    (mapSets.live.size ? `<path class="lk-b live" d="${bulkD(mapSets.live)}"/>` : "") +
    (state.topo.lossy ? `<path class="lk-b lossyl" d="${bulkD(EDGES.filter(e => e.lossy).map(e => e.k))}"/>` : "");
}

function drawHot() {
  const hot = $("topo-hot");
  const pills = $("topo-pills");
  if (!hot || !topoView) return;
  const s = state.sel;
  const hopEdges = new Set(hops.map(h => h.edge).filter(e => e >= 0));
  syncCountPills(hopEdges);
  // the filtered connection is what the user is looking at: full strength, on a soft casing, whatever is selected
  const flt = [];
  if (state.link) {
    for (const e of [state.link.e, state.link.both ? reverseEdge(state.link.e) : -1]) {
      if (e < 0) continue;
      const w = topoView.width(EDGES[e]);
      const cd = topoView.cutD(e);
      flt.push(
        `<path class="fltc" d="${topoView.geo[e].d}" stroke-width="${(w + 6).toFixed(2)}"/>` +
          `<path class="flt" d="${topoView.geo[e].d}" stroke-width="${(w + 1).toFixed(2)}"/>` +
          (cd ? `<path class="cuth" d="${cd}"/><path class="cut" d="${cd}"/>` : ""),
      );
    }
  }
  if (s == null) {
    hot.innerHTML = flt.join("");
    pills.innerHTML = "";
    return;
  }
  const { P, R } = topoView.fit;
  const out = flt;
  const pl = [];
  const desc = new Set();
  for (const d of descList) {
    const e = edgeOfRec[d];
    if (e >= 0 && !hopEdges.has(e)) desc.add(e);
  }
  for (const e of desc)
    out.push(
      `<path class="desc" d="${topoView.geo[e].d}" stroke-width="${(topoView.width(EDGES[e]) + 0.6).toFixed(2)}"/>`,
    );
  for (const h of hops) {
    let mid;
    if (h.edge >= 0) {
      const g = topoView.geo[h.edge];
      out.push(`<path class="anc" d="${g.d}"/>`);
      mid = g.mid;
    } else {
      const a = P[h.from];
      const b = P[h.to];
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const l = Math.hypot(dx, dy) || 1;
      const x0 = a.x + (dx / l) * (R[h.from] + 3);
      const y0 = a.y + (dy / l) * (R[h.from] + 3);
      const x1 = b.x - (dx / l) * (R[h.to] + 3);
      const y1 = b.y - (dy / l) * (R[h.to] + 3);
      out.push(`<path class="anc ev" d="M${round1(x0)},${round1(y0)} L${round1(x1)},${round1(y1)}"/>`);
      mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
    const w = h.n > 9 ? 24 : 17;
    const lab = `Step ${h.n}: ${laneName(h.from)} to ${laneName(h.to)}, select it`;
    pl.push(
      html`<g class="tp anc" data-hop="${h.n}" role="button" tabindex="-1" aria-label="${lab}"` +
        ` transform="translate(${round1(mid.x)},${round1(mid.y)})"><rect x="${-w / 2}" y="-8.5" width="${w}" height="17" rx="8.5"/>` +
        `<text text-anchor="middle" dy="3.7">${h.n}</text></g>`,
    );
  }
  const own = evOf[s] === E_SENT || evOf[s] === E_RECV ? edgeOfRec[s] : -1;
  if (own >= 0) {
    const g = topoView.geo[own];
    const end = evOf[s] === E_SENT ? g.at(0) : g.at(1);
    out.push(`<path class="own" d="${g.d}"/><circle class="odot" cx="${round1(end.x)}" cy="${round1(end.y)}" r="3"/>`);
  }
  hot.innerHTML = out.join("");
  pills.innerHTML = pl.join("");
}

// a numbered hop pill takes the place of the count pill on its connection (and on the twin curve beside it)
function syncCountPills(hopEdges) {
  $("topo-cnt")
    ?.querySelectorAll(".tp.cnt")
    .forEach(g => {
      const e = +g.dataset.e;
      g.classList.toggle("off", hopEdges.has(e) || hopEdges.has(reverseEdge(e)));
    });
}

// the connections used by the records on screen are drawn live
export function markLive(records) {
  if (!topoView) return;
  const next = new Set();
  for (const i of records) if (edgeOfRec[i] >= 0) next.add(edgeOfRec[i]);
  if (BULK) {
    mapSets.live = next;
    drawBulkLayers();
    return;
  }
  for (const e of mapSets.live) if (!next.has(e)) topoView.linkEls[e]?.classList.remove("live");
  for (const e of next) if (!mapSets.live.has(e)) topoView.linkEls[e]?.classList.add("live");
  mapSets.live = next;
}

function drawCounts() {
  const g = $("topo-cnt");
  if (!g || !topoView) return;
  if (EDGES.length > 12 || topoView.fit.dense) {
    g.innerHTML = "";
    return;
  }
  const placed = [];
  const { P, R } = topoView.fit;
  for (let i = 0; i < NODES.length; i++)
    if (P[i]) placed.push({ x: P[i].x - R[i] - 2, y: P[i].y - R[i] - 2, w: 2 * R[i] + 4, h: 2 * R[i] + 4 });
  for (const s of topoView.labels.values()) placed.push({ x: s.x, y: s.y, w: s.w, h: 13 });
  const overlaps = b => placed.some(p => b.x < p.x + p.w && p.x < b.x + b.w && b.y < p.y + p.h && p.y < b.y + b.h);
  let out = "";
  for (const e of EDGES) {
    const w = countPillWidth(e);
    const gm = topoView.geo[e.k];
    // a connection used both ways carries its pill outside its own curve, so the twin pills sit apart
    const off = gm.out && e.a !== e.b ? 11 : 0;
    for (const t of [0.5, 0.4, 0.6]) {
      const q = gm.at(t);
      const p = off ? { x: q.x + gm.out.x * off, y: q.y + gm.out.y * off } : q;
      const b = { x: p.x - w / 2, y: p.y - 8.5, w, h: 17 };
      if (overlaps(b)) continue;
      placed.push(b);
      out += countPill(`class="tp cnt" data-e="${e.k}"`, p, e);
      break;
    }
  }
  g.innerHTML = out;
  syncCountPills(new Set(hops.map(h => h.edge).filter(e => e >= 0)));
}

export const topoMeta = $("topo-meta");

export function renderTopoMeta() {
  if (!EDGES.length) {
    const why = nRecv
      ? "No message.sent records: the map is drawn from sends"
      : "No messages traced (message category off?)";
    topoMeta.textContent = `${why} · ${plural(NODES.length, "agent")}`;
    return;
  }
  const match = filtersActive ? EDGES.filter(e => linkMatch[e.k] > 0).length : EDGES.length;
  const conn = filtersActive
    ? `${nf(match)} of ${plural(EDGES.length, "connection")} match`
    : plural(EDGES.length, "connection");
  topoMeta.innerHTML =
    `${plural(NODES.length, "agent")} · ${conn} · <button class="lossy" id="topo-lossy" aria-pressed="${state.topo.lossy}"` +
    ` ${LOSSY ? "" : "disabled"} title="Highlight connections that lost messages">${nf(LOSSY)} lossy</button>` +
    (IN_FLIGHT ? ` · <span class="fl">${nf(IN_FLIGHT)} in flight</span>` : "");
}

export function renderRoute() {
  const el = $("topo-route");
  if (state.sel == null) {
    el.textContent = "Select a record to see its route";
    return;
  }
  const first = pathOfSel().find(i => laneOf[i] !== WORLD);
  if (first == null) {
    el.textContent = "The selection has no agent";
    return;
  }
  el.innerHTML = html`${laneName(laneOf[first])}${hops.map(h => html` <span class="hop">${h.n}</span> → ${laneName(h.to)}`)}`;
}

export function syncTopoList() {
  $("topo-list")
    .querySelectorAll("tbody tr")
    .forEach(tr => {
      const e = +tr.dataset.e;
      tr.classList.toggle("cur", !!state.link && state.link.e === e);
      tr.classList.toggle("off", filtersActive && !linkMatch[e]);
    });
}

export let mapFocusN = -1;

// one node is in the Tab order (the last focused, else the selection's agent, else the hub); arrows move from there
export function rovingNode() {
  if (!topoView) return;
  const ok = n => n >= 0 && !!topoView.nodeEls[n];
  const want = ok(mapFocusN)
    ? mapFocusN
    : ok(mapSets.sel)
      ? mapSets.sel
      : (topoLayout.hubList[0] ?? topoLayout.linked[0] ?? 0);
  topoView.nodeEls.forEach((el, i) => el && el.setAttribute("tabindex", i === want ? "0" : "-1"));
}

// draws the map and marks the selection, filters and hover on it
export function drawMap() {
  if (!drawTopology()) return;
  mapState(true);
  drawCounts();
}

export function setMapFocus(n) {
  mapFocusN = n;
}
