  const tip = $("tip");
  let tipAct = null;
  function showTip(html, rect, near, interactive) {
    tip.innerHTML = html;
    tip.hidden = false;
    tip.classList.toggle("act", !!interactive && !!tipAct);
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = rect.left + (near ? 12 : 0), y = rect.bottom + (near ? 12 : 8);
    if (x + w > innerWidth - 8) x = Math.max(8, innerWidth - 8 - w);
    if (y + h > innerHeight - 8) y = Math.max(8, rect.top - 8 - h);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { tip.hidden = true; tipAct = null; tip.classList.remove("act"); }
  tip.addEventListener("click", e => { const b = e.target.closest("[data-tact]"); if (b && tipAct) tipAct(b.dataset.tact); });
  function recTipHTML(i) {
    const r = RECORDS[i];
    const meta = [laneName(laneOf[i]), recTime(i), r.id != null ? r.id : r.cause != null ? "in " + r.cause : null]
      .filter(v => v != null).map(v => esc(v)).join(" · ");
    const kv = [];
    for (const k in r) {
      if (kv.length >= 8 || CORE.has(k) || k.startsWith("sim_") || k.startsWith("__")) continue;
      const c = r[k];
      let v = k === "receiver" ? shortAid(c) : k === "content" && c && typeof c === "object" ? (c.repr ?? c.type ?? fmt(c)) : fmt(c);
      v = String(v);
      if (v.length > 80) v = v.slice(0, 79) + "…";
      kv.push(`<div><span>${esc(k)}=</span>${esc(v)}</div>`);
    }
    let status = "";
    if (evOf[i] === E_SENT) status = sendStatus[i] === S_LOST ? ` · <span class="lo">lost</span>` : sendStatus[i] === S_FLIGHT ? " · in flight" : "";
    const lvl = LV[levelOf[i]];
    if (failOf[i]) status += ` · <span class="lo">${esc(lvl === "error" || lvl === "critical" ? lvl : "failed")}</span>`;
    return `<div class="t1">${esc(r.event)}</div><div class="t2">${meta}${status}${inferred[i] ? " · time inferred" : ""}</div>`
      + (kv.length ? `<div class="kvs">${kv.join("")}</div>` : "");
  }

  let gotoEl = null, gotoAnchor = null;
  function closeGoto(refocus) {
    if (!gotoEl) return;
    gotoEl.remove();
    gotoEl = null;
    if (refocus && gotoAnchor && gotoAnchor.isConnected) gotoAnchor.focus();
  }
  function parseGoto(s) {
    s = s.trim().toLowerCase();
    let m = /^(\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?$/.exec(s);
    if (m) {
      const tod = +m[1] * 3600 + +m[2] * 60 + (m[3] ? +m[3] : 0) + (m[4] ? +m[4] : 0);
      if (!ABS) return T0 + tod;
      let t = Math.floor(T0 / 86400) * 86400 + tod;
      if (t < T0 - 1e-9) t += 86400;
      return t;
    }
    m = /^\+?\s*((?:\d+(?:\.\d+)?\s*(?:d|h|min|m|ms|s|us|µs)\s*)+)$/.exec(s);
    if (!m) return null;
    const unit = { d: 86400, h: 3600, min: 60, m: 60, s: 1, ms: 1e-3, us: 1e-6, "µs": 1e-6 };
    let t = 0;
    for (const p of m[1].matchAll(/(\d+(?:\.\d+)?)\s*(d|h|min|ms|m|s|us|µs)/g)) t += +p[1] * unit[p[2]];
    return T0 + t;
  }
  function gotoTime(t) {
    if (state.view === "lanes" && L) {
      let b = L.bandKey.findIndex(k => ktime(k) >= t - 1e-9);
      if (b < 0) b = L.bandKey.length - 1;
      if (b < 0) return false;
      lanesBox.scrollTop = L.bandRow[b] * ROW;
      renderLanes(false);
      const el = gutterEl.querySelector(`.bl[data-b="${b}"]`);
      if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1300); }
      announce(`Went to ${timeLabel(ktime(L.bandKey[b]))}`);
      return true;
    }
    let p = 0;
    while (p < visList.length && ktime(key[visList[p]]) < t - 1e-9) p++;
    if (p >= visList.length) p = visList.length - 1;
    if (p < 0) return false;
    recBox.scrollTop = p * RH;
    renderTable(false);
    const td = rowsEl.querySelector(`tr[data-i="${visList[p]}"] td`);
    if (td && td.animate && motion()) td.animate([{ background: "var(--accent-soft)" }, { background: "transparent" }], { duration: 1200 });
    announce(`Went to ${timeLabel(ktime(key[visList[p]]))}`);
    return true;
  }
  function openGoto(anchor) {
    if (gotoEl) { closeGoto(true); return; }
    gotoAnchor = anchor || document.activeElement;
    gotoEl = document.createElement("div");
    gotoEl.className = "pop goto";
    gotoEl.setAttribute("role", "dialog");
    gotoEl.setAttribute("aria-label", "Go to time");
    const ex = ABS ? timeLabel(bandTimesAll[Math.min(bandTimesAll.length - 1, bandTimesAll.length >> 1)] || T0) : "+1h";
    const help = ABS ? "A time of day in UTC (HH:MM[:SS]) or a duration from the first record (+1h06m, +3720s)."
      : "A duration from the first record: +1h06m, +3720s or H:MM.";
    gotoEl.innerHTML = `<h3>Go to time</h3><input type="text" id="goto-q" placeholder="${esc(ex)} or +1h06m" aria-label="Time" autocomplete="off">`
      + `<p>${help}</p><p class="err" id="goto-err" role="alert"></p>`;
    document.body.append(gotoEl);
    const r = (gotoAnchor && gotoAnchor.getBoundingClientRect) ? gotoAnchor.getBoundingClientRect() : { left: 20, bottom: 80 };
    gotoEl.style.left = clamp(r.left, 8, innerWidth - Math.min(320, innerWidth - 16) - 8) + "px";
    gotoEl.style.top = Math.min(r.bottom + 6, innerHeight - 160) + "px";
    const inp = gotoEl.querySelector("input");
    inp.focus();
    inp.addEventListener("keydown", e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeGoto(true); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const t = parseGoto(inp.value);
        if (t == null) { gotoEl.querySelector("#goto-err").textContent = "Not a time: use 07:06 or +1h06m"; return; }
        closeGoto(false);
        gotoTime(t);
        focusView();
      }
    });
  }
  document.addEventListener("pointerdown", e => { if (gotoEl && !gotoEl.contains(e.target)) closeGoto(false); });

  function focusView() {
    if (state.view === "lanes") {
      const el = state.cursor != null ? plane.querySelector(`.rec[data-i="${state.cursor}"]`) : null;
      (el || lanesBox).focus({ preventScroll: true });
    } else {
      const el = state.cursor != null ? rowsEl.querySelector(`tr[data-i="${state.cursor}"]`) : null;
      (el || recBox).focus({ preventScroll: true });
    }
  }
  function goCause() {
    if (state.sel == null) return;
    const c = causeIx[state.sel];
    if (c >= 0 && c !== state.sel) select(c, { reveal: true, leftOf: true });
  }
  function goEffect() {
    const s = state.sel;
    if (s == null) return;
    let t = -1;
    if (evOf[s] === E_SENT) t = recvOf(s);
    else if (evOf[s] === E_EMIT && idOf[s] >= 0) eachEffect(idOf[s], j => { if (t < 0 && (evOf[j] === E_EVRECV || evOf[j] === E_ROLE)) t = j; });
    if (t < 0) t = descList.find(d => d > s) ?? descList[0] ?? -1;
    if (t >= 0) select(t, { reveal: true });
  }
  const keysDlg = $("keys");
  $("keys-close").addEventListener("click", () => keysDlg.close());
  keysDlg.addEventListener("click", e => { if (e.target === keysDlg) keysDlg.close(); });
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented) return;
    const t = e.target, typing = t.closest && t.closest("input, select, textarea");
    if (e.key === "Escape") {
      if (keysDlg.open) return;
      if (gotoEl) { closeGoto(true); return; }
      if (picker) { closePicker(true); return; }
      if (!tip.hidden) { hideTip(); return; }
      if (typing) return;
      if (state.sel != null) { select(null); announce("Selection cleared"); }
      return;
    }
    // letters inside the lane picker, a popover or the touch tooltip belong to it, not to the view behind
    if (typing || e.ctrlKey || e.metaKey || e.altKey || keysDlg.open || (t.closest && t.closest(".pop, .tip, [role=listbox]"))) return;
    const k = e.key;
    const lanes = state.view === "lanes";
    let done = true;
    if (k === "/") { q.focus(); q.select(); }
    else if (k === "v") setView(lanes ? "records" : "lanes");
    else if (k === "e" || k === "E") nextFail(k === "E");
    else if (k === "[") goCause();
    else if (k === "]") goEffect();
    else if (k === "f") followChain();
    else if (k === "t") openGoto(lanes ? $("corner") : $("th-time") || $("tab-records"));
    else if (k === "g") focusMap();
    else if (k === "G") toggleWide();
    else if (k === "?") keysDlg.showModal();
    else if (lanes && k === "a") openPicker();
    else if (lanes && k === "o") setOrder(state.order === "file" ? "packed" : "file");
    else if (lanes && k === "z") setFit(!state.fit);
    else if (lanes && k === "x") { if (state.cursor != null && state.lanes.includes(laneOf[state.cursor])) toggleLane(laneOf[state.cursor]); }
    else done = false;
    if (done) e.preventDefault();
  });
