import { K_ARC, K_COMB, K_LOST, K_MSG, K_SELF, K_STUB, ROW_H } from "../../core/lanes-layout.js";
import { round1 } from "../../core/util.js";
import { ghostRow, recvAgentOf, rowOf } from "../../model.js";
import { geom } from "./geometry.js";
import { CHAN, labelEnd } from "./labels.js";
import { colOfRec, layout } from "./layout.js";

const add = (o, k, d) => {
  o[k] = (o[k] || "") + d;
};

const head = (o, k, tx, ty, ux, uy, len, wid) => {
  const bx = tx - ux * len;
  const by = ty - uy * len;
  add(
    o,
    k,
    `M${round1(tx)} ${round1(ty)}L${round1(bx - uy * wid)} ${round1(by + ux * wid)}L${round1(bx + uy * wid)} ${round1(by - ux * wid)}Z`,
  );
};

function arrowPath(o, lk, hk, xs, ys, xr, yr) {
  const dx = xr - xs;
  const dy = yr - ys;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const tx = xr - ux * 6.5;
  const ty = yr - uy * 6.5;
  add(o, lk, `M${round1(xs + ux * 6.5)} ${round1(ys + uy * 6.5)}L${round1(tx - ux * 5)} ${round1(ty - uy * 5)}`);
  head(o, hk, tx, ty, ux, uy, 6, 3);
}

// A message runs along its send row (or the row boundary below it when other lanes have records there), drops down
// the receiver's channel and turns into the receipt. Boundaries and channels carry no text, so the line stays whole.
// Null when labels are hidden (Fit), where straight lines read best.
export function msgRoute(s, cv, rr, endGap) {
  const g = geom;
  const cs = colOfRec(s);
  const rs = rowOf[s];
  if (!g.labels || cs === cv || cs < 0 || cv < 0) return null;
  const xs = g.life[cs];
  const ys = rs * ROW_H + ROW_H / 2;
  const xr = g.life[cv];
  const yr = rr * ROW_H + ROW_H / 2;
  const xc = xr - CHAN;
  const yb = rr >= rs ? (rs + 1) * ROW_H : rs * ROW_H;
  const xe = xr - endGap;
  const right = xr > xs;
  let clear = true;
  for (let j = layout.rowStart[rs]; j < layout.rowStart[rs + 1] && clear; j++) {
    const v = layout.rowRecs[j];
    const c = v >= 0 ? colOfRec(v) : layout.col[recvAgentOf[-1 - v]];
    if (right ? c > cs && c < cv : c >= cv && c < cs) clear = false;
  }
  if (right) {
    const x0 = labelEnd(s) + 5;
    if (x0 < xc - 1) {
      if (clear)
        return [
          [x0, ys],
          [xc, ys],
          [xc, yr],
          [xe, yr],
        ];
      return [
        [x0, ys],
        [x0 + 4, ys],
        [x0 + 4, yb],
        [xc, yb],
        [xc, yr],
        [xe, yr],
      ];
    }
  } else if (clear)
    return [
      [xs - 6.5, ys],
      [xc, ys],
      [xc, yr],
      [xe, yr],
    ];
  return [
    [xs, ys + (rr >= rs ? 6.5 : -6.5)],
    [xs, yb],
    [xc, yb],
    [xc, yr],
    [xe, yr],
  ];
}

function roundedD(pts, dy, R) {
  const P = (x, y) => `${round1(x)} ${round1(y - dy)}`;
  const n = pts.length;
  let d = "M" + P(pts[0][0], pts[0][1]);
  for (let k = 1; k < n - 1; k++) {
    const [px, py] = pts[k - 1];
    const [x, y] = pts[k];
    const [nx, ny] = pts[k + 1];
    const l1 = Math.hypot(x - px, y - py);
    const l2 = Math.hypot(nx - x, ny - y);
    const r = Math.min(R, k === 1 ? l1 : l1 / 2, k === n - 2 ? l2 : l2 / 2);
    if (r < 0.5) {
      d += "L" + P(x, y);
      continue;
    }
    d +=
      "L" +
      P(x - ((x - px) / l1) * r, y - ((y - py) / l1) * r) +
      "Q" +
      P(x, y) +
      " " +
      P(x + ((nx - x) / l2) * r, y + ((ny - y) / l2) * r);
  }
  return d + "L" + P(pts[n - 1][0], pts[n - 1][1]);
}

