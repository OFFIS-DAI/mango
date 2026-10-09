import { plural } from "../../core/util.js";
import { EDGES, NODES, laneOf, nAgents, nRecords, unresolved } from "../../model.js";
import { state } from "../../state.js";
import { $, announce, isPhone, store } from "../dom.js";
import { computeTopoLayout, topoB, topoCard, topoLayout, topoSvg, topoView } from "./draw.js";
import { drawMap, mapState, nodeOfLane, topoMeta } from "./highlight.js";
import { focusNode } from "./interact.js";
import { renderTopoList } from "./list.js";

const lg = (d, extra) => `<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">${d}</svg>${extra}`;

const quietLine = '<path d="M1 5h20" stroke="var(--edge-quiet)" stroke-width="1.5"/>';

function setTopoMode(mode) {
  state.topo.mode = mode;
  $("tm-graph").setAttribute("aria-pressed", mode === "graph");
  $("tm-list").setAttribute("aria-pressed", mode === "list");
  topoSvg.toggleAttribute("hidden", mode !== "graph" || !EDGES.length);
  $("topo-list").hidden = mode !== "list" || !EDGES.length;
  saveTopo();
  if (mode === "list") renderTopoList();
  else drawMap();
}

function toggleTopo(open) {
  if (!EDGES.length) return;
  state.topo.open = open == null ? !state.topo.open : open;
  topoCard.classList.toggle("open", state.topo.open);
  $("topo-toggle").setAttribute("aria-expanded", state.topo.open);
  saveTopo();
  if (state.topo.open)
    requestAnimationFrame(() => {
      if (state.topo.mode === "graph") drawMap();
      else renderTopoList();
    });
}

export function toggleWide() {
  if (!EDGES.length) return;
  state.topo.wide = !state.topo.wide;
  $("main").classList.toggle("wide", state.topo.wide);
  $("topo-wide").setAttribute("aria-pressed", state.topo.wide);
  $("topo-wide").setAttribute("aria-label", state.topo.wide ? "Narrow the map" : "Widen the map");
  saveTopo();
}

const saveTopo = () =>
  store.set(
    "mango-trace-topo",
    JSON.stringify({ open: state.topo.open, wide: state.topo.wide, mode: state.topo.mode }),
  );

let topoResize = 0;

let lastTopoSize = "";

export function firstTopology() {
  if (!EDGES.length) return;
  computeTopoLayout();
  if (state.topo.open) {
    if (state.topo.mode === "graph") drawMap();
    else renderTopoList();
  }
  lastTopoSize = topoB.clientWidth + "x" + topoB.clientHeight;
}

export function initTopoCard() {
  // the map starts open where it has room and something to show; a very large one as a list
  if (!EDGES.length) state.topo.open = true;
  if (isPhone() || nAgents === 1) state.topo.open = false;
  if (NODES.length > 1000) state.topo.mode = "list";
  topoCard.classList.toggle("open", state.topo.open);
  $("topo-toggle").setAttribute("aria-expanded", state.topo.open);
  $("main").classList.toggle("wide", state.topo.wide);
  $("topo-wide").setAttribute("aria-pressed", state.topo.wide);
  $("tm-graph").setAttribute("aria-pressed", state.topo.mode === "graph");
  $("tm-list").setAttribute("aria-pressed", state.topo.mode === "list");
  if (!nRecords || !nAgents) topoCard.hidden = true;
  if (!EDGES.length) {
    topoCard.classList.add("nomsg");
    topoCard.classList.remove("open");
    $("topo-toggle").setAttribute("aria-expanded", "false");
    $("topo-toggle").setAttribute("aria-disabled", "true");
    topoSvg.setAttribute("hidden", "");
  } else {
    topoSvg.toggleAttribute("hidden", state.topo.mode !== "graph");
    $("topo-list").hidden = state.topo.mode !== "list";
  }
  topoMeta.addEventListener("click", e => {
    if (!e.target.closest("#topo-lossy")) return;
    state.topo.lossy = !state.topo.lossy;
    mapState(false);
    announce(state.topo.lossy ? "Showing lossy connections" : "Showing all connections");
  });
  $("topo-legend").innerHTML =
    `<span>${lg(
      '<path d="M1 5h18" stroke="var(--edge-msg)" stroke-width="2" stroke-linecap="round"/>' +
        '<path d="M15 2l5 3-5 3z" fill="var(--edge-msg)"/>',
      "message, width = count",
    )}</span>` +
    `<span>${lg(quietLine + '<path d="M11 1.5v7" stroke="var(--lost)" stroke-width="2.2" stroke-linecap="round"/>', "lost")}</span>` +
    `<span>${lg(quietLine + '<circle cx="11" cy="5" r="3.2" fill="var(--panel)" stroke="var(--in-flight)" stroke-width="1.5"/>', "in flight")}</span>` +
    `<span>${lg(
      '<circle cx="11" cy="5" r="4" fill="var(--panel)" stroke="var(--node-ring)" stroke-width="1.3" stroke-dasharray="2 2"/>',
      "no messages",
    )}</span>` +
    (unresolved ? `<span class="warnu">${plural(unresolved, "send")} without a known receiver</span>` : "");
  $("topo-toggle").addEventListener("click", () => toggleTopo());
  $("topo-wide").addEventListener("click", toggleWide);
  $("tm-graph").addEventListener("click", () => setTopoMode("graph"));
  $("tm-list").addEventListener("click", () => setTopoMode("list"));
  new ResizeObserver(() => {
    clearTimeout(topoResize);
    topoResize = setTimeout(() => {
      const sz = topoB.clientWidth + "x" + topoB.clientHeight;
      if (sz === lastTopoSize || !topoLayout) return;
      lastTopoSize = sz;
      drawMap();
    }, 100);
  }).observe(topoB);
}

export function focusMap() {
  if (!state.topo.open) toggleTopo(true);
  if (state.topo.mode === "list") {
    $("topo-list").querySelector("tbody tr")?.focus();
    return;
  }
  if (!topoView) drawMap();
  if (!topoView) return;
  const s = state.sel != null ? nodeOfLane(laneOf[state.sel]) : -1;
  focusNode(s >= 0 ? s : (topoLayout.hubList[0] ?? topoLayout.linked[0] ?? 0));
}
