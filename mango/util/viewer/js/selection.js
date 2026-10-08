// rel: 1 ancestor, 2 descendant, 3 selected
import { E_SENT } from "./core/schema.js";
import { WORLD, causeIx, eachEffect, eachId, edgeOfRec, evOf, idOf, laneOf, nIds, nRecords } from "./model.js";
import { state } from "./state.js";

export const rel = new Uint8Array(nRecords);

export let relList = [];

export let chain = [];

export let descList = [];

export let hops = [];

export const hopOfRec = new Map();

const seenId = new Uint8Array(nIds);

export function computeRelations() {
  for (const i of relList) rel[i] = 0;
  relList = [];
  chain = [];
  descList = [];
  hops = [];
  hopOfRec.clear();
  const s = state.sel;
  if (s == null) return;
  const seen = new Set([s]);
  for (let c = causeIx[s]; c >= 0 && !seen.has(c); c = causeIx[c]) {
    seen.add(c);
    chain.push(c);
  }
  const k0 = idOf[s];
  if (k0 >= 0) {
    const queue = [k0];
    const touched = [k0];
    seenId[k0] = 1;
    const add = j => {
      if (!rel[j] && j !== s) {
        rel[j] = 2;
        descList.push(j);
      }
    };
    for (let h = 0; h < queue.length; h++) {
      const k = queue[h];
      eachId(k, add);
      eachEffect(k, j => {
        add(j);
        const c = idOf[j];
        if (c >= 0 && !seenId[c]) {
          seenId[c] = 1;
          touched.push(c);
          queue.push(c);
        }
      });
    }
    for (const k of touched) seenId[k] = 0;
    descList.sort((a, b) => a - b);
  }
  relList = descList.slice();
  for (const c of chain) {
    if (!rel[c]) relList.push(c);
    rel[c] = 1;
  }
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

export const pathOfSel = () => (state.sel == null ? [] : [...chain].reverse().concat(state.sel));
