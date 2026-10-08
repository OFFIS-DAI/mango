// Lanes: one column per followed agent, records placed in rows so that time runs downwards and a
// record sits below what caused it, plus the connectors and task bars drawn between them.
// Rows and connectors are bucketed into tiles of TILE_ROWS rows, which the view mounts on demand.
import {
  C_WAIT,
  E_CANCEL,
  E_EMIT,
  E_EVRECV,
  E_FAIL,
  E_FIN,
  E_RECV,
  E_RESUME,
  E_ROLE,
  E_SCHED,
  E_SENT,
  E_START,
  E_WAIT,
  S_FLIGHT,
  S_LOST,
} from "./schema.js";

export const ROW_H = 22;
export const TILE_ROWS = 64;
export const TILE_H = ROW_H * TILE_ROWS;
export const K_MSG = 1;
export const K_SELF = 2;
export const K_COMB = 3;
export const K_ARC = 4;
export const K_LOST = 5;
export const K_STUB = 6;

export function lanesLayout(index, axis, graph) {
  const { SENDS, WORLD, causeIx, eachId, evOf, idOf, kindOf, laneOf, nAgents, nIds, nRecords } = index;
  const { MED_STEP, bandOf, bandTime, quantum, timeOf } = axis;
  const { MED_LAT, recvAgentOf, sendStatus } = graph;
  let lostSends = null;
  let taskSpans = null;
  const rowOf = new Int32Array(nRecords);
  const anchorIx = new Int32Array(nRecords);
  const vrowOf = new Int32Array(nRecords);
  const ghostRow = new Int32Array(nRecords).fill(-1);
  const ghostKey = s => (quantum ? Math.floor((timeOf[s] + MED_LAT) / quantum) : timeOf[s] + MED_LAT);

  function prepLanes() {
    if (lostSends) return;
    lostSends = SENDS.filter(s => sendStatus[s] === S_LOST && recvAgentOf[s] >= 0).sort(
      (a, b) => ghostKey(a) - ghostKey(b) || a - b,
    );
    taskSpans = [];
    const seen = new Uint8Array(nIds);
    for (let i = 0; i < nRecords; i++) {
      const k = idOf[i];
      if (k < 0 || seen[k] || (evOf[i] !== E_SCHED && evOf[i] !== E_START)) continue;
      seen[k] = 1;
      let start = -1;
      let first = -1;
      let end = -1;
      let w = -1;
      const waits = [];
      eachId(k, j => {
        const c = evOf[j];
        if (first < 0) first = j;
        if (c === E_START && start < 0) start = j;
        else if ((c === E_FIN || c === E_FAIL || c === E_CANCEL) && end < 0) end = j;
        else if (c === E_WAIT && w < 0) w = j;
        else if (c === E_RESUME && w >= 0) {
          waits.push(w, j);
          w = -1;
        }
      });
      if (w >= 0) waits.push(w, -1);
      taskSpans.push({ start: start >= 0 ? start : first, end, waits });
    }
  }

  function layoutLanes(lanes, fileOrder, showTasks, vis) {
    prepLanes();
    const nC = lanes.length;
    const col = new Int32Array(nAgents + 1).fill(-1);
    lanes.forEach((l, c) => {
      col[l] = c;
    });
    const WC = col[WORLD];
    const FILE = fileOrder;
    const next = new Int32Array(nC);
    const laneVis = new Int32Array(nAgents + 1);
    const bandRow = [];
    const bandKey = [];
    const gapRow = [];
    const gapLabel = [];
    let floor = 0;
    let maxRow = 0;
    let curKey = -Infinity;
    let bandStart = 0;
    let g = 0;
    let backwards = 0;
    let placed = 0;
    let nGhost = 0;
    for (const s of lostSends) ghostRow[s] = -1;
    const placeGhost = s => {
      const cr = col[recvAgentOf[s]];
      if (rowOf[s] < 0 || cr < 0) return;
      const r = FILE ? maxRow : Math.max(next[cr], bandStart, floor, rowOf[s] + 1);
      ghostRow[s] = r;
      next[cr] = r + 1;
      nGhost++;
      if (r + 1 > maxRow) maxRow = r + 1;
    };
    function openBand(k) {
      if (bandRow.length && MED_STEP > 0 && bandTime(k) - bandTime(curKey) >= 3 * MED_STEP) {
        gapRow.push(maxRow);
        gapLabel.push(bandTime(k) - bandTime(curKey));
        maxRow++;
      }
      bandStart = maxRow;
      bandRow.push(maxRow);
      bandKey.push(k);
      curKey = k;
    }
    for (let i = 0; i < nRecords; i++) {
      const p = causeIx[i];
      const pa = p >= 0 && p < i ? anchorIx[p] : -1;
      const c = col[laneOf[i]];
      if (vis[i]) laneVis[laneOf[i]]++;
      if (c < 0 || !vis[i] || kindOf[i] === C_WAIT) {
        rowOf[i] = -1;
        anchorIx[i] = pa;
        vrowOf[i] = c < 0 ? -1 : FILE ? maxRow : Math.max(next[c], bandStart, floor);
        continue;
      }
      const k = bandOf[i];
      if (k > curKey) {
        // a lost message expected before this instant gets a band of its own at its expected arrival
        while (g < lostSends.length && lostSends[g] < i && ghostKey(lostSends[g]) < k) {
          const s = lostSends[g++];
          if (ghostKey(s) > curKey && rowOf[s] >= 0 && col[recvAgentOf[s]] >= 0) openBand(ghostKey(s));
          placeGhost(s);
        }
        openBand(k);
      } else if (k < curKey) backwards++;
      while (g < lostSends.length && ghostKey(lostSends[g]) <= curKey && lostSends[g] < i) placeGhost(lostSends[g++]);
      let r;
      if (FILE || c === WC) r = maxRow;
      else r = Math.max(next[c], bandStart, floor, pa >= 0 ? rowOf[pa] + 1 : 0);
      rowOf[i] = r;
      anchorIx[i] = i;
      vrowOf[i] = r;
      next[c] = r + 1;
      placed++;
      if (r + 1 > maxRow) maxRow = r + 1;
      if (c === WC && !FILE) floor = r + 1;
    }
    while (g < lostSends.length) {
      const s = lostSends[g++];
      if (ghostKey(s) > curKey && rowOf[s] >= 0 && col[recvAgentOf[s]] >= 0) openBand(ghostKey(s));
      placeGhost(s);
    }

    const rowStart = new Int32Array(maxRow + 1);
    const rowRecs = new Int32Array(placed + nGhost);
    for (let i = 0; i < nRecords; i++) if (rowOf[i] >= 0) rowStart[rowOf[i] + 1]++;
    for (const s of lostSends) if (ghostRow[s] >= 0) rowStart[ghostRow[s] + 1]++;
    for (let r = 0; r < maxRow; r++) rowStart[r + 1] += rowStart[r];
    const fill = rowStart.slice(0, maxRow);
    const colStart = new Int32Array(nC + 1);
    for (let i = 0; i < nRecords; i++) if (rowOf[i] >= 0) colStart[col[laneOf[i]] + 1]++;
    for (let c = 0; c < nC; c++) colStart[c + 1] += colStart[c];
    const colRecs = new Int32Array(placed);
    const cfill = colStart.slice(0, nC);
    for (let i = 0; i < nRecords; i++) {
      if (rowOf[i] < 0) continue;
      rowRecs[fill[rowOf[i]]++] = i;
      colRecs[cfill[col[laneOf[i]]]++] = i;
    }
    for (const s of lostSends) if (ghostRow[s] >= 0) rowRecs[fill[ghostRow[s]]++] = -1 - s;
    const bandOfRow = new Int32Array(maxRow).fill(-1);
    for (let b = 0; b < bandRow.length; b++) {
      const end = b + 1 < bandRow.length ? bandRow[b + 1] : maxRow;
      for (let r = bandRow[b]; r < end; r++) bandOfRow[r] = b;
    }
    const gapAt = new Map();
    gapRow.forEach((r, k) => {
      bandOfRow[r] = -1;
      gapAt.set(r, gapLabel[k]);
    });

    const nTiles = Math.ceil(maxRow / TILE_ROWS);
    const cx = { kind: [], a: [], b: [], lo: [], hi: [] };
    const combs = new Map();
    const undelivered = new Map();
    const tileCx = Array.from({ length: nTiles }, () => []);
    const addCx = (kind, a, b, lo, hi) => {
      const id = cx.kind.length;
      cx.kind.push(kind);
      cx.a.push(a);
      cx.b.push(b);
      cx.lo.push(lo);
      cx.hi.push(hi);
      for (let t = Math.floor(lo / TILE_ROWS), te = Math.min(nTiles - 1, Math.floor(hi / TILE_ROWS)); t <= te; t++)
        tileCx[t].push(id);
      return id;
    };
    const colOf = i => col[laneOf[i]];
    for (let i = 0; i < nRecords; i++) {
      const r = rowOf[i];
      if (r < 0) continue;
      const ev = evOf[i];
      const p = causeIx[i];
      if (ev === E_RECV) {
        if (p >= 0 && evOf[p] === E_SENT && rowOf[p] >= 0)
          addCx(colOf(p) === colOf(i) ? K_SELF : K_MSG, p, i, Math.min(rowOf[p], r), Math.max(rowOf[p], r));
      } else if (ev === E_EVRECV) {
        if (p >= 0 && evOf[p] === E_EMIT && rowOf[p] >= 0 && colOf(p) === WC && rowOf[p] < r) {
          let cm = combs.get(p);
          if (!cm) {
            cm = { rows: [], recs: [] };
            combs.set(p, cm);
          }
          cm.recs.push(i);
        }
      } else if (ev === E_ROLE) {
        if (p >= 0 && rowOf[p] >= 0 && colOf(p) === colOf(i) && rowOf[p] < r) addCx(K_ARC, p, i, rowOf[p], r);
      } else if (ev === E_SENT) {
        if (sendStatus[i] === S_LOST && ghostRow[i] >= 0)
          undelivered.set(i, addCx(K_LOST, i, -1, Math.min(r, ghostRow[i]), Math.max(r, ghostRow[i])));
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

    const bars = [];
    const tileBars = Array.from({ length: nTiles }, () => []);
    const yAt = (i, fallback) =>
      i < 0 ? fallback : rowOf[i] >= 0 ? rowOf[i] * ROW_H + ROW_H / 2 : vrowOf[i] >= 0 ? vrowOf[i] * ROW_H : fallback;
    if (showTasks) {
      const H = maxRow * ROW_H;
      const byCol = Array.from({ length: nC }, () => []);
      for (const tk of taskSpans) {
        const c = colOf(tk.start);
        if (c < 0 || !(vis[tk.start] || (tk.end >= 0 && vis[tk.end]))) continue;
        const y0 = yAt(tk.start, -1);
        if (y0 < 0) continue;
        const y1 = tk.end >= 0 ? yAt(tk.end, H) : H;
        if (y1 < y0) continue;
        const idle = [];
        for (let w = 0; w < tk.waits.length; w += 2) {
          const a = yAt(tk.waits[w], -1);
          const b = tk.waits[w + 1] >= 0 ? yAt(tk.waits[w + 1], y1) : y1;
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
          for (
            let k = Math.floor(bar.y0 / TILE_H), ke = Math.min(nTiles - 1, Math.floor(bar.y1 / TILE_H));
            k <= ke;
            k++
          )
            tileBars[k].push(id);
        }
      }
    }
    return {
      lanes: lanes.slice(),
      col,
      WC,
      nC,
      maxRow,
      rowStart,
      rowRecs,
      colStart,
      colRecs,
      bandRow,
      bandKey,
      bandOfRow,
      gapAt,
      nTiles,
      chan,
      cx,
      combs,
      undelivered,
      tileCx,
      bars,
      tileBars,
      laneVis,
      backwards,
      placed,
      nGhost,
      FILE,
    };
  }
  return { rowOf, anchorIx, vrowOf, ghostRow, layoutLanes };
}
