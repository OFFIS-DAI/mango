import { CATS, E_EMIT, E_EVRECV, E_ROLE, E_SENT } from "../core/schema.js";
import { clamp, lowerBound, natural, plural } from "../core/util.js";
import {
  AGENTS,
  EDGES,
  FAILS,
  NODES,
  WORLD,
  aIx,
  causeIx,
  degreeOf,
  eachEffect,
  evOf,
  hasWorldRecs,
  idOf,
  laneFailed,
  laneName,
  laneOf,
  laneTotal,
  nAgents,
  nRecords,
  presentLevels,
  recvOf,
  reverseEdge,
  rowOf,
} from "../model.js";
import { descList, pathOfSel, rel } from "../selection.js";
import { pending, state, update } from "../state.js";
import { announce, isPhone, store } from "./dom.js";
import { lanesBox, recBox } from "./elements.js";
import { emit } from "./events.js";
import { hideTip, toast } from "./feedback.js";
import { gutterWidth } from "./lanes/geometry.js";
import { layout } from "./lanes/layout.js";

export function select(i, opts = {}) {
  if (i != null && (i < 0 || i >= nRecords)) return;
  if (i != null && i === state.sel && !opts.reveal) i = null;
  if (i != null && opts.reveal) {
    ensureLane(i, opts.leftOf ?? rel[i] === 1);
    pending.reveal = i;
  }
  hideTip();
  update(i != null ? { sel: i, cursor: i } : { sel: i });
}

// keepLanes: the caller has just chosen the lanes (Follow chain, partners, picker), so the Agent filter is not added to them
export function setView(v, opts = {}) {
  if (v === state.view || (v === "lanes" && !nAgents)) return;
  emit("leaving-view");
  store.set("mango-trace-view", v);
  const patch = { view: v };
  if (v === "lanes") {
    if (!state.lanes) patch.lanes = defaultLanes();
    if (
      !opts.keepLanes &&
      !state.link &&
      state.agent !== "" &&
      aIx.has(state.agent) &&
      !(patch.lanes || state.lanes).includes(aIx.get(state.agent))
    ) {
      const lanes = (patch.lanes || state.lanes).slice();
      const at = lanes[0] === WORLD ? 1 : 0;
      lanes.splice(at, 0, aIx.get(state.agent));
      patch.lanes = lanes;
    }
  }
  hideTip();
  update(patch);
}

export function setOrder(o) {
  update({ order: o });
  announce(o === "file" ? "File order: one record per row" : "Packed by cause");
}

export function setFit(f) {
  update({ fit: f });
  announce(f ? "All lanes fit the width" : "Lanes at reading width");
}

export const failPos = i => lowerBound(FAILS, i);

export function nextFail(back) {
  if (!FAILS.length) return;
  const s = state.sel;
  let k;
  if (s == null) k = back ? FAILS.length - 1 : 0;
  else if (back) {
    k = failPos(s) - 1;
    if (k < 0) k = FAILS.length - 1;
  } else {
    k = failPos(s + 1);
    if (k >= FAILS.length) k = 0;
  }
  select(FAILS[k], { reveal: true });
}

export function setFilter(patch) {
  if (patch.cats) store.set("mango-trace-cats", JSON.stringify([...patch.cats]));
  update(patch);
}

export function clearFilters() {
  setLink(null);
  setFilter({ q: "", agent: "", onlyRelated: false, cats: new Set(CATS), levels: new Set(presentLevels) });
}

export const hubOrder = ids =>
  ids.slice().sort((a, b) => degreeOf(b) - degreeOf(a) || natural(String(AGENTS[a]), String(AGENTS[b])));

export const withWorld = lanes => (hasWorldRecs && !lanes.includes(WORLD) ? [WORLD, ...lanes] : lanes);

function laneCapacity() {
  const box = lanesBox.clientWidth || recBox.clientWidth || 800;
  return clamp(Math.floor((box - gutterWidth() - (hasWorldRecs ? (isPhone() ? 40 : 56) : 0)) / 120), 2, 8);
}

