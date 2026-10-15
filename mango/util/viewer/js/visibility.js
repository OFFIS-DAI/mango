import { CATS } from "./core/schema.js";
import { lowerBound } from "./core/util.js";
import {
  EDGES,
  aIx,
  agentOf,
  edgeOfRec,
  kindOf,
  levelNames,
  levelOf,
  nRecords,
  reverseEdge,
  textMask,
} from "./model.js";
import { rel } from "./selection.js";
import { state } from "./state.js";

export const vis = new Uint8Array(nRecords);

const visBuf = new Int32Array(nRecords);

export let visList = visBuf.subarray(0, 0);

export const linkMatch = new Int32Array(EDGES.length);

export let filtersActive = false;

const linkEdges = () => {
  if (!state.link) return null;
  const s = new Uint8Array(EDGES.length);
  s[state.link.e] = 1;
  if (state.link.both && reverseEdge(state.link.e) >= 0) s[reverseEdge(state.link.e)] = 1;
  return s;
};

export function computeVisibility() {
  const catOK = CATS.map(c => state.cats.has(c));
  const lvOK = levelNames.map(l => state.levels.has(l));
  const tm = textMask(state.q);
  const lk = linkEdges();
  const agentF = state.view === "records" && state.agent !== "" ? (aIx.get(state.agent) ?? -2) : -1;
  const relF = state.onlyRelated && state.sel != null ? rel : null;
  filtersActive = !!(tm || lk || agentF !== -1 || relF || lvOK.some(v => !v));
  linkMatch.fill(0);
  let n = 0;
  for (let i = 0; i < nRecords; i++) {
    const e = edgeOfRec[i];
    const base =
      lvOK[levelOf[i]] &&
      (!tm || tm[i]) &&
      (!relF || relF[i]) &&
      (!lk || (e >= 0 && lk[e])) &&
      (agentF === -1 || agentOf[i] === agentF);
    if (base && e >= 0) linkMatch[e]++;
    if (base && catOK[kindOf[i]]) {
      vis[i] = 1;
      visBuf[n++] = i;
    } else vis[i] = 0;
  }
  visList = visBuf.subarray(0, n);
}

export const visPosAtOrAfter = i => lowerBound(visList, i);

export const visPos = i => {
  const p = visPosAtOrAfter(i);
  return p < visList.length && visList[p] === i ? p : -1;
};
