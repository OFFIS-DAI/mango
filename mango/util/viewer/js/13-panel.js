  const panel = $("panel");
  const colorOf = i => `var(--c-${CATS[kindOf[i]]})`;
  const tOf = i => ktime(key[i]);
  function itemHTML(i, here, hop, dt) {
    const r = RECORDS[i];
    const badge = hop ? `<span class="hop" title="Step ${hop}: the chain reaches ${esc(laneName(laneOf[i]))}">${hop}</span>` : "";
    return `<li class="${here ? "here" : ""}" style="--k:${failOf[i] ? "var(--fail)" : colorOf(i)}">${badge}`
      + `<button data-go="${i}">${esc(r.event)}${r.id != null ? ` <span>${esc(r.id)}</span>` : ""}<br>`
      + `<span class="agent">${esc(laneName(laneOf[i]))}${dt ? ` · ${esc(dt)}` : ""}</span></button></li>`;
  }
  function lostNote(s, to) {
    const later = (lastRecvOfAgent.get(to) ?? -1) > s;
    if (later) return `<p class="note lost"><b>Lost:</b> no receipt, although <b>${esc(to)}</b> received later messages.</p>`;
    if (!aIx.has(to)) return `<p class="note lost"><b>Lost:</b> no receipt; <b>${esc(to)}</b> wrote no records in this trace.</p>`;
    const before = TMAX - tt[s] > 0 ? ` (sent ${esc(dur(TMAX - tt[s]).slice(1))} before the trace ended)` : "";
    return `<p class="note lost"><b>Lost:</b> no receipt; <b>${esc(to)}</b> received nothing afterwards${before}.</p>`;
  }
  function messageNote(s) {
    const r = RECORDS[s];
    if (evOf[s] === E_SENT) {
      const rv = recvOf(s);
      if (rv >= 0) {
        const d = tOf(rv) - tOf(s);
        return `<p class="note">Delivered: <button data-go="${rv}">message.received</button> in <b>${esc(laneName(laneOf[rv]))}</b>`
          + ` at ${esc(recTime(rv))}${d > 0 ? ` (${esc(dur(d))})` : ""}</p>`;
      }
      const to = receiverName(s);
      if (sendStatus[s] === S_FLIGHT) return `<p class="note">In flight when the trace ended: no receipt by <b>${esc(to)}</b>.</p>`;
      if (sendStatus[s] === S_LOST) return lostNote(s, to);
      return `<p class="note">No receipt recorded${to !== "?" ? ` by <b>${esc(to)}</b>` : ""}.</p>`;
    }
    if (evOf[s] === E_RECV) {
      const p = causeIx[s];
      if (p >= 0 && evOf[p] === E_SENT) {
        const d = tOf(s) - tOf(p);
        return `<p class="note">Sent by <b>${esc(laneName(laneOf[p]))}</b> at <button data-go="${p}">${esc(recTime(p))}</button>`
          + `${d > 0 ? ` (${esc(dur(d))})` : ""}</p>`;
      }
      if (r.sender != null) return `<p class="note">Sent by <b>${esc(shortAid(r.sender))}</b>; the send is not in this trace.</p>`;
    }
    return "";
  }
  function connectionHTML() {
    const lk = state.link, list = [lk.e], rev = reverseEdge(lk.e);
    if (lk.both && rev >= 0) list.push(rev);
    let h = `<div class="conn"><h3>Connection</h3><h2>${esc(EDGES[lk.e].from)} ${lk.both ? "⇄" : "→"} ${esc(EDGES[lk.e].to)}</h2>`
      + `<div class="pacts"><button class="tool" data-act="both" aria-pressed="${lk.both}" ${rev < 0 ? "disabled" : ""}>⇄ both ways</button>`
      + `<button class="tool" data-act="clear-link">✕ clear</button></div></div>`;
    for (const e of list) {
      const E = EDGES[e], lost = E.lost - E.inFlight, max = Math.max(1, ...E.types.values());
      const lostRecs = E.sends.filter(s => sendStatus[s] === S_LOST), fl = E.sends.filter(s => sendStatus[s] === S_FLIGHT);
      const bars = [...E.types].sort((a, b) => b[1] - a[1])
        .map(([t, c]) => `<span>${esc(t ?? "?")}</span><span class="bar" style="width:${Math.max(4, 100 * c / max)}%"></span><span>${nf(c)}</span>`).join("");
      const lostBtn = s => `<button data-go="${s}">${esc(RECORDS[s].id)} · ${esc(typeOf(s) || "message")} · ${esc(recTime(s))}</button>`;
      const flBtn = s => `<button class="fl" data-go="${s}">${esc(RECORDS[s].id)} · ${esc(typeOf(s) || "message")}</button>`;
      h += `<div class="conn">${list.length > 1 ? `<h3>${esc(E.from)} → ${esc(E.to)}</h3>` : ""}`
        + `<p class="cnts">${nf(E.sent)} sent · ${nf(E.received)} received${lost ? ` · <span class="lo">${nf(lost)} lost</span>` : ""}`
        + `${E.inFlight ? ` · ${nf(E.inFlight)} in flight` : ""}</p>`
        + `<div class="tb">${bars}</div>`
        + (edgeRange(E) ? `<p class="hint mt8">first ${esc(timeLabel(E.first))} · last ${esc(timeLabel(E.last))}</p>` : "")
        + (lostRecs.length ? `<h3 class="mt10">Lost</h3><div class="lostl">${lostRecs.slice(0, 40).map(lostBtn).join("")}</div>` : "")
        + (fl.length ? `<h3 class="mt10">In flight</h3><div class="lostl">${fl.map(flBtn).join("")}</div>` : "")
        + `</div>`;
    }
    return h;
  }
  const ico = body => `<svg width="28" height="16" viewBox="0 0 28 16" aria-hidden="true">${body}</svg>`;
  const dot = (k, extra = "") => `<circle cx="14" cy="8" r="4.5" fill="var(--c-${k})"${extra}/>`;
  const NOTATION = [
    [ico(dot("message")), "sent, started, finished, log"],
    [ico(`<circle cx="14" cy="8" r="3.5" fill="var(--panel)" stroke="var(--c-message)" stroke-width="2"/>`), "received"],
    [ico(`<rect x="10.5" y="4.5" width="7" height="7" rx="1" fill="var(--c-event)" transform="rotate(45 14 8)"/>`), "event emitted, run"],
    [ico(`<rect x="9.5" y="3.5" width="9" height="9" rx="2" fill="var(--c-task)"/>`), "task scheduled"],
    [ico(`<circle cx="14" cy="8" r="3.5" fill="var(--c-message)"/>`), "handler call, task cycle"],
    [ico(`<path d="M10 4l8 8M18 4l-8 8" stroke="var(--lost)" stroke-width="2"/>`), "lost: where it should have arrived"],
    [ico(`<path d="M2 3H9Q12 3 12 6V10Q12 13 15 13H20" fill="none" stroke="var(--edge-msg)" stroke-width="1.4"/><path d="M26 13l-6 3v-6z" fill="var(--edge-msg)"/>`),
      "message, down to its receipt"],
    [ico(`<path d="M3 2V6H25M10 6V12M18 6V12" fill="none" stroke="var(--edge-evt)" stroke-width="1.4"/><path d="M10 15l-2.4-4h4.8zM18 15l-2.4-4h4.8z" fill="var(--edge-evt)"/>`),
      "event, to its receivers"],
    [ico(`<rect x="11.5" y="1" width="5" height="14" rx="2.5" fill="var(--bar-run)"/>`), "task running"],
    [ico(`<path d="M14 1.5V14.5" stroke="var(--c-wait)" stroke-width="1.6" stroke-dasharray="0 3.4" stroke-linecap="round"/>`), "task waiting"],
    [ico(`<path d="M3 8H25" stroke="var(--ancestor)" stroke-width="2.25" stroke-linecap="round"/>`), "started by: the cause chain"],
    [ico(`<path d="M3 8H25" stroke="var(--accent)" stroke-width="1.6" stroke-linecap="round"/>`), "led to"],
    [ico(`<rect x="5.5" y="0.5" width="17" height="15" rx="7.5" fill="var(--panel)" stroke="var(--ancestor)"/><text x="14" y="11.5" text-anchor="middle"`
      + ` font-size="10" font-weight="600" fill="var(--ancestor)" font-family="var(--font-data)">1</text>`), "the chain reaches another agent"],
    [ico(`<rect x="0.5" y="2.5" width="27" height="11" fill="url(#hatch)" stroke="var(--rule)"/>`), "gap: a stretch of time without records"],
  ];
  const notationHTML = () => `<div class="notation">${NOTATION.map(([i, t]) => `<div>${i}<span>${t}</span></div>`).join("")}</div>`;
  const keysNote = document.createElement("section");
  keysNote.className = "knote";
  keysNote.innerHTML = `<h3>Lanes notation</h3>` + notationHTML();
  $("keys").querySelector(".kc").after(keysNote);
  function renderPanel() {
    if (state.sel == null) {
      if (state.link) { panel.innerHTML = connectionHTML(); return; }
      panel.innerHTML = `<div><h3>Cause chain</h3><p class="hint">Select a record to see what started it and everything it led to.`
        + ` Every record names its <code>cause</code>: the message, event or task it happened in.</p></div>`
        + (state.view === "lanes" ? `<div><h3>Reading the lanes</h3>${notationHTML()}</div>` : "")
        + (presentCats.length ? `<div><h3>Categories</h3><div class="swatches">`
          + presentCats.map(c => `<span style="--k: var(--c-${c})">${CAT_LABEL[c]}</span>`).join("")
          + `</div></div>` : "")
        + `<p class="hint">Press <kbd>?</kbd> for keyboard shortcuts${state.view === "lanes" ? " and this notation" : ""}.</p>`;
      return;
    }
    const s = state.sel, r = RECORDS[s];
    const path = pathOfSel();
    const lvl = LV[levelOf[s]];
    let h = `<div><h2>${esc(r.event)}</h2><p class="sub">${esc(laneName(laneOf[s]))} · ${esc(recTime(s))}`
      + ` · <span class="lv" style="--k:${levelColor(lvl)}">${esc(lvl)}</span></p></div>`;
    h += `<div class="pacts"><button class="tool" data-act="follow" ${A ? "" : "disabled"}>Follow chain in Lanes <kbd>f</kbd></button></div>`;
    if (!vis[s]) h += `<p class="note">The selected record is hidden by the filters.</p>`;
    else if (state.view === "lanes" && state.lanes && !state.lanes.includes(laneOf[s])) {
      const name = esc(laneName(laneOf[s]));
      h += `<p class="note">Its lane, <b>${name}</b>, is not followed. <button data-act="add-lane">Follow ${name}</button></p>`;
    }
    h += messageNote(s);
    h += `<div><h3>Started by</h3>`;
    if (chain.length) {
      h += `<ol class="chain${hops.length ? " hops" : ""}">` + path.map((i, k) => {
        const d = k ? tOf(i) - tOf(path[k - 1]) : 0;
        return itemHTML(i, i === s, hopOfRec.get(i) || "", d > 0 ? dur(d) : "");
      }).join("") + `</ol>`;
    } else h += `<p class="hint">Nothing recorded: this record was not caused by a traced message, event or task.</p>`;
    h += `</div>`;
    const tally = {};
    for (const d of descList) { const ev = RECORDS[d].event; tally[ev] = (tally[ev] || 0) + 1; }
    h += `<div><h3>Led to · ${nf(descList.length)}</h3>`;
    if (descList.length) {
      h += `<div class="tally">${Object.entries(tally).map(([e, n]) => `<span>${esc(e)} ×${nf(n)}</span>`).join("")}</div>`
        + `<ol class="chain mt8">${descList.slice(0, 60).map(d => itemHTML(d)).join("")}</ol>`
        + (descList.length > 60 ? `<p class="hint">and ${nf(descList.length - 60)} more, highlighted in the ${state.view === "lanes" ? "lanes" : "table"}</p>`
          : "");
    } else {
      const why = r.id == null ? "Log lines have no id, so nothing points back to them." : "No later record names this one as its cause.";
      h += `<p class="hint">${why}</p>`;
    }
    h += `</div>`;
    const fields = Object.keys(r).filter(k => !k.startsWith("__"));
    h += `<div><h3>Fields</h3><dl class="kv">${fields.map(k => `<dt>${esc(k)}</dt><dd>${esc(fmt(r[k]))}</dd>`).join("")}</dl></div>`;
    panel.innerHTML = h;
  }
  panel.addEventListener("click", e => {
    const b = e.target.closest("[data-go]");
    if (b) { select(+b.dataset.go, { reveal: true }); return; }
    const a = e.target.closest("[data-act]");
    if (!a || a.disabled) return;
    if (a.dataset.act === "follow") followChain();
    else if (a.dataset.act === "both") setLink(state.link.e, !state.link.both);
    else if (a.dataset.act === "clear-link") setLink(null);
    else if (a.dataset.act === "add-lane" && state.sel != null) { ensureLane(state.sel, false); pendingReveal = state.sel; }
  });