export function defaultLanes() {
  const k = laneCapacity();
  const all = AGENTS.map((a, i) => i);
  if (nAgents <= k) {
    const hubs = hubOrder(all).filter(l => degreeOf(l) >= Math.max(4, 0.4 * (nAgents - 1)));
    return withWorld(hubs.concat(all.filter(l => !hubs.includes(l))));
  }
  let S = [];
  if (state.agent !== "" && aIx.has(state.agent)) S.push(aIx.get(state.agent));
  if (state.sel != null) {
    for (const i of pathOfSel()) if (laneOf[i] !== WORLD) S.push(laneOf[i]);
    const cnt = new Map();
    if (idOf[state.sel] >= 0)
      eachEffect(idOf[state.sel], j => {
        if (laneOf[j] !== WORLD) cnt.set(laneOf[j], (cnt.get(laneOf[j]) || 0) + 1);
      });
    S.push(...[...cnt.keys()].sort((a, b) => cnt.get(b) - cnt.get(a)));
  }
  S = [...new Set(S)].slice(0, k);
  const failing = all.filter(l => laneFailed[l]).sort((a, b) => laneFailed[b] - laneFailed[a]);
  const fillers = [...failing, ...hubOrder(all), ...all.slice().sort((a, b) => laneTotal[b] - laneTotal[a])];
  for (const l of fillers) {
    if (S.length >= Math.min(4, k)) break;
    if (!S.includes(l)) S.push(l);
  }
  return withWorld(S);
}

export function setLanes(lanes, opts = {}) {
  if (opts.flash != null) pending.flashLane = opts.flash;
  update({ lanes });
}

export const undoLanes = prev => ({ label: "Undo", run: () => setLanes(prev) });

export function insertSorted(lanes, l) {
  const out = lanes.slice();
  if (l === WORLD) return [WORLD, ...out];
  let at = out.length;
  for (let k = 0; k < out.length; k++)
    if (out[k] !== WORLD && out[k] > l) {
      at = k;
      break;
    }
  out.splice(at, 0, l);
  return out;
}

// when the cursor's lane goes, the cursor moves to the lane that takes its place, at the same row
export function toggleLane(l, quiet) {
  const prev = state.lanes ? state.lanes.slice() : [];
  const on = prev.includes(l);
  if (
    on &&
    layout &&
    state.view === "lanes" &&
    state.cursor != null &&
    laneOf[state.cursor] === l &&
    rowOf[state.cursor] >= 0
  ) {
    pending.cursorAt = { c: layout.col[l], row: rowOf[state.cursor], focus: lanesBox.contains(document.activeElement) };
  }
  setLanes(on ? prev.filter(x => x !== l) : insertSorted(prev, l), { flash: on ? null : l });
  const spoken = `${on ? "Stopped following" : "Added lane"} ${laneName(l)}`;
  if (quiet) announce(spoken);
  else toast(`${on ? "Stopped following" : "Added"} ${laneName(l)}`, [undoLanes(prev)], spoken);
}

export function ensureLane(i, leftOf) {
  if (state.view !== "lanes" || !state.lanes) return;
  const l = laneOf[i];
  if (state.lanes.includes(l) || (l === WORLD && !hasWorldRecs)) return;
  const prev = state.lanes.slice();
  const lanes = prev.slice();
  const cur = state.cursor != null ? lanes.indexOf(laneOf[state.cursor]) : -1;
  lanes.splice(cur < 0 ? lanes.length : leftOf ? cur : cur + 1, 0, l);
  setLanes(lanes, { flash: l });
  toast(`Added ${laneName(l)}`, [undoLanes(prev)], `Added lane ${laneName(l)}`);
}

