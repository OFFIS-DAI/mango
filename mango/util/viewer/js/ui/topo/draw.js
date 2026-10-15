import { layoutTopology } from "../../core/topo-layout.js";
import { clamp, lowerBound, natural, nf, plural, round1 } from "../../core/util.js";
import { EDGES, NODES } from "../../model.js";
import { state } from "../../state.js";
import { $ } from "../dom.js";
import { html, raw } from "../html.js";

export const topoSvg = $("topo-svg");

export const topoCard = $("topo");

export const topoB = $("topo-b");

export const CHAR_W = 6.9;

export const BULK = EDGES.length > 600;

export let topoView = null;

export const cut = s => {
  s = String(s);
  return s.length > 24 ? s.slice(0, 24) + "…" : s;
};

const sameDir = new Set(EDGES.map(e => e.a + ":" + e.b));

const hasReverse = e => e.a !== e.b && sameDir.has(e.b + ":" + e.a);

function fitTopology(W, H) {
  const n = NODES.length;
  const linked = topoLayout.linked;
  const silentRow = topoLayout.silent.length && linked.length ? 28 : 0;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const i of linked) {
    minX = Math.min(minX, topoLayout.x[i]);
    maxX = Math.max(maxX, topoLayout.x[i]);
    minY = Math.min(minY, topoLayout.y[i]);
    maxY = Math.max(maxY, topoLayout.y[i]);
  }
  const manyLinks = EDGES.length > 60;
  const longest = Math.max(0, ...linked.map(i => cut(NODES[i].id).length)) * CHAR_W;
  const padX = Math.min(W * 0.3, (manyLinks ? 0.75 : 1) * longest + 24);
  const padY = 30;
  // an axis without extent (two agents side by side) is centred rather than scaled
  const spanX = maxX - minX > 1e-9 ? maxX - minX : 0;
  const spanY = maxY - minY > 1e-9 ? maxY - minY : 0;
  let sx = spanX ? (W - 2 * padX) / spanX : Infinity;
  let sy = spanY ? (H - silentRow - 2 * padY) / spanY : Infinity;
  sx = Math.max(1, Math.min(sx, sy * 1.6));
  sy = Math.max(1, Math.min(sy, sx * 1.6));
  const offX = (W - spanX * sx) / 2;
  const offY = (H - silentRow - spanY * sy) / 2;
  let X = topoLayout.x;
  let Y = topoLayout.y;
  // a ring stretched into an ellipse crowds the ends of its long axis at equal angles; equal arc lengths spread it evenly
  if (topoLayout.mode === "ring" && spanX && spanY && Math.abs(sx - sy) > 0.02 * Math.max(sx, sy)) {
    const K = 720;
    const a0 = -Math.PI / 2;
    const cum = new Float64Array(K + 1);
    const TAU = 2 * Math.PI;
    for (let k = 1; k <= K; k++) {
      const p = a0 + ((k - 0.5) / K) * TAU;
      cum[k] = cum[k - 1] + Math.hypot(sx * Math.sin(p), sy * Math.cos(p));
    }
    X = Float64Array.from(topoLayout.x);
    Y = Float64Array.from(topoLayout.y);
    for (const i of linked) {
      if (!topoLayout.ring[i]) continue;
      const target = (((((topoLayout.ang[i] - a0) % TAU) + TAU) % TAU) / TAU) * cum[K];
      const k = clamp(lowerBound(cum, target), 1, K);
      const f = (target - cum[k - 1]) / (cum[k] - cum[k - 1] || 1);
      const th = a0 + ((k - 1 + f) / K) * TAU;
      X[i] = Math.cos(th) * topoLayout.rad[i];
      Y[i] = Math.sin(th) * topoLayout.rad[i];
    }
  }
  const P = new Array(n);
  for (const i of linked)
    P[i] = { x: spanX ? offX + (X[i] - minX) * sx : W / 2, y: spanY ? offY + (Y[i] - minY) * sy : (H - silentRow) / 2 };
  let minD = Infinity;
  for (let a = 0; a < linked.length; a++)
    for (let b = a + 1; b < linked.length; b++) {
      const i = linked[a];
      const j = linked[b];
      if (topoLayout.hubs.has(i) || topoLayout.hubs.has(j)) continue;
      const d = Math.hypot(P[i].x - P[j].x, P[i].y - P[j].y);
      if (d < minD) minD = d;
    }
  if (!isFinite(minD)) minD = 60;
  const dense = minD < 18 || manyLinks;
  // circles never overlap their neighbours: in a crowded ring they shrink below 3 px and drop their outline
  const rMax = 0.42 * minD < 3 ? Math.max(1.2, 0.42 * minD) : clamp(0.36 * minD, 3, 10);
  const rMin = Math.min(rMax, Math.max(2.5, rMax * 0.6));
  const traffic = d => d.sent + d.received;
  const maxT = Math.max(1, ...NODES.map(traffic));
  const R = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    R[i] = topoLayout.hubs.has(i)
      ? Math.min(16, Math.max(rMax * 1.5, 9))
      : rMin + (rMax - rMin) * Math.sqrt(traffic(NODES[i]) / maxT);
  }
  const sl = topoLayout.silent;
  sl.forEach((i, k) => {
    const step = Math.min(130, (W - 24) / Math.max(1, sl.length));
    P[i] = { x: 12 + R[i] + k * step, y: H - 14 };
  });
  if (!linked.length)
    sl.forEach((i, k) => {
      P[i] = { x: W / 2 + (k - (sl.length - 1) / 2) * 110, y: H / 2 };
    });
  return { P, R, dense, minD, silentRow, W, H, cx: W / 2, cy: (H - silentRow) / 2 };
}

