// Where the agents of the message graph go: a few fixed spots for up to three agents, a ring
// around the hubs when the rest is loosely linked, a force layout otherwise. Coordinates are
// in [-1, 1]; fitTopology scales them to the map. `named(id)` tells agents with their own
// name apart from the numbered ones (agent0, agent1, ...), whose labels give way first.
import { natural } from "./util.js";

export function layoutTopology(NODES, EDGES, named) {
  const n = NODES.length;
  const adj = Array.from({ length: n }, () => new Set());
  for (const e of EDGES)
    if (e.a !== e.b) {
      adj[e.a].add(e.b);
      adj[e.b].add(e.a);
    }
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  const ring = new Uint8Array(n);
  const ang = new Float64Array(n);
  const rad0 = new Float64Array(n);
  const nat = (a, b) => natural(String(NODES[a].id), String(NODES[b].id));
  // an agent that only messages itself is linked (it has a connection), just without neighbours to lay out against
  const linked = [...Array(n).keys()].filter(i => NODES[i].edges.length).sort(nat);
  const silent = [...Array(n).keys()].filter(i => !NODES[i].edges.length).sort(nat);
  const m = linked.length;
  const traffic = i => NODES[i].sent + NODES[i].received;
  let hubs = linked
    .filter(i => adj[i].size >= Math.max(4, 0.4 * (m - 1)))
    .sort((a, b) => adj[b].size - adj[a].size || nat(a, b))
    .slice(0, 4);
  const isHub = new Set(hubs);
  let mode = "force";
  let comps = [];
  if (m <= 3) {
    mode = "fixed";
    const ord = linked.slice().sort((a, b) => traffic(b) - traffic(a) || nat(a, b));
    const spots =
      m === 1
        ? [[0, 0]]
        : m === 2
          ? [
              [-1, 0],
              [1, 0],
            ]
          : [
              [0, -0.8],
              [-1, 0.6],
              [1, 0.6],
            ];
    ord.forEach((i, k) => {
      x[i] = spots[k][0];
      y[i] = spots[k][1];
    });
    if (m === 3) {
      const [, b, c] = ord;
      if (nat(b, c) > 0) {
        x[b] = 1;
        x[c] = -1;
      }
    }
    hubs = [];
    isHub.clear();
  } else if (hubs.length && m - hubs.length >= 2) {
    const rest = linked.filter(i => !isHub.has(i));
    const seen = new Set();
    for (const s of rest) {
      if (seen.has(s)) continue;
      const comp = [];
      const queue = [s];
      seen.add(s);
      for (let h = 0; h < queue.length; h++) {
        const v = queue[h];
        comp.push(v);
        for (const w of [...adj[v]].sort(nat))
          if (!isHub.has(w) && !seen.has(w)) {
            seen.add(w);
            queue.push(w);
          }
      }
      const ends = comp.filter(v => [...adj[v]].filter(w => !isHub.has(w)).length <= 1);
      if (comp.length > 2 && ends.length === 2) {
        const path = [];
        const vis = new Set();
        let v = ends.sort(nat)[0];
        while (v != null) {
          path.push(v);
          vis.add(v);
          v = [...adj[v]].find(w => !isHub.has(w) && !vis.has(w));
        }
        if (path.length === comp.length) comp.splice(0, comp.length, ...path);
      }
      comps.push(comp);
    }
    if (Math.max(...comps.map(c => c.length)) <= Math.max(8, 0.2 * rest.length)) mode = "ring";
    else comps = [];
  }
  if (mode === "ring") {
    const rest = linked.filter(i => !isHub.has(i));
    const gap = comps.length > 1 && comps.some(c => c.length > 1) ? 0.7 : 0;
    const slots = rest.length + gap * comps.length;
    const step = (2 * Math.PI) / Math.max(slots, 3);
    const two = rest.length > 120;
    let a = -Math.PI / 2 - (slots < 3 ? ((slots - 1 - gap) * step) / 2 : 0);
    let k = 0;
    for (const comp of comps) {
      for (const v of comp) {
        // named agents among numbered ones stay on the outer ring, where their labels have room
        const rad = two && k % 2 && !named(String(NODES[v].id)) ? 0.78 : 1;
        x[v] = Math.cos(a) * rad;
        y[v] = Math.sin(a) * rad;
        ring[v] = 1;
        ang[v] = a;
        rad0[v] = rad;
        a += step;
        k++;
      }
      a += gap * step;
    }
    hubs.forEach((h, j) => {
      const theta = (2 * Math.PI * j) / hubs.length - Math.PI / 2;
      const rad = hubs.length > 1 ? 0.28 : 0;
      x[h] = Math.cos(theta) * rad;
      y[h] = Math.sin(theta) * rad;
    });
  } else if (mode === "force") {
    const order = linked.slice().sort((a, b) => adj[b].size - adj[a].size || nat(a, b));
    const pin = hubs.length === 1 ? hubs[0] : -1;
    order.forEach((i, rank) => {
      const theta = (2 * Math.PI * rank) / Math.max(m, 1) - Math.PI / 2;
      const rad = i === pin ? 0 : 0.35 + 0.1 * (rank % 2);
      x[i] = Math.cos(theta) * rad;
      y[i] = Math.sin(theta) * rad;
    });
    const kk = Math.sqrt(1 / Math.max(m, 1)) * 0.9;
    const dx = new Float64Array(n);
    const dy = new Float64Array(n);
    let temp = 0.1;
    const iters = m > 150 ? 150 : 400;
    const grid = m > 400;
    for (let it = 0; it < iters; it++) {
      dx.fill(0);
      dy.fill(0);
      const rep = (i, j, p, q) => {
        let ddx = x[i] - x[j];
        let ddy = y[i] - y[j];
        let d2 = ddx * ddx + ddy * ddy;
        if (d2 < 1e-9) {
          ddx = 1e-3 * (p - q);
          ddy = 1e-3;
          d2 = ddx * ddx + ddy * ddy;
        }
        const f = (kk * kk) / d2;
        dx[i] += ddx * f;
        dy[i] += ddy * f;
        dx[j] -= ddx * f;
        dy[j] -= ddy * f;
      };
      if (grid) {
        const cell = 2 * kk;
        const cells = new Map();
        order.forEach((i, p) => {
          const key = Math.floor(x[i] / cell) + "," + Math.floor(y[i] / cell);
          (cells.get(key) || cells.set(key, []).get(key)).push(p);
        });
        for (const [key, list] of cells) {
          const [cx0, cy0] = key.split(",").map(Number);
          for (let ox = -1; ox <= 1; ox++)
            for (let oy = -1; oy <= 1; oy++) {
              const other = cells.get(cx0 + ox + "," + (cy0 + oy));
              if (!other) continue;
              for (const p of list) for (const q of other) if (p < q) rep(order[p], order[q], p, q);
            }
        }
      } else {
        for (let p = 0; p < m; p++) for (let q = p + 1; q < m; q++) rep(order[p], order[q], p, q);
      }
      for (const i of order)
        for (const j of adj[i])
          if (i < j && i !== pin && j !== pin) {
            const ddx = x[i] - x[j];
            const ddy = y[i] - y[j];
            const d = Math.hypot(ddx, ddy) || 1e-6;
            const f = d / kk;
            dx[i] -= ddx * f;
            dy[i] -= ddy * f;
            dx[j] += ddx * f;
            dy[j] += ddy * f;
          }
      for (const i of order) {
        if (i === pin) continue;
        dx[i] -= x[i] * 0.35;
        dy[i] -= y[i] * 0.35;
        const d = Math.hypot(dx[i], dy[i]) || 1e-9;
        const s = Math.min(d, temp);
        x[i] += (dx[i] / d) * s;
        y[i] += (dy[i] / d) * s;
      }
      temp = Math.max(temp * 0.985, 0.002);
    }
  }
  return { mode, x, y, ring, ang, rad: rad0, hubs: new Set(hubs), hubList: hubs, silent, linked, adj };
}
