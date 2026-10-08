  const hasWorldRecs = laneTotal[WORLD] > 0;
  const hubOrder = ids => ids.slice().sort((a, b) => degreeOf(b) - degreeOf(a) || natural(String(AG[a]), String(AG[b])));
  const withWorld = lanes => hasWorldRecs && !lanes.includes(WORLD) ? [WORLD, ...lanes] : lanes;
  function laneCapacity() {
    const box = lanesBox.clientWidth || recBox.clientWidth || 800;
    return clamp(Math.floor((box - gutterWidth() - (hasWorldRecs ? (isPhone() ? 40 : 56) : 0)) / 120), 2, 8);
  }
  function defaultLanes() {
    const k = laneCapacity(), all = AG.map((a, i) => i);
    if (A <= k) {
      const hubs = hubOrder(all).filter(l => degreeOf(l) >= Math.max(4, 0.4 * (A - 1)));
      return withWorld(hubs.concat(all.filter(l => !hubs.includes(l))));
    }
    let S = [];
    if (state.agent !== "" && aIx.has(state.agent)) S.push(aIx.get(state.agent));
    if (state.sel != null) {
      for (const i of pathOfSel()) if (laneOf[i] !== WORLD) S.push(laneOf[i]);
      const cnt = new Map();
      if (idOf[state.sel] >= 0) eachEffect(idOf[state.sel], j => { if (laneOf[j] !== WORLD) cnt.set(laneOf[j], (cnt.get(laneOf[j]) || 0) + 1); });
      S.push(...[...cnt.keys()].sort((a, b) => cnt.get(b) - cnt.get(a)));
    }
    S = [...new Set(S)].slice(0, k);
    const failing = all.filter(l => laneFailed[l]).sort((a, b) => laneFailed[b] - laneFailed[a]);
    const fillers = [...failing, ...hubOrder(all), ...all.slice().sort((a, b) => laneTotal[b] - laneTotal[a])];
    for (const l of fillers) {
      if (S.length >= Math.min(4, k)) break;
      if (!S.includes(l)) S.push(l);
    }
    return withWorld(S);
  }

  let toastTimer = 0, toastPaused = false, toastActions = [];
  const toastEl = $("toast");
  // the toast is not a live region: it speaks once through the status region, in the words of `spoken` when given
  function toast(text, actions, spoken) {
    clearTimeout(toastTimer);
    toastActions = actions || [];
    toastEl.innerHTML = `<span>${esc(text)}</span>` + toastActions.map((a, k) => `<button data-k="${k}">${esc(a.label)}</button>`).join("");
    toastEl.hidden = false;
    announce(spoken || text);
    const arm = () => {
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { if (!toastPaused) toastEl.hidden = true; else arm(); }, 5000);
    };
    arm();
  }
  toastEl.addEventListener("click", e => {
    const b = e.target.closest("button[data-k]");
    if (!b) return;
    toastEl.hidden = true;
    toastActions[+b.dataset.k].run();
  });
  for (const [ev, v] of [["pointerenter", true], ["pointerleave", false], ["focusin", true], ["focusout", false]]) {
    toastEl.addEventListener(ev, () => { toastPaused = v; });
  }

  let flashLane = -1;
  function setLanes(lanes, opts = {}) {
    state.lanes = lanes;
    if (opts.flash != null) flashLane = opts.flash;
    if (state.view === "lanes") invalidate(F_LAYOUT | F_MAP | F_HASH | F_RAIL | F_HEAD | F_PANEL);
    else { LSTALE = true; invalidate(F_MAP | F_HASH); }
    updatePickButton();
  }
  const undoLanes = prev => ({ label: "Undo", run: () => setLanes(prev) });
  function insertSorted(lanes, l) {
    const out = lanes.slice();
    if (l === WORLD) return [WORLD, ...out];
    let at = out.length;
    for (let k = 0; k < out.length; k++) if (out[k] !== WORLD && out[k] > l) { at = k; break; }
    out.splice(at, 0, l);
    return out;
  }
  // when the cursor's lane goes, the cursor moves to the lane that takes its place, at the same row
  let pendingCursorAt = null;
  function toggleLane(l, quiet) {
    const prev = state.lanes ? state.lanes.slice() : [];
    const on = prev.includes(l);
    if (on && L && state.view === "lanes" && state.cursor != null && laneOf[state.cursor] === l && rowOf[state.cursor] >= 0) {
      pendingCursorAt = { c: L.col[l], row: rowOf[state.cursor], focus: lanesBox.contains(document.activeElement) };
    }
    setLanes(on ? prev.filter(x => x !== l) : insertSorted(prev, l), { flash: on ? null : l });
    const spoken = `${on ? "Stopped following" : "Added lane"} ${laneName(l)}`;
    if (quiet) announce(spoken);
    else toast(`${on ? "Stopped following" : "Added"} ${laneName(l)}`, [undoLanes(prev)], spoken);
  }
  function ensureLane(i, leftOf) {
    if (state.view !== "lanes" || !state.lanes) return;
    const l = laneOf[i];
    if (state.lanes.includes(l) || (l === WORLD && !hasWorldRecs)) return;
    const prev = state.lanes.slice(), lanes = prev.slice();
    const cur = state.cursor != null ? lanes.indexOf(laneOf[state.cursor]) : -1;
    lanes.splice(cur < 0 ? lanes.length : leftOf ? cur : cur + 1, 0, l);
    setLanes(lanes, { flash: l });
    toast(`Added ${laneName(l)}`, [undoLanes(prev)], `Added lane ${laneName(l)}`);
  }
  function followChain(all) {
    if (state.sel == null) { toast("Select a record to follow its chain"); return; }
    const path = pathOfSel(), out = [];
    for (const i of path) if (laneOf[i] !== WORLD && !out.includes(laneOf[i])) out.push(laneOf[i]);
    const cnt = new Map();
    for (const d of descList) if (laneOf[d] !== WORLD) cnt.set(laneOf[d], (cnt.get(laneOf[d]) || 0) + 1);
    for (const l of [...cnt.keys()].sort((a, b) => cnt.get(b) - cnt.get(a))) if (!out.includes(l)) out.push(l);
    const total = out.length, lanes = all ? out : out.slice(0, 8);
    if (path.some(i => laneOf[i] === WORLD) && hasWorldRecs) lanes.unshift(WORLD);
    const prev = state.lanes ? state.lanes.slice() : null;
    setLanes(lanes);
    if (state.view !== "lanes") setView("lanes", { keepLanes: true });
    const n = lanes.filter(l => l !== WORLD).length;
    const acts = prev ? [undoLanes(prev)] : [];
    const spoken = `Followed ${plural(n, "agent")}`;
    if (total > n) toast(`${n} of ${total} agents in this chain`, [{ label: "Show all", run: () => followChain(true) }, ...acts], spoken);
    else toast(`Following ${plural(n, "agent")} of this chain`, acts, spoken);
    pendingReveal = state.sel;
  }
  const pickBtn = $("pick");
  function updatePickButton() {
    const n = (state.lanes || []).filter(l => l !== WORLD).length;
    pickBtn.innerHTML = `<span class="lg">Lanes <b>${nf(n)}</b> of ${nf(A)}</span><span class="sh"><b>${nf(n)}</b>/${nf(A)}</span>`
      + ` <span class="caret" aria-hidden="true">▾</span>`;
    pickBtn.setAttribute("aria-label", `Lanes: following ${nf(n)} of ${plural(A, "agent")}`);
  }

  let picker = null;
  function closePicker(refocus) {
    if (!picker) return;
    picker.remove();
    picker = null;
    pickBtn.setAttribute("aria-expanded", "false");
    if (refocus) pickBtn.focus();
  }
  function openPicker() {
    if (picker) { closePicker(true); return; }
    if (state.view !== "lanes") setView("lanes", { keepLanes: true });
    picker = document.createElement("div");
    picker.className = "pop";
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-label", "Follow agents");
    const nFailing = AG.filter((a, l) => laneFailed[l]).length;
    picker.innerHTML = `<h3>Follow agents</h3><input type="search" id="pk-q" placeholder="Search agents…" aria-label="Search agents" autocomplete="off">`
      + `<div class="pbtn"><button class="tool" data-pk="all">All</button><button class="tool" data-pk="none">None</button>`
      + `<button class="tool" data-pk="chain" ${state.sel == null ? "disabled" : ""}>Chain of selection</button>`
      + `<button class="tool" data-pk="fail" ${nFailing ? "" : "disabled"}>With failures (${nFailing})</button></div>`
      + `<ul class="plist" id="pk-list" role="listbox" aria-multiselectable="true" aria-label="Agents" tabindex="0"></ul>`;
    document.body.append(picker);
    const r = pickBtn.getBoundingClientRect();
    picker.style.left = clamp(r.left, 8, innerWidth - Math.min(320, innerWidth - 16) - 8) + "px";
    picker.style.top = Math.min(r.bottom + 6, innerHeight - 200) + "px";
    pickBtn.setAttribute("aria-expanded", "true");
    const qi = picker.querySelector("#pk-q"), list = picker.querySelector("#pk-list");
    let active = 0, items = [];
    const ids = (hasWorldRecs ? [WORLD] : []).concat(AG.map((a, l) => l));
    const itemHTML = (l, k, lanes) => {
      const f = laneFailed[l], p = l === WORLD ? 0 : degreeOf(l);
      const meta = `${nf(laneTotal[l])}${l === WORLD ? " records" : ""}` + (f ? ` · <b>${nf(f)} failed</b>` : "")
        + (l === WORLD ? "" : ` · ${plural(p, "partner")}`);
      const cls = (l === WORLD ? "world " : "") + (k === active ? "act" : "");
      return `<li role="option" id="pk-${l}" data-l="${l}" aria-selected="${lanes.includes(l)}" class="${cls}">`
        + `<span class="cb"></span><span class="pn">${esc(laneName(l))}</span><span class="pm">${meta}</span></li>`;
    };
    const render = () => {
      const t = qi.value.trim().toLowerCase();
      items = ids.filter(l => !t || laneName(l).toLowerCase().includes(t));
      active = clamp(active, 0, Math.max(0, items.length - 1));
      const lanes = state.lanes || [];
      list.innerHTML = items.map((l, k) => itemHTML(l, k, lanes)).join("") || `<li class="pm" aria-disabled="true">No agent matches</li>`;
      if (items.length) list.setAttribute("aria-activedescendant", "pk-" + items[active]);
      list.querySelector("li.act")?.scrollIntoView({ block: "nearest" });
    };
    const addMatches = () => {
      const add = items.filter(l => !(state.lanes || []).includes(l));
      if (!add.length) return;
      const prev = (state.lanes || []).slice();
      let ls = prev;
      for (const l of add) ls = insertSorted(ls, l);
      setLanes(ls);
      toast(`Added ${plural(add.length, "lane")}`, [undoLanes(prev)]);
    };
    render();
    qi.focus();
    qi.addEventListener("input", () => { active = 0; render(); });
    qi.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); addMatches(); render(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); list.focus(); }
    });
    let typed = "", typedAt = 0;
    const typeAhead = ch => {
      const now = performance.now();
      typed = now - typedAt < 800 ? typed + ch.toLowerCase() : ch.toLowerCase();
      typedAt = now;
      // the same letter again steps to the next agent starting with it
      const q = [...typed].every(c => c === typed[0]) ? typed[0] : typed, from = q.length > 1 ? active : active + 1;
      for (let n = 0; n < items.length; n++) {
        const k = (from + n) % items.length;
        if (laneName(items[k]).toLowerCase().startsWith(q)) { active = k; return true; }
      }
      return false;
    };
    list.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") active = clamp(active + (e.key === "ArrowDown" ? 1 : -1), 0, items.length - 1);
      else if (e.key === "Home" || e.key === "End") active = e.key === "Home" ? 0 : items.length - 1;
      else if ((e.key === " " || e.key === "Enter") && items.length) toggleLane(items[active], true);
      else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && items.length) typeAhead(e.key);
      else return;
      e.preventDefault();
      render();
    });
    list.addEventListener("click", e => {
      const li = e.target.closest("li[data-l]");
      if (!li) return;
      active = items.indexOf(+li.dataset.l);
      toggleLane(+li.dataset.l, true);
      render();
    });
    picker.addEventListener("click", e => {
      const b = e.target.closest("[data-pk]");
      if (!b || b.disabled) return;
      const prev = (state.lanes || []).slice(), k = b.dataset.pk;
      if (k === "all") setLanes(ids.slice());
      else if (k === "none") setLanes([]);
      else if (k === "chain") {
        const out = [];
        for (const i of pathOfSel()) if (!out.includes(laneOf[i])) out.push(laneOf[i]);
        setLanes(out.includes(WORLD) ? [WORLD, ...out.filter(l => l !== WORLD)] : out);
      } else if (k === "fail") setLanes(withWorld(AG.map((a, l) => l).filter(l => laneFailed[l])));
      toast("Lanes changed", [undoLanes(prev)]);
      render();
    });
    picker.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePicker(true); } });
  }
  pickBtn.addEventListener("click", openPicker);
  document.addEventListener("pointerdown", e => {
    if (picker && !picker.contains(e.target) && e.target !== pickBtn && !pickBtn.contains(e.target)) closePicker(false);
  });

  lhead.addEventListener("click", e => {
    const x = e.target.closest("[data-x]");
    if (x) { toggleLane(+x.dataset.x); return; }
    if (e.target.closest("#corner")) openGoto(e.target.closest("#corner"));
  });
  let drag = null;
  const dropLine = document.createElement("div");
  dropLine.className = "dropline";
  lhead.addEventListener("pointerdown", e => {
    const h = e.target.closest(".lh");
    if (!h || e.target.closest("button") || e.button !== 0) return;
    drag = { l: +h.dataset.l, x: e.clientX, on: false, el: h, id: e.pointerId };
  });
  lhead.addEventListener("pointermove", e => {
    const h = e.target.closest(".lh");
    peekLane(h ? +h.dataset.l : null);
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.on && Math.abs(e.clientX - drag.x) > 5) {
      drag.on = true;
      drag.el.classList.add("drag");
      lhead.setPointerCapture(e.pointerId);
      lanesIn.append(dropLine);
    }
    if (!drag.on) return;
    const at = dropIndex(e.clientX);
    const x = at < L.nC ? GEO.x[at] : GEO.contentW;
    dropLine.style.left = GEO.gw + x + "px";
    dropLine.style.height = Math.min(lanesIn.offsetHeight, lanesBox.scrollTop + lanesBox.clientHeight) + "px";
    drag.at = at;
  });
  const dropIndex = cx => {
    const x = cx - lanesBox.getBoundingClientRect().left + lanesBox.scrollLeft - GEO.gw;
    let at = 0;
    while (at < L.nC && GEO.x[at] + GEO.w[at] / 2 < x) at++;
    return at;
  };
  const endDrag = () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    d.el.classList.remove("drag");
    dropLine.remove();
    if (!d.on || d.at == null) return;
    const lanes = state.lanes.slice(), from = lanes.indexOf(d.l);
    let to = d.at;
    if (to > from) to--;
    if (to === from) return;
    lanes.splice(from, 1);
    lanes.splice(to, 0, d.l);
    setLanes(lanes);
    announce(`Moved ${laneName(d.l)}`);
  };
  lhead.addEventListener("pointerup", endDrag);
  lhead.addEventListener("pointercancel", endDrag);
  lhead.addEventListener("pointerleave", () => { if (!drag) peekLane(null); });
  function moveLane(dir) {
    if (state.cursor == null || !state.lanes) return;
    const lanes = state.lanes.slice(), from = lanes.indexOf(laneOf[state.cursor]), to = from + dir;
    if (from < 0 || to < 0 || to >= lanes.length) return;
    lanes.splice(from, 1);
    lanes.splice(to, 0, laneOf[state.cursor]);
    setLanes(lanes);
    pendingFocus = state.cursor;
    announce(`Moved ${laneName(laneOf[state.cursor])} to position ${to + 1}`);
  }

  let hovRec = -1, tipTimer = 0;
  plane.addEventListener("pointermove", e => {
    const el = e.target.closest(".rec[data-i]");
    setHov(el ? +el.dataset.i : -1, el);
  });
  plane.addEventListener("pointerleave", () => setHov(-1));
  function recConnectors(i) {
    const r = rowOf[i];
    if (r < 0 || !L) return [];
    const out = [];
    for (const id of L.tileCx[Math.floor(r / TR)]) {
      const k = L.cx.kind[id], a = L.cx.a[id];
      if (a === i || L.cx.b[id] === i) out.push([id, -1]);
      else if (k === K_COMB && L.combs.get(a).recs.includes(i)) out.push([id, i]);
    }
    return out;
  }
  function drawOverlay(list) {
    if (!list.length || !GEO) { ovl.innerHTML = ""; return; }
    let lo = Infinity, hi = -Infinity;
    for (const [id] of list) { lo = Math.min(lo, L.cx.lo[id]); hi = Math.max(hi, L.cx.hi[id]); }
    const y0 = (lo - 1) * ROW, H = (hi - lo + 4) * ROW, o = {};
    for (const [id, only] of list) {
      if (only >= 0) combPath(o, "ovl", "oh", L.cx.a[id], [only], y0);
      else cxGeom(o, id, y0, "ov");
    }
    const boxes = [];
    for (const [t, m] of mounted) if ((t + 1) * TILE > y0 && t * TILE < y0 + H) for (const v of m.boxes) boxes.push(v);
    ovl.setAttribute("width", GEO.width);
    ovl.setAttribute("height", H);
    ovl.style.top = y0 + "px";
    ovl.innerHTML = boxes.length
      ? knockout("ko-ov", GEO.width, H, boxes, y0) + `<g clip-path="url(#ko-ov)">${pathsOf(o)}</g>`
      : pathsOf(o);
  }
  // in Lanes every connector points down, so the tooltip opens above the row, on the side away from an arrow coming in
  function showRecTip(i, el) {
    tip.innerHTML = recTipHTML(i);
    tip.hidden = false;
    tip.classList.remove("act");
    const w = tip.offsetWidth, h = tip.offsetHeight, r = el.getBoundingClientRect(), gx = r.left + 12;
    const side = j => j >= 0 && rowOf[j] >= 0 && colOfRec(j) !== colOfRec(i) ? Math.sign(GEO.life[colOfRec(j)] - GEO.life[colOfRec(i)]) : 0;
    const above = r.top - 8 - h >= 8;
    const avoid = above ? (evOf[i] === E_RECV || evOf[i] === E_EVRECV ? side(causeIx[i]) : 0) : evOf[i] === E_SENT ? side(recvOf(i)) : 0;
    const x = avoid > 0 ? gx - 14 - w : gx + 14;
    tip.style.left = clamp(x, 8, Math.max(8, innerWidth - 8 - w)) + "px";
    tip.style.top = (above ? r.top - 8 - h : Math.min(r.bottom + 8, innerHeight - 8 - h)) + "px";
  }
  function setHov(i, el) {
    if (i === hovRec) return;
    if (hovRec >= 0) plane.querySelector(`.rec[data-i="${hovRec}"]`)?.classList.remove("hov");
    hovRec = i;
    clearTimeout(tipTimer);
    if (i < 0) {
      xHov.hidden = gHov.hidden = true;
      drawOverlay([]);
      hideTip();
      peek(null);
      updateFpill();
      return;
    }
    el = el || plane.querySelector(`.rec[data-i="${i}"]`);
    el?.classList.add("hov");
    const r = rowOf[i];
    if (r >= 0) {
      xHov.style.top = gHov.style.top = r * ROW + "px";
      xHov.hidden = gHov.hidden = false;
      gHov.innerHTML = `<div class="bl">${lonelyLabel(ktime(key[i]))}</div>`;
    }
    drawOverlay(recConnectors(i));
    peek(i);
    if (el) tipTimer = setTimeout(() => { if (el.isConnected) showRecTip(i, el); }, 120);
    updateFpill();
  }
  function updateFpill() {
    if (!GEO || GEO.labels) { fpill.hidden = true; return; }
    const fe = document.activeElement, fi = fe && fe.matches && fe.matches(".rec[data-i]:focus-visible") ? +fe.dataset.i : -1;
    const i = hovRec >= 0 ? hovRec : fi >= 0 ? fi : state.sel != null && rowOf[state.sel] >= 0 ? state.sel : -1;
    if (i < 0 || rowOf[i] < 0) { fpill.hidden = true; return; }
    fpill.innerHTML = labelHTML(i);
    fpill.hidden = false;
    const x = GEO.life[colOfRec(i)] + 10;
    fpill.style.top = rowOf[i] * ROW + 1 + "px";
    fpill.style.left = Math.min(x, GEO.width - 200) + "px";
  }
  function positionSelRow() {
    const s = state.sel;
    const r = s != null && L && vis[s] ? (rowOf[s] >= 0 ? rowOf[s] : -1) : -1;
    xSel.hidden = gSel.hidden = r < 0;
    if (r < 0) return;
    xSel.style.top = gSel.style.top = r * ROW + "px";
    gSel.innerHTML = `<div class="bl">${lonelyLabel(ktime(key[s]))}</div>`;
  }

  // fromRow: an earlier row that should come into view with the record when both fit (the step into its agent)
  function lanesReveal(i, center, fromRow) {
    const r = rowOf[i] >= 0 ? rowOf[i] : vrowOf[i];
    if (r < 0 || !GEO) return false;
    const vh = lanesBox.clientHeight - GEO.headH, top = lanesBox.scrollTop;
    const ya = (fromRow != null && fromRow >= 0 && fromRow < r && (r - fromRow + 2) * ROW <= vh ? fromRow : r) * ROW, yb = (r + 1) * ROW;
    if (ya < top || yb > top + vh) lanesBox.scrollTop = center ? (ya + yb - vh) / 2 : ya < top ? ya : yb - vh;
    const c = L.col[laneOf[i]], vw = lanesBox.clientWidth - GEO.gw, sl = lanesBox.scrollLeft;
    if (c >= 0) {
      const x0 = GEO.life[c] - 14, x1 = Math.min(GEO.x[c] + GEO.w[c], GEO.life[c] + 160, GEO.width);
      if (x0 < sl) lanesBox.scrollLeft = x0;
      else if (x1 > sl + vw) lanesBox.scrollLeft = Math.min(x0, x1 - vw);
    }
    return true;
  }

  const colList = c => L.colRecs.subarray(L.colStart[c], L.colStart[c + 1]);
  function rowBound(list, row) {
    let lo = 0, hi = list.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (rowOf[list[m]] < row) lo = m + 1;
      else hi = m;
    }
    return lo;
  }
  function nearestIn(list, row) {
    if (!list.length) return -1;
    const lo = rowBound(list, row);
    if (lo >= list.length) return list[list.length - 1];
    if (lo === 0) return list[0];
    return row - rowOf[list[lo - 1]] < rowOf[list[lo]] - row ? list[lo - 1] : list[lo];
  }
  function lanesCursorStart() {
    const c = state.cursor;
    if (c != null && rowOf[c] >= 0) return c;
    const r = Math.floor(lanesBox.scrollTop / ROW) + 1;
    for (let k = 0; k < L.nC; k++) {
      const n = nearestIn(colList(k), r);
      if (n >= 0) return n;
    }
    return -1;
  }
  function lanesMove(k) {
    if (!L || !L.placed) return;
    let i = lanesCursorStart();
    if (i < 0) return;
    if (i === state.cursor || rowOf[state.cursor] >= 0) {
      const c = colOfRec(i), list = colList(c), at = lowerBound(list, i);
      const page = Math.max(1, Math.floor((lanesBox.clientHeight - GEO.headH) / ROW) - 2);
      if (k === "ArrowUp") i = list[Math.max(0, at - 1)];
      else if (k === "ArrowDown") i = list[Math.min(list.length - 1, at + 1)];
      else if (k === "Home") i = list[0];
      else if (k === "End") i = list[list.length - 1];
      else if (k === "PageUp" || k === "PageDown") i = nearestIn(list, rowOf[i] + (k === "PageUp" ? -page : page));
      else if (k === "ArrowLeft" || k === "ArrowRight") {
        const d = k === "ArrowLeft" ? -1 : 1, row = rowOf[i];
        for (let c2 = c + d; c2 >= 0 && c2 < L.nC; c2 += d) {
          const l2 = colList(c2);
          if (!l2.length) continue;
          const lo = rowBound(l2, row);
          const after = lo < l2.length ? l2[lo] : -1, before = lo > 0 ? l2[lo - 1] : -1;
          i = after >= 0 && (before < 0 || rowOf[after] - row <= row - rowOf[before]) ? after : before;
          break;
        }
      }
    }
    lanesCursor(i);
  }
  function lanesCursor(i) {
    const old = state.cursor;
    state.cursor = i;
    lanesReveal(i, false);
    renderLanes(false);
    if (old != null) plane.querySelector(`.rec[data-i="${old}"]`)?.setAttribute("tabindex", "-1");
    const el = plane.querySelector(`.rec[data-i="${i}"]`);
    if (el) {
      el.tabIndex = 0;
      el.focus({ preventScroll: true });
      showRecTip(i, el);
    }
  }
  plane.addEventListener("click", e => {
    const el = e.target.closest(".rec[data-i]");
    if (!el) return;
    state.cursor = +el.dataset.i;
    select(+el.dataset.i);
  });
  plane.addEventListener("focusin", e => {
    const el = e.target.closest(".rec[data-i]");
    if (el && el.matches(":focus-visible")) showRecTip(+el.dataset.i, el);
    updateFpill();
  });
  plane.addEventListener("focusout", () => { hideTip(); requestAnimationFrame(updateFpill); });
  lanesBox.addEventListener("keydown", e => {
    if (e.target.closest("button, input")) return;
    if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); moveLane(e.key === "ArrowLeft" ? -1 : 1); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(e.key)) {
      e.preventDefault();
      lanesMove(e.key);
    } else if ((e.key === "Enter" || e.key === " ") && state.cursor != null && rowOf[state.cursor] >= 0) {
      e.preventDefault();
      select(state.cursor);
    }
  });
  lanesBox.addEventListener("scroll", () => { if (state.view === "lanes") scrollFrame(); }, { passive: true });
  lanesBox.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const a = b.dataset.act;
    if (a === "clear-filters") clearFilters();
    else if (a === "follow") followChain();
    else if (a === "pick") openPicker();
    else if (a === "lane") toggleLane(+b.dataset.l);
  });

  function renderLanesEmpty() {
    const el = $("lempty");
    const lanes = state.lanes || [];
    const pick = `<button class="tool" data-act="pick">Choose lanes…</button>`;
    if (!lanes.length) {
      const sugg = hubOrder(AG.map((a, l) => l)).slice(0, 5);
      el.innerHTML = `<h3>Choose agents to follow side by side</h3><p>Each followed agent gets a lane; records line up by time.</p>`
        + `<div class="acts">${sugg.map(l => `<button class="tool" data-act="lane" data-l="${l}">${esc(laneName(l))}</button>`).join("")}</div>`
        + `<div class="acts"><button class="tool" data-act="follow" ${state.sel == null ? "disabled" : ""}>Follow chain</button>${pick}</div>`;
      el.hidden = false;
    } else if (L && !L.placed) {
      el.innerHTML = `<h3>No records in these lanes match the filters</h3><p>Change the search, clear the filters or follow other agents.</p>`
        + `<div class="acts"><button class="tool" data-act="clear-filters">Clear filters</button>${pick}</div>`;
      el.hidden = false;
    } else el.hidden = true;
    let hint = $("lanes-one");
    if (A === 1 && !hint) {
      hint = document.createElement("div");
      hint.id = "lanes-one";
      hint.className = "hintbar";
      hint.textContent = "Lanes compare agents side by side; this trace has one agent.";
      $("view").append(hint);
    }
    if (hint) hint.hidden = state.view !== "lanes";
  }