function edgeGeom(e, fit) {
  const { P, R } = fit;
  const a = P[e.a];
  const b = P[e.b];
  if (e.a === e.b) {
    const vx = a.x - fit.cx;
    const vy = a.y - fit.cy;
    const l = Math.hypot(vx, vy) || 1;
    const ux = l > 1 ? vx / l : 0;
    const uy = l > 1 ? vy / l : -1;
    const r = R[e.a];
    const cx0 = a.x + ux * (r + 8);
    const cy0 = a.y + uy * (r + 8);
    const px = -uy;
    const py = ux;
    const s = { x: a.x + (ux * 0.7 + px * 0.7) * r, y: a.y + (uy * 0.7 + py * 0.7) * r };
    const t = { x: a.x + (ux * 0.7 - px * 0.7) * r, y: a.y + (uy * 0.7 - py * 0.7) * r };
    const d = `M${round1(s.x)},${round1(s.y)} C${round1(cx0 + px * 12)},${round1(cy0 + py * 12)} ${round1(cx0 - px * 12)},${round1(cy0 - py * 12)} ${round1(t.x)},${round1(t.y)}`;
    const mid = { x: cx0 + ux * 6, y: cy0 + uy * 6 };
    return {
      d,
      straight: d,
      at: () => ({ x: cx0, y: cy0 }),
      tan: () => ({ x: -px, y: -py }),
      mid,
      out: { x: ux, y: uy },
    };
  }
  const vx = b.x - a.x;
  const vy = b.y - a.y;
  const len = Math.hypot(vx, vy) || 1;
  const nx = -vy / len;
  const ny = vx / len;
  const recip = hasReverse(e);
  const bend = recip ? Math.min(12, len * 0.14) : 0;
  let c = { x: (a.x + b.x) / 2 + nx * bend, y: (a.y + b.y) / 2 + ny * bend };
  if (topoLayout.ring[e.a] && topoLayout.ring[e.b] && len < 0.3 * Math.hypot(a.x - fit.cx, a.y - fit.cy)) {
    const mx = (a.x + b.x) / 2 - fit.cx;
    const my = (a.y + b.y) / 2 - fit.cy;
    const ml = Math.hypot(mx, my) || 1;
    const out = len * 0.32 + (recip ? (natural(String(e.from), String(e.to)) < 0 ? 7 : -3) : 0);
    c = { x: (a.x + b.x) / 2 + (mx / ml) * out, y: (a.y + b.y) / 2 + (my / ml) * out };
  }
  const toward = (p, d) => {
    const l = Math.hypot(c.x - p.x, c.y - p.y) || 1;
    return { x: p.x + ((c.x - p.x) / l) * d, y: p.y + ((c.y - p.y) / l) * d };
  };
  const p0 = toward(a, R[e.a] + 2);
  const p2 = toward(b, R[e.b] + 2.5);
  const at = t => {
    const u = 1 - t;
    return { x: u * u * p0.x + 2 * t * u * c.x + t * t * p2.x, y: u * u * p0.y + 2 * t * u * c.y + t * t * p2.y };
  };
  const tan = t => {
    const dx = 2 * (1 - t) * (c.x - p0.x) + 2 * t * (p2.x - c.x);
    const dy = 2 * (1 - t) * (c.y - p0.y) + 2 * t * (p2.y - c.y);
    const l = Math.hypot(dx, dy) || 1;
    return { x: dx / l, y: dy / l };
  };
  const q0 = { x: a.x + (vx / len) * (R[e.a] + 2), y: a.y + (vy / len) * (R[e.a] + 2) };
  const q2 = { x: b.x - (vx / len) * (R[e.b] + 2.5), y: b.y - (vy / len) * (R[e.b] + 2.5) };
  return {
    d: `M${round1(p0.x)},${round1(p0.y)} Q${round1(c.x)},${round1(c.y)} ${round1(p2.x)},${round1(p2.y)}`,
    straight: `M${round1(q0.x)},${round1(q0.y)} L${round1(q2.x)},${round1(q2.y)}`,
    at,
    tan,
    mid: at(0.5),
    out: recip ? { x: nx, y: ny } : null,
  };
}

