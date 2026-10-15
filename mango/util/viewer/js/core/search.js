// Search over whole records (free text) or single fields (field=value), with every token required.
import { fmt } from "./util.js";

export function createSearch(RECORDS, keySet, idle) {
  const nRecords = RECORDS.length;
  const textCache = new Array(nRecords);

  let textBuilt = 0;

  let textStarted = false;

  const textOf = i => textCache[i] ?? (textCache[i] = JSON.stringify(RECORDS[i]).toLowerCase());

  // the lowercase copies double the heap on big traces, so they are only built once someone starts searching
  function prebuildText() {
    if (textStarted) return;
    textStarted = true;
    idle(buildText);
  }

  function buildText(deadline) {
    const until = performance.now() + 12;
    while (textBuilt < nRecords && (deadline ? deadline.timeRemaining() > 2 : performance.now() < until)) {
      const end = Math.min(nRecords, textBuilt + 400);
      for (let i = textBuilt; i < end; i++) textOf(i);
      textBuilt = end;
    }
    if (textBuilt < nRecords) idle(buildText);
  }

  const lowerKeys = new Map();

  for (const k of keySet) {
    const l = k.toLowerCase();
    (lowerKeys.get(l) || lowerKeys.set(l, []).get(l)).push(k);
  }

  let maskFor = null;

  let maskVal = null;

  function textMask(query) {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) return null;
    const sig = tokens.join(" ");
    if (maskFor === sig) return maskVal;
    const mask = new Uint8Array(nRecords).fill(1);
    for (const t of tokens) {
      const eq = t.indexOf("=");
      if (eq > 0) {
        const cands = lowerKeys.get(t.slice(0, eq)) || [];
        const want = t.slice(eq + 1);
        for (let i = 0; i < nRecords; i++) {
          if (!mask[i]) continue;
          const r = RECORDS[i];
          let k;
          if (cands.length === 1) k = Object.prototype.hasOwnProperty.call(r, cands[0]) ? cands[0] : undefined;
          else k = Object.keys(r).find(x => x.toLowerCase() === t.slice(0, eq));
          if (k === undefined) mask[i] = 0;
          else if (want !== "" && !fmt(r[k]).toLowerCase().includes(want)) mask[i] = 0;
        }
      } else {
        for (let i = 0; i < nRecords; i++) if (mask[i] && !textOf(i).includes(t)) mask[i] = 0;
      }
    }
    maskFor = sig;
    maskVal = mask;
    return mask;
  }
  return {
    prebuildText,
    textMask,
  };
}
