import { ROW_H, TILE_ROWS } from "../../core/lanes-layout.js";
import { E_EMIT, E_RECV, E_SENT } from "../../core/schema.js";
import { anchorIx, causeIx, evOf, firstOf, idOf, laneOf, rowOf, vrowOf } from "../../model.js";
import { descList, hops, pathOfSel } from "../../selection.js";
import { state } from "../../state.js";
import { geom } from "./geometry.js";
import { rowBoxes } from "./labels.js";
import { colOfRec, layout } from "./layout.js";
import { combPath, cxGeom, dashPath, msgPath, msgRoute, segment, selfPath } from "./paths.js";
import { vis } from "../../visibility.js";

export let thread = null;

const P_LIFE = 1;

const P_MSG = 2;

const P_SELF = 3;

const P_COMB = 4;

const P_DASH = 5;

const P_OWN = 6;

function threadPiece(p, c, cls) {
  const cp = colOfRec(p);
  const cc = colOfRec(c);
  let k;
  if (evOf[p] === E_SENT && causeIx[c] === p && evOf[c] === E_RECV) k = cp === cc ? P_SELF : P_MSG;
  else if (evOf[p] === E_EMIT && causeIx[c] === p && cp === layout.WC && cp !== cc) k = P_COMB;
  else if (cp === cc) k = P_LIFE;
  else k = P_DASH;
  return { k, p, c, cls, lo: Math.min(rowOf[p], rowOf[c]), hi: Math.max(rowOf[p], rowOf[c]) };
}

export function computeThread(fresh) {
  thread = null;
  const s = state.sel;
  if (s == null || !layout) return;
  const pieces = [];
  const pills = [];
  const path = pathOfSel();
  const placed = path.filter(i => rowOf[i] >= 0);
  const pos = new Map(path.map((i, k) => [i, k]));
  for (let k = 1; k < placed.length; k++) {
    const pc = threadPiece(placed[k - 1], placed[k], "anc");
    pieces.push(pc);
    if (colOfRec(pc.p) !== colOfRec(pc.c)) {
      const a = pos.get(pc.p);
      const b = pos.get(pc.c);
      const hop = hops.find(h => pos.get(h.rec) > a && pos.get(h.rec) <= b);
      if (hop) pc.hop = hop.n;
    }
  }
  // overlapping thread segments are merged: thousands of stacked strokes made every repaint slow
  const lastOfId = new Map();
  const life = new Map();
  const combs = new Map();
  if (idOf[s] >= 0 && rowOf[s] >= 0) lastOfId.set(idOf[s], s);
  for (const d of descList) {
    if (rowOf[d] < 0) continue;
    const k = idOf[d];
    let a;
    if (k >= 0 && firstOf[k] !== d) a = lastOfId.has(k) ? lastOfId.get(k) : anchorIx[firstOf[k]];
    else a = causeIx[d] >= 0 && causeIx[d] < d ? anchorIx[causeIx[d]] : -1;
    if (k >= 0) lastOfId.set(k, d);
    if (a < 0 || a === d || rowOf[a] < 0) continue;
    const pc = threadPiece(a, d, "desc");
    if (pc.k === P_LIFE) {
      const c = colOfRec(d);
      (life.get(c) || life.set(c, []).get(c)).push([rowOf[a], rowOf[d], a, d]);
    } else if (pc.k === P_COMB) {
      let cm = combs.get(a);
      if (!cm) {
        cm = { ...pc, recs: [] };
        combs.set(a, cm);
        pieces.push(cm);
      }
      cm.recs.push(d);
      cm.hi = Math.max(cm.hi, rowOf[d]);
    } else pieces.push(pc);
  }
  for (const list of life.values()) {
    list.sort((x, y) => x[0] - y[0]);
    let cur = null;
    for (const iv of list) {
      if (cur && iv[0] <= cur.hi) {
        if (iv[1] > cur.hi) {
          cur.hi = iv[1];
          cur.c = iv[3];
        }
        continue;
      }
      cur = { k: P_LIFE, p: iv[2], c: iv[3], cls: "desc", lo: iv[0], hi: iv[1] };
      pieces.push(cur);
    }
  }
  const own = layout.undelivered.get(s);
  if (own !== undefined)
    pieces.push({ k: P_OWN, cx: own, p: s, c: s, cls: "own", lo: layout.cx.lo[own], hi: layout.cx.hi[own] + 2 });
  const tile = Array.from({ length: layout.nTiles }, () => []);
  pieces.forEach((pc, id) => {
    for (
      let t = Math.floor(pc.lo / TILE_ROWS), te = Math.min(layout.nTiles - 1, Math.floor(pc.hi / TILE_ROWS));
      t <= te;
      t++
    )
      tile[t].push(id);
  });
  thread = { pieces, tile, pills, fresh };
  let waitRing = null;
  if (rowOf[s] < 0 && vrowOf[s] >= 0 && layout.col[laneOf[s]] >= 0 && vis[s]) {
    const y = vrowOf[s] * ROW_H;
    waitRing = { t: Math.floor(vrowOf[s] / TILE_ROWS), y, c: layout.col[laneOf[s]] };
  }
  thread.waitRing = waitRing;
  placePills();
}

