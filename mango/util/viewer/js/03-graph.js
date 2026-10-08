  const S_LOST = 1, S_FLIGHT = 2;
  const edgeOfRec = new Int32Array(N).fill(-1), sendStatus = new Uint8Array(N), recvAgentOf = new Int32Array(N).fill(-1);
  const NODES = [], nodeIx = new Map(), EDGES = [], edgeIx = new Map();
  let unresolved = 0, MED_LAT = 0;
  {
    const node = id => {
      let k = nodeIx.get(id);
      if (k === undefined) {
        k = NODES.length;
        nodeIx.set(id, k);
        NODES.push({ id, k, lane: aIx.has(id) ? aIx.get(id) : -1, records: 0, failed: 0, sent: 0, received: 0, partners: new Set(), edges: [] });
      }
      return NODES[k];
    };
    for (const a of AG) { const d = node(a); d.records = laneTotal[aIx.get(a)]; }
    for (let i = 0; i < N; i++) if (agentOf[i] >= 0 && failOf[i]) NODES[agentOf[i]].failed++;
    const lat = [];
    for (const s of SENDS) {
      const r = RECORDS[s], ri = recvOf(s);
      const from = r.agent || r.sender;
      const to = r.receiver_id || (ri >= 0 ? RECORDS[ri].agent : null) || aidOf(r.receiver);
      if (from == null || to == null || from === "" || to === "") { unresolved++; continue; }
      const kk = from + "\u0000" + to;
      let e = edgeIx.get(kk);
      if (e === undefined) {
        e = EDGES.length;
        edgeIx.set(kk, e);
        const a = node(from), b = node(to);
        EDGES.push({ k: e, from, to, a: a.k, b: b.k, sent: 0, received: 0, lost: 0, inFlight: 0, types: new Map(),
          first: r.sim_time === undefined ? null : r.sim_time, last: null, sends: [] });
        a.edges.push(e);
        if (b !== a) b.edges.push(e);
      }
      const E = EDGES[e];
      E.sent++;
      E.sends.push(s);
      edgeOfRec[s] = e;
      if (aIx.has(to)) recvAgentOf[s] = aIx.get(to);
      if (ri >= 0) {
        E.received++;
        NODES[E.b].received++;
        edgeOfRec[ri] = e;
        lat.push(tt[ri] - tt[s]);
      }
      const type = r.content && typeof r.content === "object" && r.content.type !== undefined ? r.content.type : null;
      E.types.set(type, (E.types.get(type) || 0) + 1);
      E.last = r.sim_time === undefined ? null : r.sim_time;
      NODES[E.a].sent++;
      NODES[E.a].partners.add(E.b);
      NODES[E.b].partners.add(E.a);
    }
    MED_LAT = median(lat);
    for (const E of EDGES) {
      E.lost = E.sent - E.received;
      if (!E.lost) continue;
      for (const s of E.sends) {
        if (recvOf(s) >= 0) continue;
        const late = inferred[s] || tt[s] + MED_LAT >= TMAX;
        const later = (lastRecvOfAgent.get(E.to) ?? -1) > s;
        if (late && !later) { sendStatus[s] = S_FLIGHT; E.inFlight++; } else sendStatus[s] = S_LOST;
      }
    }
    for (const E of EDGES) E.lossy = E.lost - E.inFlight > 0;
  }
  const LOSSY = EDGES.filter(e => e.lossy).length, IN_FLIGHT = EDGES.reduce((n, e) => n + e.inFlight, 0);
  const degreeOf = l => l >= 0 && l < A ? NODES[l].partners.size - (NODES[l].partners.has(l) ? 1 : 0) : 0;
  const reverseEdge = e => { const r = edgeIx.get(EDGES[e].to + "\u0000" + EDGES[e].from); return r === undefined || r === e ? -1 : r; };
  const shortAid = s => aidOf(s) || (s == null ? "" : String(s));
  const typeOf = i => { const c = RECORDS[i].content; return c && typeof c === "object" && c.type != null ? String(c.type) : null; };