function clipPoly(pts, lo, hi) {
  const out = [];
  let cur = null;
  for (let k = 1; k < pts.length; k++) {
    const [x1, y1] = pts[k - 1];
    const [x2, y2] = pts[k];
    let t0 = 0;
    let t1 = 1;
    if (y1 !== y2) {
      const ta = (lo - y1) / (y2 - y1);
      const tb = (hi - y1) / (y2 - y1);
      t0 = Math.max(0, Math.min(ta, tb));
      t1 = Math.min(1, Math.max(ta, tb));
    } else if (y1 < lo || y1 > hi) t1 = -1;
    if (t1 <= t0) {
      cur = null;
      continue;
    }
    const a = [x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0];
    const b = [x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1];
    if (!cur || t0 > 0) {
      cur = [a];
      out.push(cur);
    }
    cur.push(b);
    if (t1 < 1) cur = null;
  }
  return out;
}

function routeD(o, k, pts, y0) {
  const parts = tileSpan ? clipPoly(pts, tileSpan[0] + y0, tileSpan[1] + y0) : [pts];
  for (const p of parts) if (p.length > 1) add(o, k, roundedD(p, y0, 4));
}

export function selfPath(o, lk, hk, x, ya, yb) {
  add(o, lk, `M${x + 5} ${ya + 3}C${x + 16.5} ${ya + 5} ${x + 16.5} ${yb - 5} ${x + 7.5} ${yb - 2}`);
  head(o, hk, x + 6.2, yb - 1.4, -0.94, 0.34, 5, 2.6);
}

// dashed lines are rastered along their whole length, so while a tile is drawn they are cut to its rows (plus a margin)
let tileSpan = null;
export function setTileSpan(span) {
  tileSpan = span;
}

export function segment(o, k, x1, y1, x2, y2) {
  let t0 = 0;
  let t1 = 1;
  if (tileSpan && y2 !== y1) {
    const ta = (tileSpan[0] - y1) / (y2 - y1);
    const tb = (tileSpan[1] - y1) / (y2 - y1);
    t0 = Math.max(0, Math.min(ta, tb));
    t1 = Math.min(1, Math.max(ta, tb));
    if (t1 <= t0) return;
  }
  add(
    o,
    k,
    `M${round1(x1 + (x2 - x1) * t0)} ${round1(y1 + (y2 - y1) * t0)}L${round1(x1 + (x2 - x1) * t1)} ${round1(y1 + (y2 - y1) * t1)}`,
  );
}

export function dashPath(o, lk, xs, ys, xr, yr) {
  const dx = xr - xs;
  const dy = yr - ys;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  segment(o, lk, xs + ux * 7, ys + uy * 7, xr - ux * 7, yr - uy * 7);
}

export function combPath(o, lk, hk, e, recs, y0) {
  const g = geom;
  const xw = g.life[layout.WC];
  const ye = rowOf[e] * ROW_H + ROW_H / 2 - y0;
  const byRow = new Map();
  for (const j of recs) {
    const r = rowOf[j];
    (byRow.get(r) || byRow.set(r, []).get(r)).push(g.life[colOfRec(j)]);
  }
  const rows = [...byRow.keys()].sort((a, b) => a - b);
  if (!rows.length) return;
  const lastTop = rows[rows.length - 1] * ROW_H - y0;
  add(o, lk, `M${xw + 0.5} ${round1(ye + 6.5)}V${lastTop - 3}`);
  for (const r of rows) {
    const yt = r * ROW_H - y0;
    const xs = byRow.get(r);
    const right = xs.filter(x => x > xw);
    const left = xs.filter(x => x < xw);
    for (const side of [right, left]) {
      if (!side.length) continue;
      const sg = side === right ? 1 : -1;
      const far = sg > 0 ? Math.max(...side) : Math.min(...side);
      add(o, lk, `M${xw + 0.5} ${yt - 3}Q${xw + 0.5} ${yt} ${xw + 0.5 + 3 * sg} ${yt}H${far - 3 * sg}`);
      for (const x of side) {
        add(o, lk, `M${x - 3 * sg} ${yt}Q${x} ${yt} ${x} ${yt + 2.5}`);
        head(o, hk, x, yt + 6, 0, 1, 4.5, 2.4);
      }
    }
  }
}

export const msgPath = (o, lk, hk, s, rv, y0) => {
  const xs = geom.life[colOfRec(s)];
  const xr = geom.life[colOfRec(rv)];
  const ys = rowOf[s] * ROW_H + ROW_H / 2 - y0;
  const yr = rowOf[rv] * ROW_H + ROW_H / 2 - y0;
  const pts = msgRoute(s, colOfRec(rv), rowOf[rv], 11.5);
  if (!pts) {
    arrowPath(o, lk, hk, xs, ys, xr, yr);
    return;
  }
  add(o, lk, roundedD(pts, y0, 4));
  head(o, hk, xr - 6.5, yr, 1, 0, 6, 3);
};

