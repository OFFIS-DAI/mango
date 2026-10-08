  const recBox = $("records-box"), rowsEl = $("rows"), tableEl = $("table");
  const SIMCOL = CLOCK === "sim";
  const RH = 30, OVER = 20, CH = 7.5;
  const MULTIDAY = ABS && N > 0 && dayOf(TMIN) !== dayOf(TMAX);
  const COLS = (() => {
    const len = { lvl: 6, agent: 6, event: 6, id: 6, cause: 6 };
    const fit = (k, v) => {
      if (v == null) return;
      const n = String(v).length;
      if (n > len[k]) len[k] = n;
    };
    for (let i = 0; i < N; i++) {
      const r = RECORDS[i];
      fit("agent", r.agent);
      fit("event", r.event);
      fit("id", r.id);
      fit("cause", r.cause);
    }
    for (const l of LV) fit("lvl", l);
    const cap = { lvl: 8, agent: 22, event: 26, id: 18, cause: 18 };
    // the event cell starts with a 10 px category bar
    const ch = (k, extra = 0) => ({ w: `calc(${Math.min(len[k], cap[k])}ch + ${20 + extra}px)`, px: Math.min(len[k], cap[k]) * CH + 20 + extra });
    let stLen = 5;
    for (let k = 0; k < bandTimesAll.length; k++) {
      const t = bandTimesAll[k];
      stLen = Math.max(stLen, timeParts(t, finer(t, k ? bandTimesAll[k - 1] : null)).join("").length);
    }
    const stPx = Math.max(78, Math.ceil((stLen + 1) * CH + 20 + (MULTIDAY ? 44 : 0)));
    return {
      st: { w: `${stPx}px`, px: stPx, h: `<button id="th-time" title="Go to a time (t)">Sim time</button>` },
      ms: { w: "60px", px: 60, h: "+ms" },
      lvl: { ...ch("lvl"), h: "Level" },
      agent: { ...ch("agent"), h: "Agent" },
      event: { ...ch("event", 10), h: "Event" },
      id: { ...ch("id"), h: "id" },
      cause: { ...ch("cause"), h: "cause" },
      details: { w: "auto", px: 160, h: "Details" },
    };
  })();
  // +ms is constant within a simulated instant, so it is the first column to go when space is short
  function pickCols() {
    const time = SIMCOL ? ["st"] : ["ms"];
    if (isPhone()) return time.concat(["event", "agent", "lvl", "id", "cause", "details"]);
    const full = time.concat(SIMCOL ? ["ms"] : [], ["lvl", "agent", "event", "id", "cause", "details"]);
    const need = full.reduce((s, k) => s + COLS[k].px, 0);
    return SIMCOL && need > (recBox.clientWidth || 1200) ? full.filter(k => k !== "ms") : full;
  }
  let cols = [], colKey = "";
  function applyCols() {
    const next = pickCols(), k = next.join();
    if (k === colKey) return false;
    cols = next;
    colKey = k;
    $("cols").innerHTML = cols.map(c => `<col style="width:${COLS[c].w}">`).join("");
    $("thead").innerHTML = cols.map(c => `<th scope="col"${c === "ms" ? ' class="t"' : ""}>${COLS[c].h}</th>`).join("");
    tableEl.style.minWidth = Math.round(cols.reduce((s, c) => s + COLS[c].px, 0)) + "px";
    return true;
  }
  const details = r => {
    let out = "";
    for (const k in r) {
      if (CORE.has(k) || k.startsWith("__") || (SIMCOL && k === "sim_time")) continue;
      out += (out ? "  " : "") + k + "=" + fmt(r[k]);
    }
    return out;
  };
  const ref = v => v == null ? "" : `<span class="ref">${esc(v)}</span>`;
  let dimOn = false;
  const relClass = i => { const v = rel[i]; return v === 3 ? "sel" : v === 1 ? "anc" : v === 2 ? "desc" : dimOn ? "dim" : ""; };
  const visPosAtOrAfter = i => lowerBound(visList, i);
  const visPos = i => { const p = visPosAtOrAfter(i); return p < visList.length && visList[p] === i ? p : -1; };
  let tWin = [-1, -1];
  const theadH = () => tableEl.tHead.offsetHeight || 32;

  function simCell(i, pos) {
    const t = ktime(key[i]), rep = pos > 0 && key[visList[pos - 1]] === key[i];
    const text = rep ? esc(timeLabel(t)) : timeHTML(t, pos > 0 ? ktime(key[visList[pos - 1]]) : null);
    const title = inferred[i] ? ' title="no sim_time; placed by its effects/neighbours"' : "";
    return `<td class="st${rep ? " rep" : ""}"${title}>${inferred[i] ? "≈" : ""}${text}</td>`;
  }
  function cellHTML(c, i, pos) {
    const r = RECORDS[i];
    switch (c) {
      case "st": return simCell(i, pos);
      case "ms": {
        const ms = (parseTs(r.timestamp) - W0) * 1000;
        return `<td class="t">${isNaN(ms) ? "" : ms.toFixed(1)}</td>`;
      }
      case "lvl": return `<td class="lvl" style="--k:${levelColor(LV[levelOf[i]])}">${esc(LV[levelOf[i]])}</td>`;
      case "agent": return `<td class="agent">${esc(r.agent ?? "")}</td>`;
      case "event": return `<td><span class="ev" style="--k:var(--c-${CATS[kindOf[i]]})">${esc(r.event)}</span></td>`;
      case "id": return `<td>${ref(r.id)}</td>`;
      case "cause": return `<td>${ref(r.cause)}</td>`;
      default: {
        const det = esc(details(r));
        return `<td class="details" title="${det}">${det}</td>`;
      }
    }
  }
  function rowHTML(i, pos) {
    const cls = (failOf[i] ? "fail " : "") + relClass(i);
    const attrs = (agentOf[i] >= 0 ? ` data-a="${agentOf[i]}"` : "") + (edgeOfRec[i] >= 0 ? ` data-e="${edgeOfRec[i]}"` : "")
      + ` aria-rowindex="${pos + 2}"${i === state.sel ? ' aria-current="true"' : ""} tabindex="${i === state.cursor ? 0 : -1}"`;
    return `<tr data-i="${i}"${attrs} class="${cls}">` + cols.map(c => cellHTML(c, i, pos)).join("") + "</tr>";
  }

  function renderTable(force) {
    if (applyCols()) force = true;
    dimOn = selShown();
    const n = visList.length;
    const top = Math.max(0, recBox.scrollTop - theadH());
    const first = Math.max(0, Math.min(n, Math.floor(top / RH) - OVER));
    const last = Math.min(n, first + Math.ceil(recBox.clientHeight / RH) + 2 * OVER);
    if (!force && first === tWin[0] && last === tWin[1]) return;
    tWin = [first, last];
    const spacer = h => `<tr class="spacer" aria-hidden="true"><td colspan="${cols.length}" style="height:${h}px"></td></tr>`;
    const html = [first ? spacer(first * RH) : ""];
    for (let p = first; p < last; p++) html.push(rowHTML(visList[p], p));
    if (last < n) html.push(spacer((n - last) * RH));
    const hadFocus = rowsEl.contains(document.activeElement);
    rowsEl.innerHTML = html.join("");
    tableEl.setAttribute("aria-rowcount", n + 1);
    const empty = $("empty");
    empty.hidden = n > 0;
    if (!n) {
      empty.innerHTML = N
        ? `<h3>No records match the filters</h3><p>Change the search or clear the filters to see records again.</p>`
          + `<div class="acts"><button class="tool" data-act="clear-filters">Clear filters</button></div>`
        : `<h3>No records in this trace</h3>`;
    }
    const cur = state.cursor != null ? rowsEl.querySelector(`tr[data-i="${state.cursor}"]`) : null;
    recBox.tabIndex = cur ? -1 : 0;
    if (hadFocus) (cur || recBox).focus({ preventScroll: true });
    liveChanged();
  }
  recBox.addEventListener("scroll", () => { if (state.view === "records") scrollFrame(); }, { passive: true });

  function tableRowsOnScreen() {
    const top = recBox.scrollTop;
    // a row counts once more than a sliver of it shows
    const a = Math.max(0, Math.floor((top + 2) / RH)), b = Math.min(visList.length, Math.ceil((top + recBox.clientHeight - theadH() - 2) / RH));
    return [a, b];
  }
  function tableScrollTo(pos, mode) {
    const h = recBox.clientHeight - theadH(), y = pos * RH;
    const cur = recBox.scrollTop;
    if (mode === "center") { if (y < cur || y + RH > cur + h) recBox.scrollTop = y - (h - RH) / 2; }
    else if (y < cur) recBox.scrollTop = y;
    else if (y + RH > cur + h) recBox.scrollTop = y + RH - h;
  }

  rowsEl.addEventListener("click", e => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    state.cursor = +tr.dataset.i;
    select(+tr.dataset.i);
  });
  rowsEl.addEventListener("pointerover", e => {
    const tr = e.target.closest("tr[data-i]");
    peek(tr ? +tr.dataset.i : null);
  });
  rowsEl.addEventListener("pointerleave", () => peek(null));
  recBox.addEventListener("click", e => { if (e.target.closest("[data-act=clear-filters]")) clearFilters(); });
  $("thead").addEventListener("click", e => { if (e.target.closest("#th-time")) openGoto(e.target.closest("#th-time")); });

  function moveTableCursor(k) {
    const n = visList.length;
    if (!n) return;
    let p = state.cursor != null ? visPos(state.cursor) : -1;
    if (p < 0) p = state.cursor != null ? Math.min(n - 1, visPosAtOrAfter(state.cursor)) : Math.floor(recBox.scrollTop / RH);
    else {
      const page = Math.max(1, Math.floor((recBox.clientHeight - theadH()) / RH) - 1);
      if (k === "ArrowUp") p--;
      else if (k === "ArrowDown") p++;
      else if (k === "Home") p = 0;
      else if (k === "End") p = n - 1;
      else p += k === "PageUp" ? -page : page;
    }
    p = clamp(p, 0, n - 1);
    state.cursor = visList[p];
    tableScrollTo(p);
    renderTable(true);
    rowsEl.querySelector(`tr[data-i="${state.cursor}"]`)?.focus({ preventScroll: true });
  }
  recBox.addEventListener("keydown", e => {
    if (e.target.closest("input, button, select")) return;
    if (["ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(e.key) && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      moveTableCursor(e.key);
    } else if ((e.key === "Enter" || e.key === " ") && state.cursor != null && vis[state.cursor]) {
      e.preventDefault();
      select(state.cursor);
    }
  });