function piecePoints(pc) {
  const g = geom;
  const xa = g.life[colOfRec(pc.p)];
  const ya = rowOf[pc.p] * ROW_H + ROW_H / 2;
  const xb = g.life[colOfRec(pc.c)];
  const yb = rowOf[pc.c] * ROW_H + ROW_H / 2;
  if (pc.k === P_COMB)
    return [
      [xa, rowOf[pc.c] * ROW_H],
      [xb, rowOf[pc.c] * ROW_H],
    ];
  if (pc.k === P_MSG)
    return (
      msgRoute(pc.p, colOfRec(pc.c), rowOf[pc.c], 11.5) || [
        [xa, ya],
        [xb, yb],
      ]
    );
  return [
    [xa, ya],
    [xb, yb],
  ];
}

function pillSpot(pts, w, cache) {
  const lens = [];
  let total = 0;
  for (let k = 1; k < pts.length; k++) {
    const l = Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]);
    lens.push(l);
    total += l;
  }
  const at = d => {
    for (let k = 0; k < lens.length; k++) {
      if (d <= lens[k] || k === lens.length - 1) {
        const f = lens[k] ? Math.min(1, d / lens[k]) : 0;
        return [pts[k][0] + (pts[k + 1][0] - pts[k][0]) * f, pts[k][1] + (pts[k + 1][1] - pts[k][1]) * f];
      }
      d -= lens[k];
    }
    return pts[0];
  };
  const margin = Math.min(14, total / 3);
  let best = at(total / 2);
  let bestScore = Infinity;
  for (let d = margin, step = Math.max(3, total / 300); d <= total - margin; d += step) {
    const [x, y] = at(d);
    const x0 = x - w / 2 - 1;
    const x1 = x + w / 2 + 1;
    const y0 = y - 9.5;
    const y1 = y + 9.5;
    let cover = 0;
    for (let r = Math.floor(y0 / ROW_H); r <= Math.floor(y1 / ROW_H); r++) {
      for (const b of rowBoxes(r, cache)) {
        const ox = Math.min(x1, b[2]) - Math.max(x0, b[0]);
        const oy = Math.min(y1, b[3]) - Math.max(y0, b[1]);
        if (ox > 0 && oy > 0) cover += ox * oy;
      }
    }
    const score = cover * 100 + Math.abs(d - total / 2);
    if (score < bestScore) {
      bestScore = score;
      best = [x, y];
    }
  }
  return best;
}

export function placePills() {
  if (!thread || !geom) return;
  const cache = new Map();
  thread.pills = [];
  for (const pc of thread.pieces) {
    if (!pc.hop) continue;
    const [x, y] = pillSpot(piecePoints(pc), String(pc.hop).length > 1 ? 24 : 17, cache);
    thread.pills.push({ n: pc.hop, x, y });
  }
  if (thread.waitRing) thread.waitRing.x = geom.life[thread.waitRing.c] + 0.5;
}

export function threadGeom(o, pc, y0) {
  const g = geom;
  const lk = pc.cls;
  const hk = pc.cls === "anc" ? "ah" : "dh";
  if (pc.k === P_OWN) {
    cxGeom(o, pc.cx, y0, "own");
    return;
  }
  const xa = g.life[colOfRec(pc.p)];
  const ya = rowOf[pc.p] * ROW_H + ROW_H / 2 - y0;
  const xb = g.life[colOfRec(pc.c)];
  const yb = rowOf[pc.c] * ROW_H + ROW_H / 2 - y0;
  if (pc.k === P_LIFE) {
    if (yb - ya > 13) segment(o, lk, xa + 0.5, ya + 6.5, xa + 0.5, yb - 6.5);
  } else if (pc.k === P_MSG) msgPath(o, lk, hk, pc.p, pc.c, y0);
  else if (pc.k === P_SELF) selfPath(o, lk, hk, xa, ya, yb);
  else if (pc.k === P_COMB) combPath(o, lk, hk, pc.p, pc.recs || [pc.c], y0);
  else dashPath(o, lk + "d", xa, ya, xb, yb);
}