// line and head classes of a connector as drawn in the base layer, as the selection's own connector, or in the hover overlay
function cxKeys(kind, mode) {
  if (mode === "own") return kind === K_LOST ? ["ownl", ""] : kind === K_STUB ? ["owns", "ownsh"] : ["own", "oh"];
  if (mode === "ov") return kind === K_LOST ? ["ovd", ""] : kind === K_STUB ? ["ovd", "ovs"] : ["ovl", "oh"];
  if (kind === K_LOST) return ["lost", ""];
  if (kind === K_STUB) return ["stub", "sth"];
  return kind === K_ARC || kind === K_COMB ? ["evt", "eh"] : ["msg", "mh"];
}

export function cxGeom(o, id, y0, mode) {
  const g = geom;
  const kind = layout.cx.kind[id];
  const a = layout.cx.a[id];
  const b = layout.cx.b[id];
  const [lk, hk] = cxKeys(kind, mode);
  const xa = g.life[colOfRec(a)];
  const ya = rowOf[a] * ROW_H + ROW_H / 2 - y0;
  if (kind === K_MSG) msgPath(o, lk, hk, a, b, y0);
  else if (kind === K_SELF || kind === K_ARC) selfPath(o, lk, hk, xa, ya, rowOf[b] * ROW_H + ROW_H / 2 - y0);
  else if (kind === K_COMB) combPath(o, lk, hk, a, layout.combs.get(a).recs, y0);
  else if (kind === K_LOST) {
    const cg = layout.col[recvAgentOf[a]];
    const pts = msgRoute(a, cg, ghostRow[a], 7);
    if (pts) routeD(o, lk, pts, y0);
    else dashPath(o, lk, xa, ya, g.life[cg], ghostRow[a] * ROW_H + ROW_H / 2 - y0);
  } else if (kind === K_STUB) {
    const rl = recvAgentOf[a];
    const rc = rl >= 0 ? layout.col[rl] : -1;
    // a stub toward a lane on the left leaves the glyph steeply; otherwise it leaves after the label, like a message arrow
    const left = rc >= 0 && rc !== colOfRec(a) && g.life[rc] < xa;
    const len = left ? Math.hypot(0.55, 1) : Math.hypot(1, 0.36);
    const ux = left ? -0.55 / len : 1 / len;
    const uy = left ? 1 / len : 0.36 / len;
    const x0 = left || !g.labels ? xa + ux * 6.5 : labelEnd(a) + 5;
    const y0s = left || !g.labels ? ya + uy * 6.5 : ya;
    const tx = x0 + ux * 28;
    const ty = y0s + uy * 28;
    const bx = tx - ux * 5;
    const by = ty - uy * 5;
    add(o, lk, `M${round1(x0)} ${round1(y0s)}L${round1(tx)} ${round1(ty)}`);
    add(
      o,
      hk,
      `M${round1(bx - uy * 3)} ${round1(by + ux * 3)}L${round1(tx)} ${round1(ty)}L${round1(bx + uy * 3)} ${round1(by - ux * 3)}`,
    );
  }
}

// the rasterizer walks a whole path for every raster tile it touches, so long merged paths are cut into row chunks
export function chunked(proto, list, rowOfItem, fn) {
  const parts = new Map();
  for (const item of list) {
    const c = Math.floor(rowOfItem(item) / 8);
    let p = parts.get(c);
    if (!p) {
      p = { ...proto };
      parts.set(c, p);
    }
    fn(p, item);
  }
  return [...parts.values()];
}

export const pathsOf = (o, attrs) =>
  Object.keys(o)
    .filter(k => o[k])
    .map(k => `<path class="${k}" d="${o[k]}"${attrs && attrs[k] ? attrs[k] : ""}/>`)
    .join("");

export const pill = (x, y, h, w) =>
  `M${round1(x)} ${round1(y + w / 2)}a${w / 2} ${w / 2} 0 0 1 ${w} 0v${round1(Math.max(0, h - w))}a${w / 2} ${w / 2} 0 0 1 ${-w} 0z`;

export const rectP = (x, y, w, h) => `M${round1(x)} ${round1(y)}h${w}v${h}h${-w}z`;

// label boxes are cut out of the connector layers (clip-rule evenodd), so lines pass behind the text instead of through it
export const KO_TOP = 5;

const KO_H = 13;

export const knockout = (id, w, h, boxes, dy) => {
  let d = `M-4 -4H${w + 4}V${h + 4}H-4Z`;
  for (let k = 0; k < boxes.length; k += 3)
    d += `M${round1(boxes[k])} ${round1(boxes[k + 1] - dy)}h${round1(boxes[k + 2])}v${KO_H}h${-round1(boxes[k + 2])}z`;
  return `<clipPath id="${id}" clipPathUnits="userSpaceOnUse"><path clip-rule="evenodd" d="${d}"/></clipPath>`;
};
