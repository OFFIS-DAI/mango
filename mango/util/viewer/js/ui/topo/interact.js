import { nf, plural } from "../../core/util.js";
import { EDGES, NODES, edgeOfRec, laneOf, nAgents, reverseEdge } from "../../model.js";
import { hops } from "../../selection.js";
import { state } from "../../state.js";
import { nodeAction, select, setLink } from "../actions.js";
import { edgeRange } from "../describe.js";
import { $ } from "../dom.js";
import { focusView } from "../elements.js";
import { emit, on } from "../events.js";
import { hideTip, setTipAction, showTip, tip } from "../feedback.js";
import { html } from "../html.js";
import { BULK, countPill, mapSets, topoLayout, topoSvg, topoView } from "./draw.js";
import { bulkD, mapFocusN, nodeOfLane, rovingNode, setMapFocus } from "./highlight.js";

let mapHov = null;

let peeked = [];

const nodeTip = n => {
  const d = NODES[n];
  const p = d.partners.size - (d.partners.has(n) ? 1 : 0);
  return (
    html`<div class="t1">${d.id}</div><div class="t2">${d.lane >= 0 ? plural(d.records, "record") : "no records in this trace"}` +
    (d.failed ? html` · <span class="lo">${nf(d.failed)} failed</span>` : "") +
    ` · sent ${nf(d.sent)} · received ${nf(d.received)} · ${plural(p, "partner")}</div>`
  );
};

const edgeTip = e => {
  const E = EDGES[e];
  const lost = E.lost - E.inFlight;
  const types = [...E.types]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([t, c]) => `${t ?? "?"} ${nf(c)}`)
    .join(" · ");
  return (
    html`<div class="t1">${E.from} → ${E.to}</div><div class="t2">${nf(E.sent)} sent · ${nf(E.received)} received` +
    (lost ? html` · <span class="lo">${nf(lost)} lost</span>` : "") +
    html`${E.inFlight ? ` · ${nf(E.inFlight)} in flight` : ""}</div>` +
    html`<div class="t2">${types}${edgeRange(E) ? " · " + edgeRange(E) : ""}</div>`
  );
};

const rectOf = el => (el.getBoundingClientRect ? el.getBoundingClientRect() : el);

export function setMapHover(h, anchorEl, quiet) {
  const key = h ? (h.n != null ? "n" + h.n : "e" + h.e) : "";
  if ((mapHov ? mapHov.key : "") === key) return;
  if (topoView) {
    topoSvg.querySelectorAll(".hov, .near, .split").forEach(el => el.classList.remove("hov", "near", "split"));
    $("topo-hp")?.remove();
    $("topo-hl")?.remove();
  }
  mapHov = h ? { ...h, key } : null;
  // the table and the lanes mark what is hovered too
  emit("map-hover", h ? (h.n != null ? { n: h.n } : { e: h.e }) : null);
  if (!h) {
    topoSvg.classList.remove("hovering");
    if (!quiet) hideTip();
    return;
  }
  if (topoView) topoSvg.classList.add("hovering");
  if (h.n != null) {
    const d = NODES[h.n];
    if (topoView) {
      topoView.nodeEls[h.n]?.classList.add("hov");
      for (const e of d.edges) {
        topoView.linkEls[e]?.classList.add("hov");
        topoView.nodeEls[EDGES[e].a === h.n ? EDGES[e].b : EDGES[e].a]?.classList.add("near");
      }
      if (BULK)
        $("topo-hot").insertAdjacentHTML("beforeend", `<path id="topo-hl" class="hovl" d="${bulkD(d.edges)}"/>`);
    }
    if (!quiet && anchorEl) showTip(nodeTip(h.n), rectOf(anchorEl), true);
    return;
  }
  const E = EDGES[h.e];
  if (topoView) {
    const el = topoView.linkEls[h.e];
    el?.classList.add("hov");
    if (el && (el.classList.contains("both") || el.classList.contains("twin"))) {
      el.classList.add("split");
      topoView.linkEls[reverseEdge(h.e)]?.classList.add("split");
    }
    topoView.nodeEls[E.a]?.classList.add("near");
    topoView.nodeEls[E.b]?.classList.add("near");
    if (BULK || !el)
      $("topo-hot").insertAdjacentHTML("beforeend", `<path id="topo-hl" class="hovl one" d="${topoView.geo[h.e].d}"/>`);
    $("topo-pills").insertAdjacentHTML(
      "beforeend",
      countPill(`id="topo-hp" class="tp cnt hot"`, topoView.geo[h.e].mid, E),
    );
  }
  if (!quiet && anchorEl) showTip(edgeTip(h.e), rectOf(anchorEl), true);
}

