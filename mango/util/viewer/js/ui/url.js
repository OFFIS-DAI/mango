import { AGENTS, EDGES, WORLD, aIx, edgeIx, hasWorldRecs, nAgents, nRecords } from "../model.js";
import { state } from "../state.js";

const encId = s => encodeURIComponent(String(s)).replace(/~/g, "%7E");

export function readHash() {
  const out = {};
  const h = location.hash.replace(/^#/, "");
  if (!h) return out;
  for (const tok of h.split("&")) {
    const m = /^r(\d+)$/.exec(tok);
    if (m) {
      if (+m[1] < nRecords) out.sel = +m[1];
      continue;
    }
    const eq = tok.indexOf("=");
    const k = eq < 0 ? tok : tok.slice(0, eq);
    const v = eq < 0 ? "" : tok.slice(eq + 1);
    try {
      if (k === "v" && (v === "lanes" || v === "records")) out.view = v;
      else if (k === "a") {
        const ids = v
          .split(",")
          .filter(Boolean)
          .map(x => (x === "~" ? WORLD : aIx.get(decodeURIComponent(x))));
        const lanes = [...new Set(ids.filter(l => l !== undefined && (l !== WORLD || hasWorldRecs)))];
        // a link from another run may name only agents this trace lacks: fall back to the default lanes then
        if (lanes.length || !ids.length) out.lanes = lanes;
      } else if (k === "o" && v === "file") out.order = "file";
      else if (k === "fit") out.fit = true;
      else if (k === "link") {
        const t = v.replace(/<>/g, "%3C%3E").replace(/>/g, "%3E");
        const re = /%3C%3E|%3E/gi;
        for (let m2 = re.exec(t); m2; m2 = re.exec(t)) {
          const from = decodeURIComponent(t.slice(0, m2.index));
          const to = decodeURIComponent(t.slice(m2.index + m2[0].length));
          const e = edgeIx.get(from + "\u0000" + to);
          if (e !== undefined) {
            out.link = { e, both: m2[0].length > 3 };
            break;
          }
        }
      }
    } catch (err) {
      /* malformed token */
    }
  }
  if (out.view === "lanes" && !nAgents) delete out.view;
  return out;
}

let hashTimer = 0;

export function writeHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const parts = [];
    if (state.sel != null) parts.push("r" + state.sel);
    // the lanes, order and Fit travel with every link, so a Records link opens the same Lanes picture later
    if (state.view === "lanes") parts.push("v=lanes");
    if (state.lanes) parts.push("a=" + state.lanes.map(l => (l === WORLD ? "~" : encId(AGENTS[l]))).join(","));
    if (state.order === "file") parts.push("o=file");
    if (state.fit) parts.push("fit");
    if (state.link) {
      const E = EDGES[state.link.e];
      parts.push("link=" + encId(E.from) + (state.link.both ? "%3C%3E" : "%3E") + encId(E.to));
    }
    const h = parts.length ? "#" + parts.join("&") : "";
    if (h !== location.hash && !(h === "" && !location.hash))
      history.replaceState(null, "", h || location.pathname + location.search);
  }, 100);
}
