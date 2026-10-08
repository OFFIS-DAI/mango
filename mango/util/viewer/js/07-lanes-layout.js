  const ROW = 22, TR = 64, TILE = ROW * TR;
  const K_MSG = 1, K_SELF = 2, K_COMB = 3, K_ARC = 4, K_LOST = 5, K_STUB = 6;
  let L = null, LSTALE = true, GHOSTS = null, TASKS = null;
  const rowOf = new Int32Array(N), anchorIx = new Int32Array(N), vrowOf = new Int32Array(N), ghostRow = new Int32Array(N).fill(-1);
  const ghostKey = s => Q ? Math.floor((tt[s] + MED_LAT) / Q) : tt[s] + MED_LAT;

  function prepLanes() {
    if (GHOSTS) return;
    GHOSTS = SENDS.filter(s => sendStatus[s] === S_LOST && recvAgentOf[s] >= 0)
      .sort((a, b) => ghostKey(a) - ghostKey(b) || a - b);
    TASKS = [];
    const seen = new Uint8Array(M);
    for (let i = 0; i < N; i++) {
      const k = idOf[i];
      if (k < 0 || seen[k] || (evOf[i] !== E_SCHED && evOf[i] !== E_START)) continue;
      seen[k] = 1;
      let start = -1, first = -1, end = -1, w = -1;
      const waits = [];
      eachId(k, j => {
        const c = evOf[j];
        if (first < 0) first = j;
        if (c === E_START && start < 0) start = j;
        else if ((c === E_FIN || c === E_FAIL || c === E_CANCEL) && end < 0) end = j;
        else if (c === E_WAIT && w < 0) w = j;
        else if (c === E_RESUME && w >= 0) { waits.push(w, j); w = -1; }
      });
      if (w >= 0) waits.push(w, -1);
      TASKS.push({ start: start >= 0 ? start : first, end, waits });
    }
  }

  function layoutLanes() {
    prepLanes();
    const lanes = state.lanes || [], nC = lanes.length;
    const col = new Int32Array(A + 1).fill(-1);
    lanes.forEach((l, c) => { col[l] = c; });
    const WC = col[WORLD], FILE = state.order === "file";
    const next = new Int32Array(nC), laneVis = new Int32Array(A + 1);
    const bandRow = [], bandKey = [], gapRow = [], gapLabel = [];
    let floor = 0, maxRow = 0, curKey = -Infinity, bandStart = 0, g = 0, backwards = 0, placed = 0, nGhost = 0;
    for (const s of GHOSTS) ghostRow[s] = -1;
    const placeGhost = s => {
      const cr = col[recvAgentOf[s]];
      if (rowOf[s] < 0 || cr < 0) return;
      const r = FILE ? maxRow : Math.max(next[cr], bandStart, floor, rowOf[s] + 1);
      ghostRow[s] = r; next[cr] = r + 1; nGhost++;
      if (r + 1 > maxRow) maxRow = r + 1;
    };
    function openBand(k) {
      if (bandRow.length && MED_STEP > 0 && ktime(k) - ktime(curKey) >= 3 * MED_STEP) {
        gapRow.push(maxRow); gapLabel.push(ktime(k) - ktime(curKey)); maxRow++;
      }
      bandStart = maxRow; bandRow.push(maxRow); bandKey.push(k); curKey = k;
    }
    for (let i = 0; i < N; i++) {
      const p = causeIx[i];
      const pa = p >= 0 && p < i ? anchorIx[p] : -1;
      const c = col[laneOf[i]];
      if (vis[i]) laneVis[laneOf[i]]++;
      if (c < 0 || !vis[i] || kindOf[i] === C_WAIT) {
        rowOf[i] = -1; anchorIx[i] = pa;
        vrowOf[i] = c < 0 ? -1 : FILE ? maxRow : Math.max(next[c], bandStart, floor);
        continue;
      }
      const k = key[i];
      if (k > curKey) {
        // a lost message expected before this instant gets a band of its own at its expected arrival
        while (g < GHOSTS.length && GHOSTS[g] < i && ghostKey(GHOSTS[g]) < k) {
          const s = GHOSTS[g++];
          if (ghostKey(s) > curKey && rowOf[s] >= 0 && col[recvAgentOf[s]] >= 0) openBand(ghostKey(s));
          placeGhost(s);
        }
        openBand(k);
      } else if (k < curKey) backwards++;
      while (g < GHOSTS.length && ghostKey(GHOSTS[g]) <= curKey && GHOSTS[g] < i) placeGhost(GHOSTS[g++]);
      let r;
      if (FILE || c === WC) r = maxRow;
      else r = Math.max(next[c], bandStart, floor, pa >= 0 ? rowOf[pa] + 1 : 0);
      rowOf[i] = r; anchorIx[i] = i; vrowOf[i] = r; next[c] = r + 1; placed++;
      if (r + 1 > maxRow) maxRow = r + 1;
      if (c === WC && !FILE) floor = r + 1;
    }
    while (g < GHOSTS.length) {
      const s = GHOSTS[g++];
      if (ghostKey(s) > curKey && rowOf[s] >= 0 && col[recvAgentOf[s]] >= 0) openBand(ghostKey(s));
      placeGhost(s);
    }

    const rowStart = new Int32Array(maxRow + 1), rowRecs = new Int32Array(placed + nGhost);
    for (let i = 0; i < N; i++) if (rowOf[i] >= 0) rowStart[rowOf[i] + 1]++;
    for (const s of GHOSTS) if (ghostRow[s] >= 0) rowStart[ghostRow[s] + 1]++;
    for (let r = 0; r < maxRow; r++) rowStart[r + 1] += rowStart[r];
    const fill = rowStart.slice(0, maxRow);
    const colStart = new Int32Array(nC + 1);
    for (let i = 0; i < N; i++) if (rowOf[i] >= 0) colStart[col[laneOf[i]] + 1]++;
    for (let c = 0; c < nC; c++) colStart[c + 1] += colStart[c];
    const colRecs = new Int32Array(placed), cfill = colStart.slice(0, nC);
    for (let i = 0; i < N; i++) {
      if (rowOf[i] < 0) continue;
      rowRecs[fill[rowOf[i]]++] = i;
      colRecs[cfill[col[laneOf[i]]]++] = i;
    }
    for (const s of GHOSTS) if (ghostRow[s] >= 0) rowRecs[fill[ghostRow[s]]++] = -1 - s;
    const bandOfRow = new Int32Array(maxRow).fill(-1);
    for (let b = 0; b < bandRow.length; b++) {
      const end = b + 1 < bandRow.length ? bandRow[b + 1] : maxRow;
      for (let r = bandRow[b]; r < end; r++) bandOfRow[r] = b;
    }
    const gapAt = new Map();
    gapRow.forEach((r, k) => { bandOfRow[r] = -1; gapAt.set(r, gapLabel[k]); });

    const nTiles = Math.ceil(maxRow / TR);
    const cx = { kind: [], a: [], b: [], lo: [], hi: [] }, combs = new Map(), undelivered = new Map();
    const tileCx = Array.from({ length: nTiles }, () => []);
    const addCx = (kind, a, b, lo, hi) => {
      const id = cx.kind.length;
      cx.kind.push(kind); cx.a.push(a); cx.b.push(b); cx.lo.push(lo); cx.hi.push(hi);
      for (let t = Math.floor(lo / TR), te = Math.min(nTiles - 1, Math.floor(hi / TR)); t <= te; t++) tileCx[t].push(id);
      return id;
    };
    const colOf = i => col[laneOf[i]];
    for (let i = 0; i < N; i++) {
      const r = rowOf[i];
      if (r < 0) continue;
      const ev = evOf[i], p = causeIx[i];
      if (ev === E_RECV) {
        if (p >= 0 && evOf[p] === E_SENT && rowOf[p] >= 0) addCx(colOf(p) === colOf(i) ? K_SELF : K_MSG, p, i, Math.min(rowOf[p], r), Math.max(rowOf[p], r));
      } else if (ev === E_EVRECV) {
        if (p >= 0 && evOf[p] === E_EMIT && rowOf[p] >= 0 && colOf(p) === WC && rowOf[p] < r) {
          let cm = combs.get(p);
          if (!cm) { cm = { rows: [], recs: [] }; combs.set(p, cm); }
          cm.recs.push(i);
        }
      } else if (ev === E_ROLE) {
        if (p >= 0 && rowOf[p] >= 0 && colOf(p) === colOf(i) && rowOf[p] < r) addCx(K_ARC, p, i, rowOf[p], r);
      } else if (ev === E_SENT) {
        if (sendStatus[i] === S_LOST && ghostRow[i] >= 0) undelivered.set(i, addCx(K_LOST, i, -1, Math.min(r, ghostRow[i]), Math.max(r, ghostRow[i])));
        else if (sendStatus[i] === S_FLIGHT) undelivered.set(i, addCx(K_STUB, i, -1, r, r + 2));
      }
    }
    for (const [e, cm] of combs) {
      let hi = rowOf[e];
      for (const j of cm.recs) if (rowOf[j] > hi) hi = rowOf[j];
      addCx(K_COMB, e, -1, rowOf[e], hi);
    }
    // rows in which a message runs down a lane's channel; labels from the left stop before it there
    const chan = new Uint8Array(nC * maxRow);
    for (let id = 0; id < cx.kind.length; id++) {
      const k = cx.kind[id];
      if (k !== K_MSG && k !== K_LOST) continue;
      const c = k === K_MSG ? colOf(cx.b[id]) : col[recvAgentOf[cx.a[id]]];
      if (c >= 0) chan.fill(1, c * maxRow + cx.lo[id], c * maxRow + cx.hi[id] + 1);
    }

    const bars = [], tileBars = Array.from({ length: nTiles }, () => []);
    const yAt = (i, fallback) => i < 0 ? fallback : rowOf[i] >= 0 ? rowOf[i] * ROW + ROW / 2 : vrowOf[i] >= 0 ? vrowOf[i] * ROW : fallback;
    if (state.cats.has("task")) {
      const H = maxRow * ROW;
      const byCol = Array.from({ length: nC }, () => []);
      for (const tk of TASKS) {
        const c = colOf(tk.start);
        if (c < 0 || !(vis[tk.start] || (tk.end >= 0 && vis[tk.end]))) continue;
        const y0 = yAt(tk.start, -1);
        if (y0 < 0) continue;
        const y1 = tk.end >= 0 ? yAt(tk.end, H) : H;
        if (y1 < y0) continue;
        const idle = [];
        for (let w = 0; w < tk.waits.length; w += 2) {
          const a = yAt(tk.waits[w], -1), b = tk.waits[w + 1] >= 0 ? yAt(tk.waits[w + 1], y1) : y1;
          if (a >= 0 && b > a) idle.push(Math.max(a, y0), Math.min(b, y1));
        }
        byCol[c].push({ c, y0, y1, idle, end: tk.end >= 0 ? evOf[tk.end] : 0, open: tk.end < 0, t: 0 });
      }
      for (const list of byCol) {
        list.sort((a, b) => a.y0 - b.y0);
        const endAt = [-Infinity, -Infinity, -Infinity];
        for (const bar of list) {
          let t = endAt.findIndex(y => y <= bar.y0);
          if (t < 0) t = 2;
          bar.t = t;
          endAt[t] = Math.max(endAt[t], bar.y1);
          const id = bars.length;
          bars.push(bar);
          for (let k = Math.floor(bar.y0 / TILE), ke = Math.min(nTiles - 1, Math.floor(bar.y1 / TILE)); k <= ke; k++) tileBars[k].push(id);
        }
      }
    }
    L = { lanes: lanes.slice(), col, WC, nC, maxRow, rowStart, rowRecs, colStart, colRecs, bandRow, bandKey, bandOfRow, gapAt, nTiles, chan,
      cx, combs, undelivered, tileCx, bars, tileBars, laneVis, backwards, placed, nGhost, FILE };
    LSTALE = false;
  }
  const colOfRec = i => L.col[laneOf[i]];
