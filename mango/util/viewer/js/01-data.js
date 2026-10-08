  const RECORDS = JSON.parse(document.getElementById("trace-data").textContent);
  const N = RECORDS.length;
  const CORE = new Set(["event", "category", "id", "cause", "agent", "timestamp", "level"]);
  const CATS = ["message", "event", "task", "wait", "run", "app"];
  const CAT_LABEL = { message: "Messages", event: "Events", task: "Tasks", wait: "Waiting", run: "Run", app: "Your logs" };
  const C_WAIT = CATS.indexOf("wait");
  const LEVELS = ["debug", "info", "warning", "error", "critical"];

  const $ = id => document.getElementById(id);
  const natural = new Intl.Collator(undefined, { numeric: true }).compare;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = v => (v !== null && typeof v === "object") ? JSON.stringify(v) : String(v);
  const nf = n => n.toLocaleString("en-US");
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const plural = (n, one, many) => `${nf(n)} ${n === 1 ? one : many || one + "s"}`;
  const levelColor = l => `var(--l-${l === "critical" ? "error" : LEVELS.includes(l) ? l : "info"})`;
  const aidOf = s => { const m = /aid=(['"])(.*?)\1/.exec(s == null ? "" : String(s)); return m ? m[2] : null; };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  };
  const motion = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isPhone = () => innerWidth <= 640;

  const E_SENT = 1, E_RECV = 2, E_EMIT = 3, E_EVRECV = 4, E_ROLE = 5, E_HANDLER = 6, E_SCHED = 7, E_START = 8, E_CYCLE = 9,
    E_WAIT = 10, E_RESUME = 11, E_FIN = 12, E_FAIL = 13, E_CANCEL = 14;
  const EVCODE = new Map([["message.sent", E_SENT], ["message.received", E_RECV], ["event.emitted", E_EMIT],
    ["role_event.received", E_ROLE], ["handler.called", E_HANDLER], ["task.scheduled", E_SCHED], ["task.started", E_START],
    ["task.cycle", E_CYCLE], ["task.waiting", E_WAIT], ["task.resumed", E_RESUME], ["task.finished", E_FIN],
    ["task.failed", E_FAIL], ["task.cancelled", E_CANCEL]]);

  const agentSet = new Set();
  for (let i = 0; i < N; i++) { const a = RECORDS[i].agent; if (a != null) agentSet.add(a); }
  const AG = [...agentSet].sort((a, b) => natural(String(a), String(b)));
  const A = AG.length, WORLD = A;
  const aIx = new Map(AG.map((a, i) => [a, i]));
  const laneName = l => l === WORLD ? "no agent" : String(AG[l]);

  const kindOf = new Uint8Array(N), levelOf = new Uint8Array(N), failOf = new Uint8Array(N), evOf = new Uint8Array(N);
  const agentOf = new Int32Array(N), laneOf = new Int32Array(N);
  const idOf = new Int32Array(N).fill(-1), causeOf = new Int32Array(N).fill(-1), causeIx = new Int32Array(N).fill(-1);
  const T = new Float64Array(N);
  const idNum = new Map(), idStr = [];
  const intern = v => { let k = idNum.get(v); if (k === undefined) { k = idStr.length; idNum.set(v, k); idStr.push(v); } return k; };
  const LV = [], lvIx = new Map();
  const catCount = new Array(CATS.length).fill(0);
  const laneTotal = new Int32Array(A + 1), laneFailed = new Int32Array(A + 1);
  const keySet = new Set(), FAILS = [], SENDS = [];
  const lastRecvOfAgent = new Map();
  let nRecv = 0, nEvRecv = 0, nSched = 0, nHandler = 0, nAgentRecs = 0, nAgentSim = 0, simMin = Infinity, simMax = -Infinity;
  const kindIx = new Map(CATS.map((c, i) => [c, i]));

  for (let i = 0; i < N; i++) {
    const r = RECORDS[i];
    for (const k in r) keySet.add(k);
    const ev = r.event;
    let code = EVCODE.get(ev) || 0;
    if (!code && typeof ev === "string" && ev.endsWith("_event.received")) code = E_EVRECV;
    evOf[i] = code;
    if (code === E_RECV) nRecv++;
    else if (code === E_EVRECV || code === E_ROLE) nEvRecv++;
    else if (code === E_SCHED) nSched++;
    else if (code === E_HANDLER) nHandler++;
    const kc = kindIx.get(r.category || "app");
    kindOf[i] = kc === undefined ? kindIx.get("app") : kc;
    catCount[kindOf[i]]++;
    const lvl = String(r.level || "info").toLowerCase();
    let li = lvIx.get(lvl);
    if (li === undefined) { li = LV.length; LV.push(lvl); lvIx.set(lvl, li); }
    levelOf[i] = li;
    const fail = /\.failed$/.test(ev || "") || lvl === "error" || lvl === "critical";
    failOf[i] = fail ? 1 : 0;
    if (fail) FAILS.push(i);
    const ag = r.agent != null ? aIx.get(r.agent) : -1;
    agentOf[i] = ag;
    let lane = ag;
    if (lane < 0) lane = code === E_SENT && r.sender != null && aIx.has(r.sender) ? aIx.get(r.sender) : WORLD;
    laneOf[i] = lane;
    laneTotal[lane]++;
    if (fail) laneFailed[lane]++;
    const st = r.sim_time;
    if (typeof st === "number") {
      T[i] = st;
      if (st < simMin) simMin = st;
      if (st > simMax) simMax = st;
      if (ag >= 0) nAgentSim++;
    } else T[i] = NaN;
    if (ag >= 0) nAgentRecs++;
    if (r.id != null) idOf[i] = intern(r.id);
    if (r.cause != null) causeOf[i] = intern(r.cause);
    if (code === E_SENT) SENDS.push(i);
    else if (code === E_RECV && r.agent != null) lastRecvOfAgent.set(r.agent, i);
  }
  const M = idStr.length;
  const firstOf = new Int32Array(M).fill(-1), recvOfId = new Int32Array(M).fill(-1);
  const idStart = new Int32Array(M + 1), cStart = new Int32Array(M + 1);
  for (let i = 0; i < N; i++) {
    const k = idOf[i];
    if (k >= 0) { idStart[k + 1]++; if (firstOf[k] < 0) firstOf[k] = i; }
    const c = causeOf[i];
    if (c >= 0) { cStart[c + 1]++; if (evOf[i] === E_RECV) recvOfId[c] = i; }
  }
  for (let k = 0; k < M; k++) { idStart[k + 1] += idStart[k]; cStart[k + 1] += cStart[k]; }
  const idList = new Int32Array(idStart[M]), cList = new Int32Array(cStart[M]);
  {
    const a = idStart.slice(0, M), b = cStart.slice(0, M);
    for (let i = 0; i < N; i++) {
      if (idOf[i] >= 0) idList[a[idOf[i]]++] = i;
      if (causeOf[i] >= 0) { cList[b[causeOf[i]]++] = i; causeIx[i] = firstOf[causeOf[i]]; }
    }
  }
  const eachId = (k, f) => { for (let j = idStart[k], e = idStart[k + 1]; j < e; j++) f(idList[j]); };
  const eachEffect = (k, f) => { for (let j = cStart[k], e = cStart[k + 1]; j < e; j++) f(cList[j]); };
  const recvOf = s => idOf[s] >= 0 ? recvOfId[idOf[s]] : -1;
  const presentLevels = [...LV].sort((a, b) => (LEVELS.indexOf(a) + 1 || 99) - (LEVELS.indexOf(b) + 1 || 99));
  const levelCount = new Array(LV.length).fill(0);
  for (let i = 0; i < N; i++) levelCount[levelOf[i]]++;
