// The trace as typed arrays indexed by record: what each record is, whose lane it belongs to,
// and how ids and causes link records. Built once; filters and layouts read it on every change.

// schema: VIEWER_SCHEMA of tracing.py, which says what the record names mean
import { CATS, E_EVRECV, E_HANDLER, E_RECV, E_ROLE, E_SCHED, E_SENT, LEVELS, ROLE_CODES } from "./schema.js";
import { natural } from "./util.js";

export function indexTrace(RECORDS, schema) {
  const EVCODE = new Map(Object.entries(schema.roles).map(([name, role]) => [name, ROLE_CODES[role]]));
  const nRecords = RECORDS.length;

  const agentSet = new Set();

  for (let i = 0; i < nRecords; i++) {
    const a = RECORDS[i].agent;
    if (a != null) agentSet.add(a);
  }

  const AGENTS = [...agentSet].sort((a, b) => natural(String(a), String(b)));

  const nAgents = AGENTS.length;

  const WORLD = nAgents;

  const aIx = new Map(AGENTS.map((a, i) => [a, i]));

  const laneName = l => (l === WORLD ? "no agent" : String(AGENTS[l]));

  const kindOf = new Uint8Array(nRecords);

  const levelOf = new Uint8Array(nRecords);

  const failOf = new Uint8Array(nRecords);

  const evOf = new Uint8Array(nRecords);

  const agentOf = new Int32Array(nRecords);

  const laneOf = new Int32Array(nRecords);

  const idOf = new Int32Array(nRecords).fill(-1);

  const causeOf = new Int32Array(nRecords).fill(-1);

  const causeIx = new Int32Array(nRecords).fill(-1);

  const simTime = new Float64Array(nRecords);

  const idNum = new Map();

  const idStr = [];

  const intern = v => {
    let k = idNum.get(v);
    if (k === undefined) {
      k = idStr.length;
      idNum.set(v, k);
      idStr.push(v);
    }
    return k;
  };

  const levelNames = [];

  const levelIx = new Map();

  const catCount = new Array(CATS.length).fill(0);

  const laneTotal = new Int32Array(nAgents + 1);

  const laneFailed = new Int32Array(nAgents + 1);

  const keySet = new Set();

  const FAILS = [];

  const SENDS = [];

  const lastRecvOfAgent = new Map();

  let nRecv = 0;

  let nEvRecv = 0;

  let nSched = 0;

  let nHandler = 0;

  let nAgentRecs = 0;

  let nAgentSim = 0;

  let simMin = Infinity;

  let simMax = -Infinity;

  const kindIx = new Map(CATS.map((c, i) => [c, i]));

  for (let i = 0; i < nRecords; i++) {
    const r = RECORDS[i];
    for (const k in r) keySet.add(k);
    const ev = r.event;
    let code = EVCODE.get(ev) || 0;
    if (!code && typeof ev === "string" && ev.endsWith(schema.event_received)) code = E_EVRECV;
    evOf[i] = code;
    if (code === E_RECV) nRecv++;
    else if (code === E_EVRECV || code === E_ROLE) nEvRecv++;
    else if (code === E_SCHED) nSched++;
    else if (code === E_HANDLER) nHandler++;
    const kc = kindIx.get(r.category || "app");
    kindOf[i] = kc === undefined ? kindIx.get("app") : kc;
    catCount[kindOf[i]]++;
    const lvl = String(r.level || "info").toLowerCase();
    let li = levelIx.get(lvl);
    if (li === undefined) {
      li = levelNames.length;
      levelNames.push(lvl);
      levelIx.set(lvl, li);
    }
    levelOf[i] = li;
    const fail = String(ev || "").endsWith(schema.failed) || lvl === "error" || lvl === "critical";
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
      simTime[i] = st;
      if (st < simMin) simMin = st;
      if (st > simMax) simMax = st;
      if (ag >= 0) nAgentSim++;
    } else simTime[i] = NaN;
    if (ag >= 0) nAgentRecs++;
    if (r.id != null) idOf[i] = intern(r.id);
    if (r.cause != null) causeOf[i] = intern(r.cause);
    if (code === E_SENT) SENDS.push(i);
    else if (code === E_RECV && r.agent != null) lastRecvOfAgent.set(r.agent, i);
  }

  const nIds = idStr.length;

  const firstOf = new Int32Array(nIds).fill(-1);

  const recvOfId = new Int32Array(nIds).fill(-1);

  const idStart = new Int32Array(nIds + 1);

  const cStart = new Int32Array(nIds + 1);

  for (let i = 0; i < nRecords; i++) {
    const k = idOf[i];
    if (k >= 0) {
      idStart[k + 1]++;
      if (firstOf[k] < 0) firstOf[k] = i;
    }
    const c = causeOf[i];
    if (c >= 0) {
      cStart[c + 1]++;
      if (evOf[i] === E_RECV) recvOfId[c] = i;
    }
  }

  for (let k = 0; k < nIds; k++) {
    idStart[k + 1] += idStart[k];
    cStart[k + 1] += cStart[k];
  }

  const idList = new Int32Array(idStart[nIds]);

  const cList = new Int32Array(cStart[nIds]);

  {
    const a = idStart.slice(0, nIds);
    const b = cStart.slice(0, nIds);
    for (let i = 0; i < nRecords; i++) {
      if (idOf[i] >= 0) idList[a[idOf[i]]++] = i;
      if (causeOf[i] >= 0) {
        cList[b[causeOf[i]]++] = i;
        causeIx[i] = firstOf[causeOf[i]];
      }
    }
  }

  const eachId = (k, f) => {
    for (let j = idStart[k], e = idStart[k + 1]; j < e; j++) f(idList[j]);
  };

  const eachEffect = (k, f) => {
    for (let j = cStart[k], e = cStart[k + 1]; j < e; j++) f(cList[j]);
  };

  const recvOf = s => (idOf[s] >= 0 ? recvOfId[idOf[s]] : -1);

  const hasWorldRecs = laneTotal[WORLD] > 0;
  const presentLevels = [...levelNames].sort((a, b) => (LEVELS.indexOf(a) + 1 || 99) - (LEVELS.indexOf(b) + 1 || 99));

  const levelCount = new Array(levelNames.length).fill(0);

  for (let i = 0; i < nRecords; i++) levelCount[levelOf[i]]++;
  return {
    AGENTS,
    FAILS,
    SENDS,
    WORLD,
    aIx,
    agentOf,
    catCount,
    causeIx,
    eachEffect,
    eachId,
    evOf,
    failOf,
    firstOf,
    hasWorldRecs,
    idOf,
    keySet,
    kindOf,
    laneFailed,
    laneName,
    laneOf,
    laneTotal,
    lastRecvOfAgent,
    levelCount,
    levelIx,
    levelNames,
    levelOf,
    nAgentRecs,
    nAgentSim,
    nAgents,
    nEvRecv,
    nHandler,
    nIds,
    nRecords,
    nRecv,
    nSched,
    presentLevels,
    recvOf,
    simMax,
    simMin,
    simTime,
  };
}
