import { clamp, round1 } from "../../core/util.js";
import { NODES } from "../../model.js";
import { hops } from "../../selection.js";
import { $ } from "../dom.js";
import { html } from "../html.js";
import { CHAR_W, badgeOf, badgeShift, cut, mapSets, shortIds, topoLayout, topoView } from "./draw.js";

function labelPriority(i) {
  const d = NODES[i];
  return (
    (i === mapSets.sel ? 1e9 : 0) +
    (mapSets.chain.has(i) ? 1e8 : 0) +
    (topoLayout.hubs.has(i) ? 1e7 : 0) +
    (d.failed ? 1e6 : 0) +
    (mapSets.fol.has(i) ? 1e5 : 0) +
    d.sent +
    d.received
  );
}

const hopMid = h => {
  if (h.edge >= 0) return topoView.geo[h.edge].mid;
  const a = topoView.fit.P[h.from];
  const b = topoView.fit.P[h.to];
  return a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null;
};

// Labels go to the first free spot (radially outward, right, left, above, below) that clears nodes with their rings,
// failure badges, hop pills and other labels. When a common prefix crowds the ring, every prefixed node gets its number.
function labelLayout(short) {
  const { P, R, W, H, dense, silentRow, cx, cy } = topoView.fit;
  const ringed = new Set([mapSets.sel, ...mapSets.chain, ...mapSets.dag]);
  const nodeR = i => (ringed.has(i) ? R[i] + 6 : R[i] + 1.5);
  const solid = [];
  const labels = [];
  for (let i = 0; i < NODES.length; i++) {
    if (!P[i]) continue;
    const rr = nodeR(i);
    solid.push({ x: P[i].x - rr, y: P[i].y - rr, w: 2 * rr, h: 2 * rr, n: i });
    if (NODES[i].failed) {
      const b = badgeOf(i, topoView.fit);
      const sh = badgeShift(i, ringed.has(i)) || { x: 0, y: 0 };
      solid.push({
        x: P[i].x + b.x + sh.x - b.r - 1,
        y: P[i].y + b.y + sh.y - b.r - 1,
        w: 2 * b.r + 2,
        h: 2 * b.r + 2,
      });
    }
  }
  for (const hp of hops) {
    const m = hopMid(hp);
    if (m) solid.push({ x: m.x - 13, y: m.y - 9.5, w: 26, h: 19 });
  }
  // a label's own node is a disc it already clears radially; its square box would reject every diagonal spot
  const hit = (list, b, gx, own) =>
    list.some(p => p.n !== own && b.x - gx < p.x + p.w && p.x < b.x + b.w + gx && b.y < p.y + p.h && p.y < b.y + b.h);
  const free = (b, gx, own) =>
    b.x > 1 &&
    b.x + b.w < W - 1 &&
    b.y > 1 &&
    b.y + b.h < H - silentRow - 1 &&
    !hit(solid, b, 0, own) &&
    !hit(labels, b, gx, -1);
  // beside a numbered group the named agents (aggregator, grid-operator) are the distinct roles: they are placed
  // before the numbers crowd them out, and may also sit inside the ring
  const named = i => !!shortIds && shortIds.of(String(NODES[i].id)) == null;
  const prio = i => labelPriority(i) + (named(i) ? 5e6 : 0);
  const linked = topoLayout.linked;
  const order = linked.slice().sort((a, b) => prio(b) - prio(a));
  const result = new Map();
  const loose = !dense || linked.length <= 80;
  let missed = 0;
  const spotsFor = (i, w, hgt, extra, wide) => {
    const p = P[i];
    const g = nodeR(i) + 3 + extra;
    const dx = p.x - cx;
    const dy = p.y - cy;
    const l = Math.hypot(dx, dy);
    const spots = [];
    if (l > 4) {
      const ux = dx / l;
      const uy = dy / l;
      const ax = p.x + ux * g;
      const ay = p.y + uy * g;
      const anchor = ux > 0.35 ? "start" : ux < -0.35 ? "end" : "middle";
      const sy = uy > 0.35 ? ay : uy < -0.35 ? ay - hgt : ay - hgt / 2;
      spots.push({ x: anchor === "start" ? ax : anchor === "end" ? ax - w : ax - w / 2, y: sy, anchor });
      if (anchor === "middle" && wide)
        spots.push({ x: ax - w + 8, y: sy, anchor: "end" }, { x: ax - 8, y: sy, anchor: "start" });
    }
    spots.push(
      { x: p.x + g, y: p.y - hgt / 2, anchor: "start" },
      { x: p.x - g - w, y: p.y - hgt / 2, anchor: "end" },
      { x: p.x - w / 2, y: p.y - g - hgt, anchor: "middle" },
      { x: p.x - w / 2, y: p.y + g, anchor: "middle" },
    );
    return spots;
  };
  for (const i of order) {
    const pr = prio(i);
    const must = pr >= 1e7;
    if (dense && pr < 1e5 && !loose) continue;
    const sh = short && shortIds ? shortIds.of(String(NODES[i].id)) : null;
    const text = sh ?? cut(NODES[i].id);
    const w = text.length * CHAR_W;
    const hgt = 13;
    const gx = sh ? 4 : 10;
    const ringOnly = dense && topoLayout.ring[i] && !must && !named(i);
    let spot = null;
    for (const extra of must ? [0, 8, 16] : named(i) ? [0, 8] : [0]) {
      const spots = spotsFor(i, w, hgt, extra, must || named(i));
      if (ringOnly && spots.length > 1) spots.length = 1;
      spot = spots.find(s => free({ x: s.x, y: s.y, w, h: hgt }, gx, i));
      if (spot) break;
    }
    let pill = false;
    if (!spot && must) {
      const s = spotsFor(i, w, hgt, 0)[0];
      const c = { x: clamp(s.x, 2, W - w - 2), y: clamp(s.y, 2, H - hgt - 2), anchor: "start" };
      if (i === mapSets.sel || !hit(labels, { x: c.x - 4, y: c.y - 2, w: w + 8, h: 17 }, 2, -1)) {
        spot = c;
        pill = true;
      }
    }
    if (spot) {
      labels.push({ x: spot.x, y: spot.y, w, h: hgt });
      result.set(i, { ...spot, w, pill, text, short: sh != null });
    } else if (sh != null || shortIds?.of(String(NODES[i].id)) != null) missed++;
  }
  return { result, missed };
}