export function followChain(all) {
  if (state.sel == null) {
    toast("Select a record to follow its chain");
    return;
  }
  const path = pathOfSel();
  const out = [];
  for (const i of path) if (laneOf[i] !== WORLD && !out.includes(laneOf[i])) out.push(laneOf[i]);
  const cnt = new Map();
  for (const d of descList) if (laneOf[d] !== WORLD) cnt.set(laneOf[d], (cnt.get(laneOf[d]) || 0) + 1);
  for (const l of [...cnt.keys()].sort((a, b) => cnt.get(b) - cnt.get(a))) if (!out.includes(l)) out.push(l);
  const total = out.length;
  const lanes = all ? out : out.slice(0, 8);
  if (path.some(i => laneOf[i] === WORLD) && hasWorldRecs) lanes.unshift(WORLD);
  const prev = state.lanes ? state.lanes.slice() : null;
  setLanes(lanes);
  if (state.view !== "lanes") setView("lanes", { keepLanes: true });
  const n = lanes.filter(l => l !== WORLD).length;
  const acts = prev ? [undoLanes(prev)] : [];
  const spoken = `Followed ${plural(n, "agent")}`;
  if (total > n)
    toast(
      `${n} of ${total} agents in this chain`,
      [{ label: "Show all", run: () => followChain(true) }, ...acts],
      spoken,
    );
  else toast(`Following ${plural(n, "agent")} of this chain`, acts, spoken);
  pending.reveal = state.sel;
}

export function nodeAction(n, partners) {
  const d = NODES[n];
  if (partners) {
    followPartners(n);
    return;
  }
  if (d.lane < 0) {
    toast(`${d.id} has no records in this trace`);
    return;
  }
  if (state.view === "records") {
    const v = state.agent === AGENTS[d.lane] ? "" : String(AGENTS[d.lane]);
    setFilter({ agent: v });
    announce(v ? `Filtered to agent ${v}` : "Agent filter cleared");
  } else toggleLane(d.lane);
}

function followPartners(n) {
  const d = NODES[n];
  const others = [...d.partners].filter(k => k !== n).sort((a, b) => natural(String(NODES[a].id), String(NODES[b].id)));
  const ids = [n, ...others].filter(k => NODES[k].lane >= 0).map(k => NODES[k].lane);
  if (!ids.length) return;
  const prev = state.lanes ? state.lanes.slice() : null;
  setLanes(ids);
  if (state.view !== "lanes") setView("lanes", { keepLanes: true });
  toast(
    `Following ${d.id} and ${plural(ids.length - 1, "partner")}`,
    prev ? [undoLanes(prev)] : [],
    `Followed ${plural(ids.length, "agent")}`,
  );
}

// prev is null when the filter came with the page (a shared link); clearing it then falls back to the default lanes
let linkSaved = null;

export function linkFromPage() {
  linkSaved = { prev: null, applied: state.lanes ? state.lanes.slice() : [] };
}

export function setLink(e, both) {
  if (e == null) {
    if (!state.link) return;
    update({ link: null });
    if (linkSaved && state.lanes && String(state.lanes) === String(linkSaved.applied))
      setLanes(linkSaved.prev || defaultLanes());
    linkSaved = null;
    announce("Connection filter cleared");
  } else {
    const E = EDGES[e];
    const rev = reverseEdge(e);
    if (!state.link || !linkSaved) linkSaved = { prev: state.lanes && state.lanes.length ? state.lanes.slice() : null };
    update({ link: { e, both: !!both } });
    const lanes = [...new Set([NODES[E.a].lane, NODES[E.b].lane].filter(l => l >= 0))];
    linkSaved.applied = lanes;
    setLanes(lanes);
    const n = E.sent + (both && rev >= 0 ? EDGES[rev].sent : 0);
    const what = both && rev >= 0 ? `between ${E.from} and ${E.to}` : `from ${E.from} to ${E.to}`;
    announce(`Showing ${plural(n, "message")} ${what}`);
  }
}

export function goCause() {
  if (state.sel == null) return;
  const c = causeIx[state.sel];
  if (c >= 0 && c !== state.sel) select(c, { reveal: true, leftOf: true });
}

export function goEffect() {
  const s = state.sel;
  if (s == null) return;
  let t = -1;
  if (evOf[s] === E_SENT) t = recvOf(s);
  else if (evOf[s] === E_EMIT && idOf[s] >= 0)
    eachEffect(idOf[s], j => {
      if (t < 0 && (evOf[j] === E_EVRECV || evOf[j] === E_ROLE)) t = j;
    });
  if (t < 0) t = descList.find(d => d > s) ?? descList[0] ?? -1;
  if (t >= 0) select(t, { reveal: true });
}