function peek(i) {
  if (!topoView) return;
  for (const el of peeked) el.classList.remove("peek");
  peeked = [];
  if (i == null || i < 0) return;
  const n = nodeOfLane(laneOf[i]);
  if (n >= 0 && topoView.nodeEls[n]) {
    topoView.nodeEls[n].classList.add("peek");
    peeked.push(topoView.nodeEls[n]);
  }
  const e = edgeOfRec[i];
  if (e >= 0 && topoView.linkEls[e]) {
    topoView.linkEls[e].classList.add("peek");
    peeked.push(topoView.linkEls[e]);
  }
}

function peekLane(l) {
  if (l == null || l >= nAgents) {
    if (mapHov && mapHov.fromLane) setMapHover(null);
    return;
  }
  setMapHover({ n: l, fromLane: true }, null, true);
}

function bulkHit(x, y) {
  if (!topoView || !topoView.grid) return -1;
  let best = -1;
  let bd = 64;
  const gx = Math.floor(x / 24);
  const gy = Math.floor(y / 24);
  for (let ox = -1; ox <= 1; ox++)
    for (let oy = -1; oy <= 1; oy++) {
      const list = topoView.grid.get(gx + ox + "," + (gy + oy));
      if (!list) continue;
      for (let k = 0; k < list.length; k += 3) {
        const d = (list[k + 1] - x) ** 2 + (list[k + 2] - y) ** 2;
        if (d < bd) {
          bd = d;
          best = list[k];
        }
      }
    }
  return best;
}

const svgPoint = e => {
  const r = topoSvg.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
};

function mapTarget(e) {
  const t = e.target;
  const pl = t.closest && t.closest(".tp[data-hop]");
  if (pl) return { hop: +pl.dataset.hop, el: pl };
  const nd = t.closest && t.closest(".nd");
  if (nd) return { n: +nd.dataset.n, el: nd };
  const lk = t.closest && t.closest(".lk");
  if (lk) return { e: +lk.dataset.e, el: lk };
  if (BULK) {
    const p = svgPoint(e);
    const b = bulkHit(p.x, p.y);
    const box = {
      left: e.clientX - 4,
      right: e.clientX + 4,
      top: e.clientY - 4,
      bottom: e.clientY + 4,
      width: 8,
      height: 8,
    };
    if (b >= 0) return { e: b, el: { getBoundingClientRect: () => box } };
  }
  return null;
}

let lastPointer = "mouse";

let touchArm = null;

export function focusNode(n) {
  if (!topoView || !topoView.nodeEls[n]) return;
  setMapFocus(n);
  rovingNode();
  topoView.nodeEls[n].focus();
}

function focusLink(e) {
  if (!topoView) return;
  let el = topoView.linkEls[e];
  if (!el) {
    $("topo-proxy")?.remove();
    const lab = `${EDGES[e].from} to ${EDGES[e].to}, ${plural(EDGES[e].sent, "message")}`;
    $("topo-pills").insertAdjacentHTML(
      "beforeend",
      html`<g id="topo-proxy" class="lk proxy" data-e="${e}" role="button" tabindex="-1" aria-label="${lab}">` +
        `<path class="ln" d="${topoView.geo[e].d}"/></g>`,
    );
    el = $("topo-proxy");
  }
  el.focus();
}

function stepLink(d, cur) {
  const n = mapFocusN;
  if (n < 0) return;
  const list = NODES[n].edges;
  if (!list.length) return;
  const k = cur < 0 ? (d > 0 ? 0 : list.length - 1) : (list.indexOf(cur) + d + list.length) % list.length;
  focusLink(list[k]);
}

