  const wallSpan = N ? Date.parse(RECORDS[N - 1].timestamp) - Date.parse(RECORDS[0].timestamp) : NaN;
  const simSpan = CLOCK === "sim" ? TMAX - TMIN : 0;
  const showSim = CLOCK === "sim" && simSpan > 0 && simSpan > 10 * wallSpan / 1000;
  $("stats").innerHTML = [[N, "records"], [A, "agents"], [nRecv, "messages"], [nEvRecv, "events"], [nSched, "tasks"], [nHandler, "handler calls"]]
    .map(([n, l]) => `<span><b>${nf(n)}</b> ${l}</span>`).join("")
    + (isNaN(wallSpan) ? "" : `<span><b>${nf(Math.round(wallSpan))}</b> ms</span>`)
    + (showSim ? `<span><b>${esc(dur(simSpan).slice(1))}</b> simulated</span>` : "")
    + `<button id="fails" ${FAILS.length ? "" : "disabled"} title="Select the next failure or error (e, Shift: previous)"></button>`;
  const failsBtn = $("fails");
  const lowerBound = (arr, v) => {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (arr[m] < v) lo = m + 1;
      else hi = m;
    }
    return lo;
  };
  const failPos = i => lowerBound(FAILS, i);
  function updateFailsButton() {
    const s = state.sel, k = s != null && failOf[s] ? failPos(s) : -1;
    failsBtn.innerHTML = k >= 0
      ? `error <b>${nf(k + 1)}</b> of ${nf(FAILS.length)}`
      : `<b>${nf(FAILS.length)}</b> ${FAILS.length === 1 ? "error" : "errors"}`;
  }
  function nextFail(back) {
    if (!FAILS.length) return;
    const s = state.sel;
    let k;
    if (s == null) k = back ? FAILS.length - 1 : 0;
    else if (back) { k = failPos(s) - 1; if (k < 0) k = FAILS.length - 1; }
    else { k = failPos(s + 1); if (k >= FAILS.length) k = 0; }
    select(FAILS[k], { reveal: true });
  }
  failsBtn.addEventListener("click", e => {
    if (e.shiftKey) nextFail(true);
    else if (state.sel != null && failOf[state.sel]) nextFail(false);
    else select(FAILS[0], { reveal: true });
  });

  const agentSel = $("agent");
  const cut32 = a => { a = String(a); return a.length > 32 ? a.slice(0, 31) + "…" : a; };
  agentSel.innerHTML = `<option value="">All</option>` + AG.map(a => `<option value="${esc(a)}" title="${esc(a)}">${esc(cut32(a))}</option>`).join("");
  agentSel.addEventListener("change", () => setFilter({ agent: agentSel.value }));

  const presentCats = CATS.filter((c, k) => catCount[k] > 0);
  const catsEl = $("cats");
  catsEl.innerHTML = presentCats.map(c =>
    `<button class="chip" style="--k: var(--c-${c})" data-cat="${c}" aria-pressed="${state.cats.has(c)}">`
    + `<span class="dot"></span>${CAT_LABEL[c]} <span>${nf(catCount[CATS.indexOf(c)])}</span></button>`
  ).join("");
  catsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    const c = b.dataset.cat, cats = new Set(state.cats);
    cats.has(c) ? cats.delete(c) : cats.add(c);
    b.setAttribute("aria-pressed", cats.has(c));
    store.set("mango-trace-cats", JSON.stringify([...cats]));
    setFilter({ cats });
  });

  const levelsEl = $("levels");
  levelsEl.innerHTML = `<label>Level</label>` + presentLevels.map(l =>
    `<button class="chip" style="--k: ${levelColor(l)}" data-level="${esc(l)}" aria-pressed="true">`
    + `<span class="dot"></span>${esc(l)} <span>${nf(levelCount[lvIx.get(l)])}</span></button>`
  ).join("");
  levelsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-level]");
    if (!b) return;
    const l = b.dataset.level, levels = new Set(state.levels);
    levels.has(l) ? levels.delete(l) : levels.add(l);
    b.setAttribute("aria-pressed", levels.has(l));
    setFilter({ levels });
  });

  const q = $("q");
  let qTimer = 0;
  q.addEventListener("focus", prebuildText);
  q.addEventListener("input", () => {
    clearTimeout(qTimer);
    if (N > 20000) qTimer = setTimeout(() => setFilter({ q: q.value }), 150);
    else setFilter({ q: q.value });
  });
  $("only-related").addEventListener("change", e => setFilter({ onlyRelated: e.target.checked }));

  // field names come from the data, so new fields are filterable without changes here
  const fieldKeys = [...keySet].filter(k => !k.startsWith("__") && k !== "timestamp").sort();
  const fieldsOpen = !isPhone();
  $("fields").innerHTML = `<button class="fdis" id="fields-t" aria-expanded="${fieldsOpen}" aria-controls="fields-l">`
    + `<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M3.2 1.5 6.8 5 3.2 8.5" fill="none" stroke="currentColor"`
    + ` stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>Fields <span>${nf(fieldKeys.length)}</span></button>`
    + `<span id="fields-l"${fieldsOpen ? "" : " hidden"}>${fieldKeys.map(k => `<button data-key="${esc(k)}">${esc(k)}</button>`).join(" ")}</span>`;
  $("fields").addEventListener("click", e => {
    const t = e.target.closest("#fields-t");
    if (t) {
      const open = t.getAttribute("aria-expanded") !== "true";
      t.setAttribute("aria-expanded", open);
      $("fields-l").hidden = !open;
      return;
    }
    const b = e.target.closest("[data-key]");
    if (!b) return;
    q.value = (q.value.trim() ? q.value.trim() + " " : "") + b.dataset.key + "=";
    q.focus();
  });

  const linkChip = $("linkchip");
  function renderLinkChip() {
    const lk = state.link;
    linkChip.hidden = !lk;
    if (!lk) { linkChip.innerHTML = ""; return; }
    const E = EDGES[lk.e], rev = reverseEdge(lk.e);
    const bothTitle = rev < 0 ? "No messages the other way" : "Also show the reverse direction";
    linkChip.innerHTML = `<span class="lc-t" title="Connection filter">${esc(E.from)} ${lk.both ? "⇄" : "→"} ${esc(E.to)}</span>`
      + `<button data-act="both" aria-pressed="${lk.both}" ${rev < 0 ? "disabled" : ""} title="${bothTitle}" aria-label="Both directions">⇄</button>`
      + `<button data-act="clear" title="Clear the connection filter" aria-label="Clear the connection filter">✕</button>`;
  }
  linkChip.addEventListener("click", e => {
    const b = e.target.closest("button[data-act]");
    if (!b || b.disabled) return;
    if (b.dataset.act === "both") setLink(state.link.e, !state.link.both);
    else setLink(null);
  });

  const F_FILTER = F_VIS | F_TABLE | F_LAYOUT | F_MAP | F_RAIL | F_PANEL | F_HEAD;
  function setFilter(patch) {
    Object.assign(state, patch);
    invalidate(F_FILTER);
  }
  function clearFilters() {
    q.value = "";
    agentSel.value = "";
    $("only-related").checked = false;
    state.cats = new Set(CATS);
    store.set("mango-trace-cats", JSON.stringify(CATS));
    catsEl.querySelectorAll("[data-cat]").forEach(b => b.setAttribute("aria-pressed", "true"));
    levelsEl.querySelectorAll("[data-level]").forEach(b => b.setAttribute("aria-pressed", "true"));
    setLink(null);
    setFilter({ q: "", agent: "", onlyRelated: false, levels: new Set(presentLevels) });
  }
