import { ROW_H } from "../../core/lanes-layout.js";
import {
  CATS,
  CORE,
  E_CYCLE,
  E_EMIT,
  E_EVRECV,
  E_HANDLER,
  E_RECV,
  E_ROLE,
  E_SCHED,
  E_SENT,
  S_FLIGHT,
  S_LOST,
} from "../../core/schema.js";
import { fmt } from "../../core/util.js";
import {
  RECORDS,
  causeIx,
  evOf,
  failOf,
  kindOf,
  laneName,
  laneOf,
  nRecords,
  recTime,
  recvAgentOf,
  rowOf,
  sendStatus,
  shortAid,
  typeOf,
} from "../../model.js";
import { receiverName } from "../describe.js";
import { html } from "../html.js";
import { charW, geom } from "./geometry.js";
import { colOfRec, layout } from "./layout.js";

const SKIP_KV = new Set([...CORE, "src", "process", "sender", "receiver", "receiver_id", "content"]);

// the label of a record in Lanes: what it is and, where its lane does not show it, where it goes or comes from
export function labelHTML(i) {
  const r = RECORDS[i];
  const ev = evOf[i];
  const ty = typeOf(i);
  if (ev === E_SENT) {
    const rl = recvAgentOf[i];
    const hidden = rl < 0 || layout.col[rl] < 0;
    const to = hidden ? html`<span class="m"> → ${receiverName(i)}</span>` : "";
    let status = "";
    if (sendStatus[i] === S_LOST)
      status = html`<span class="lo"> · lost</span>${hidden ? html`<i class="xb"></i>` : ""}`;
    else if (sendStatus[i] === S_FLIGHT) status = html`<span class="m"> · in flight</span>`;
    return html`${ty || "message"}${to}${status}`;
  }
  if (ev === E_RECV) {
    const p = causeIx[i];
    const from = r.sender != null ? shortAid(r.sender) : "?";
    return html`${ty || "message"}${p < 0 || rowOf[p] < 0 ? html`<span class="m"> ← ${from}</span>` : ""}`;
  }
  if (ev === E_EMIT)
    return html`${ty || "event"}<span class="m"> → ${r.target != null ? shortAid(r.target) : "all"}</span>`;
  if (ev === E_EVRECV || ev === E_ROLE) return html`${ty || r.event}`;
  if (ev === E_HANDLER) return html`<span class="m">ƒ </span>${r.handler ?? "handler"}`;
  let kv = "";
  let n = 0;
  for (const k in r) {
    if (n >= 3 || SKIP_KV.has(k) || k.startsWith("sim_") || k.startsWith("__")) continue;
    let v = fmt(r[k]);
    if (v.length > 40) v = v.slice(0, 39) + "…";
    kv += ` ${k}=${v}`;
    n++;
  }
  return html`${r.event}${kv ? html`<span class="m">${kv}</span>` : ""}`;
}

export const ghostLabel = s => html`lost · ${typeOf(s) || "message"} ← ${laneName(laneOf[s])}`;

export const textLen = markup => markup.replace(/<[^>]*>/g, "").replace(/&(?:amp|lt|gt|quot);/g, "_").length;

export const glyphClass = i => {
  const ev = evOf[i];
  if (ev === E_RECV || ev === E_EVRECV || ev === E_ROLE) return "g-ring";
  if (ev === E_EMIT || CATS[kindOf[i]] === "run") return "g-dia";
  if (ev === E_SCHED) return "g-sq";
  if (ev === E_HANDLER || ev === E_CYCLE) return "g-sm";
  return "";
};

export const ariaOf = i => {
  const r = RECORDS[i];
  return `${laneName(laneOf[i])}, ${recTime(i)}, ${r.event}${failOf[i] ? ", failed" : ""}${r.cause != null ? `, caused by ${r.cause}` : ""}`;
};

// Records of one row sorted by lane; a label runs until shortly before the next lifeline that has a record in the same row.
export function rowEntries(r) {
  const ents = [];
  for (let j = layout.rowStart[r]; j < layout.rowStart[r + 1]; j++) {
    const v = layout.rowRecs[j];
    ents.push([v, v >= 0 ? colOfRec(v) : layout.col[recvAgentOf[-1 - v]]]);
  }
  if (ents.length > 1) ents.sort((a, b) => a[1] - b[1]);
  return ents;
}

// a message drops into its receiver along a channel CHAN px left of the lifeline; labels end LGAP px before a lifeline
export const CHAN = 17;

const LGAP = 23;

export function labelLim(ents, k, r) {
  const g = geom;
  const c = ents[k][1];
  const nx = k + 1 < ents.length ? ents[k + 1][1] : -1;
  const start = g.life[c] + 16;
  let lim = nx >= 0 && nx !== c ? g.life[nx] - LGAP - start : g.width - start - 8;
  if (c !== layout.WC) {
    lim = Math.min(lim, Math.max(g.W - 28, 420));
    const end = nx >= 0 && nx !== c ? nx : layout.nC;
    for (let c2 = c + 1; c2 < end && g.life[c2] - LGAP - start < lim; c2++) {
      if (layout.chan[c2 * layout.maxRow + r]) {
        lim = g.life[c2] - LGAP - start;
        break;
      }
    }
  }
  return Math.max(0, Math.floor(lim));
}

export let labelGen = 1;

// label widths are cached per record; a new generation measures them again
export function remeasureLabels() {
  labelGen++;
}

export const labelW = new Float32Array(nRecords);

export const labelStamp = new Int32Array(nRecords);

export function labelEnd(i) {
  const c = colOfRec(i);
  if (!geom.labels || rowOf[i] < 0) return geom.life[c];
  if (labelStamp[i] !== labelGen) {
    const ents = rowEntries(rowOf[i]);
    const k = ents.findIndex(e => e[0] === i);
    labelW[i] = Math.min(textLen(labelHTML(i)) * charW, labelLim(ents, k, rowOf[i]));
    labelStamp[i] = labelGen;
  }
  return geom.life[c] + 16 + labelW[i];
}

export function rowBoxes(r, cache) {
  let out = cache.get(r);
  if (out) return out;
  out = [];
  if (r >= 0 && r < layout.maxRow) {
    const g = geom;
    const ents = rowEntries(r);
    const y = r * ROW_H;
    for (let k = 0; k < ents.length; k++) {
      const [v, c] = ents[k];
      if (c < 0) continue;
      out.push([g.life[c] - 7, y + 4, g.life[c] + 7, y + 18]);
      if (!g.labels) continue;
      const tw = Math.min(textLen(v < 0 ? ghostLabel(-1 - v) : labelHTML(v)) * charW, labelLim(ents, k, r));
      if (tw > 0) out.push([g.life[c] + 14, y + 5, g.life[c] + 18 + tw, y + 18]);
    }
  }
  cache.set(r, out);
  return out;
}