export function initMap() {
  on("peek", peek);
  on("peek-lane", peekLane);
  topoSvg.addEventListener("pointermove", e => {
    if (e.pointerType === "touch") return;
    const t = mapTarget(e);
    if (!t || t.hop != null) {
      if (!touchArm) setMapHover(null);
      return;
    }
    setMapHover(t.n != null ? { n: t.n } : { e: t.e }, t.el);
  });
  topoSvg.addEventListener("pointerleave", () => {
    if (!touchArm) setMapHover(null);
  });
  topoSvg.addEventListener("pointerdown", e => {
    lastPointer = e.pointerType;
  });
  topoSvg.addEventListener("click", e => {
    const t = mapTarget(e);
    if (!t) {
      touchArm = null;
      hideTip();
      return;
    }
    if (t.hop != null) {
      const h = hops[t.hop - 1];
      if (h) select(h.rec, { reveal: true });
      return;
    }
    if (lastPointer === "touch") {
      const k = t.n != null ? "n" + t.n : "e" + t.e;
      if (touchArm !== k) {
        touchArm = k;
        let acts;
        if (t.n == null) acts = [[`Show ${plural(EDGES[t.e].sent, "message")}`, "main"]];
        else {
          const followed = mapSets.fol.has(t.n);
          const main =
            state.view === "lanes"
              ? followed
                ? "Stop following"
                : "Follow"
              : followed
                ? "Clear agent filter"
                : "Filter to this agent";
          acts = [
            [main, "main"],
            ["Follow with partners", "partners"],
          ];
        }
        const buttons = acts.map(([l, a]) => html`<button class="tool" data-tact="${a}">${l}</button>`).join("");
        setTipAction(a => {
          touchArm = null;
          hideTip();
          if (t.n != null) nodeAction(t.n, a === "partners");
          else setLink(t.e, false);
        });
        showTip(
          (t.n != null ? nodeTip(t.n) : edgeTip(t.e)) + `<div class="tacts">${buttons}</div>`,
          rectOf(t.el),
          true,
          true,
        );
        return;
      }
      touchArm = null;
      hideTip();
    }
    if (t.n != null) nodeAction(t.n, e.shiftKey);
    else setLink(t.e, false);
  });
  topoSvg.addEventListener("focusin", e => {
    const nd = e.target.closest(".nd");
    const lk = e.target.closest(".lk");
    if (nd) {
      setMapFocus(+nd.dataset.n);
      rovingNode();
      setMapHover({ n: mapFocusN }, nd);
    } else if (lk) setMapHover({ e: +lk.dataset.e }, lk);
  });
  topoSvg.addEventListener("focusout", e => {
    if (!topoSvg.contains(e.relatedTarget) && !tip.contains(e.relatedTarget) && !touchArm) setMapHover(null);
  });
  topoSvg.addEventListener("keydown", e => {
    const el = document.activeElement;
    if (!topoView || !el) return;
    const nd = el.closest(".nd");
    const lk = el.closest(".lk");
    const pl = el.closest(".tp[data-hop]");
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (lk) focusNode(mapFocusN >= 0 ? mapFocusN : EDGES[+lk.dataset.e].a);
      else {
        setMapHover(null);
        focusView();
      }
      return;
    }
    if (pl && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      const h = hops[+pl.dataset.hop - 1];
      if (h) select(h.rec, { reveal: true });
      return;
    }
    if (lk) {
      const ei = +lk.dataset.e;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setLink(ei, false);
      } else if (e.key === "c" || e.key === "C") {
        e.preventDefault();
        stepLink(e.key === "C" ? -1 : 1, ei);
      }
      return;
    }
    if (!nd) return;
    const n = +nd.dataset.n;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      nodeAction(n, e.shiftKey);
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      focusNode(topoLayout.hubList[0] ?? topoLayout.linked[0] ?? n);
      return;
    }
    if (e.key === "c" || e.key === "C") {
      e.preventDefault();
      setMapFocus(n);
      stepLink(e.key === "C" ? -1 : 1, -1);
      return;
    }
    const dir = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!dir) return;
    e.preventDefault();
    const p = topoView.fit.P[n];
    let best = -1;
    let bd = Infinity;
    topoView.fit.P.forEach((q, k) => {
      if (!q || k === n) return;
      const dx = q.x - p.x;
      const dy = q.y - p.y;
      const d = Math.hypot(dx, dy);
      if (!d || (dx * dir[0] + dy * dir[1]) / d < 0.5) return;
      const score = d * (2 - (dx * dir[0] + dy * dir[1]) / d);
      if (score < bd) {
        bd = score;
        best = k;
      }
    });
    if (best >= 0) focusNode(best);
  });
}
