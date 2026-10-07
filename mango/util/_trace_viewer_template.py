"""HTML template of the trace viewer (see :mod:`mango.util.trace_viewer`).

``__TITLE__`` and ``__DATA__`` are replaced when rendering. The page needs no
server and no network: the trace is embedded as JSON.
"""

PAGE = r"""<title>__TITLE__</title>
<style>
/* Layout: summary line, filter bar, then timeline table beside a sticky
   cause panel; the panel drops below the table on narrow screens. */
:root {
  --bg: #f4f6f9;
  --panel: #ffffff;
  --fg: #18202c;
  --muted: #5a6677;
  --line: #dbe1e9;
  --accent: #2b59c3;
  --accent-soft: #e4ecfb;
  --ancestor-soft: #efe7fb;
  --ancestor: #7445c9;
  --fail: #c2362b;
  --fail-soft: #fbe9e7;
  --c-run: #6b7686;
  --c-message: #2b6fd6;
  --c-event: #b7790f;
  --c-task: #10846f;
  --c-wait: #7d8a9c;
  --c-app: #b03c8a;
  --l-debug: #7d8a9c;
  --l-info: #2b6fd6;
  --l-warning: #b7790f;
  --l-error: #c2362b;
  --font-ui: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #11151c; --panel: #181e27; --fg: #e2e7ee; --muted: #93a0b2;
    --line: #29313d; --accent: #7ea4ff; --accent-soft: #1c2a47;
    --ancestor-soft: #2a2140; --ancestor: #b493ff; --fail: #ff7b6e;
    --fail-soft: #3a1d1b; --c-run: #93a0b2; --c-message: #6ea2ff;
    --c-event: #e3a83a; --c-task: #3cc4a6; --c-wait: #8592a5; --c-app: #ea7cc5;
    --l-debug: #8592a5; --l-info: #6ea2ff; --l-warning: #e3a83a; --l-error: #ff7b6e;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #11151c; --panel: #181e27; --fg: #e2e7ee; --muted: #93a0b2;
  --line: #29313d; --accent: #7ea4ff; --accent-soft: #1c2a47;
  --ancestor-soft: #2a2140; --ancestor: #b493ff; --fail: #ff7b6e;
  --fail-soft: #3a1d1b; --c-run: #93a0b2; --c-message: #6ea2ff;
  --c-event: #e3a83a; --c-task: #3cc4a6; --c-wait: #8592a5; --c-app: #ea7cc5;
  --l-debug: #8592a5; --l-info: #6ea2ff; --l-warning: #e3a83a; --l-error: #ff7b6e;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.45 var(--font-ui);
}
.wrap { padding: 20px 16px 32px; max-width: 1500px; margin: 0 auto; display: grid; gap: 14px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; }
h1 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -0.01em; text-wrap: balance; }
.stats { display: flex; flex-wrap: wrap; gap: 4px 16px; color: var(--muted); font-variant-numeric: tabular-nums; }
.stats b { color: var(--fg); font-weight: 600; }
.stats button { font: inherit; border: 0; background: none; padding: 0; cursor: pointer; color: var(--fail); }
.stats button b { color: var(--fail); }
.stats button:disabled { color: var(--muted); cursor: default; }
.stats button:disabled b { color: var(--fg); }

.filters { display: grid; gap: 8px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.row label { color: var(--muted); font-size: 12px; }
input[type="search"], select {
  font: 13px var(--font-data); color: var(--fg); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px;
}
input[type="search"] { flex: 1 1 260px; min-width: 0; }
input:focus-visible, select:focus-visible, button:focus-visible, tr:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 1px;
}
.chip {
  font: 12px var(--font-ui); border: 1px solid var(--line); background: var(--bg); color: var(--muted);
  border-radius: 999px; padding: 3px 10px 3px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
}
.chip[aria-pressed="true"] { color: var(--fg); border-color: var(--muted); background: var(--panel); }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--k); }
.chip[aria-pressed="false"] .dot { background: transparent; box-shadow: inset 0 0 0 1.5px var(--k); }
.check { display: inline-flex; gap: 5px; align-items: center; font-size: 13px; color: var(--fg); cursor: pointer; }
.fields { font-size: 12px; color: var(--muted); }
.fields button { font: 12px var(--font-data); border: 0; background: none; color: var(--accent); cursor: pointer; padding: 0 2px; }

main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 400px); gap: 14px; align-items: start; }
@media (max-width: 980px) { main { grid-template-columns: minmax(0, 1fr); } }

.table-box { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow-x: auto; min-width: 0; }
table { border-collapse: collapse; width: 100%; font: 12.5px/1.4 var(--font-data); font-variant-numeric: tabular-nums; }
th { position: sticky; top: 0; background: var(--panel); text-align: left; font: 600 11px var(--font-ui);
  letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); padding: 8px 10px; border-bottom: 1px solid var(--line); }
td { padding: 5px 10px; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
td.details { white-space: normal; color: var(--muted); min-width: 220px; }
tbody tr { cursor: pointer; }
tbody tr:hover td { background: var(--bg); }
td.t { color: var(--muted); text-align: right; }
td.lvl { color: var(--k); font-size: 11.5px; }
.ev { display: inline-flex; align-items: center; gap: 7px; font-weight: 500; }
.ev::before { content: ""; width: 3px; height: 14px; border-radius: 2px; background: var(--k); flex: none; }
.ref { color: var(--accent); }
tr.anc td { background: var(--ancestor-soft); }
tr.desc td { background: var(--accent-soft); }
tr.sel td { background: var(--accent-soft); box-shadow: inset 0 1px 0 var(--accent), inset 0 -1px 0 var(--accent); }
tr.dim td { opacity: 0.38; }
tr.fail td { color: var(--fail); }
tr.fail td:first-child { box-shadow: inset 3px 0 0 var(--fail); }
.empty { padding: 28px 16px; color: var(--muted); text-align: center; font-family: var(--font-ui); }

aside { position: sticky; top: calc(env(safe-area-inset-top, 0px) + 12px); background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 14px; display: grid; gap: 14px; max-height: calc(100vh - 24px); overflow: auto; min-width: 0; }
aside h2 { margin: 0; font-size: 15px; font-weight: 600; font-family: var(--font-data); word-break: break-all; }
aside h3 { margin: 0 0 6px; font: 600 11px var(--font-ui); letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
.hint { color: var(--muted); margin: 0; }
.chain { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.chain li { display: grid; grid-template-columns: 14px minmax(0, 1fr); gap: 6px; align-items: start; }
.chain li::before { content: ""; width: 9px; height: 9px; margin-top: 5px; border-radius: 50%; border: 2px solid var(--k); background: var(--panel); }
.chain li.here::before { background: var(--k); }
.chain button { font: 12.5px var(--font-data); text-align: left; background: none; border: 0; padding: 2px 0; color: var(--fg); cursor: pointer; min-width: 0; overflow-wrap: anywhere; }
.chain button span { color: var(--muted); }
.chain .agent { color: var(--muted); font-size: 11.5px; }
.kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 3px 12px; font: 12.5px var(--font-data); margin: 0; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.tally { display: flex; flex-wrap: wrap; gap: 6px; }
.tally span { font: 12px var(--font-data); padding: 2px 8px; border-radius: 999px; background: var(--bg); border: 1px solid var(--line); }
@media (prefers-reduced-motion: no-preference) { tr td { transition: background-color 120ms, opacity 120ms; } }
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">

<div class="wrap">
  <header>
    <h1 id="title">__TITLE__</h1>
    <div class="stats" id="stats"></div>
  </header>

  <section class="filters" aria-label="Filters">
    <div class="row">
      <input type="search" id="q" placeholder="Filter: free text or field=value, e.g. agent=a1 event=task.failed" aria-label="Filter records">
      <label for="agent">Agent</label>
      <select id="agent"></select>
    </div>
    <div class="row" id="cats" role="group" aria-label="Categories"></div>
    <div class="row" id="levels" role="group" aria-label="Log levels"></div>
    <div class="row">
      <label class="check"><input type="checkbox" id="only-related"> Only records related to the selection</label>
      <span class="fields" id="fields"></span>
    </div>
  </section>

  <main>
    <div class="table-box">
      <table>
        <thead><tr><th>+ms</th><th>Level</th><th>Agent</th><th>Event</th><th>id</th><th>cause</th><th>Details</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
      <div class="empty" id="empty" hidden>No records match these filters.</div>
    </div>
    <aside id="panel" aria-live="polite"></aside>
  </main>
</div>

<script type="application/json" id="trace-data">__DATA__</script>
<script>
(() => {
  const RECORDS = JSON.parse(document.getElementById("trace-data").textContent);
  const CORE = new Set(["event", "category", "id", "cause", "agent", "timestamp", "level"]);
  const CATS = ["message", "event", "task", "wait", "run", "app"];
  const CAT_LABEL = { message: "Messages", event: "Events", task: "Tasks", wait: "Waiting", run: "Run", app: "Your logs" };

  const LEVELS = ["debug", "info", "warning", "error", "critical"];
  const level = r => String(r.level || "info").toLowerCase();
  const levelColor = l => `var(--l-${l === "critical" ? "error" : LEVELS.includes(l) ? l : "info"})`;
  const kind = r => r.category || "app";
  const color = r => `var(--c-${kind(r)})`;
  const isFail = r => /\.failed$/.test(r.event || "") || level(r) === "error" || level(r) === "critical";
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = v => (v !== null && typeof v === "object") ? JSON.stringify(v) : String(v);

  const t0 = RECORDS.length ? Date.parse(RECORDS[0].timestamp) : NaN;
  const firstById = new Map(), byId = new Map(), byCause = new Map();
  RECORDS.forEach((r, i) => {
    r.__i = i;
    if (r.id != null) {
      if (!firstById.has(r.id)) firstById.set(r.id, r);
      (byId.get(r.id) || byId.set(r.id, []).get(r.id)).push(i);
    }
    if (r.cause != null) (byCause.get(r.cause) || byCause.set(r.cause, []).get(r.cause)).push(i);
  });

  // Records that started r, nearest first.
  function ancestors(r) {
    const out = [], seen = new Set();
    let c = r.cause;
    while (c != null && !seen.has(c)) {
      seen.add(c);
      const p = firstById.get(c);
      if (!p) break;
      out.push(p);
      c = p.cause;
    }
    return out;
  }
  // r's own lifecycle and everything it led to.
  function descendants(r) {
    const res = new Set();
    if (r.id == null) return res;
    const queue = [r.id], seen = new Set([r.id]);
    while (queue.length) {
      const id = queue.shift();
      for (const i of byId.get(id) || []) res.add(i);
      for (const i of byCause.get(id) || []) {
        res.add(i);
        const cid = RECORDS[i].id;
        if (cid != null && !seen.has(cid)) { seen.add(cid); queue.push(cid); }
      }
    }
    res.delete(r.__i);
    return res;
  }

  // ---- state
  const state = { q: "", agent: "", cats: new Set(CATS), levels: null, onlyRelated: false, sel: null };
  try {
    const saved = JSON.parse(localStorage.getItem("mango-trace-cats") || "null");
    if (Array.isArray(saved)) state.cats = new Set(saved.filter(c => CATS.includes(c)));
  } catch (e) { /* storage unavailable */ }

  // ---- header
  const agents = [...new Set(RECORDS.map(r => r.agent).filter(a => a != null))].sort();
  const count = ev => RECORDS.filter(r => r.event === ev).length;
  const fails = RECORDS.filter(isFail);
  const tEnd = RECORDS.length ? Date.parse(RECORDS[RECORDS.length - 1].timestamp) : NaN;
  const stats = [
    [RECORDS.length, "records"],
    [agents.length, "agents"],
    [count("message.received"), "messages"],
    [RECORDS.filter(r => /_event\.received$/.test(r.event || "")).length, "events"],
    [count("task.scheduled"), "tasks"],
    [count("handler.called"), "handler calls"],
  ];
  const statsEl = document.getElementById("stats");
  statsEl.innerHTML = stats.map(([n, l]) => `<span><b>${n}</b> ${l}</span>`).join("")
    + (isNaN(tEnd - t0) ? "" : `<span><b>${Math.round(tEnd - t0)}</b> ms</span>`)
    + `<button id="fails" ${fails.length ? "" : "disabled"} title="Select the first failure or error"><b>${fails.length}</b> errors</button>`;
  document.getElementById("fails").addEventListener("click", () => select(fails[0].__i, true));

  // ---- filters
  const agentSel = document.getElementById("agent");
  agentSel.innerHTML = `<option value="">All</option>` + agents.map(a => `<option>${esc(a)}</option>`).join("");
  agentSel.addEventListener("change", () => { state.agent = agentSel.value; render(); });

  const present = new Set(RECORDS.map(kind));
  const catsEl = document.getElementById("cats");
  catsEl.innerHTML = CATS.filter(c => present.has(c)).map(c =>
    `<button class="chip" style="--k: var(--c-${c})" data-cat="${c}" aria-pressed="${state.cats.has(c)}"><span class="dot"></span>${CAT_LABEL[c]} <span>${RECORDS.filter(r => kind(r) === c).length}</span></button>`
  ).join("");
  catsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    const c = b.dataset.cat;
    state.cats.has(c) ? state.cats.delete(c) : state.cats.add(c);
    b.setAttribute("aria-pressed", state.cats.has(c));
    try { localStorage.setItem("mango-trace-cats", JSON.stringify([...state.cats])); } catch (e) { /* ignore */ }
    render();
  });

  const presentLevels = [...new Set(RECORDS.map(level))]
    .sort((a, b) => (LEVELS.indexOf(a) + 1 || 99) - (LEVELS.indexOf(b) + 1 || 99));
  state.levels = new Set(presentLevels);
  const levelsEl = document.getElementById("levels");
  levelsEl.innerHTML = `<label>Level</label>` + presentLevels.map(l =>
    `<button class="chip" style="--k: ${levelColor(l)}" data-level="${esc(l)}" aria-pressed="true"><span class="dot"></span>${esc(l)} <span>${RECORDS.filter(r => level(r) === l).length}</span></button>`
  ).join("");
  levelsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-level]");
    if (!b) return;
    const l = b.dataset.level;
    state.levels.has(l) ? state.levels.delete(l) : state.levels.add(l);
    b.setAttribute("aria-pressed", state.levels.has(l));
    render();
  });

  const q = document.getElementById("q");
  q.addEventListener("input", () => { state.q = q.value; render(); });
  document.getElementById("only-related").addEventListener("change", e => { state.onlyRelated = e.target.checked; render(); });

  // field names come from the data, so new fields are filterable without changes here
  const keys = [...new Set(RECORDS.flatMap(r => Object.keys(r)))].filter(k => !k.startsWith("__") && k !== "timestamp").sort();
  document.getElementById("fields").innerHTML = "Fields: " + keys.map(k => `<button data-key="${esc(k)}">${esc(k)}</button>`).join(" ");
  document.getElementById("fields").addEventListener("click", e => {
    const b = e.target.closest("[data-key]");
    if (!b) return;
    q.value = (q.value.trim() ? q.value.trim() + " " : "") + b.dataset.key + "=";
    q.focus();
  });

  function matcher(text) {
    const tokens = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return r => tokens.every(t => {
      const eq = t.indexOf("=");
      if (eq > 0) {
        const key = t.slice(0, eq), want = t.slice(eq + 1);
        const k = Object.keys(r).find(k => k.toLowerCase() === key);
        if (k === undefined) return false;
        return want === "" || fmt(r[k]).toLowerCase().includes(want);
      }
      return JSON.stringify(r).toLowerCase().includes(t);
    });
  }

  // ---- table
  const rowsEl = document.getElementById("rows");
  const details = r => Object.keys(r)
    .filter(k => !CORE.has(k) && !k.startsWith("__"))
    .map(k => `${esc(k)}=${esc(fmt(r[k]))}`).join("  ");
  const ref = v => v == null ? "" : `<span class="ref">${esc(v)}</span>`;

  function render() {
    const match = matcher(state.q);
    let related = null, anc = new Set(), desc = new Set();
    if (state.sel != null) {
      const s = RECORDS[state.sel];
      anc = new Set(ancestors(s).map(r => r.__i));
      desc = descendants(s);
      related = new Set([state.sel, ...anc, ...desc]);
    }
    const html = [];
    for (const r of RECORDS) {
      if (!state.cats.has(kind(r))) continue;
      if (!state.levels.has(level(r))) continue;
      if (state.agent && r.agent !== state.agent) continue;
      if (!match(r)) continue;
      if (state.onlyRelated && related && !related.has(r.__i)) continue;
      const cls = [
        isFail(r) ? "fail" : "",
        r.__i === state.sel ? "sel" : anc.has(r.__i) ? "anc" : desc.has(r.__i) ? "desc" : related ? "dim" : "",
      ].join(" ").trim();
      const t = Date.parse(r.timestamp) - t0;
      html.push(`<tr data-i="${r.__i}" tabindex="0" class="${cls}">`
        + `<td class="t">${isNaN(t) ? "" : t.toFixed(1)}</td>`
        + `<td class="lvl" style="--k:${levelColor(level(r))}">${esc(level(r))}</td>`
        + `<td>${esc(r.agent ?? "")}</td>`
        + `<td><span class="ev" style="--k:${color(r)}">${esc(r.event)}</span></td>`
        + `<td>${ref(r.id)}</td><td>${ref(r.cause)}</td>`
        + `<td class="details">${details(r)}</td></tr>`);
    }
    rowsEl.innerHTML = html.join("");
    document.getElementById("empty").hidden = html.length > 0;
    renderPanel();
  }

  rowsEl.addEventListener("click", e => {
    const tr = e.target.closest("tr[data-i]");
    if (tr) select(+tr.dataset.i);
  });
  rowsEl.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tr = e.target.closest("tr[data-i]");
    if (tr) { e.preventDefault(); select(+tr.dataset.i); }
  });

  function select(i, scroll) {
    state.sel = state.sel === i && !scroll ? null : i;
    render();
    if (state.sel != null) {
      history.replaceState(null, "", "#r" + state.sel);
      if (scroll) rowsEl.querySelector(`tr[data-i="${i}"]`)?.scrollIntoView({ block: "center" });
    }
  }

  // ---- panel
  const panel = document.getElementById("panel");
  const item = (r, here) => `<li class="${here ? "here" : ""}" style="--k:${color(r)}"><button data-go="${r.__i}">${esc(r.event)}`
    + `${r.id != null ? ` <span>${esc(r.id)}</span>` : ""}<br><span class="agent">${esc(r.agent ?? "no agent")}</span></button></li>`;

  function renderPanel() {
    if (state.sel == null) {
      panel.innerHTML = `<h3>Cause chain</h3><p class="hint">Select a record to see what started it and everything it led to.`
        + ` Every record names its <code>cause</code>: the message, event or task it happened in.</p>`
        + `<h3>Legend</h3><div class="tally">`
        + CATS.filter(c => present.has(c)).map(c => `<span style="border-color: var(--c-${c})">${CAT_LABEL[c]}</span>`).join("")
        + `</div>`;
      return;
    }
    const r = RECORDS[state.sel];
    const chain = ancestors(r).reverse();
    const desc = [...descendants(r)].sort((a, b) => a - b).map(i => RECORDS[i]);
    const tally = {};
    desc.forEach(d => { tally[d.event] = (tally[d.event] || 0) + 1; });
    const fields = Object.keys(r).filter(k => !k.startsWith("__"));
    panel.innerHTML = `<h2>${esc(r.event)}</h2>`
      + `<div><h3>Started by</h3>${chain.length
          ? `<ol class="chain">${chain.map(c => item(c)).join("")}${item(r, true)}</ol>`
          : `<p class="hint">Nothing recorded: this record was not caused by a traced message, event or task.</p>`}</div>`
      + `<div><h3>Led to · ${desc.length}</h3>${desc.length
          ? `<div class="tally">${Object.entries(tally).map(([e, n]) => `<span>${esc(e)} ×${n}</span>`).join("")}</div>`
            + `<ol class="chain" style="margin-top:8px">${desc.slice(0, 60).map(d => item(d)).join("")}</ol>`
            + (desc.length > 60 ? `<p class="hint">and ${desc.length - 60} more, highlighted in the table</p>` : "")
          : `<p class="hint">${r.id == null ? "Log lines have no id, so nothing points back to them." : "No later record names this one as its cause."}</p>`}</div>`
      + `<div><h3>Fields</h3><dl class="kv">${fields.map(k => `<dt>${esc(k)}</dt><dd>${esc(fmt(r[k]))}</dd>`).join("")}</dl></div>`;
  }
  panel.addEventListener("click", e => {
    const b = e.target.closest("[data-go]");
    if (b) select(+b.dataset.go, true);
  });

  const m = /^#r(\d+)$/.exec(location.hash);
  if (m && RECORDS[+m[1]]) state.sel = +m[1];
  else if (fails.length) state.sel = fails[0].__i;
  render();
  if (state.sel != null) rowsEl.querySelector(`tr[data-i="${state.sel}"]`)?.scrollIntoView({ block: "center" });
})();
</script>
"""