export const badgeOf = (i, fit) => {
  const r = fit.R[i];
  return fit.minD < 18
    ? { x: r * 0.72, y: -r * 0.72, r: 2.5, small: true }
    : { x: r * 0.72 + 2.5, y: -r * 0.72 - 2.5, r: 6.5 };
};

// a selection or chain ring sits at r + 4: the badge moves out so it does not cover that ring
export const badgeShift = (i, ringed) => {
  if (!ringed || !topoView) return null;
  const b = badgeOf(i, topoView.fit);
  const d = Math.hypot(b.x, b.y);
  const want = topoView.fit.R[i] + 5.5 + b.r;
  return want > d ? { x: (b.x / d) * (want - d), y: (b.y / d) * (want - d) } : null;
};

// draws the map afresh; false when there is nothing to draw (closed, shown as a list, or no room)
export function drawTopology() {
  if (!topoLayout) computeTopoLayout();
  const W = topoB.clientWidth;
  const H = topoB.clientHeight;
  if (!W || !H || !EDGES.length || state.topo.mode !== "graph" || !state.topo.open) return false;
  const t0 = performance.now();
  const fit = fitTopology(W, H);
  const merge = fit.dense && EDGES.length > 60;
  const maxSent = Math.max(1, ...EDGES.map(e => e.sent));
  const width = e => (fit.dense ? 0.6 : 1) + (fit.dense ? 1.6 : 2.6) * Math.sqrt(e.sent / maxSent);
  const rate = e => (e.lost - e.inFlight) / e.sent;
  const lossyList = EDGES.filter(e => e.lossy).sort((a, b) => rate(b) - rate(a) || b.lost - a.lost);
  const maxRate = lossyList.length ? rate(lossyList[0]) : 1;
  const cutSet = new Set(fit.dense ? lossyList.slice(0, 12) : lossyList);
  const geo = EDGES.map(e => edgeGeom(e, fit));
  // links stay solid (3:1) until a hairball forms; only then are they thinned out by count
  const linkOpacity = EDGES.length > 200 ? clamp(9 / Math.sqrt(EDGES.length), 0.12, 0.6) : 1;
  topoSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  topoSvg.style.setProperty("--lo", linkOpacity.toFixed(2));
  topoSvg.setAttribute(
    "aria-label",
    `Message topology, ${plural(NODES.length, "agent")}, ${plural(EDGES.length, "connection")}`,
  );
  const mk = (id, cls) =>
    `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"` +
    ` markerUnits="userSpaceOnUse"><path class="${cls}" d="M0.5,1 L9.5,5 L0.5,9 L2.6,5 z"/></marker>`;
  const h = [
    `<defs>${mk("tm-b", "mk-b")}${mk("tm-q", "mk-q")}${mk("tm-a", "mk-a")}${mk("tm-n", "mk-n")}${mk("tm-l", "mk-l")}</defs>`,
  ];
  const drawnPair = new Set();
  const cutD = (e, g) => {
    if (!(e.lossy && cutSet.has(e))) return "";
    const p = g.at(0.8);
    const t = g.tan(0.8);
    const L2 = fit.dense ? 3 : 4;
    return `M${round1(p.x - t.y * L2)},${round1(p.y + t.x * L2)} L${round1(p.x + t.y * L2)},${round1(p.y - t.x * L2)}`;
  };
  const lossMark = (e, g) => {
    const d = cutD(e, g);
    if (d) return `<path class="cuth" d="${d}"/><path class="cut" d="${d}"/>`;
    if (!e.lossy && e.inFlight > 0) {
      const p = g.at(0.8);
      return `<circle class="fl" cx="${round1(p.x)}" cy="${round1(p.y)}" r="3.5"/>`;
    }
    return "";
  };
  if (BULK) {
    const bulk = ["", "", "", ""];
    let cuts = "";
    for (const e of EDGES) {
      const pairKey = Math.min(e.a, e.b) + ":" + Math.max(e.a, e.b);
      if (hasReverse(e)) {
        if (drawnPair.has(pairKey)) continue;
        drawnPair.add(pairKey);
      }
      bulk[Math.min(3, Math.floor(Math.sqrt(e.sent / maxSent) * 4))] += geo[e.k].straight;
      cuts += cutD(e, geo[e.k]);
    }
    h.push(
      `<g class="bulk" aria-hidden="true">${bulk.map((d, k) => `<path class="lk-b" d="${d}" stroke-width="${(0.6 + 0.5 * k).toFixed(1)}"/>`).join("")}` +
        `<path class="cuth" d="${cuts}"/><path class="cut" d="${cuts}"/></g>`,
    );
    h.push(`<g class="bulk-match" aria-hidden="true"></g><g class="bulk-live" aria-hidden="true"></g>`);
  } else {
    h.push(`<g class="links">`);
    for (const e of EDGES) {
      const g = geo[e.k];
      const recip = hasReverse(e);
      let cls = "lk" + (e.lossy ? " lossy" : "");
      const pairKey = Math.min(e.a, e.b) + ":" + Math.max(e.a, e.b);
      if (merge && recip) {
        cls += drawnPair.has(pairKey) ? " twin" : " both";
        drawnPair.add(pairKey);
      }
      const w = width(e).toFixed(2);
      const lost = e.lost - e.inFlight;
      const lab = `${e.from} to ${e.to}, ${plural(e.sent, "message")}${lost ? `, ${nf(lost)} lost` : ""}${e.inFlight ? `, ${nf(e.inFlight)} in flight` : ""}`;
      const lr = e.lossy ? ` style="--lr:${(0.35 + (0.65 * rate(e)) / (maxRate || 1)).toFixed(2)}"` : "";
      h.push(
        html`<g class="${cls}" data-e="${e.k}" role="button" tabindex="-1" aria-pressed="false" aria-label="${lab}"${raw(lr)}>` +
          `<path class="hit" d="${g.d}"/>` +
          `<path class="ln" d="${merge && recip ? g.straight : g.d}" stroke-width="${w}"/>` +
          (merge && recip ? `<path class="lc" d="${g.d}" stroke-width="${w}"/>` : "") +
          lossMark(e, g) +
          `</g>`,
      );
    }
    h.push(`</g>`);
  }
  h.push(`<g class="hot" id="topo-hot"></g><g class="nodes">`);
  for (let i = 0; i < NODES.length; i++) {
    const d = NODES[i];
    const p = fit.P[i];
    const r = fit.R[i];
    if (!p) continue;
    const cls =
      "nd" +
      (topoLayout.silent.includes(i) ? " silent" : "") +
      (!d.records && d.lane < 0 ? " norec" : "") +
      (topoLayout.hubs.has(i) ? " hub" : "") +
      (r < 2.5 ? " tiny" : "");
    let badge = "";
    if (d.failed) {
      const b = badgeOf(i, fit);
      badge =
        `<g class="bdg"><circle class="bd" cx="${round1(b.x)}" cy="${round1(b.y)}" r="${b.r}"/>` +
        (b.small
          ? ""
          : `<text class="bn" x="${round1(b.x)}" y="${round1(b.y + 3.3)}" text-anchor="middle">${d.failed > 99 ? "99+" : d.failed}</text>`) +
        `</g>`;
    }
    h.push(
      `<g class="${cls}" data-n="${i}" transform="translate(${round1(p.x)},${round1(p.y)})" role="button" tabindex="-1" aria-pressed="false"` +
        html` aria-label="${nodeLabel(i)}">` +
        `<circle class="ring" r="${round1(r + 4)}"/><circle class="dot" r="${round1(r)}"/><circle class="in" r="${round1(r * 0.45)}"/>` +
        `<circle class="fr" r="${round1(r + 5)}"/>${badge}<text class="nm"></text></g>`,
    );
  }
  h.push(`</g><g class="cnt" id="topo-cnt" aria-hidden="true"></g><g class="pills" id="topo-pills"></g>`);
  h.push(`<text class="cl" id="topo-cap" x="10" y="16"></text>`);
  if (topoLayout.silent.length && topoLayout.linked.length)
    h.push(`<text class="cl" x="${W - 10}" y="${H - 10}" text-anchor="end">no messages</text>`);
  topoSvg.innerHTML = h.join("");
  const nodeEls = new Array(NODES.length);
  const linkEls = new Array(EDGES.length);
  topoSvg.querySelectorAll(".nd").forEach(el => {
    nodeEls[+el.dataset.n] = el;
  });
  topoSvg.querySelectorAll(".lk").forEach(el => {
    linkEls[+el.dataset.e] = el;
  });
  let grid = null;
  if (BULK) {
    grid = new Map();
    for (const e of EDGES) {
      const a = fit.P[e.a];
      const b = fit.P[e.b];
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      const steps = Math.max(1, Math.ceil(len / 8));
      for (let s = 0; s <= steps; s++) {
        const x = a.x + ((b.x - a.x) * s) / steps;
        const y = a.y + ((b.y - a.y) * s) / steps;
        const k = Math.floor(x / 24) + "," + Math.floor(y / 24);
        (grid.get(k) || grid.set(k, []).get(k)).push(e.k, x, y);
      }
    }
  }
  topoView = {
    fit,
    geo,
    width,
    merge,
    nodeEls,
    linkEls,
    grid,
    cutSet,
    cutD: e => cutD(EDGES[e], geo[e]),
    labelsKey: "",
    ms: performance.now() - t0,
  };
  return true;
}

