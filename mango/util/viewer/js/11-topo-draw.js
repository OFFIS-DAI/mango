  const topoSvg = $("topo-svg"), topoCard = $("topo"), topoB = $("topo-b");
  const CHAR_W = 6.9, BULK = EDGES.length > 600;
  let TD = null;
  const cut = s => { s = String(s); return s.length > 24 ? s.slice(0, 24) + "…" : s; };
  const sameDir = new Set(EDGES.map(e => e.a + ":" + e.b));
  const hasReverse = e => e.a !== e.b && sameDir.has(e.b + ":" + e.a);

  function fitTopology(W, H) {
    const n = NODES.length, linked = TL.linked;
    const silentRow = TL.silent.length && linked.length ? 28 : 0;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const i of linked) {
      minX = Math.min(minX, TL.x[i]); maxX = Math.max(maxX, TL.x[i]);
      minY = Math.min(minY, TL.y[i]); maxY = Math.max(maxY, TL.y[i]);
    }
    const manyLinks = EDGES.length > 60;
    const longest = Math.max(0, ...linked.map(i => cut(NODES[i].id).length)) * CHAR_W;
    const padX = Math.min(W * 0.3, (manyLinks ? 0.75 : 1) * longest + 24), padY = 30;
    // an axis without extent (two agents side by side) is centred rather than scaled
    const spanX = maxX - minX > 1e-9 ? maxX - minX : 0, spanY = maxY - minY > 1e-9 ? maxY - minY : 0;
    let sx = spanX ? (W - 2 * padX) / spanX : Infinity, sy = spanY ? (H - silentRow - 2 * padY) / spanY : Infinity;
    sx = Math.max(1, Math.min(sx, sy * 1.6));
    sy = Math.max(1, Math.min(sy, sx * 1.6));
    const offX = (W - spanX * sx) / 2, offY = (H - silentRow - spanY * sy) / 2;
    let X = TL.x, Y = TL.y;
    // a ring stretched into an ellipse crowds the ends of its long axis at equal angles; equal arc lengths spread it evenly
    if (TL.mode === "ring" && spanX && spanY && Math.abs(sx - sy) > 0.02 * Math.max(sx, sy)) {
      const K = 720, a0 = -Math.PI / 2, cum = new Float64Array(K + 1), TAU = 2 * Math.PI;
      for (let k = 1; k <= K; k++) { const p = a0 + (k - 0.5) / K * TAU; cum[k] = cum[k - 1] + Math.hypot(sx * Math.sin(p), sy * Math.cos(p)); }
      X = Float64Array.from(TL.x);
      Y = Float64Array.from(TL.y);
      for (const i of linked) {
        if (!TL.ring[i]) continue;
        const target = (((TL.ang[i] - a0) % TAU) + TAU) % TAU / TAU * cum[K];
        const k = clamp(lowerBound(cum, target), 1, K), f = (target - cum[k - 1]) / ((cum[k] - cum[k - 1]) || 1);
        const th = a0 + (k - 1 + f) / K * TAU;
        X[i] = Math.cos(th) * TL.rad[i];
        Y[i] = Math.sin(th) * TL.rad[i];
      }
    }
    const P = new Array(n);
    for (const i of linked) P[i] = { x: spanX ? offX + (X[i] - minX) * sx : W / 2, y: spanY ? offY + (Y[i] - minY) * sy : (H - silentRow) / 2 };
    let minD = Infinity;
    for (let a = 0; a < linked.length; a++) for (let b = a + 1; b < linked.length; b++) {
      const i = linked[a], j = linked[b];
      if (TL.hubs.has(i) || TL.hubs.has(j)) continue;
      const d = Math.hypot(P[i].x - P[j].x, P[i].y - P[j].y);
      if (d < minD) minD = d;
    }
    if (!isFinite(minD)) minD = 60;
    const dense = minD < 18 || manyLinks;
    // circles never overlap their neighbours: in a crowded ring they shrink below 3 px and drop their outline
    const rMax = 0.42 * minD < 3 ? Math.max(1.2, 0.42 * minD) : clamp(0.36 * minD, 3, 10), rMin = Math.min(rMax, Math.max(2.5, rMax * 0.6));
    const traffic = d => d.sent + d.received, maxT = Math.max(1, ...NODES.map(traffic));
    const R = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      R[i] = TL.hubs.has(i) ? Math.min(16, Math.max(rMax * 1.5, 9)) : rMin + (rMax - rMin) * Math.sqrt(traffic(NODES[i]) / maxT);
    }
    const sl = TL.silent;
    sl.forEach((i, k) => {
      const step = Math.min(130, (W - 24) / Math.max(1, sl.length));
      P[i] = { x: 12 + R[i] + k * step, y: H - 14 };
    });
    if (!linked.length) sl.forEach((i, k) => { P[i] = { x: W / 2 + (k - (sl.length - 1) / 2) * 110, y: H / 2 }; });
    return { P, R, dense, minD, silentRow, W, H, cx: W / 2, cy: (H - silentRow) / 2 };
  }

  function edgeGeom(e, F) {
    const { P, R } = F, a = P[e.a], b = P[e.b];
    if (e.a === e.b) {
      const vx = a.x - F.cx, vy = a.y - F.cy, l = Math.hypot(vx, vy) || 1, ux = l > 1 ? vx / l : 0, uy = l > 1 ? vy / l : -1;
      const r = R[e.a], cx0 = a.x + ux * (r + 8), cy0 = a.y + uy * (r + 8);
      const px = -uy, py = ux;
      const s = { x: a.x + (ux * 0.7 + px * 0.7) * r, y: a.y + (uy * 0.7 + py * 0.7) * r };
      const t = { x: a.x + (ux * 0.7 - px * 0.7) * r, y: a.y + (uy * 0.7 - py * 0.7) * r };
      const d = `M${r1d(s.x)},${r1d(s.y)} C${r1d(cx0 + px * 12)},${r1d(cy0 + py * 12)} ${r1d(cx0 - px * 12)},${r1d(cy0 - py * 12)} ${r1d(t.x)},${r1d(t.y)}`;
      const mid = { x: cx0 + ux * 6, y: cy0 + uy * 6 };
      return { d, straight: d, at: () => ({ x: cx0, y: cy0 }), tan: () => ({ x: -px, y: -py }), mid, out: { x: ux, y: uy } };
    }
    const vx = b.x - a.x, vy = b.y - a.y, len = Math.hypot(vx, vy) || 1, nx = -vy / len, ny = vx / len;
    const recip = hasReverse(e);
    const bend = recip ? Math.min(12, len * 0.14) : 0;
    let c = { x: (a.x + b.x) / 2 + nx * bend, y: (a.y + b.y) / 2 + ny * bend };
    if (TL.ring[e.a] && TL.ring[e.b] && len < 0.3 * Math.hypot(a.x - F.cx, a.y - F.cy)) {
      const mx = (a.x + b.x) / 2 - F.cx, my = (a.y + b.y) / 2 - F.cy, ml = Math.hypot(mx, my) || 1;
      const out = len * 0.32 + (recip ? (natural(String(e.from), String(e.to)) < 0 ? 7 : -3) : 0);
      c = { x: (a.x + b.x) / 2 + mx / ml * out, y: (a.y + b.y) / 2 + my / ml * out };
    }
    const toward = (p, d) => { const l = Math.hypot(c.x - p.x, c.y - p.y) || 1; return { x: p.x + (c.x - p.x) / l * d, y: p.y + (c.y - p.y) / l * d }; };
    const p0 = toward(a, R[e.a] + 2), p2 = toward(b, R[e.b] + 2.5);
    const at = t => {
      const u = 1 - t;
      return { x: u * u * p0.x + 2 * t * u * c.x + t * t * p2.x, y: u * u * p0.y + 2 * t * u * c.y + t * t * p2.y };
    };
    const tan = t => {
      const dx = 2 * (1 - t) * (c.x - p0.x) + 2 * t * (p2.x - c.x), dy = 2 * (1 - t) * (c.y - p0.y) + 2 * t * (p2.y - c.y), l = Math.hypot(dx, dy) || 1;
      return { x: dx / l, y: dy / l };
    };
    const q0 = { x: a.x + vx / len * (R[e.a] + 2), y: a.y + vy / len * (R[e.a] + 2) };
    const q2 = { x: b.x - vx / len * (R[e.b] + 2.5), y: b.y - vy / len * (R[e.b] + 2.5) };
    return {
      d: `M${r1d(p0.x)},${r1d(p0.y)} Q${r1d(c.x)},${r1d(c.y)} ${r1d(p2.x)},${r1d(p2.y)}`,
      straight: `M${r1d(q0.x)},${r1d(q0.y)} L${r1d(q2.x)},${r1d(q2.y)}`,
      at, tan, mid: at(0.5), out: recip ? { x: nx, y: ny } : null,
    };
  }
  const badgeOf = (i, F) => {
    const r = F.R[i];
    return F.minD < 18 ? { x: r * 0.72, y: -r * 0.72, r: 2.5, small: true } : { x: r * 0.72 + 2.5, y: -r * 0.72 - 2.5, r: 6.5 };
  };
  // a selection or chain ring sits at r + 4: the badge moves out so it does not cover that ring
  const badgeShift = (i, ringed) => {
    if (!ringed || !TD) return null;
    const b = badgeOf(i, TD.F), d = Math.hypot(b.x, b.y), want = TD.F.R[i] + 5.5 + b.r;
    return want > d ? { x: b.x / d * (want - d), y: b.y / d * (want - d) } : null;
  };

  function drawTopology() {
    if (!TL) layoutTopology();
    const W = topoB.clientWidth, H = topoB.clientHeight;
    if (!W || !H || !EDGES.length || state.topo.mode !== "graph" || !state.topo.open) return;
    const t0 = performance.now();
    const F = fitTopology(W, H);
    const merge = F.dense && EDGES.length > 60;
    const maxSent = Math.max(1, ...EDGES.map(e => e.sent));
    const width = e => (F.dense ? 0.6 : 1) + (F.dense ? 1.6 : 2.6) * Math.sqrt(e.sent / maxSent);
    const rate = e => (e.lost - e.inFlight) / e.sent;
    const lossyList = EDGES.filter(e => e.lossy).sort((a, b) => rate(b) - rate(a) || b.lost - a.lost);
    const maxRate = lossyList.length ? rate(lossyList[0]) : 1;
    const cutSet = new Set(F.dense ? lossyList.slice(0, 12) : lossyList);
    const geo = EDGES.map(e => edgeGeom(e, F));
    // links stay solid (3:1) until a hairball forms; only then are they thinned out by count
    const linkOpacity = EDGES.length > 200 ? clamp(9 / Math.sqrt(EDGES.length), 0.12, 0.6) : 1;
    topoSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    topoSvg.style.setProperty("--lo", linkOpacity.toFixed(2));
    topoSvg.setAttribute("aria-label", `Message topology, ${plural(NODES.length, "agent")}, ${plural(EDGES.length, "connection")}`);
    const mk = (id, cls) => `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"`
      + ` markerUnits="userSpaceOnUse"><path class="${cls}" d="M0.5,1 L9.5,5 L0.5,9 L2.6,5 z"/></marker>`;
    const h = [`<defs>${mk("tm-b", "mk-b")}${mk("tm-q", "mk-q")}${mk("tm-a", "mk-a")}${mk("tm-n", "mk-n")}${mk("tm-l", "mk-l")}</defs>`];
    const drawnPair = new Set();
    const cutD = (e, g) => {
      if (!(e.lossy && cutSet.has(e))) return "";
      const p = g.at(0.8), t = g.tan(0.8), L2 = F.dense ? 3 : 4;
      return `M${r1d(p.x - t.y * L2)},${r1d(p.y + t.x * L2)} L${r1d(p.x + t.y * L2)},${r1d(p.y - t.x * L2)}`;
    };
    const lossMark = (e, g) => {
      const d = cutD(e, g);
      if (d) return `<path class="cuth" d="${d}"/><path class="cut" d="${d}"/>`;
      if (!e.lossy && e.inFlight > 0) { const p = g.at(0.8); return `<circle class="fl" cx="${r1d(p.x)}" cy="${r1d(p.y)}" r="3.5"/>`; }
      return "";
    };
    if (BULK) {
      const bulk = ["", "", "", ""];
      let cuts = "";
      for (const e of EDGES) {
        const pairKey = Math.min(e.a, e.b) + ":" + Math.max(e.a, e.b);
        if (hasReverse(e)) { if (drawnPair.has(pairKey)) continue; drawnPair.add(pairKey); }
        bulk[Math.min(3, Math.floor(Math.sqrt(e.sent / maxSent) * 4))] += geo[e.k].straight;
        cuts += cutD(e, geo[e.k]);
      }
      h.push(`<g class="bulk" aria-hidden="true">${bulk.map((d, k) => `<path class="lk-b" d="${d}" stroke-width="${(0.6 + 0.5 * k).toFixed(1)}"/>`).join("")}`
        + `<path class="cuth" d="${cuts}"/><path class="cut" d="${cuts}"/></g>`);
      h.push(`<g class="bulk-match" aria-hidden="true"></g><g class="bulk-live" aria-hidden="true"></g>`);
    } else {
      h.push(`<g class="links">`);
      for (const e of EDGES) {
        const g = geo[e.k], recip = hasReverse(e);
        let cls = "lk" + (e.lossy ? " lossy" : "");
        const pairKey = Math.min(e.a, e.b) + ":" + Math.max(e.a, e.b);
        if (merge && recip) { cls += drawnPair.has(pairKey) ? " twin" : " both"; drawnPair.add(pairKey); }
        const w = width(e).toFixed(2), lost = e.lost - e.inFlight;
        const lab = `${e.from} to ${e.to}, ${plural(e.sent, "message")}${lost ? `, ${nf(lost)} lost` : ""}${e.inFlight ? `, ${nf(e.inFlight)} in flight` : ""}`;
        const lr = e.lossy ? ` style="--lr:${(0.35 + 0.65 * rate(e) / (maxRate || 1)).toFixed(2)}"` : "";
        h.push(`<g class="${cls}" data-e="${e.k}" role="button" tabindex="-1" aria-pressed="false" aria-label="${esc(lab)}"${lr}>`
          + `<path class="hit" d="${g.d}"/>`
          + `<path class="ln" d="${merge && recip ? g.straight : g.d}" stroke-width="${w}"/>`
          + (merge && recip ? `<path class="lc" d="${g.d}" stroke-width="${w}"/>` : "")
          + lossMark(e, g) + `</g>`);
      }
      h.push(`</g>`);
    }
    h.push(`<g class="hot" id="topo-hot"></g><g class="nodes">`);
    for (let i = 0; i < NODES.length; i++) {
      const d = NODES[i], p = F.P[i], r = F.R[i];
      if (!p) continue;
      const cls = "nd" + (TL.silent.includes(i) ? " silent" : "") + (!d.records && d.lane < 0 ? " norec" : "") + (TL.hubs.has(i) ? " hub" : "")
        + (r < 2.5 ? " tiny" : "");
      let badge = "";
      if (d.failed) {
        const b = badgeOf(i, F);
        badge = `<g class="bdg"><circle class="bd" cx="${r1d(b.x)}" cy="${r1d(b.y)}" r="${b.r}"/>`
          + (b.small ? "" : `<text class="bn" x="${r1d(b.x)}" y="${r1d(b.y + 3.3)}" text-anchor="middle">${d.failed > 99 ? "99+" : d.failed}</text>`) + `</g>`;
      }
      h.push(`<g class="${cls}" data-n="${i}" transform="translate(${r1d(p.x)},${r1d(p.y)})" role="button" tabindex="-1" aria-pressed="false"`
        + ` aria-label="${esc(nodeLabel(i))}">`
        + `<circle class="ring" r="${r1d(r + 4)}"/><circle class="dot" r="${r1d(r)}"/><circle class="in" r="${r1d(r * 0.45)}"/>`
        + `<circle class="fr" r="${r1d(r + 5)}"/>${badge}<text class="nm"></text></g>`);
    }
    h.push(`</g><g class="cnt" id="topo-cnt" aria-hidden="true"></g><g class="pills" id="topo-pills"></g>`);
    h.push(`<text class="cl" id="topo-cap" x="10" y="16"></text>`);
    if (TL.silent.length && TL.linked.length) h.push(`<text class="cl" x="${W - 10}" y="${H - 10}" text-anchor="end">no messages</text>`);
    topoSvg.innerHTML = h.join("");
    const nodeEls = new Array(NODES.length), linkEls = new Array(EDGES.length);
    topoSvg.querySelectorAll(".nd").forEach(el => { nodeEls[+el.dataset.n] = el; });
    topoSvg.querySelectorAll(".lk").forEach(el => { linkEls[+el.dataset.e] = el; });
    let grid = null;
    if (BULK) {
      grid = new Map();
      for (const e of EDGES) {
        const a = F.P[e.a], b = F.P[e.b], len = Math.hypot(b.x - a.x, b.y - a.y), steps = Math.max(1, Math.ceil(len / 8));
        for (let s = 0; s <= steps; s++) {
          const x = a.x + (b.x - a.x) * s / steps, y = a.y + (b.y - a.y) * s / steps, k = Math.floor(x / 24) + "," + Math.floor(y / 24);
          (grid.get(k) || grid.set(k, []).get(k)).push(e.k, x, y);
        }
      }
    }
    TD = { F, geo, width, merge, nodeEls, linkEls, grid, cutSet, cutD: e => cutD(EDGES[e], geo[e]), labelsKey: "", ms: performance.now() - t0 };
    mapState(true);
    drawCounts();
  }
  const nodeLabel = i => {
    const d = NODES[i];
    return `${d.id}, ${d.lane >= 0 ? plural(d.records, "record") : "no records in this trace"}${d.failed ? `, ${nf(d.failed)} failed` : ""}`
      + `, sent ${nf(d.sent)}, received ${nf(d.received)}, ${plural(d.partners.size - (d.partners.has(i) ? 1 : 0), "partner")}`;
  };

  const SHORT = (() => {
    const pre = new Map();
    for (const d of NODES) {
      const m = /^(.*\D)\d+$/.exec(String(d.id));
      if (m) pre.set(m[1], (pre.get(m[1]) || 0) + 1);
    }
    const best = [...pre].sort((a, b) => b[1] - a[1])[0];
    if (!best || best[1] < Math.max(3, 0.5 * NODES.length)) return null;
    const prefix = best[0];
    return { prefix, of: id => id.startsWith(prefix) && /^\d+$/.test(id.slice(prefix.length)) ? id.slice(prefix.length) : null };
  })();
  function labelPriority(i) {
    const d = NODES[i];
    return (i === mapSets.sel ? 1e9 : 0) + (mapSets.chain.has(i) ? 1e8 : 0) + (TL.hubs.has(i) ? 1e7 : 0)
      + (d.failed ? 1e6 : 0) + (mapSets.fol.has(i) ? 1e5 : 0) + d.sent + d.received;
  }
  const hopMid = h => {
    if (h.edge >= 0) return TD.geo[h.edge].mid;
    const a = TD.F.P[h.from], b = TD.F.P[h.to];
    return a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null;
  };
  // Labels go to the first free spot (radially outward, right, left, above, below) that clears nodes with their rings,
  // failure badges, hop pills and other labels. When a common prefix crowds the ring, every prefixed node gets its number.
  function labelLayout(short) {
    const { P, R, W, H, dense, silentRow, cx, cy } = TD.F;
    const ringed = new Set([mapSets.sel, ...mapSets.chain, ...mapSets.dag]);
    const nodeR = i => ringed.has(i) ? R[i] + 6 : R[i] + 1.5;
    const solid = [], labels = [];
    for (let i = 0; i < NODES.length; i++) {
      if (!P[i]) continue;
      const rr = nodeR(i);
      solid.push({ x: P[i].x - rr, y: P[i].y - rr, w: 2 * rr, h: 2 * rr, n: i });
      if (NODES[i].failed) {
        const b = badgeOf(i, TD.F), sh = badgeShift(i, ringed.has(i)) || { x: 0, y: 0 };
        solid.push({ x: P[i].x + b.x + sh.x - b.r - 1, y: P[i].y + b.y + sh.y - b.r - 1, w: 2 * b.r + 2, h: 2 * b.r + 2 });
      }
    }
    for (const hp of hops) {
      const m = hopMid(hp);
      if (m) solid.push({ x: m.x - 13, y: m.y - 9.5, w: 26, h: 19 });
    }
    // a label's own node is a disc it already clears radially; its square box would reject every diagonal spot
    const hit = (list, b, gx, own) => list.some(p => p.n !== own && b.x - gx < p.x + p.w && p.x < b.x + b.w + gx && b.y < p.y + p.h && p.y < b.y + b.h);
    const free = (b, gx, own) => b.x > 1 && b.x + b.w < W - 1 && b.y > 1 && b.y + b.h < H - silentRow - 1
      && !hit(solid, b, 0, own) && !hit(labels, b, gx, -1);
    // beside a numbered group the named agents (aggregator, grid-operator) are the distinct roles: they are placed
    // before the numbers crowd them out, and may also sit inside the ring
    const named = i => !!SHORT && SHORT.of(String(NODES[i].id)) == null;
    const prio = i => labelPriority(i) + (named(i) ? 5e6 : 0);
    const linked = TL.linked, order = linked.slice().sort((a, b) => prio(b) - prio(a));
    const result = new Map();
    const loose = !dense || linked.length <= 80;
    let missed = 0;
    const spotsFor = (i, w, hgt, extra, wide) => {
      const p = P[i], g = nodeR(i) + 3 + extra, dx = p.x - cx, dy = p.y - cy, l = Math.hypot(dx, dy), spots = [];
      if (l > 4) {
        const ux = dx / l, uy = dy / l, ax = p.x + ux * g, ay = p.y + uy * g;
        const anchor = ux > 0.35 ? "start" : ux < -0.35 ? "end" : "middle", sy = uy > 0.35 ? ay : uy < -0.35 ? ay - hgt : ay - hgt / 2;
        spots.push({ x: anchor === "start" ? ax : anchor === "end" ? ax - w : ax - w / 2, y: sy, anchor });
        if (anchor === "middle" && wide) spots.push({ x: ax - w + 8, y: sy, anchor: "end" }, { x: ax - 8, y: sy, anchor: "start" });
      }
      spots.push({ x: p.x + g, y: p.y - hgt / 2, anchor: "start" }, { x: p.x - g - w, y: p.y - hgt / 2, anchor: "end" },
        { x: p.x - w / 2, y: p.y - g - hgt, anchor: "middle" }, { x: p.x - w / 2, y: p.y + g, anchor: "middle" });
      return spots;
    };
    for (const i of order) {
      const pr = prio(i), must = pr >= 1e7;
      if (dense && pr < 1e5 && !loose) continue;
      const sh = short && SHORT ? SHORT.of(String(NODES[i].id)) : null;
      const text = sh ?? cut(NODES[i].id), w = text.length * CHAR_W, hgt = 13, gx = sh ? 4 : 10;
      const ringOnly = dense && TL.ring[i] && !must && !named(i);
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
        if (i === mapSets.sel || !hit(labels, { x: c.x - 4, y: c.y - 2, w: w + 8, h: 17 }, 2, -1)) { spot = c; pill = true; }
      }
      if (spot) {
        labels.push({ x: spot.x, y: spot.y, w, h: hgt });
        result.set(i, { ...spot, w, pill, text, short: sh != null });
      } else if (sh != null || SHORT?.of(String(NODES[i].id)) != null) missed++;
    }
    return { result, missed };
  }
  function placeLabels() {
    if (!TD) return;
    const { P, R } = TD.F;
    let lay = labelLayout(false), short = false;
    if (SHORT && lay.missed) { lay = labelLayout(true); short = true; }
    const result = lay.result;
    for (let i = 0; i < NODES.length; i++) {
      const el = TD.nodeEls[i];
      if (!el) continue;
      const t = el.querySelector(".nm"), p = P[i], r = R[i];
      el.querySelector(".npill")?.remove();
      const s = result.get(i);
      const text = s ? s.text : cut(NODES[i].id);
      t.textContent = text;
      if (TL.silent.includes(i)) {
        t.setAttribute("x", r + 5); t.setAttribute("y", 4); t.setAttribute("text-anchor", "start"); t.setAttribute("class", "nm");
        continue;
      }
      if (s) {
        const tx = (s.anchor === "start" ? s.x : s.anchor === "end" ? s.x + s.w : s.x + s.w / 2) - p.x;
        t.setAttribute("x", r1d(tx)); t.setAttribute("y", r1d(s.y + 10 - p.y)); t.setAttribute("text-anchor", s.anchor);
        t.setAttribute("class", "nm" + (s.pill ? " pill" : ""));
        if (s.pill) {
          const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
          rect.setAttribute("class", "npill");
          rect.setAttribute("x", r1d(s.x - p.x - 4)); rect.setAttribute("y", r1d(s.y - p.y - 2));
          rect.setAttribute("width", r1d(s.w + 8)); rect.setAttribute("height", 17); rect.setAttribute("rx", 4);
          el.insertBefore(rect, t);
          el.parentNode.appendChild(el);
        }
      } else {
        t.setAttribute("x", 0); t.setAttribute("y", r1d(-r - 6)); t.setAttribute("text-anchor", "middle");
        t.setAttribute("class", "nm tight");
      }
      if (String(NODES[i].id).length > 24 || (s && s.short)) t.innerHTML = `${esc(text)}<title>${esc(NODES[i].id)}</title>`;
    }
    $("topo-cap").textContent = short ? `N = ${SHORT.prefix}N` : "";
    TD.labels = result;
  }

  function drawCounts() {
    const g = $("topo-cnt");
    if (!g || !TD) return;
    if (EDGES.length > 12 || TD.F.dense) { g.innerHTML = ""; return; }
    const placed = [];
    const { P, R } = TD.F;
    for (let i = 0; i < NODES.length; i++) if (P[i]) placed.push({ x: P[i].x - R[i] - 2, y: P[i].y - R[i] - 2, w: 2 * R[i] + 4, h: 2 * R[i] + 4 });
    for (const s of TD.labels.values()) placed.push({ x: s.x, y: s.y, w: s.w, h: 13 });
    const overlaps = b => placed.some(p => b.x < p.x + p.w && p.x < b.x + b.w && b.y < p.y + p.h && p.y < b.y + b.h);
    let out = "";
    for (const e of EDGES) {
      const lost = e.lost - e.inFlight, text = nf(e.sent), extra = lost ? ` · ${nf(lost)} lost` : "";
      const w = (text.length + extra.length) * 6.4 + 12, gm = TD.geo[e.k];
      // a connection used both ways carries its pill outside its own curve, so the twin pills sit apart
      const off = gm.out && e.a !== e.b ? 11 : 0;
      for (const t of [0.5, 0.4, 0.6]) {
        const q = gm.at(t), p = off ? { x: q.x + gm.out.x * off, y: q.y + gm.out.y * off } : q;
        const b = { x: p.x - w / 2, y: p.y - 8.5, w, h: 17 };
        if (overlaps(b)) continue;
        placed.push(b);
        out += `<g class="tp cnt" data-e="${e.k}" transform="translate(${r1d(p.x)},${r1d(p.y)})">`
          + `<rect x="${r1d(-w / 2)}" y="-8.5" width="${r1d(w)}" height="17" rx="8.5"/>`
          + `<text text-anchor="middle" dy="3.6">${text}${extra ? `<tspan class="lo">${extra}</tspan>` : ""}</text></g>`;
        break;
      }
    }
    g.innerHTML = out;
    syncCountPills(new Set(hops.map(h => h.edge).filter(e => e >= 0)));
  }
