  const state = {
    view: "records", q: "", agent: "", cats: new Set(CATS), levels: new Set(presentLevels), onlyRelated: false,
    link: null, sel: null, cursor: null, lanes: null, order: "packed", fit: false,
    topo: { open: true, wide: false, mode: "graph", lossy: false },
  };
  try {
    const saved = JSON.parse(store.get("mango-trace-cats") || "null");
    if (Array.isArray(saved)) state.cats = new Set(saved.filter(c => CATS.includes(c)));
  } catch (e) { /* storage unavailable */ }
  try {
    const t = JSON.parse(store.get("mango-trace-topo") || "null");
    if (t && typeof t === "object") {
      if (typeof t.open === "boolean") state.topo.open = t.open;
      if (typeof t.wide === "boolean") state.topo.wide = t.wide;
      if (t.mode === "list" || t.mode === "graph") state.topo.mode = t.mode;
    }
  } catch (e) { /* storage unavailable */ }
  if (store.get("mango-trace-view") === "lanes" && A) state.view = "lanes";

  const F_VIS = 1, F_REL = 2, F_TABLE = 4, F_LAYOUT = 8, F_TILES = 16, F_MAP = 32, F_PANEL = 64, F_RAIL = 128, F_HASH = 256, F_VIEW = 512, F_HEAD = 1024;
  let dirty = 0, raf = 0;
  function invalidate(f) {
    dirty |= f;
    if (!raf) raf = requestAnimationFrame(flush);
  }
  function flushNow() { if (raf) { cancelAnimationFrame(raf); raf = 0; } flush(); }

  const textCache = new Array(N);
  let textBuilt = 0, textStarted = false;
  const textOf = i => textCache[i] ?? (textCache[i] = JSON.stringify(RECORDS[i]).toLowerCase());
  // the lowercase copies double the heap on big traces, so they are only built once someone starts searching
  function prebuildText() {
    if (textStarted) return;
    textStarted = true;
    idle(buildText);
  }
  function buildText(deadline) {
    const until = performance.now() + 12;
    while (textBuilt < N && (deadline ? deadline.timeRemaining() > 2 : performance.now() < until)) {
      const end = Math.min(N, textBuilt + 400);
      for (let i = textBuilt; i < end; i++) textOf(i);
      textBuilt = end;
    }
    if (textBuilt < N) idle(buildText);
  }
  const lowerKeys = new Map();
  for (const k of keySet) { const l = k.toLowerCase(); (lowerKeys.get(l) || lowerKeys.set(l, []).get(l)).push(k); }
  let maskFor = null, maskVal = null;
  function textMask() {
    const tokens = state.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) return null;
    const sig = tokens.join(" ");
    if (maskFor === sig) return maskVal;
    const mask = new Uint8Array(N).fill(1);
    for (const t of tokens) {
      const eq = t.indexOf("=");
      if (eq > 0) {
        const cands = lowerKeys.get(t.slice(0, eq)) || [], want = t.slice(eq + 1);
        for (let i = 0; i < N; i++) {
          if (!mask[i]) continue;
          const r = RECORDS[i];
          let k;
          if (cands.length === 1) k = Object.prototype.hasOwnProperty.call(r, cands[0]) ? cands[0] : undefined;
          else k = Object.keys(r).find(x => x.toLowerCase() === t.slice(0, eq));
          if (k === undefined) mask[i] = 0;
          else if (want !== "" && !fmt(r[k]).toLowerCase().includes(want)) mask[i] = 0;
        }
      } else {
        for (let i = 0; i < N; i++) if (mask[i] && !textOf(i).includes(t)) mask[i] = 0;
      }
    }
    maskFor = sig;
    maskVal = mask;
    return mask;
  }

  const vis = new Uint8Array(N), visBuf = new Int32Array(N);
  let visList = visBuf.subarray(0, 0);
  const linkMatch = new Int32Array(EDGES.length);
  let filtersActive = false;
  const linkEdges = () => {
    if (!state.link) return null;
    const s = new Uint8Array(EDGES.length);
    s[state.link.e] = 1;
    if (state.link.both && reverseEdge(state.link.e) >= 0) s[reverseEdge(state.link.e)] = 1;
    return s;
  };
  function computeVisibility() {
    const catOK = CATS.map(c => state.cats.has(c)), lvOK = LV.map(l => state.levels.has(l));
    const tm = textMask(), lk = linkEdges();
    const agentF = state.view === "records" && state.agent !== "" ? aIx.get(state.agent) ?? -2 : -1;
    const relF = state.onlyRelated && state.sel != null ? rel : null;
    filtersActive = !!(tm || lk || agentF !== -1 || relF || lvOK.some(v => !v));
    linkMatch.fill(0);
    let n = 0;
    for (let i = 0; i < N; i++) {
      const e = edgeOfRec[i];
      const base = lvOK[levelOf[i]] && (!tm || tm[i]) && (!relF || relF[i]) && (!lk || (e >= 0 && lk[e]))
        && (agentF === -1 || agentOf[i] === agentF);
      if (base && e >= 0) linkMatch[e]++;
      if (base && catOK[kindOf[i]]) { vis[i] = 1; visBuf[n++] = i; } else vis[i] = 0;
    }
    visList = visBuf.subarray(0, n);
  }

  // rel: 1 ancestor, 2 descendant, 3 selected
  const rel = new Uint8Array(N);
  let relList = [], chain = [], descList = [], hops = [];
  const hopOfRec = new Map();
  const seenId = new Uint8Array(M);
  function computeRelations() {
    for (const i of relList) rel[i] = 0;
    relList = []; chain = []; descList = []; hops = []; hopOfRec.clear();
    const s = state.sel;
    if (s == null) return;
    const seen = new Set([s]);
    for (let c = causeIx[s]; c >= 0 && !seen.has(c); c = causeIx[c]) { seen.add(c); chain.push(c); }
    const k0 = idOf[s];
    if (k0 >= 0) {
      const queue = [k0], touched = [k0];
      seenId[k0] = 1;
      const add = j => { if (!rel[j] && j !== s) { rel[j] = 2; descList.push(j); } };
      for (let h = 0; h < queue.length; h++) {
        const k = queue[h];
        eachId(k, add);
        eachEffect(k, j => {
          add(j);
          const c = idOf[j];
          if (c >= 0 && !seenId[c]) { seenId[c] = 1; touched.push(c); queue.push(c); }
        });
      }
      for (const k of touched) seenId[k] = 0;
      descList.sort((a, b) => a - b);
    }
    relList = descList.slice();
    for (const c of chain) { if (!rel[c]) relList.push(c); rel[c] = 1; }
    rel[s] = 3;
    relList.push(s);
    let prev = -1;
    for (let k = chain.length - 1; k >= -1; k--) {
      const i = k >= 0 ? chain[k] : s;
      const a = laneOf[i] === WORLD ? -1 : laneOf[i];
      if (a < 0) continue;
      if (prev >= 0 && a !== prev) {
        const via = causeIx[i] >= 0 && evOf[causeIx[i]] === E_SENT ? causeIx[i] : -1;
        hops.push({ n: hops.length + 1, rec: i, from: prev, to: a, edge: via >= 0 ? edgeOfRec[via] : -1, via });
        hopOfRec.set(i, hops.length);
      }
      prev = a;
    }
  }
  const selShown = () => state.sel != null && vis[state.sel] === 1 && (state.view !== "lanes" || !L || L.col[laneOf[state.sel]] >= 0);
  const pathOfSel = () => state.sel == null ? [] : [...chain].reverse().concat(state.sel);
  const HOPCH = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
  const hopGlyph = n => n <= 20 ? HOPCH[n - 1] : String(n);

  const idle = f => (window.requestIdleCallback ? requestIdleCallback(f, { timeout: 1500 }) : setTimeout(() => f(null), 50));
  const announce = text => { const el = $("sr"); el.textContent = ""; setTimeout(() => { el.textContent = text; }, 30); };
