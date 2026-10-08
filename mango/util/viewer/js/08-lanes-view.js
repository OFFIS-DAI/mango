  const lanesBox = $("lanes"), lanesPanel = $("lanes-panel"), lhead = $("lhead"), gutterEl = $("gutter"), plane = $("plane");
  const lanesIn = $("lanes-in");
  let GEO = null, THREAD = null, mountedCols = null;
  const mounted = new Map();
  const r1d = v => Math.round(v * 10) / 10;
  const SKIP_KV = new Set([...CORE, "src", "process", "sender", "receiver", "receiver_id", "content"]);

  const measureCtx = document.createElement("canvas").getContext("2d");
  const textW = (text, font) => {
    if (!measureCtx) return text.length * 6.6;
    measureCtx.font = font;
    return measureCtx.measureText(text).width;
  };
  const fontVar = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim() || "monospace";
  function gutterFit(phone) {
    const base = gutterWidth(), ck = phone + ":" + CW;
    if (!L || !L.bandKey.length) return { gw: base, elide: false };
    if (L.gutter && L.gutter.ck === ck) return L.gutter;
    const mono = "11px " + fontVar("--font-data"), ui = "10.5px " + fontVar("--font-ui");
    let full = 0, short = 0;
    for (let b = 0; b < L.bandKey.length; b++) {
      const t = ktime(L.bandKey[b]), x = timeBits(t, b ? ktime(L.bandKey[b - 1]) : null), dw = x.date ? textW(x.date, ui) + 5 : 0;
      full = Math.max(full, dw + textW(x.same + x.rest, mono));
      if (b) short = Math.max(short, dw + textW((x.same ? "…" : "") + x.rest, mono));
    }
    const pad = 22, elide = phone && full + pad > base, gw = Math.ceil(Math.max(base, (elide ? short : full) + pad));
    L.gutter = { ck, gw, elide, first: elide ? tailLabel(ktime(L.bandKey[0]), gw - pad, mono) : null };
    return L.gutter;
  }
  function tailLabel(t, room, mono) {
    const parts = timeParts(t), n = parts.length;
    while (parts.length > 1 && textW(parts.join(""), mono) > room) parts.shift();
    return (parts.length < n ? `<span class="m">…</span>` : "") + parts.join("");
  }
  const lonelyLabel = t => GEO && GEO.elide ? tailLabel(t, GEO.gw - 22, "11px " + fontVar("--font-data")) : timeHTML(t, null);

  function geometry() {
    const lanes = state.lanes || [], phone = isPhone();
    const gf = gutterFit(phone), gw = gf.gw, ww = phone ? 40 : 56;
    const box = lanesBox.clientWidth || recBox.clientWidth || 800;
    const hasW = lanes.includes(WORLD), nA = lanes.length - (hasW ? 1 : 0);
    const avail = Math.max(0, box - gw - (hasW ? ww : 0));
    // on a phone two lanes share the screen exactly, so revealing a record never scrolls the no-agent lane under the time column
    let W = 0;
    if (nA && state.fit) W = Math.max(16, avail / nA);
    else if (nA && phone) W = clamp(avail / Math.min(nA, 2), 100, 320);
    // lanes that would overflow the box by a little shrink to 120 px first, so no lane opens cut in half
    else if (nA) W = avail / nA >= 120 ? Math.min(320, avail / nA) : 148;
    W = Math.floor(W);
    const x = [], w = [], life = [];
    let cx = 0;
    for (const l of lanes) {
      const lw = l === WORLD ? ww : W;
      x.push(cx);
      w.push(lw);
      life.push(Math.round(l === WORLD || state.fit ? cx + lw / 2 : cx + 24));
      cx += lw;
    }
    const labels = !(state.fit && W < 100);
    return { gw, ww, x, w, life, contentW: cx, width: Math.max(cx, box - gw), W, labels, headH: labels ? 52 : 112, box, elide: gf.elide, first: gf.first };
  }

  const receiverName = i => {
    const r = RECORDS[i];
    return r.receiver_id || (recvOf(i) >= 0 ? RECORDS[recvOf(i)].agent : null) || shortAid(r.receiver) || "?";
  };
  function labelHTML(i) {
    const r = RECORDS[i], ev = evOf[i], ty = typeOf(i);
    if (ev === E_SENT) {
      let s = esc(ty || "message");
      const rl = recvAgentOf[i], hidden = rl < 0 || L.col[rl] < 0;
      if (hidden) s += `<span class="m"> → ${esc(receiverName(i))}</span>`;
      if (sendStatus[i] === S_LOST) s += `<span class="lo"> · lost</span>` + (hidden ? `<i class="xb"></i>` : "");
      else if (sendStatus[i] === S_FLIGHT) s += `<span class="m"> · in flight</span>`;
      return s;
    }
    if (ev === E_RECV) {
      const p = causeIx[i];
      return esc(ty || "message") + (p < 0 || rowOf[p] < 0 ? `<span class="m"> ← ${esc(r.sender != null ? shortAid(r.sender) : "?")}</span>` : "");
    }
    if (ev === E_EMIT) return esc(ty || "event") + `<span class="m"> → ${r.target != null ? esc(shortAid(r.target)) : "all"}</span>`;
    if (ev === E_EVRECV || ev === E_ROLE) return esc(ty || r.event);
    if (ev === E_HANDLER) return `<span class="m">ƒ </span>${esc(r.handler ?? "handler")}`;
    let kv = "", n = 0;
    for (const k in r) {
      if (n >= 3 || SKIP_KV.has(k) || k.startsWith("sim_") || k.startsWith("__")) continue;
      let v = fmt(r[k]);
      if (v.length > 40) v = v.slice(0, 39) + "…";
      kv += ` ${esc(k)}=${esc(v)}`;
      n++;
    }
    return esc(r.event) + (kv ? `<span class="m">${kv}</span>` : "");
  }
  const ghostLabel = s => `lost · ${esc(typeOf(s) || "message")} ← ${esc(laneName(laneOf[s]))}`;
  const textLen = html => html.replace(/<[^>]*>/g, "").replace(/&(?:amp|lt|gt|quot);/g, "_").length;
  const glyphClass = i => {
    const ev = evOf[i];
    if (ev === E_RECV || ev === E_EVRECV || ev === E_ROLE) return "g-ring";
    if (ev === E_EMIT || CATS[kindOf[i]] === "run") return "g-dia";
    if (ev === E_SCHED) return "g-sq";
    if (ev === E_HANDLER || ev === E_CYCLE) return "g-sm";
    return "";
  };
  const ariaOf = i => {
    const r = RECORDS[i];
    return `${laneName(laneOf[i])}, ${recTime(i)}, ${r.event}${failOf[i] ? ", failed" : ""}${r.cause != null ? `, caused by ${r.cause}` : ""}`;
  };

  let CW = 6.9;
  function measureCW() {
    const s = document.createElement("span");
    s.style.cssText = "position:absolute;visibility:hidden;white-space:pre;font:11.5px var(--font-data)";
    s.textContent = "0".repeat(40);
    document.body.append(s);
    const w = s.getBoundingClientRect().width / 40;
    s.remove();
    return w || 6.9;
  }
  CW = measureCW();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      const w = measureCW();
      if (Math.abs(w - CW) > 0.05) { CW = w; labelGen++; invalidate(F_HEAD); }
    });
  }

  // Records of one row sorted by lane; a label runs until shortly before the next lifeline that has a record in the same row.
  function rowEntries(r) {
    const ents = [];
    for (let j = L.rowStart[r]; j < L.rowStart[r + 1]; j++) {
      const v = L.rowRecs[j];
      ents.push([v, v >= 0 ? colOfRec(v) : L.col[recvAgentOf[-1 - v]]]);
    }
    if (ents.length > 1) ents.sort((a, b) => a[1] - b[1]);
    return ents;
  }
  // a message drops into its receiver along a channel CHAN px left of the lifeline; labels end LGAP px before a lifeline
  const CHAN = 17, LGAP = 23;
  function labelLim(ents, k, r) {
    const g = GEO, c = ents[k][1], nx = k + 1 < ents.length ? ents[k + 1][1] : -1, start = g.life[c] + 16;
    let lim = nx >= 0 && nx !== c ? g.life[nx] - LGAP - start : g.width - start - 8;
    if (c !== L.WC) {
      lim = Math.min(lim, Math.max(g.W - 28, 420));
      const end = nx >= 0 && nx !== c ? nx : L.nC;
      for (let c2 = c + 1; c2 < end && g.life[c2] - LGAP - start < lim; c2++) {
        if (L.chan[c2 * L.maxRow + r]) { lim = g.life[c2] - LGAP - start; break; }
      }
    }
    return Math.max(0, Math.floor(lim));
  }
  let labelGen = 1;
  const labelW = new Float32Array(N), labelStamp = new Int32Array(N);
  function labelEnd(i) {
    const c = colOfRec(i);
    if (!GEO.labels || rowOf[i] < 0) return GEO.life[c];
    if (labelStamp[i] !== labelGen) {
      const ents = rowEntries(rowOf[i]), k = ents.findIndex(e => e[0] === i);
      labelW[i] = Math.min(textLen(labelHTML(i)) * CW, labelLim(ents, k, rowOf[i]));
      labelStamp[i] = labelGen;
    }
    return GEO.life[c] + 16 + labelW[i];
  }
  function rowBoxes(r, cache) {
    let out = cache.get(r);
    if (out) return out;
    out = [];
    if (r >= 0 && r < L.maxRow) {
      const g = GEO, ents = rowEntries(r), y = r * ROW;
      for (let k = 0; k < ents.length; k++) {
        const [v, c] = ents[k];
        if (c < 0) continue;
        out.push([g.life[c] - 7, y + 4, g.life[c] + 7, y + 18]);
        if (!g.labels) continue;
        const tw = Math.min(textLen(v < 0 ? ghostLabel(-1 - v) : labelHTML(v)) * CW, labelLim(ents, k, r));
        if (tw > 0) out.push([g.life[c] + 14, y + 5, g.life[c] + 18 + tw, y + 18]);
      }
    }
    cache.set(r, out);
    return out;
  }

  const add = (o, k, d) => { o[k] = (o[k] || "") + d; };
  const head = (o, k, tx, ty, ux, uy, len, wid) => {
    const bx = tx - ux * len, by = ty - uy * len;
    add(o, k, `M${r1d(tx)} ${r1d(ty)}L${r1d(bx - uy * wid)} ${r1d(by + ux * wid)}L${r1d(bx + uy * wid)} ${r1d(by - ux * wid)}Z`);
  };
  function arrowPath(o, lk, hk, xs, ys, xr, yr) {
    const dx = xr - xs, dy = yr - ys, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    const tx = xr - ux * 6.5, ty = yr - uy * 6.5;
    add(o, lk, `M${r1d(xs + ux * 6.5)} ${r1d(ys + uy * 6.5)}L${r1d(tx - ux * 5)} ${r1d(ty - uy * 5)}`);
    head(o, hk, tx, ty, ux, uy, 6, 3);
  }
  // A message runs along its send row (or the row boundary below it when other lanes have records there), drops down
  // the receiver's channel and turns into the receipt. Boundaries and channels carry no text, so the line stays whole.
  // Null when labels are hidden (Fit), where straight lines read best.
  function msgRoute(s, cv, rr, endGap) {
    const g = GEO, cs = colOfRec(s), rs = rowOf[s];
    if (!g.labels || cs === cv || cs < 0 || cv < 0) return null;
    const xs = g.life[cs], ys = rs * ROW + ROW / 2, xr = g.life[cv], yr = rr * ROW + ROW / 2;
    const xc = xr - CHAN, yb = rr >= rs ? (rs + 1) * ROW : rs * ROW, xe = xr - endGap, right = xr > xs;
    let clear = true;
    for (let j = L.rowStart[rs]; j < L.rowStart[rs + 1] && clear; j++) {
      const v = L.rowRecs[j], c = v >= 0 ? colOfRec(v) : L.col[recvAgentOf[-1 - v]];
      if (right ? c > cs && c < cv : c >= cv && c < cs) clear = false;
    }
    if (right) {
      const x0 = labelEnd(s) + 5;
      if (x0 < xc - 1) {
        if (clear) return [[x0, ys], [xc, ys], [xc, yr], [xe, yr]];
        return [[x0, ys], [x0 + 4, ys], [x0 + 4, yb], [xc, yb], [xc, yr], [xe, yr]];
      }
    } else if (clear) return [[xs - 6.5, ys], [xc, ys], [xc, yr], [xe, yr]];
    return [[xs, ys + (rr >= rs ? 6.5 : -6.5)], [xs, yb], [xc, yb], [xc, yr], [xe, yr]];
  }
  function roundedD(pts, dy, R) {
    const P = (x, y) => `${r1d(x)} ${r1d(y - dy)}`, n = pts.length;
    let d = "M" + P(pts[0][0], pts[0][1]);
    for (let k = 1; k < n - 1; k++) {
      const [px, py] = pts[k - 1], [x, y] = pts[k], [nx, ny] = pts[k + 1];
      const l1 = Math.hypot(x - px, y - py), l2 = Math.hypot(nx - x, ny - y);
      const r = Math.min(R, k === 1 ? l1 : l1 / 2, k === n - 2 ? l2 : l2 / 2);
      if (r < 0.5) { d += "L" + P(x, y); continue; }
      d += "L" + P(x - (x - px) / l1 * r, y - (y - py) / l1 * r) + "Q" + P(x, y) + " " + P(x + (nx - x) / l2 * r, y + (ny - y) / l2 * r);
    }
    return d + "L" + P(pts[n - 1][0], pts[n - 1][1]);
  }
  function clipPoly(pts, lo, hi) {
    const out = [];
    let cur = null;
    for (let k = 1; k < pts.length; k++) {
      const [x1, y1] = pts[k - 1], [x2, y2] = pts[k];
      let t0 = 0, t1 = 1;
      if (y1 !== y2) {
        const ta = (lo - y1) / (y2 - y1), tb = (hi - y1) / (y2 - y1);
        t0 = Math.max(0, Math.min(ta, tb));
        t1 = Math.min(1, Math.max(ta, tb));
      } else if (y1 < lo || y1 > hi) t1 = -1;
      if (t1 <= t0) { cur = null; continue; }
      const a = [x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0], b = [x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1];
      if (!cur || t0 > 0) { cur = [a]; out.push(cur); }
      cur.push(b);
      if (t1 < 1) cur = null;
    }
    return out;
  }
  function routeD(o, k, pts, y0) {
    const parts = tileSpan ? clipPoly(pts, tileSpan[0] + y0, tileSpan[1] + y0) : [pts];
    for (const p of parts) if (p.length > 1) add(o, k, roundedD(p, y0, 4));
  }
  function selfPath(o, lk, hk, x, ya, yb) {
    add(o, lk, `M${x + 5} ${ya + 3}C${x + 16.5} ${ya + 5} ${x + 16.5} ${yb - 5} ${x + 7.5} ${yb - 2}`);
    head(o, hk, x + 6.2, yb - 1.4, -0.94, 0.34, 5, 2.6);
  }
  // dashed lines are rastered along their whole length, so while a tile is drawn they are cut to its rows (plus a margin)
  let tileSpan = null;
  function segment(o, k, x1, y1, x2, y2) {
    let t0 = 0, t1 = 1;
    if (tileSpan && y2 !== y1) {
      const ta = (tileSpan[0] - y1) / (y2 - y1), tb = (tileSpan[1] - y1) / (y2 - y1);
      t0 = Math.max(0, Math.min(ta, tb));
      t1 = Math.min(1, Math.max(ta, tb));
      if (t1 <= t0) return;
    }
    add(o, k, `M${r1d(x1 + (x2 - x1) * t0)} ${r1d(y1 + (y2 - y1) * t0)}L${r1d(x1 + (x2 - x1) * t1)} ${r1d(y1 + (y2 - y1) * t1)}`);
  }
  function dashPath(o, lk, xs, ys, xr, yr) {
    const dx = xr - xs, dy = yr - ys, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    segment(o, lk, xs + ux * 7, ys + uy * 7, xr - ux * 7, yr - uy * 7);
  }
  function combPath(o, lk, hk, e, recs, y0) {
    const g = GEO, xw = g.life[L.WC], ye = rowOf[e] * ROW + ROW / 2 - y0;
    const byRow = new Map();
    for (const j of recs) {
      const r = rowOf[j];
      (byRow.get(r) || byRow.set(r, []).get(r)).push(g.life[colOfRec(j)]);
    }
    const rows = [...byRow.keys()].sort((a, b) => a - b);
    if (!rows.length) return;
    const lastTop = rows[rows.length - 1] * ROW - y0;
    add(o, lk, `M${xw + .5} ${r1d(ye + 6.5)}V${lastTop - 3}`);
    for (const r of rows) {
      const yt = r * ROW - y0, xs = byRow.get(r);
      const right = xs.filter(x => x > xw), left = xs.filter(x => x < xw);
      for (const side of [right, left]) {
        if (!side.length) continue;
        const sg = side === right ? 1 : -1, far = sg > 0 ? Math.max(...side) : Math.min(...side);
        add(o, lk, `M${xw + .5} ${yt - 3}Q${xw + .5} ${yt} ${xw + .5 + 3 * sg} ${yt}H${far - 3 * sg}`);
        for (const x of side) {
          add(o, lk, `M${x - 3 * sg} ${yt}Q${x} ${yt} ${x} ${yt + 2.5}`);
          head(o, hk, x, yt + 6, 0, 1, 4.5, 2.4);
        }
      }
    }
  }
  const msgPath = (o, lk, hk, s, rv, y0) => {
    const xs = GEO.life[colOfRec(s)], xr = GEO.life[colOfRec(rv)], ys = rowOf[s] * ROW + ROW / 2 - y0, yr = rowOf[rv] * ROW + ROW / 2 - y0;
    const pts = msgRoute(s, colOfRec(rv), rowOf[rv], 11.5);
    if (!pts) { arrowPath(o, lk, hk, xs, ys, xr, yr); return; }
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
  function cxGeom(o, id, y0, mode) {
    const g = GEO, kind = L.cx.kind[id], a = L.cx.a[id], b = L.cx.b[id], [lk, hk] = cxKeys(kind, mode);
    const xa = g.life[colOfRec(a)], ya = rowOf[a] * ROW + ROW / 2 - y0;
    if (kind === K_MSG) msgPath(o, lk, hk, a, b, y0);
    else if (kind === K_SELF || kind === K_ARC) selfPath(o, lk, hk, xa, ya, rowOf[b] * ROW + ROW / 2 - y0);
    else if (kind === K_COMB) combPath(o, lk, hk, a, L.combs.get(a).recs, y0);
    else if (kind === K_LOST) {
      const cg = L.col[recvAgentOf[a]], pts = msgRoute(a, cg, ghostRow[a], 7);
      if (pts) routeD(o, lk, pts, y0);
      else dashPath(o, lk, xa, ya, g.life[cg], ghostRow[a] * ROW + ROW / 2 - y0);
    } else if (kind === K_STUB) {
      const rl = recvAgentOf[a], rc = rl >= 0 ? L.col[rl] : -1;
      // a stub toward a lane on the left leaves the glyph steeply; otherwise it leaves after the label, like a message arrow
      const left = rc >= 0 && rc !== colOfRec(a) && g.life[rc] < xa;
      const len = left ? Math.hypot(0.55, 1) : Math.hypot(1, 0.36), ux = left ? -0.55 / len : 1 / len, uy = left ? 1 / len : 0.36 / len;
      const x0 = left || !g.labels ? xa + ux * 6.5 : labelEnd(a) + 5, y0s = left || !g.labels ? ya + uy * 6.5 : ya;
      const tx = x0 + ux * 28, ty = y0s + uy * 28, bx = tx - ux * 5, by = ty - uy * 5;
      add(o, lk, `M${r1d(x0)} ${r1d(y0s)}L${r1d(tx)} ${r1d(ty)}`);
      add(o, hk, `M${r1d(bx - uy * 3)} ${r1d(by + ux * 3)}L${r1d(tx)} ${r1d(ty)}L${r1d(bx + uy * 3)} ${r1d(by - ux * 3)}`);
    }
  }
  // the rasterizer walks a whole path for every raster tile it touches, so long merged paths are cut into row chunks
  function chunked(proto, list, rowOfItem, fn) {
    const parts = new Map();
    for (const item of list) {
      const c = Math.floor(rowOfItem(item) / 8);
      let p = parts.get(c);
      if (!p) { p = { ...proto }; parts.set(c, p); }
      fn(p, item);
    }
    return [...parts.values()];
  }
  const pathsOf = (o, attrs) => Object.keys(o).filter(k => o[k]).map(k => `<path class="${k}" d="${o[k]}"${attrs && attrs[k] ? attrs[k] : ""}/>`).join("");
  const pill = (x, y, h, w) => `M${r1d(x)} ${r1d(y + w / 2)}a${w / 2} ${w / 2} 0 0 1 ${w} 0v${r1d(Math.max(0, h - w))}a${w / 2} ${w / 2} 0 0 1 ${-w} 0z`;
  const rectP = (x, y, w, h) => `M${r1d(x)} ${r1d(y)}h${w}v${h}h${-w}z`;
  // label boxes are cut out of the connector layers (clip-rule evenodd), so lines pass behind the text instead of through it
  const KO_TOP = 5, KO_H = 13;
  const knockout = (id, w, h, boxes, dy) => {
    let d = `M-4 -4H${w + 4}V${h + 4}H-4Z`;
    for (let k = 0; k < boxes.length; k += 3) d += `M${r1d(boxes[k])} ${r1d(boxes[k + 1] - dy)}h${r1d(boxes[k + 2])}v${KO_H}h${-r1d(boxes[k + 2])}z`;
    return `<clipPath id="${id}" clipPathUnits="userSpaceOnUse"><path clip-rule="evenodd" d="${d}"/></clipPath>`;
  };

  function bandsSVG(r0, r1, width) {
    let st = "", gp = "", ru = "", run = -1;
    for (let r = r0; r <= r1; r++) {
      const b = r < r1 ? L.bandOfRow[r] : -2, odd = b >= 0 && (b & 1) === 1;
      if (odd && run < 0) run = r;
      if (!odd && run >= 0) { st += rectP(0, (run - r0) * ROW, width, (r - run) * ROW); run = -1; }
      if (r < r1) {
        if (b === -1) gp += rectP(0, (r - r0) * ROW, width, ROW);
        if (r > 0 && (b === -1 || L.bandRow[b] === r || L.bandOfRow[r - 1] === -1)) ru += `M0 ${(r - r0) * ROW + .5}h${width}`;
      }
    }
    return { st, gp, ru };
  }

  function tileRecs(t, r0, r1, boxes, rboxes) {
    const g = GEO, recs = [];
    const lo = mountedCols ? mountedCols[0] : 0, hi = mountedCols ? mountedCols[1] : L.nC - 1;
    const hasSel = selShown();
    for (let r = r0; r < r1; r++) {
      if (L.rowStart[r] === L.rowStart[r + 1]) continue;
      const ents = rowEntries(r), y = (r - r0) * ROW;
      for (let k = 0; k < ents.length; k++) {
        const [v, c] = ents[k];
        if (c < lo || c > hi) continue;
        const lim = labelLim(ents, k, r);
        const w = g.labels ? 28 + lim : 24, left = g.life[c] - 12;
        const html = !g.labels ? "" : v < 0 ? ghostLabel(-1 - v) : labelHTML(v);
        if (g.labels) {
          const tw = Math.min(textLen(html) * CW, lim);
          if (v >= 0) { labelW[v] = tw; labelStamp[v] = labelGen; }
          if (tw > 0) {
            boxes.push(g.life[c] + 14, r * ROW + KO_TOP, tw + 4);
            if (v >= 0 && rel[v]) rboxes.push(g.life[c] + 14, r * ROW + KO_TOP, tw + 4);
          }
        }
        const pos = `top:${y}px;left:${left}px;width:${w}px`;
        if (v < 0) {
          const s = -1 - v;
          recs.push(`<div class="rec g-x${s === state.sel ? " on" : ""}" aria-hidden="true" data-g="${s}" data-row="${r}" style="${pos}">${html}</div>`);
          continue;
        }
        const rv = rel[v];
        const cls = "rec k-" + CATS[kindOf[v]] + " " + glyphClass(v) + (failOf[v] ? " fail" : LV[levelOf[v]] === "warning" ? " warn" : "")
          + (rv === 3 ? " sel" : rv === 1 ? " anc" : rv === 2 ? " desc" : hasSel ? " dim" : "") + (v === hovRec ? " hov" : "");
        recs.push(`<div class="${cls}" data-i="${v}" data-row="${r}" role="button" tabindex="${v === state.cursor ? 0 : -1}" aria-pressed="${rv === 3}"`
          + ` aria-label="${esc(ariaOf(v))}" style="${pos}">${html}</div>`);
      }
    }
    return recs.join("");
  }

  function tileHTML(t) {
    const g = GEO, r0 = t * TR, r1 = Math.min(L.maxRow, r0 + TR), H = (r1 - r0) * ROW, y0 = r0 * ROW, Wd = g.width;
    const boxes = [], rboxes = [], recs = tileRecs(t, r0, r1, boxes, rboxes);
    const kid = "ko" + t, clip = boxes.length ? ` clip-path="url(#${kid})"` : "";
    // the selection's thread passes over faded labels; only the labels of its own records cut it
    const rclip = rboxes.length ? ` clip-path="url(#kr${t})"` : "";
    const bg = bandsSVG(r0, r1, Wd);
    let sep = "", ll = "";
    for (let c = 1; c < L.nC; c++) sep += `M${g.x[c] - .5} 0V${H}`;
    if (g.contentW < Wd) sep += `M${g.contentW - .5} 0V${H}`;
    for (let c = 0; c < L.nC; c++) ll += `M${g.life[c] + .5} 0V${H}`;
    const out = [`<svg class="bg" width="${Wd}" height="${H}" aria-hidden="true">${boxes.length ? knockout(kid, Wd, H, boxes, y0) : ""}`
      + (rboxes.length ? knockout("kr" + t, Wd, H, rboxes, y0) : "")
      + `<path class="st" d="${bg.st}"/><path class="gp" d="${bg.gp}"/><path class="ru" d="${bg.ru}"/>`
      + `<path class="ru" d="${sep}"${clip}/><path class="ll" d="${ll}"${clip}/></svg>`];

    const o = { run: "", fade: "", idle: "", cf: "", cc: "" };
    const showIdle = state.cats.has("wait");
    // bars are cut to the tile (plus a margin that hides the cut ends): a dotted idle line is rastered along its whole length
    const lo = y0 - 8, hi = y0 + H + 8;
    for (const id of L.tileBars[t]) {
      const bar = L.bars[id], x = g.life[bar.c] - 6 * bar.t + .5;
      const end = bar.open ? Math.max(bar.y0, bar.y1 - ROW) : bar.y1;
      let y = bar.y0;
      for (let k = 0; k <= bar.idle.length; k += 2) {
        const last = k >= bar.idle.length;
        const a = last ? end : Math.min(bar.idle[k], end), b = last ? end : Math.min(bar.idle[k + 1], end);
        const ra = Math.max(y, lo), rb = Math.min(a, hi);
        if (rb > ra) o.run += pill(x - 2.5, ra - y0, rb - ra, 5);
        if (!last && showIdle && b > a && b > lo && a < hi) {
          const ia = a < lo ? a + Math.ceil((lo - a) / 3.4) * 3.4 : a, ib = Math.min(b, hi);
          if (ib > ia) o.idle += `M${x} ${r1d(ia - y0)}V${r1d(ib - y0)}`;
        }
        if (b > y) y = b;
        if (y > hi) break;
      }
      if (bar.open) o.fade += rectP(x - 2.5, end - y0, 5, bar.y1 - end);
      if (bar.end === E_FAIL) o.cf += rectP(x - 3.5, bar.y1 - y0 - 1, 7, 2);
      else if (bar.end === E_CANCEL) o.cc += rectP(x - 3.5, bar.y1 - y0 - 1, 7, 2);
    }
    tileSpan = [-8, H + 8];
    const cxParts = chunked({}, L.tileCx[t], id => L.cx.lo[id], (p, id) => cxGeom(p, id, y0, ""));
    out.push(`<svg class="cx" width="${Wd}" height="${H}" aria-hidden="true"><g${clip}>${pathsOf(o)}${cxParts.map(p => pathsOf(p)).join("")}</g></svg>`);

    if (THREAD && THREAD.tile[t] && THREAD.tile[t].length) {
      const proto = { desc: "", descd: "", dh: "", anc: "", ancd: "", ah: "", own: "", oh: "", ownl: "", owns: "", ownsh: "" };
      const hParts = chunked(proto, THREAD.tile[t], pid => THREAD.pieces[pid].lo, (p, pid) => threadGeom(p, THREAD.pieces[pid], y0));
      const draw = THREAD.fresh ? " draw" : "";
      const len = { anc: ' pathLength="1" style="--len:1"', desc: ' pathLength="1" style="--len:1"' };
      const wr = THREAD.waitRing;
      const extra = wr && wr.t === t ? `<circle class="wr" cx="${wr.x}" cy="${wr.y - y0}" r="5"/>` : "";
      const paths = hParts.map(p => pathsOf(p, len)).join("");
      out.push(`<svg class="hl${draw}" width="${Wd}" height="${H}" aria-hidden="true"><g${rclip}>${paths}</g>${extra}</svg>`);
    }

    tileSpan = null;
    out.push(recs);
    if (THREAD && THREAD.pills.length) {
      let p = "";
      for (const pl of THREAD.pills) {
        if (pl.y < y0 - 10 || pl.y >= y0 + H + 10) continue;
        const w = String(pl.n).length > 1 ? 24 : 17, x = r1d(pl.x), y = r1d(pl.y - y0);
        p += `<g class="hp"><rect x="${r1d(x - w / 2)}" y="${r1d(y - 8.5)}" width="${w}" height="17" rx="8.5"/>`
          + `<text x="${x}" y="${r1d(y + 3.8)}" text-anchor="middle">${pl.n}</text></g>`;
      }
      if (p) out.push(`<svg class="pl" width="${Wd}" height="${H}" aria-hidden="true">${p}</svg>`);
    }
    return { html: out.join(""), boxes };
  }

  // gaps are measured between the rows on screen, so with filters or unfollowed lanes they only mean "nothing shown here"
  const gapsComplete = () => !filtersActive && presentCats.every(c => state.cats.has(c)) && L.nC >= A + (hasWorldRecs ? 1 : 0);
  function gtileHTML(t) {
    const g = GEO, r0 = t * TR, r1 = Math.min(L.maxRow, r0 + TR), H = (r1 - r0) * ROW, uiFont = fontVar("--font-ui");
    const bg = bandsSVG(r0, r1, g.gw), complete = gapsComplete();
    let h = `<svg class="bg" width="${g.gw}" height="${H}" aria-hidden="true">`
      + `<path class="st" d="${bg.st}"/><path class="gp" d="${bg.gp}"/><path class="ru" d="${bg.ru}"/></svg>`;
    for (let r = r0; r < r1; r++) {
      const b = L.bandOfRow[r], top = (r - r0) * ROW;
      if (b >= 0 && L.bandRow[b] === r) {
        const lab = b === 0 && g.first ? g.first : timeHTML(ktime(L.bandKey[b]), b ? ktime(L.bandKey[b - 1]) : null, g.elide);
        h += `<div class="bl" data-b="${b}" style="top:${top}px">${lab}</div>`;
      } else if (b === -1 && L.gapAt.has(r)) {
        const d = L.gapAt.get(r), full = dur(d), title = `${full.slice(1)} without ${complete ? "records" : "matching records in these lanes"}`;
        const label = textW(full, "500 10.5px " + uiFont) + 10 > g.gw - 14 ? dur(d, true) : full;
        h += `<div class="gl${complete ? "" : " part"}" style="top:${top}px" title="${esc(title)}">${esc(label)}</div>`;
      }
    }
    return h;
  }

  function renderHead() {
    const g = GEO;
    lhead.style.height = g.headH + "px";
    const clock = CLOCK === "sim" ? "Sim time" : "Wall time";
    let h = `<button class="corner${UNIT < 1 ? " fine" : ""}" id="corner" style="width:${g.gw}px" title="Go to a time (t)">`
      + `<span class="ck"><span class="lg">${clock}</span><span class="sh">Time</span></span>`
      + `<span class="ct" id="corner-t"></span><span class="cd" id="corner-d"></span></button>`;
    L.lanes.forEach((l, c) => {
      const tot = laneTotal[l], v = L.laneVis[l], f = laneFailed[l];
      const meta = (v === tot ? (f && g.W < 190 ? nf(tot) : plural(tot, "record")) : `${nf(v)} of ${nf(tot)}`) + (f ? ` · <b>${nf(f)} failed</b>` : "");
      const cls = "lh" + (l === WORLD ? " world" : "") + (g.labels ? "" : " vert") + ((state.fit || g.W < 148) && g.labels && l !== WORLD ? " fitw" : "");
      const title = esc(laneName(l)) + (l === WORLD ? ": records without an agent" : "");
      h += `<div class="${cls}" data-l="${l}" style="width:${g.w[c]}px;--ll:${g.life[c] - g.x[c]}px" title="${title}">`
        + `<span class="nm">${esc(laneName(l))}</span>`
        + (l === WORLD ? "" : `<span class="mt">${meta}</span>`
          + `<button class="x" data-x="${l}" aria-label="Stop following ${esc(laneName(l))}" title="Stop following (x)">✕</button>`)
        + `</div>`;
    });
    lhead.innerHTML = h;
    lanesBox.setAttribute("aria-label", "Agent lanes: " + L.lanes.map(laneName).join(", "));
  }

  function sizeLanes() {
    const g = GEO, H = L.maxRow * ROW;
    lanesIn.style.width = g.gw + g.width + "px";
    gutterEl.style.width = g.gw + "px";
    gutterEl.style.height = H + "px";
    plane.style.width = g.width + "px";
    plane.style.height = H + "px";
    lanesBox.style.setProperty("--gw", g.gw + "px");
    lanesBox.classList.toggle("nolabel", !g.labels);
    lanesBox.classList.toggle("has-sel", selShown());
    pinEl.style.top = g.headH + "px";
  }
  const pinEl = document.createElement("div");
  pinEl.className = "pin";
  pinEl.setAttribute("aria-hidden", "true");
  const xHov = document.createElement("div"), xSel = document.createElement("div"), gHov = document.createElement("div"), gSel = document.createElement("div");
  xHov.className = "xrow hov";
  xSel.className = "xrow sel";
  gHov.className = "gxrow hov";
  gSel.className = "gxrow sel";
  const ovl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  ovl.setAttribute("class", "ov");
  ovl.setAttribute("aria-hidden", "true");
  const fpill = document.createElement("div");
  fpill.className = "fpill";
  for (const el of [xHov, xSel, gHov, gSel, fpill]) el.hidden = true;
  plane.append(xHov, xSel, ovl, fpill);
  gutterEl.append(pinEl, gHov, gSel);

  function removeTiles() {
    for (const m of mounted.values()) { m.el.remove(); m.gel.remove(); }
    mounted.clear();
  }
  function renderLanes(rebuild) {
    if (!L || !GEO) return;
    const g = GEO;
    const ae = document.activeElement, hadFocus = !!ae && plane.contains(ae);
    if (rebuild) { removeTiles(); labelGen++; }
    if (L.nC > 24 && !state.fit) {
      const x0 = lanesBox.scrollLeft, x1 = x0 + lanesBox.clientWidth - g.gw;
      let f = 0;
      while (f < L.nC - 1 && g.x[f] + g.w[f] < x0) f++;
      let l = f;
      while (l < L.nC - 1 && g.x[l + 1] < x1) l++;
      const leftOut = f - 2 < mountedCols?.[0] && mountedCols[0] > 0, rightOut = l + 2 > mountedCols?.[1] && mountedCols[1] < L.nC - 1;
      if (!mountedCols || leftOut || rightOut || rebuild) {
        const nc = [Math.max(0, f - 4), Math.min(L.nC - 1, l + 4)];
        if (!mountedCols || nc[0] !== mountedCols[0] || nc[1] !== mountedCols[1]) { mountedCols = nc; removeTiles(); }
      }
    } else mountedCols = null;
    const top = lanesBox.scrollTop, vh = Math.max(0, lanesBox.clientHeight - g.headH);
    const a = Math.max(0, Math.floor((top - 704) / TILE)), b = Math.min(L.nTiles - 1, Math.floor((top + vh + 704) / TILE));
    for (const [t, m] of mounted) if (t < a || t > b) { m.el.remove(); m.gel.remove(); mounted.delete(t); }
    for (let t = a; t <= b; t++) {
      if (mounted.has(t)) continue;
      const el = document.createElement("div"), gel = document.createElement("div");
      const h = (Math.min(L.maxRow, (t + 1) * TR) - t * TR) * ROW;
      el.className = "tile";
      el.style.cssText = `top:${t * TILE}px;height:${h}px;width:${g.width}px`;
      gel.className = "gtile";
      gel.style.cssText = `top:${t * TILE}px;height:${h}px;width:${g.gw}px`;
      const tile = tileHTML(t);
      el.innerHTML = tile.html;
      gel.innerHTML = gtileHTML(t);
      plane.append(el);
      gutterEl.append(gel);
      mounted.set(t, { el, gel, boxes: tile.boxes });
    }
    if (THREAD) THREAD.fresh = false;
    if (hadFocus && !plane.contains(document.activeElement)) {
      const el = state.cursor != null ? plane.querySelector(`.rec[data-i="${state.cursor}"]`) : null;
      if (el) { el.tabIndex = 0; el.focus({ preventScroll: true }); } else lanesBox.focus({ preventScroll: true });
    }
    updatePinned();
    liveChanged();
  }

  function bandAtRow(r) {
    if (!L.maxRow) return -1;
    r = clamp(r, 0, L.maxRow - 1);
    let b = L.bandOfRow[r];
    for (let k = r; b < 0 && k < L.maxRow; k++) b = L.bandOfRow[k];
    for (let k = r; b < 0 && k >= 0; k--) b = L.bandOfRow[k];
    return b;
  }
  function updatePinned() {
    const ct = $("corner-t"), cd = $("corner-d");
    const b = L && L.maxRow ? bandAtRow(Math.floor(lanesBox.scrollTop / ROW)) : -1;
    if (b < 0) {
      pinEl.innerHTML = "";
      pinEl.style.visibility = "hidden";
      if (ct) { ct.textContent = ""; cd.textContent = ""; }
      return;
    }
    const t = ktime(L.bandKey[b]);
    if (ct) {
      ct.textContent = timeLabel(t);
      cd.textContent = t - T0 > 0 ? dur(t - T0, true) : "start";
      cd.title = t - T0 > 0 ? dur(t - T0).slice(1) + " after the first record" : "";
    }
    const show = L.bandRow[b] * ROW < lanesBox.scrollTop - 2;
    pinEl.innerHTML = show ? `<div class="bl">${lonelyLabel(t)}</div>` : "";
    pinEl.style.visibility = show ? "visible" : "hidden";
    let next = L.bandRow[b] + 1;
    while (next < L.maxRow && L.bandOfRow[next] === b) next++;
    const push = Math.min(0, next * ROW - lanesBox.scrollTop - ROW);
    pinEl.style.transform = push ? `translateY(${push}px)` : "";
  }

  function rowsOnScreen() {
    if (state.view === "lanes" && L && GEO) {
      const a = Math.floor((lanesBox.scrollTop + 2) / ROW), b = Math.min(L.maxRow, Math.ceil((lanesBox.scrollTop + lanesBox.clientHeight - GEO.headH - 2) / ROW));
      const out = [];
      for (let r = Math.max(0, a); r < b; r++) for (let j = L.rowStart[r]; j < L.rowStart[r + 1]; j++) if (L.rowRecs[j] >= 0) out.push(L.rowRecs[j]);
      return out;
    }
    const [a, b] = tableRowsOnScreen();
    return Array.from(visList.subarray(a, b));
  }

  const P_LIFE = 1, P_MSG = 2, P_SELF = 3, P_COMB = 4, P_DASH = 5, P_OWN = 6;
  function threadPiece(p, c, cls) {
    const cp = colOfRec(p), cc = colOfRec(c);
    let k;
    if (evOf[p] === E_SENT && causeIx[c] === p && evOf[c] === E_RECV) k = cp === cc ? P_SELF : P_MSG;
    else if (evOf[p] === E_EMIT && causeIx[c] === p && cp === L.WC && cp !== cc) k = P_COMB;
    else if (cp === cc) k = P_LIFE;
    else k = P_DASH;
    return { k, p, c, cls, lo: Math.min(rowOf[p], rowOf[c]), hi: Math.max(rowOf[p], rowOf[c]) };
  }
  function computeThread(fresh) {
    THREAD = null;
    const s = state.sel;
    if (s == null || !L) return;
    const pieces = [], pills = [];
    const path = pathOfSel();
    const placed = path.filter(i => rowOf[i] >= 0);
    const pos = new Map(path.map((i, k) => [i, k]));
    for (let k = 1; k < placed.length; k++) {
      const pc = threadPiece(placed[k - 1], placed[k], "anc");
      pieces.push(pc);
      if (colOfRec(pc.p) !== colOfRec(pc.c)) {
        const a = pos.get(pc.p), b = pos.get(pc.c);
        const hop = hops.find(h => pos.get(h.rec) > a && pos.get(h.rec) <= b);
        if (hop) pc.hop = hop.n;
      }
    }
    // overlapping thread segments are merged: thousands of stacked strokes made every repaint slow
    const lastOfId = new Map(), life = new Map(), combs = new Map();
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
        if (!cm) { cm = { ...pc, recs: [] }; combs.set(a, cm); pieces.push(cm); }
        cm.recs.push(d);
        cm.hi = Math.max(cm.hi, rowOf[d]);
      } else pieces.push(pc);
    }
    for (const list of life.values()) {
      list.sort((x, y) => x[0] - y[0]);
      let cur = null;
      for (const iv of list) {
        if (cur && iv[0] <= cur.hi) {
          if (iv[1] > cur.hi) { cur.hi = iv[1]; cur.c = iv[3]; }
          continue;
        }
        cur = { k: P_LIFE, p: iv[2], c: iv[3], cls: "desc", lo: iv[0], hi: iv[1] };
        pieces.push(cur);
      }
    }
    const own = L.undelivered.get(s);
    if (own !== undefined) pieces.push({ k: P_OWN, cx: own, p: s, c: s, cls: "own", lo: L.cx.lo[own], hi: L.cx.hi[own] + 2 });
    const tile = Array.from({ length: L.nTiles }, () => []);
    pieces.forEach((pc, id) => {
      for (let t = Math.floor(pc.lo / TR), te = Math.min(L.nTiles - 1, Math.floor(pc.hi / TR)); t <= te; t++) tile[t].push(id);
    });
    THREAD = { pieces, tile, pills, fresh };
    let waitRing = null;
    if (rowOf[s] < 0 && vrowOf[s] >= 0 && L.col[laneOf[s]] >= 0 && vis[s]) {
      const y = vrowOf[s] * ROW;
      waitRing = { t: Math.floor(vrowOf[s] / TR), y, c: L.col[laneOf[s]] };
    }
    THREAD.waitRing = waitRing;
    placePills();
  }
  function piecePoints(pc) {
    const g = GEO, xa = g.life[colOfRec(pc.p)], ya = rowOf[pc.p] * ROW + ROW / 2, xb = g.life[colOfRec(pc.c)], yb = rowOf[pc.c] * ROW + ROW / 2;
    if (pc.k === P_COMB) return [[xa, rowOf[pc.c] * ROW], [xb, rowOf[pc.c] * ROW]];
    if (pc.k === P_MSG) return msgRoute(pc.p, colOfRec(pc.c), rowOf[pc.c], 11.5) || [[xa, ya], [xb, yb]];
    return [[xa, ya], [xb, yb]];
  }
  function pillSpot(pts, w, cache) {
    const lens = [];
    let total = 0;
    for (let k = 1; k < pts.length; k++) { const l = Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]); lens.push(l); total += l; }
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
    let best = at(total / 2), bestScore = Infinity;
    for (let d = margin, step = Math.max(3, total / 300); d <= total - margin; d += step) {
      const [x, y] = at(d), x0 = x - w / 2 - 1, x1 = x + w / 2 + 1, y0 = y - 9.5, y1 = y + 9.5;
      let cover = 0;
      for (let r = Math.floor(y0 / ROW); r <= Math.floor(y1 / ROW); r++) {
        for (const b of rowBoxes(r, cache)) {
          const ox = Math.min(x1, b[2]) - Math.max(x0, b[0]), oy = Math.min(y1, b[3]) - Math.max(y0, b[1]);
          if (ox > 0 && oy > 0) cover += ox * oy;
        }
      }
      const score = cover * 100 + Math.abs(d - total / 2);
      if (score < bestScore) { bestScore = score; best = [x, y]; }
    }
    return best;
  }
  function placePills() {
    if (!THREAD || !GEO) return;
    const cache = new Map();
    THREAD.pills = [];
    for (const pc of THREAD.pieces) {
      if (!pc.hop) continue;
      const [x, y] = pillSpot(piecePoints(pc), String(pc.hop).length > 1 ? 24 : 17, cache);
      THREAD.pills.push({ n: pc.hop, x, y });
    }
    if (THREAD.waitRing) THREAD.waitRing.x = GEO.life[THREAD.waitRing.c] + .5;
  }
  function threadGeom(o, pc, y0) {
    const g = GEO;
    const lk = pc.cls, hk = pc.cls === "anc" ? "ah" : "dh";
    if (pc.k === P_OWN) { cxGeom(o, pc.cx, y0, "own"); return; }
    const xa = g.life[colOfRec(pc.p)], ya = rowOf[pc.p] * ROW + ROW / 2 - y0, xb = g.life[colOfRec(pc.c)], yb = rowOf[pc.c] * ROW + ROW / 2 - y0;
    if (pc.k === P_LIFE) { if (yb - ya > 13) segment(o, lk, xa + .5, ya + 6.5, xa + .5, yb - 6.5); }
    else if (pc.k === P_MSG) msgPath(o, lk, hk, pc.p, pc.c, y0);
    else if (pc.k === P_SELF) selfPath(o, lk, hk, xa, ya, yb);
    else if (pc.k === P_COMB) combPath(o, lk, hk, pc.p, pc.recs || [pc.c], y0);
    else dashPath(o, lk + "d", xa, ya, xb, yb);
  }
