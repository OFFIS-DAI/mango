  let pendingReveal = null, pendingAnchor = null, pendingFocus = null, revealWindow = true;
  function select(i, opts = {}) {
    if (i != null && (i < 0 || i >= N)) return;
    if (i != null && i === state.sel && !opts.reveal) i = null;
    if (i != null && opts.reveal) {
      ensureLane(i, opts.leftOf ?? rel[i] === 1);
      pendingReveal = i;
    }
    if (i != null) state.cursor = i;
    state.sel = i;
    hideTip();
    invalidate(F_REL | (state.onlyRelated ? F_VIS : 0) | F_TABLE | F_TILES | F_MAP | F_PANEL | F_RAIL | F_HASH);
  }
  function reveal(i, inWindow) {
    if (state.view === "lanes") {
      if (!L || (rowOf[i] < 0 && vrowOf[i] < 0)) return;
      let from = -1;
      if (THREAD && i === state.sel) for (const pc of THREAD.pieces) if (pc.hop && (from < 0 || pc.lo > from)) from = pc.lo;
      lanesReveal(i, true, from);
      renderLanes(false);
      const top = lanesBox.getBoundingClientRect().top + GEO.headH + (rowOf[i] >= 0 ? rowOf[i] : vrowOf[i]) * ROW - lanesBox.scrollTop;
      if (inWindow) intoWindow(top, top + ROW);
    } else {
      const p = visPos(i);
      if (p < 0) return;
      tableScrollTo(p, "center");
      renderTable(false);
      const tr = rowsEl.querySelector(`tr[data-i="${i}"]`);
      if (tr && inWindow) { const b = tr.getBoundingClientRect(); intoWindow(b.top, b.bottom); }
    }
  }
  // the view card can reach below the fold: the page scrolls too, to show the whole card if that brings the row into view
  function intoWindow(top, bottom) {
    const m = 8, vh = innerHeight;
    if (top >= m && bottom <= vh - m) return;
    let dy = $("view").getBoundingClientRect().top - 12;
    if (top - dy < m || bottom - dy > vh - m) dy = top < m ? top - m - 44 : bottom - vh + m + 44;
    scrollBy({ top: dy, behavior: motion() ? "smooth" : "auto" });
  }
  // the anchor keeps its distance from the top of the view card: y is measured from the scroller's top, header included
  const lanesRowOf = i => rowOf[i] >= 0 ? rowOf[i] : vrowOf[i] >= 0 && L.col[laneOf[i]] >= 0 && vis[i] ? vrowOf[i] : -1;
  function captureAnchor() {
    if (state.view === "lanes") {
      if (!L || !GEO) return null;
      const top = lanesBox.scrollTop, vh = lanesBox.clientHeight - GEO.headH, s = state.sel, sr = s != null ? lanesRowOf(s) : -1;
      if (sr >= 0 && sr * ROW >= top && sr * ROW < top + vh) return { i: s, y: GEO.headH + sr * ROW - top };
      for (let r = Math.max(0, Math.floor(top / ROW)); r < L.maxRow; r++) {
        for (let j = L.rowStart[r]; j < L.rowStart[r + 1]; j++) if (L.rowRecs[j] >= 0) return { i: L.rowRecs[j], y: GEO.headH + r * ROW - top };
      }
      return null;
    }
    const top = recBox.scrollTop, th = theadH(), vh = recBox.clientHeight - th, s = state.sel;
    const p = s != null ? visPos(s) : -1;
    if (p >= 0 && p * RH >= top && p * RH < top + vh) return { i: s, y: th + p * RH - top };
    const q0 = Math.min(visList.length - 1, Math.floor(top / RH));
    return q0 >= 0 ? { i: visList[q0], y: th + q0 * RH - top } : null;
  }
  function applyAnchor() {
    const a = pendingAnchor;
    pendingAnchor = null;
    if (!a) return;
    if (state.view === "lanes") {
      if (!L) return;
      let j = a.i;
      while (j < N && lanesRowOf(j) < 0) j++;
      if (j >= N) return;
      lanesBox.scrollTop = lanesRowOf(j) * ROW - (a.y - GEO.headH);
      const c = colOfRec(j), vw = lanesBox.clientWidth - GEO.gw, sl = lanesBox.scrollLeft;
      if (GEO.life[c] - 14 < sl || Math.min(GEO.x[c] + GEO.w[c], GEO.life[c] + 160) > sl + vw) lanesBox.scrollLeft = Math.max(0, GEO.life[c] - 40);
      renderLanes(false);
    } else {
      const p = Math.min(visList.length - 1, visPosAtOrAfter(a.i));
      if (p < 0) return;
      recBox.scrollTop = p * RH - (a.y - theadH());
      renderTable(false);
    }
  }
  const tabs = { records: $("tab-records"), lanes: $("tab-lanes") };
  function applyView() {
    const lanes = state.view === "lanes";
    recBox.hidden = lanes;
    lanesPanel.hidden = !lanes;
    $("ltools").hidden = !lanes;
    $("agent-pick").hidden = lanes;
    for (const [v, b] of Object.entries(tabs)) {
      b.setAttribute("aria-selected", v === state.view);
      b.tabIndex = v === state.view ? 0 : -1;
    }
    $("view").setAttribute("aria-label", lanes ? "Lanes" : "Records");
  }
  // keepLanes: the caller has just chosen the lanes (Follow chain, partners, picker), so the Agent filter is not added to them
  function setView(v, opts = {}) {
    if (v === state.view || (v === "lanes" && !A)) return;
    pendingAnchor = captureAnchor();
    state.view = v;
    store.set("mango-trace-view", v);
    if (v === "lanes") {
      if (!state.lanes) state.lanes = defaultLanes();
      if (!opts.keepLanes && !state.link && state.agent !== "" && aIx.has(state.agent) && !state.lanes.includes(aIx.get(state.agent))) {
        const lanes = state.lanes.slice(), at = lanes[0] === WORLD ? 1 : 0;
        lanes.splice(at, 0, aIx.get(state.agent));
        state.lanes = lanes;
      }
      updatePickButton();
    }
    hideTip();
    closePicker(false);
    invalidate(F_VIEW | F_VIS | F_LAYOUT | F_MAP | F_RAIL | F_HASH | F_PANEL | F_TABLE);
  }
  function setOrder(o) {
    state.order = o;
    $("o-packed").setAttribute("aria-pressed", o === "packed");
    $("o-file").setAttribute("aria-pressed", o === "file");
    announce(o === "file" ? "File order: one record per row" : "Packed by cause");
    invalidate(F_LAYOUT | F_HASH | F_RAIL);
  }
  function setFit(f) {
    state.fit = f;
    $("fit").setAttribute("aria-pressed", f);
    announce(f ? "All lanes fit the width" : "Lanes at reading width");
    invalidate(F_HEAD | F_HASH);
  }
  $("o-packed").addEventListener("click", () => setOrder("packed"));
  $("o-file").addEventListener("click", () => setOrder("file"));
  $("fit").addEventListener("click", () => setFit(!state.fit));
  $("follow").addEventListener("click", () => followChain());
  for (const [v, b] of Object.entries(tabs)) {
    b.addEventListener("click", () => { if (b.getAttribute("aria-disabled") !== "true") setView(v); });
    b.addEventListener("keydown", e => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const to = v === "records" ? "lanes" : "records";
      if (to === "lanes" && !A) return;
      setView(to);
      tabs[to].focus();
    });
  }
  if (!A) { tabs.lanes.setAttribute("aria-disabled", "true"); tabs.lanes.title = "No agent records"; }

  const statusEl = $("status");
  function updateStatus() {
    if (state.view === "lanes" && L) {
      let s = `${plural(L.maxRow, "row")} · ${nf(L.placed)} shown`;
      if (L.backwards) s += ` · ${nf(L.backwards)} out of order`;
      if (state.onlyRelated && state.sel != null) {
        let n = 0;
        const lanesOff = new Set();
        for (const i of relList) if (vis[i] && L.col[laneOf[i]] < 0) { n++; lanesOff.add(laneOf[i]); }
        if (n) {
          s += ` · ${plural(n, "related record")} in ${plural(lanesOff.size, "lane")} not followed`
            + ` · <button data-act="follow-st">Follow chain</button>`;
        }
      }
      statusEl.innerHTML = s;
    } else statusEl.textContent = visList.length === N ? plural(N, "record") : `${nf(visList.length)} of ${plural(N, "record")}`;
  }
  statusEl.addEventListener("click", e => { if (e.target.closest("[data-act=follow-st]")) followChain(); });
  const selbar = $("selbar");
  function updateSelbar() {
    const s = state.sel;
    selbar.hidden = !isPhone() || s == null;
    if (selbar.hidden) return;
    selbar.classList.toggle("fail", !!failOf[s]);
    selbar.innerHTML = `<span class="sb-t">${esc(RECORDS[s].event)} <span class="m">${esc(laneName(laneOf[s]))}</span></span>`
      + `<button class="tool" data-act="details">Details ↓</button>`;
  }
  selbar.addEventListener("click", e => {
    if (e.target.closest("[data-act=details]")) panel.scrollIntoView({ behavior: motion() ? "smooth" : "auto", block: "start" });
  });

  function flashHeader(l) {
    const el = lhead.querySelector(`.lh[data-l="${l}"]`);
    if (!el) return;
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), motion() ? 650 : 1200);
  }

  function flush() {
    raf = 0;
    const d = dirty;
    dirty = 0;
    if (d & F_REL) computeRelations();
    if (d & F_VIS) { computeVisibility(); LSTALE = true; }
    if (d & F_VIEW) applyView();
    if (state.view === "lanes" && A) {
      const relayout = LSTALE || !L || !!(d & F_LAYOUT);
      if (relayout) layoutLanes();
      const regeo = relayout || !GEO || !!(d & F_HEAD);
      if (regeo) GEO = geometry();
      if (relayout || d & (F_REL | F_TILES)) computeThread(!!(d & F_REL));
      else if (regeo) placePills();
      if (regeo) { renderHead(); sizeLanes(); }
      lanesBox.classList.toggle("has-sel", selShown());
      if (regeo || d & (F_REL | F_TILES)) { renderLanes(true); positionSelRow(); updateFpill(); }
      else if (d & F_TABLE) renderLanes(false);
      renderLanesEmpty();
      if (flashLane >= 0) { flashHeader(flashLane); flashLane = -1; }
      if (pendingCursorAt) {
        const pc = pendingCursorAt;
        pendingCursorAt = null;
        const c0 = Math.min(pc.c, L.nC - 1);
        let i = -1;
        for (let d = 0; i < 0 && d < L.nC; d++) {
          for (const c of d ? [c0 - d, c0 + d] : [c0]) if (i < 0 && c >= 0 && c < L.nC) i = nearestIn(colList(c), pc.row);
        }
        if (i >= 0) {
          state.cursor = i;
          lanesReveal(i, false);
          renderLanes(false);
          if (pc.focus) pendingFocus = i;
        }
      }
    } else if (d & (F_VIS | F_REL | F_TABLE | F_VIEW)) renderTable(true);
    if (pendingAnchor) applyAnchor();
    if (pendingReveal != null) { const i = pendingReveal; pendingReveal = null; reveal(i, revealWindow); }
    revealWindow = true;
    if (pendingFocus != null) {
      const i = pendingFocus;
      pendingFocus = null;
      plane.querySelector(`.rec[data-i="${i}"]`)?.focus({ preventScroll: true });
    }
    if (d & (F_MAP | F_REL | F_VIS | F_VIEW | F_LAYOUT)) mapState(false);
    if (d & (F_PANEL | F_REL | F_VIS)) renderPanel();
    if (d & (F_RAIL | F_REL | F_VIS | F_VIEW | F_LAYOUT | F_HEAD)) drawRail();
    if (d & (F_HASH | F_REL | F_VIEW | F_LAYOUT)) writeHash();
    updateStatus();
    updateFailsButton();
    updateSelbar();
  }

  let sRaf = 0;
  function scrollFrame() {
    if (sRaf) return;
    sRaf = requestAnimationFrame(() => {
      sRaf = 0;
      if (state.view === "lanes") renderLanes(false);
      else renderTable(false);
      drawRailViewport();
      const ae = document.activeElement, onRec = ae && ae.closest && ae.closest(".rec");
      if (!tip.hidden && !tip.classList.contains("act") && !onRec) hideTip();
    });
  }
  // beside the side column the card takes the rest of the first screen, so its bottom and scrollbar are never below the fold
  function fitCard() {
    const root = document.documentElement.style;
    if (innerWidth <= 980) { root.removeProperty("--card-h"); return; }
    const top = $("view").getBoundingClientRect().top + scrollY;
    root.setProperty("--card-h", Math.round(clamp(innerHeight - top - 12, 420, Math.min(1400, innerHeight - 24))) + "px");
  }
  addEventListener("resize", fitCard);
  {
    const ro = new ResizeObserver(fitCard);
    ro.observe(document.querySelector("header"));
    ro.observe(document.querySelector(".filters"));
  }
  let viewW = 0;
  new ResizeObserver(() => {
    const w = $("view").clientWidth;
    if (w !== viewW) { viewW = w; invalidate(F_HEAD | F_RAIL | F_TABLE); }
    else invalidate(F_TABLE | F_RAIL);
  }).observe($("view"));

  addEventListener("hashchange", () => {
    const hs = readHash();
    setView(hs.view || "records");
    if ((hs.order || "packed") !== state.order) setOrder(hs.order || "packed");
    if (!!hs.fit !== state.fit) setFit(!!hs.fit);
    if (hs.link) setLink(hs.link.e, hs.link.both);
    else if (state.link) setLink(null);
    if (hs.lanes) setLanes(hs.lanes);
    if (hs.sel != null && hs.sel !== state.sel) select(hs.sel, { reveal: true });
  });

  {
    const hs = readHash();
    // a link without v= is a Records link (a bare #r683), whatever view was used last
    if (location.hash.length > 1) state.view = hs.view || "records";
    if (hs.order) { state.order = hs.order; $("o-packed").setAttribute("aria-pressed", "false"); $("o-file").setAttribute("aria-pressed", "true"); }
    if (hs.fit) { state.fit = true; $("fit").setAttribute("aria-pressed", "true"); }
    if (hs.link) state.link = hs.link;
    state.sel = hs.sel != null ? hs.sel : FAILS.length ? FAILS[0] : null;
    state.cursor = state.sel;
    computeRelations();
    if (hs.lanes) {
      state.lanes = hs.lanes;
      // a link to a record in a lane it does not follow opens with that lane added, as navigating there would
      const l = hs.sel != null ? laneOf[hs.sel] : -1;
      if (state.view === "lanes" && l >= 0 && !state.lanes.includes(l) && (l !== WORLD || hasWorldRecs)) {
        state.lanes = insertSorted(state.lanes, l);
        flashLane = l;
      }
    } else if (state.link) {
      const E = EDGES[state.link.e];
      state.lanes = [...new Set([NODES[E.a].lane, NODES[E.b].lane].filter(l => l >= 0))];
    } else if (state.view === "lanes") state.lanes = defaultLanes();
    if (state.link) linkSaved = { prev: null, applied: state.lanes ? state.lanes.slice() : [] };
    if (!EDGES.length) state.topo.open = true;
    if (isPhone() || A === 1) state.topo.open = false;
    if (NODES.length > 1000) state.topo.mode = "list";
    topoCard.classList.toggle("open", state.topo.open);
    $("topo-toggle").setAttribute("aria-expanded", state.topo.open);
    $("main").classList.toggle("wide", state.topo.wide);
    $("topo-wide").setAttribute("aria-pressed", state.topo.wide);
    $("tm-graph").setAttribute("aria-pressed", state.topo.mode === "graph");
    $("tm-list").setAttribute("aria-pressed", state.topo.mode === "list");
    if (!N || !A) topoCard.hidden = true;
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
    renderTopoMeta();
    renderRoute();
    renderLinkChip();
    updatePickButton();
    applyView();
    pendingReveal = state.sel;
    revealWindow = false;
    fitCard();
    dirty = F_VIS | F_TABLE | F_LAYOUT | F_PANEL | F_RAIL | F_HEAD | F_VIEW;
    flushNow();
    idle(() => {
      if (EDGES.length) {
        layoutTopology();
        if (state.topo.open) { if (state.topo.mode === "graph") drawTopology(); else renderTopoList(); }
        lastTopoSize = topoB.clientWidth + "x" + topoB.clientHeight;
      }
      mapState(true);
    });
  }