const nodeLabel = i => {
  const d = NODES[i];
  return (
    `${d.id}, ${d.lane >= 0 ? plural(d.records, "record") : "no records in this trace"}${d.failed ? `, ${nf(d.failed)} failed` : ""}` +
    `, sent ${nf(d.sent)}, received ${nf(d.received)}, ${plural(d.partners.size - (d.partners.has(i) ? 1 : 0), "partner")}`
  );
};

export let topoLayout = null;

// beside a numbered group (agent0, agent1, ...) the agents with names of their own are the distinct roles
const namedAgent = id => !!shortIds && shortIds.of(id) == null;

export function computeTopoLayout() {
  topoLayout = layoutTopology(NODES, EDGES, namedAgent);
}

export const shortIds = (() => {
  const pre = new Map();
  for (const d of NODES) {
    const m = /^(.*\D)\d+$/.exec(String(d.id));
    if (m) pre.set(m[1], (pre.get(m[1]) || 0) + 1);
  }
  const best = [...pre].sort((a, b) => b[1] - a[1])[0];
  if (!best || best[1] < Math.max(3, 0.5 * NODES.length)) return null;
  const prefix = best[0];
  return {
    prefix,
    of: id => (id.startsWith(prefix) && /^\d+$/.test(id.slice(prefix.length)) ? id.slice(prefix.length) : null),
  };
})();

export const mapSets = { fol: new Set(), chain: new Set(), dag: new Set(), sel: -1, live: new Set(), hops: new Set() };

// the pill with the number of messages (and losses) of connection E, centred on p
export const countPillWidth = E => countText(E).length * 6.4 + 12;
const countText = E => nf(E.sent) + (E.lost - E.inFlight ? ` · ${nf(E.lost - E.inFlight)} lost` : "");
export function countPill(attrs, p, E) {
  const lost = E.lost - E.inFlight;
  const w = countPillWidth(E);
  return (
    `<g ${attrs} transform="translate(${round1(p.x)},${round1(p.y)})">` +
    `<rect x="${round1(-w / 2)}" y="-8.5" width="${round1(w)}" height="17" rx="8.5"/>` +
    `<text text-anchor="middle" dy="3.6">${nf(E.sent)}${lost ? `<tspan class="lo"> · ${nf(lost)} lost</tspan>` : ""}</text></g>`
  );
}