export function placeLabels() {
  if (!topoView) return;
  const { P, R } = topoView.fit;
  let lay = labelLayout(false);
  let short = false;
  if (shortIds && lay.missed) {
    lay = labelLayout(true);
    short = true;
  }
  const result = lay.result;
  for (let i = 0; i < NODES.length; i++) {
    const el = topoView.nodeEls[i];
    if (!el) continue;
    const t = el.querySelector(".nm");
    const p = P[i];
    const r = R[i];
    el.querySelector(".npill")?.remove();
    const s = result.get(i);
    const text = s ? s.text : cut(NODES[i].id);
    t.textContent = text;
    if (topoLayout.silent.includes(i)) {
      t.setAttribute("x", r + 5);
      t.setAttribute("y", 4);
      t.setAttribute("text-anchor", "start");
      t.setAttribute("class", "nm");
      continue;
    }
    if (s) {
      const tx = (s.anchor === "start" ? s.x : s.anchor === "end" ? s.x + s.w : s.x + s.w / 2) - p.x;
      t.setAttribute("x", round1(tx));
      t.setAttribute("y", round1(s.y + 10 - p.y));
      t.setAttribute("text-anchor", s.anchor);
      t.setAttribute("class", "nm" + (s.pill ? " pill" : ""));
      if (s.pill) {
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("class", "npill");
        rect.setAttribute("x", round1(s.x - p.x - 4));
        rect.setAttribute("y", round1(s.y - p.y - 2));
        rect.setAttribute("width", round1(s.w + 8));
        rect.setAttribute("height", 17);
        rect.setAttribute("rx", 4);
        el.insertBefore(rect, t);
        el.parentNode.appendChild(el);
      }
    } else {
      t.setAttribute("x", 0);
      t.setAttribute("y", round1(-r - 6));
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("class", "nm tight");
    }
    if (String(NODES[i].id).length > 24 || (s && s.short)) t.innerHTML = html`${text}<title>${NODES[i].id}</title>`;
  }
  $("topo-cap").textContent = short ? `N = ${shortIds.prefix}N` : "";
  topoView.labels = result;
}
