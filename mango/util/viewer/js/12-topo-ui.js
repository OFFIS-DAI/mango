  const mapSets = { fol: new Set(), chain: new Set(), dag: new Set(), sel: -1, live: new Set(), hops: new Set() };
  const nodeOfLane = l => l >= 0 && l < A ? l : -1;
  let mapFocusN = -1, labelsKey = "";
  function mapState(forceLabels) {
    if (!TD) return;
    const s = state.sel;
    const fol = new Set();
    if (state.view === "lanes") { for (const l of state.lanes || []) if (l < A) fol.add(l); }
    else if (state.agent !== "" && aIx.has(state.agent)) fol.add(aIx.get(state.agent));
    const chainSet = new Set(), dag = new Set();
    for (const c of chain) { const n = nodeOfLane(laneOf[c]); if (n >= 0) chainSet.add(n); }
    for (const d of descList) { const n = nodeOfLane(laneOf[d]); if (n >= 0) dag.add(n); }
    const selN = s != null ? nodeOfLane(laneOf[s]) : -1;
    mapSets.fol = fol; mapSets.chain = chainSet; mapSets.dag = dag; mapSets.sel = selN;
    mapSets.hops = new Set(hops.filter(h => h.edge >= 0).map(h => h.edge));
    const key = [selN, [...fol], [...chainSet], [...dag], hops.map(h => h.edge + ">" + h.to)].join("|");
    const labelsDirty = forceLabels || key !== labelsKey;
    labelsKey = key;
    // with every agent followed the mark would be on every node and say nothing
    const folShown = fol.size < A ? fol : new Set();
    topoSvg.classList.toggle("has-sel", s != null);
    topoSvg.classList.toggle("has-fol", folShown.size > 0);
    topoSvg.classList.toggle("lossy", state.topo.lossy);
    const ghosting = filtersActive;
    topoSvg.classList.toggle("ghosting", ghosting);
    TD.nodeEls.forEach((el, i) => {
      if (!el) return;
      el.classList.toggle("fol", folShown.has(i));
      el.classList.toggle("sel", i === selN);
      el.classList.toggle("anc", i !== selN && chainSet.has(i));
      el.classList.toggle("dag", i !== selN && !chainSet.has(i) && dag.has(i));
      el.setAttribute("aria-pressed", fol.has(i));
      const bd = el.querySelector(".bdg");
      if (bd) {
        const out = badgeShift(i, i === selN || chainSet.has(i) || dag.has(i));
        bd.setAttribute("transform", out ? `translate(${r1d(out.x)},${r1d(out.y)})` : "");
      }
    });
    TD.linkEls.forEach((el, e) => {
      if (!el) return;
      el.classList.toggle("ghost", ghosting && !linkMatch[e] && !mapSets.hops.has(e));
      el.setAttribute("aria-pressed", !!state.link && (state.link.e === e || (state.link.both && reverseEdge(state.link.e) === e)));
    });
    if (BULK) drawBulkLayers();
    drawHot();
    if (labelsDirty) placeLabels();
    rovingNode();
    renderTopoMeta();
    renderRoute();
    if (state.topo.mode === "list") syncTopoList();
  }
  // one node is in the Tab order (the last focused, else the selection's agent, else the hub); arrows move from there
  function rovingNode() {
    if (!TD) return;
    const ok = n => n >= 0 && !!TD.nodeEls[n];
    const want = ok(mapFocusN) ? mapFocusN : ok(mapSets.sel) ? mapSets.sel : TL.hubList[0] ?? TL.linked[0] ?? 0;
    TD.nodeEls.forEach((el, i) => el && el.setAttribute("tabindex", i === want ? "0" : "-1"));
  }

  function bulkD(set) {
    let d = "";
    for (const e of set) d += TD.geo[e].straight;
    return d;
  }
  function drawBulkLayers() {
    const m = topoSvg.querySelector(".bulk-match"), lv = topoSvg.querySelector(".bulk-live");
    if (!m) return;
    m.innerHTML = filtersActive ? `<path class="lk-b on" d="${bulkD(EDGES.filter(e => linkMatch[e.k] > 0).map(e => e.k))}"/>` : "";
    lv.innerHTML = (mapSets.live.size ? `<path class="lk-b live" d="${bulkD(mapSets.live)}"/>` : "")
      + (state.topo.lossy ? `<path class="lk-b lossyl" d="${bulkD(EDGES.filter(e => e.lossy).map(e => e.k))}"/>` : "");
  }

  function drawHot() {
    const hot = $("topo-hot"), pills = $("topo-pills");
    if (!hot || !TD) return;
    const s = state.sel;
    const hopEdges = new Set(hops.map(h => h.edge).filter(e => e >= 0));
    syncCountPills(hopEdges);
    // the filtered connection is what the user is looking at: full strength, on a soft casing, whatever is selected
    const flt = [];
    if (state.link) {
      for (const e of [state.link.e, state.link.both ? reverseEdge(state.link.e) : -1]) {
        if (e < 0) continue;
        const w = TD.width(EDGES[e]);
        const cd = TD.cutD(e);
        flt.push(`<path class="fltc" d="${TD.geo[e].d}" stroke-width="${(w + 6).toFixed(2)}"/>`
          + `<path class="flt" d="${TD.geo[e].d}" stroke-width="${(w + 1).toFixed(2)}"/>`
          + (cd ? `<path class="cuth" d="${cd}"/><path class="cut" d="${cd}"/>` : ""));
      }
    }
    if (s == null) { hot.innerHTML = flt.join(""); pills.innerHTML = ""; return; }
    const { P, R } = TD.F;
    const out = flt, pl = [];
    const desc = new Set();
    for (const d of descList) {
      const e = edgeOfRec[d];
      if (e >= 0 && !hopEdges.has(e)) desc.add(e);
    }
    for (const e of desc) out.push(`<path class="desc" d="${TD.geo[e].d}" stroke-width="${(TD.width(EDGES[e]) + 0.6).toFixed(2)}"/>`);
    for (const h of hops) {
      let mid;
      if (h.edge >= 0) {
        const g = TD.geo[h.edge];
        out.push(`<path class="anc" d="${g.d}"/>`);
        mid = g.mid;
      } else {
        const a = P[h.from], b = P[h.to];
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1;
        const x0 = a.x + dx / l * (R[h.from] + 3), y0 = a.y + dy / l * (R[h.from] + 3), x1 = b.x - dx / l * (R[h.to] + 3), y1 = b.y - dy / l * (R[h.to] + 3);
        out.push(`<path class="anc ev" d="M${r1d(x0)},${r1d(y0)} L${r1d(x1)},${r1d(y1)}"/>`);
        mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      }
      const w = h.n > 9 ? 24 : 17;
      const lab = `Step ${h.n}: ${esc(laneName(h.from))} to ${esc(laneName(h.to))}, select it`;
      pl.push(`<g class="tp anc" data-hop="${h.n}" role="button" tabindex="-1" aria-label="${lab}"`
        + ` transform="translate(${r1d(mid.x)},${r1d(mid.y)})"><rect x="${-w / 2}" y="-8.5" width="${w}" height="17" rx="8.5"/>`
        + `<text text-anchor="middle" dy="3.7">${h.n}</text></g>`);
    }
    const own = (evOf[s] === E_SENT || evOf[s] === E_RECV) ? edgeOfRec[s] : -1;
    if (own >= 0) {
      const g = TD.geo[own], end = evOf[s] === E_SENT ? g.at(0) : g.at(1);
      out.push(`<path class="own" d="${g.d}"/><circle class="odot" cx="${r1d(end.x)}" cy="${r1d(end.y)}" r="3"/>`);
    }
    hot.innerHTML = out.join("");
    pills.innerHTML = pl.join("");
  }

  // a numbered hop pill takes the place of the count pill on its connection (and on the twin curve beside it)
  function syncCountPills(hopEdges) {
    $("topo-cnt")?.querySelectorAll(".tp.cnt").forEach(g => {
      const e = +g.dataset.e;
      g.classList.toggle("off", hopEdges.has(e) || hopEdges.has(reverseEdge(e)));
    });
  }

  let liveRaf = 0;
  function liveChanged() {
    if (liveRaf || !TD) return;
    liveRaf = requestAnimationFrame(() => {
      liveRaf = 0;
      if (!TD) return;
      const next = new Set();
      for (const i of rowsOnScreen()) if (edgeOfRec[i] >= 0) next.add(edgeOfRec[i]);
      if (BULK) { mapSets.live = next; drawBulkLayers(); return; }
      for (const e of mapSets.live) if (!next.has(e)) TD.linkEls[e]?.classList.remove("live");
      for (const e of next) if (!mapSets.live.has(e)) TD.linkEls[e]?.classList.add("live");
      mapSets.live = next;
    });
  }

  const hoverRule = document.createElement("style");
  hoverRule.id = "hover-rule";
  document.head.append(hoverRule);
  let mapHov = null, peeked = [];
  const nodeTip = n => {
    const d = NODES[n], p = d.partners.size - (d.partners.has(n) ? 1 : 0);
    return `<div class="t1">${esc(d.id)}</div><div class="t2">${d.lane >= 0 ? plural(d.records, "record") : "no records in this trace"}`
      + `${d.failed ? ` · <span class="lo">${nf(d.failed)} failed</span>` : ""}`
      + ` · sent ${nf(d.sent)} · received ${nf(d.received)} · ${plural(p, "partner")}</div>`;
  };
  const edgeRange = E => {
    const f = typeof E.first === "number" ? E.first : null, l = typeof E.last === "number" ? E.last : null;
    if (f == null || CLOCK !== "sim") return "";
    return f === l ? timeLabel(f) : `${timeLabel(f)}–${timeLabel(l)}`;
  };
  const edgeTip = e => {
    const E = EDGES[e], lost = E.lost - E.inFlight;
    const types = [...E.types].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([t, c]) => `${esc(t ?? "?")} ${nf(c)}`).join(" · ");
    return `<div class="t1">${esc(E.from)} → ${esc(E.to)}</div><div class="t2">${nf(E.sent)} sent · ${nf(E.received)} received`
      + `${lost ? ` · <span class="lo">${nf(lost)} lost</span>` : ""}${E.inFlight ? ` · ${nf(E.inFlight)} in flight` : ""}</div>`
      + `<div class="t2">${types}${edgeRange(E) ? " · " + edgeRange(E) : ""}</div>`;
  };
  const rectOf = el => el.getBoundingClientRect ? el.getBoundingClientRect() : el;
  function setMapHover(h, anchorEl, quiet) {
    const key = h ? (h.n != null ? "n" + h.n : "e" + h.e) : "";
    if ((mapHov ? mapHov.key : "") === key) return;
    if (TD) {
      topoSvg.querySelectorAll(".hov, .near, .split").forEach(el => el.classList.remove("hov", "near", "split"));
      $("topo-hp")?.remove();
      $("topo-hl")?.remove();
    }
    lhead.querySelectorAll(".lh.peek").forEach(el => el.classList.remove("peek"));
    hoverRule.textContent = "";
    mapHov = h ? { ...h, key } : null;
    if (!h) {
      topoSvg.classList.remove("hovering");
      if (!quiet) hideTip();
      if (state.view === "lanes" && hovRec < 0) drawOverlay([]);
      return;
    }
    if (TD) topoSvg.classList.add("hovering");
    if (h.n != null) {
      const d = NODES[h.n];
      if (TD) {
        TD.nodeEls[h.n]?.classList.add("hov");
        for (const e of d.edges) {
          TD.linkEls[e]?.classList.add("hov");
          TD.nodeEls[EDGES[e].a === h.n ? EDGES[e].b : EDGES[e].a]?.classList.add("near");
        }
        if (BULK) $("topo-hot").insertAdjacentHTML("beforeend", `<path id="topo-hl" class="hovl" d="${bulkD(d.edges)}"/>`);
      }
      if (d.lane >= 0) {
        hoverRule.textContent = `#rows tr[data-a="${d.lane}"] td.agent { color: var(--accent); font-weight: 600; }`;
        lhead.querySelector(`.lh[data-l="${d.lane}"]`)?.classList.add("peek");
      }
      if (!quiet && anchorEl) showTip(nodeTip(h.n), rectOf(anchorEl), true);
      return;
    }
    const E = EDGES[h.e];
    if (TD) {
      const el = TD.linkEls[h.e];
      el?.classList.add("hov");
      if (el && (el.classList.contains("both") || el.classList.contains("twin"))) {
        el.classList.add("split");
        TD.linkEls[reverseEdge(h.e)]?.classList.add("split");
      }
      TD.nodeEls[E.a]?.classList.add("near");
      TD.nodeEls[E.b]?.classList.add("near");
      if (BULK || !el) $("topo-hot").insertAdjacentHTML("beforeend", `<path id="topo-hl" class="hovl one" d="${TD.geo[h.e].d}"/>`);
      const p = TD.geo[h.e].mid, lost = E.lost - E.inFlight, text = nf(E.sent) + (lost ? ` · ${nf(lost)} lost` : "");
      const w = text.length * 6.4 + 12;
      $("topo-pills").insertAdjacentHTML("beforeend", `<g id="topo-hp" class="tp cnt hot" transform="translate(${r1d(p.x)},${r1d(p.y)})">`
        + `<rect x="${r1d(-w / 2)}" y="-8.5" width="${r1d(w)}" height="17" rx="8.5"/>`
        + `<text text-anchor="middle" dy="3.6">${nf(E.sent)}${lost ? `<tspan class="lo"> · ${nf(lost)} lost</tspan>` : ""}</text></g>`);
    }
    hoverRule.textContent = `#rows tr[data-e="${h.e}"] td { background: var(--hover-row); }`;
    if (state.view === "lanes" && L) {
      const list = [];
      for (const t of mounted.keys()) {
        for (const id of L.tileCx[t] || []) {
          const k = L.cx.kind[id];
          if ((k === K_MSG || k === K_SELF || k === K_LOST || k === K_STUB) && edgeOfRec[L.cx.a[id]] === h.e) list.push([id, -1]);
        }
      }
      drawOverlay(list);
    }
    if (!quiet && anchorEl) showTip(edgeTip(h.e), rectOf(anchorEl), true);
  }
  function peek(i) {
    if (!TD) return;
    for (const el of peeked) el.classList.remove("peek");
    peeked = [];
    if (i == null || i < 0) return;
    const n = nodeOfLane(laneOf[i]);
    if (n >= 0 && TD.nodeEls[n]) { TD.nodeEls[n].classList.add("peek"); peeked.push(TD.nodeEls[n]); }
    const e = edgeOfRec[i];
    if (e >= 0 && TD.linkEls[e]) { TD.linkEls[e].classList.add("peek"); peeked.push(TD.linkEls[e]); }
  }
  function peekLane(l) {
    if (l == null || l >= A) { if (mapHov && mapHov.fromLane) setMapHover(null); return; }
    setMapHover({ n: l, fromLane: true }, null, true);
  }

  function bulkHit(x, y) {
    if (!TD || !TD.grid) return -1;
    let best = -1, bd = 64;
    const gx = Math.floor(x / 24), gy = Math.floor(y / 24);
    for (let ox = -1; ox <= 1; ox++) for (let oy = -1; oy <= 1; oy++) {
      const list = TD.grid.get((gx + ox) + "," + (gy + oy));
      if (!list) continue;
      for (let k = 0; k < list.length; k += 3) {
        const d = (list[k + 1] - x) ** 2 + (list[k + 2] - y) ** 2;
        if (d < bd) { bd = d; best = list[k]; }
      }
    }
    return best;
  }
  const svgPoint = e => { const r = topoSvg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  function mapTarget(e) {
    const t = e.target;
    const pl = t.closest && t.closest(".tp[data-hop]");
    if (pl) return { hop: +pl.dataset.hop, el: pl };
    const nd = t.closest && t.closest(".nd");
    if (nd) return { n: +nd.dataset.n, el: nd };
    const lk = t.closest && t.closest(".lk");
    if (lk) return { e: +lk.dataset.e, el: lk };
    if (BULK) {
      const p = svgPoint(e), b = bulkHit(p.x, p.y);
      const box = { left: e.clientX - 4, right: e.clientX + 4, top: e.clientY - 4, bottom: e.clientY + 4, width: 8, height: 8 };
      if (b >= 0) return { e: b, el: { getBoundingClientRect: () => box } };
    }
    return null;
  }
  let lastPointer = "mouse", touchArm = null;
  topoSvg.addEventListener("pointermove", e => {
    if (e.pointerType === "touch") return;
    const t = mapTarget(e);
    if (!t || t.hop != null) { if (!touchArm) setMapHover(null); return; }
    setMapHover(t.n != null ? { n: t.n } : { e: t.e }, t.el);
  });
  topoSvg.addEventListener("pointerleave", () => { if (!touchArm) setMapHover(null); });
  topoSvg.addEventListener("pointerdown", e => { lastPointer = e.pointerType; });
  topoSvg.addEventListener("click", e => {
    const t = mapTarget(e);
    if (!t) { touchArm = null; hideTip(); return; }
    if (t.hop != null) { const h = hops[t.hop - 1]; if (h) select(h.rec, { reveal: true }); return; }
    if (lastPointer === "touch") {
      const k = t.n != null ? "n" + t.n : "e" + t.e;
      if (touchArm !== k) {
        touchArm = k;
        let acts;
        if (t.n == null) acts = [[`Show ${plural(EDGES[t.e].sent, "message")}`, "main"]];
        else {
          const on = mapSets.fol.has(t.n);
          const main = state.view === "lanes" ? (on ? "Stop following" : "Follow") : (on ? "Clear agent filter" : "Filter to this agent");
          acts = [[main, "main"], ["Follow with partners", "partners"]];
        }
        const buttons = acts.map(([l, a]) => `<button class="tool" data-tact="${a}">${esc(l)}</button>`).join("");
        tipAct = a => {
          touchArm = null;
          hideTip();
          if (t.n != null) nodeAction(t.n, a === "partners");
          else setLink(t.e, false);
        };
        showTip((t.n != null ? nodeTip(t.n) : edgeTip(t.e)) + `<div class="tacts">${buttons}</div>`, rectOf(t.el), true, true);
        return;
      }
      touchArm = null;
      hideTip();
    }
    if (t.n != null) nodeAction(t.n, e.shiftKey);
    else setLink(t.e, false);
  });
  topoSvg.addEventListener("focusin", e => {
    const nd = e.target.closest(".nd"), lk = e.target.closest(".lk");
    if (nd) { mapFocusN = +nd.dataset.n; rovingNode(); setMapHover({ n: mapFocusN }, nd); }
    else if (lk) setMapHover({ e: +lk.dataset.e }, lk);
  });
  topoSvg.addEventListener("focusout", e => {
    if (!topoSvg.contains(e.relatedTarget) && !tip.contains(e.relatedTarget) && !touchArm) setMapHover(null);
  });

  function nodeAction(n, partners) {
    const d = NODES[n];
    if (partners) { followPartners(n); return; }
    if (d.lane < 0) { toast(`${d.id} has no records in this trace`); return; }
    if (state.view === "records") {
      const v = state.agent === AG[d.lane] ? "" : String(AG[d.lane]);
      agentSel.value = v;
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
    toast(`Following ${d.id} and ${plural(ids.length - 1, "partner")}`, prev ? [undoLanes(prev)] : [], `Followed ${plural(ids.length, "agent")}`);
  }

  // prev is null when the filter came with the page (a shared link); clearing it then falls back to the default lanes
  let linkSaved = null;
  function setLink(e, both) {
    if (e == null) {
      if (!state.link) return;
      state.link = null;
      if (linkSaved && state.lanes && String(state.lanes) === String(linkSaved.applied)) setLanes(linkSaved.prev || defaultLanes());
      linkSaved = null;
      announce("Connection filter cleared");
    } else {
      const E = EDGES[e], rev = reverseEdge(e);
      if (!state.link || !linkSaved) linkSaved = { prev: state.lanes && state.lanes.length ? state.lanes.slice() : null };
      state.link = { e, both: !!both };
      const lanes = [...new Set([NODES[E.a].lane, NODES[E.b].lane].filter(l => l >= 0))];
      linkSaved.applied = lanes;
      setLanes(lanes);
      const n = E.sent + (both && rev >= 0 ? EDGES[rev].sent : 0);
      const what = both && rev >= 0 ? `between ${E.from} and ${E.to}` : `from ${E.from} to ${E.to}`;
      announce(`Showing ${plural(n, "message")} ${what}`);
    }
    renderLinkChip();
    invalidate(F_FILTER | F_HASH);
  }

  function focusNode(n) {
    if (!TD || !TD.nodeEls[n]) return;
    mapFocusN = n;
    rovingNode();
    TD.nodeEls[n].focus();
  }
  function focusLink(e) {
    if (!TD) return;
    let el = TD.linkEls[e];
    if (!el) {
      $("topo-proxy")?.remove();
      const lab = `${esc(EDGES[e].from)} to ${esc(EDGES[e].to)}, ${plural(EDGES[e].sent, "message")}`;
      $("topo-pills").insertAdjacentHTML("beforeend", `<g id="topo-proxy" class="lk proxy" data-e="${e}" role="button" tabindex="-1" aria-label="${lab}">`
        + `<path class="ln" d="${TD.geo[e].d}"/></g>`);
      el = $("topo-proxy");
    }
    el.focus();
  }
  function focusMap() {
    if (!state.topo.open) toggleTopo(true);
    if (state.topo.mode === "list") { $("topo-list").querySelector("tbody tr")?.focus(); return; }
    if (!TD) drawTopology();
    if (!TD) return;
    const s = state.sel != null ? nodeOfLane(laneOf[state.sel]) : -1;
    focusNode(s >= 0 ? s : TL.hubList[0] ?? TL.linked[0] ?? 0);
  }
  topoSvg.addEventListener("keydown", e => {
    const el = document.activeElement;
    if (!TD || !el) return;
    const nd = el.closest(".nd"), lk = el.closest(".lk"), pl = el.closest(".tp[data-hop]");
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (lk) focusNode(mapFocusN >= 0 ? mapFocusN : EDGES[+lk.dataset.e].a);
      else { setMapHover(null); focusView(); }
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
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setLink(ei, false); }
      else if (e.key === "c" || e.key === "C") { e.preventDefault(); stepLink(e.key === "C" ? -1 : 1, ei); }
      return;
    }
    if (!nd) return;
    const n = +nd.dataset.n;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nodeAction(n, e.shiftKey); return; }
    if (e.key === "Home") { e.preventDefault(); focusNode(TL.hubList[0] ?? TL.linked[0] ?? n); return; }
    if (e.key === "c" || e.key === "C") { e.preventDefault(); mapFocusN = n; stepLink(e.key === "C" ? -1 : 1, -1); return; }
    const dir = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!dir) return;
    e.preventDefault();
    const p = TD.F.P[n];
    let best = -1, bd = Infinity;
    TD.F.P.forEach((q, k) => {
      if (!q || k === n) return;
      const dx = q.x - p.x, dy = q.y - p.y, d = Math.hypot(dx, dy);
      if (!d || (dx * dir[0] + dy * dir[1]) / d < 0.5) return;
      const score = d * (2 - (dx * dir[0] + dy * dir[1]) / d);
      if (score < bd) { bd = score; best = k; }
    });
    if (best >= 0) focusNode(best);
  });
  function stepLink(d, cur) {
    const n = mapFocusN;
    if (n < 0) return;
    const list = NODES[n].edges;
    if (!list.length) return;
    const k = cur < 0 ? (d > 0 ? 0 : list.length - 1) : (list.indexOf(cur) + d + list.length) % list.length;
    focusLink(list[k]);
  }

  const topoMeta = $("topo-meta");
  function renderTopoMeta() {
    if (!EDGES.length) {
      const why = nRecv ? "No message.sent records: the map is drawn from sends" : "No messages traced (message category off?)";
      topoMeta.textContent = `${why} · ${plural(NODES.length, "agent")}`;
      return;
    }
    const match = filtersActive ? EDGES.filter(e => linkMatch[e.k] > 0).length : EDGES.length;
    const conn = filtersActive ? `${nf(match)} of ${plural(EDGES.length, "connection")} match` : plural(EDGES.length, "connection");
    topoMeta.innerHTML = `${plural(NODES.length, "agent")} · ${conn} · <button class="lossy" id="topo-lossy" aria-pressed="${state.topo.lossy}"`
      + ` ${LOSSY ? "" : "disabled"} title="Highlight connections that lost messages">${nf(LOSSY)} lossy</button>`
      + (IN_FLIGHT ? ` · <span class="fl">${nf(IN_FLIGHT)} in flight</span>` : "");
  }
  topoMeta.addEventListener("click", e => {
    if (!e.target.closest("#topo-lossy")) return;
    state.topo.lossy = !state.topo.lossy;
    mapState(false);
    announce(state.topo.lossy ? "Showing lossy connections" : "Showing all connections");
  });
  const lg = (d, extra) => `<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">${d}</svg>${extra}`;
  const quietLine = '<path d="M1 5h20" stroke="var(--edge-quiet)" stroke-width="1.5"/>';
  $("topo-legend").innerHTML = `<span>${lg('<path d="M1 5h18" stroke="var(--edge-msg)" stroke-width="2" stroke-linecap="round"/>'
      + '<path d="M15 2l5 3-5 3z" fill="var(--edge-msg)"/>', "message, width = count")}</span>`
    + `<span>${lg(quietLine + '<path d="M11 1.5v7" stroke="var(--lost)" stroke-width="2.2" stroke-linecap="round"/>', "lost")}</span>`
    + `<span>${lg(quietLine + '<circle cx="11" cy="5" r="3.2" fill="var(--panel)" stroke="var(--in-flight)" stroke-width="1.5"/>', "in flight")}</span>`
    + `<span>${lg('<circle cx="11" cy="5" r="4" fill="var(--panel)" stroke="var(--node-ring)" stroke-width="1.3" stroke-dasharray="2 2"/>',
      "no messages")}</span>`
    + (unresolved ? `<span class="warnu">${plural(unresolved, "send")} without a known receiver</span>` : "");
  function renderRoute() {
    const el = $("topo-route");
    if (state.sel == null) { el.textContent = "Select a record to see its route"; return; }
    const first = pathOfSel().find(i => laneOf[i] !== WORLD);
    if (first == null) { el.textContent = "The selection has no agent"; return; }
    el.innerHTML = esc(laneName(laneOf[first])) + hops.map(h => ` <span class="hop">${h.n}</span> → ${esc(laneName(h.to))}`).join("");
  }

  let listSort = { key: "from", dir: 1 };
  const LIST_COLS = [["from", "From"], ["to", "To"], ["sent", "Sent", 1], ["received", "Received", 1], ["lost", "Lost", 1], ["inFlight", "In flight", 1],
    ["types", "Types"], ["first", "First"], ["last", "Last"]];
  function listRowHTML(E, r) {
    const lost = E.lost - E.inFlight, tf = v => typeof v === "number" ? (CLOCK === "sim" ? timeLabel(v) : String(v)) : "";
    return `<tr tabindex="${r ? -1 : 0}" data-e="${E.k}" data-from="${esc(E.from)}" data-to="${esc(E.to)}" data-sent="${E.sent}" data-received="${E.received}"`
      + ` data-lost="${E.lost}" data-inflight="${E.inFlight}" data-types="${esc(JSON.stringify([...E.types]))}"`
      + ` data-first="${esc(JSON.stringify(E.first))}" data-last="${esc(JSON.stringify(E.last))}">`
      + `<td>${esc(E.from)}</td><td>${esc(E.to)}</td><td class="n">${nf(E.sent)}</td><td class="n">${nf(E.received)}</td>`
      + `<td class="n${lost ? " lo" : ""}">${nf(lost)}</td><td class="n${E.inFlight ? " fl" : ""}">${nf(E.inFlight)}</td>`
      + `<td class="ty">${[...E.types].map(([t, c]) => `${esc(t ?? "?")} ${nf(c)}`).join(", ")}</td><td>${tf(E.first)}</td><td>${tf(E.last)}</td></tr>`;
  }
  function renderTopoList() {
    const box = $("topo-list");
    const val = (E, k) => {
      if (k === "lost") return E.lost - E.inFlight;
      if (k === "types") return E.types.size;
      if (k === "first" || k === "last") return typeof E[k] === "number" ? E[k] : -Infinity;
      return E[k];
    };
    const rows = EDGES.slice().sort((a, b) => {
      const k = listSort.key, va = val(a, k), vb = val(b, k);
      const c = typeof va === "number" && typeof vb === "number" ? va - vb : natural(String(va), String(vb));
      return listSort.dir * (c || natural(String(a.from), String(b.from)) || natural(String(a.to), String(b.to)));
    });
    const sortAttr = k => listSort.key === k ? ` aria-sort="${listSort.dir > 0 ? "ascending" : "descending"}"` : "";
    box.innerHTML = `<table class="tlist" aria-label="Connections"><thead><tr>`
      + LIST_COLS.map(([k, l, num]) => `<th scope="col" class="${num ? "n" : ""}"${sortAttr(k)}><button data-sort="${k}">${l}</button></th>`).join("")
      + `</tr></thead><tbody>${rows.map(listRowHTML).join("")}</tbody></table>`;
    syncTopoList();
  }
  function syncTopoList() {
    $("topo-list").querySelectorAll("tbody tr").forEach(tr => {
      const e = +tr.dataset.e;
      tr.classList.toggle("cur", !!state.link && state.link.e === e);
      tr.classList.toggle("off", filtersActive && !linkMatch[e]);
    });
  }
  $("topo-list").addEventListener("click", e => {
    const s = e.target.closest("[data-sort]");
    if (s) {
      listSort = { key: s.dataset.sort, dir: listSort.key === s.dataset.sort ? -listSort.dir : 1 };
      renderTopoList();
      $("topo-list").querySelector(`[data-sort="${s.dataset.sort}"]`).focus();
      return;
    }
    const tr = e.target.closest("tbody tr");
    if (tr) setLink(+tr.dataset.e, false);
  });
  $("topo-list").addEventListener("keydown", e => {
    const tr = e.target.closest("tbody tr");
    if (!tr) return;
    let to = null;
    if (e.key === "ArrowDown") to = tr.nextElementSibling;
    else if (e.key === "ArrowUp") to = tr.previousElementSibling;
    else if (e.key === "Home") to = tr.parentElement.firstElementChild;
    else if (e.key === "End") to = tr.parentElement.lastElementChild;
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setLink(+tr.dataset.e, false); return; }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); focusView(); return; }
    if (!to) return;
    e.preventDefault();
    tr.tabIndex = -1;
    to.tabIndex = 0;
    to.focus();
  });
  $("topo-list").addEventListener("pointerover", e => { const tr = e.target.closest("tbody tr"); setMapHover(tr ? { e: +tr.dataset.e } : null, null, true); });
  $("topo-list").addEventListener("pointerleave", () => setMapHover(null, null, true));
  $("topo-list").addEventListener("focusin", e => { const tr = e.target.closest("tbody tr"); if (tr) setMapHover({ e: +tr.dataset.e }, null, true); });

  function setTopoMode(mode) {
    state.topo.mode = mode;
    $("tm-graph").setAttribute("aria-pressed", mode === "graph");
    $("tm-list").setAttribute("aria-pressed", mode === "list");
    topoSvg.toggleAttribute("hidden", mode !== "graph" || !EDGES.length);
    $("topo-list").hidden = mode !== "list" || !EDGES.length;
    saveTopo();
    if (mode === "list") renderTopoList();
    else drawTopology();
  }
  function toggleTopo(open) {
    if (!EDGES.length) return;
    state.topo.open = open == null ? !state.topo.open : open;
    topoCard.classList.toggle("open", state.topo.open);
    $("topo-toggle").setAttribute("aria-expanded", state.topo.open);
    saveTopo();
    if (state.topo.open) requestAnimationFrame(() => { if (state.topo.mode === "graph") drawTopology(); else renderTopoList(); });
  }
  function toggleWide() {
    if (!EDGES.length) return;
    state.topo.wide = !state.topo.wide;
    $("main").classList.toggle("wide", state.topo.wide);
    $("topo-wide").setAttribute("aria-pressed", state.topo.wide);
    $("topo-wide").setAttribute("aria-label", state.topo.wide ? "Narrow the map" : "Widen the map");
    saveTopo();
  }
  const saveTopo = () => store.set("mango-trace-topo", JSON.stringify({ open: state.topo.open, wide: state.topo.wide, mode: state.topo.mode }));
  $("topo-toggle").addEventListener("click", () => toggleTopo());
  $("topo-wide").addEventListener("click", toggleWide);
  $("tm-graph").addEventListener("click", () => setTopoMode("graph"));
  $("tm-list").addEventListener("click", () => setTopoMode("list"));
  let topoResize = 0, lastTopoSize = "";
  new ResizeObserver(() => {
    clearTimeout(topoResize);
    topoResize = setTimeout(() => {
      const sz = topoB.clientWidth + "x" + topoB.clientHeight;
      if (sz === lastTopoSize || !TL) return;
      lastTopoSize = sz;
      drawTopology();
    }, 100);
  }).observe(topoB);
