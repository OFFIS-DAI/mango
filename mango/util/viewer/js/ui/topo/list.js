import { natural, nf } from "../../core/util.js";
import { CLOCK, EDGES, timeLabel } from "../../model.js";
import { setLink } from "../actions.js";
import { $ } from "../dom.js";
import { focusView } from "../elements.js";
import { html } from "../html.js";
import { syncTopoList } from "./highlight.js";
import { setMapHover } from "./interact.js";

let listSort = { key: "from", dir: 1 };

const LIST_COLS = [
  ["from", "From"],
  ["to", "To"],
  ["sent", "Sent", 1],
  ["received", "Received", 1],
  ["lost", "Lost", 1],
  ["inFlight", "In flight", 1],
  ["types", "Types"],
  ["first", "First"],
  ["last", "Last"],
];

function listRowHTML(E, r) {
  const lost = E.lost - E.inFlight;
  const tf = v => (typeof v === "number" ? (CLOCK === "sim" ? timeLabel(v) : String(v)) : "");
  return (
    html`<tr tabindex="${r ? -1 : 0}" data-e="${E.k}" data-from="${E.from}" data-to="${E.to}" data-sent="${E.sent}" data-received="${E.received}"` +
    html` data-lost="${E.lost}" data-inflight="${E.inFlight}" data-types="${JSON.stringify([...E.types])}"` +
    html` data-first="${JSON.stringify(E.first)}" data-last="${JSON.stringify(E.last)}">` +
    html`<td>${E.from}</td><td>${E.to}</td><td class="n">${nf(E.sent)}</td><td class="n">${nf(E.received)}</td>` +
    html`<td class="n${lost ? " lo" : ""}">${nf(lost)}</td><td class="n${E.inFlight ? " fl" : ""}">${nf(E.inFlight)}</td>` +
    html`<td class="ty">${[...E.types].map(([t, c]) => `${t ?? "?"} ${nf(c)}`).join(", ")}</td><td>${tf(E.first)}</td><td>${tf(E.last)}</td></tr>`
  );
}

export function renderTopoList() {
  const box = $("topo-list");
  const val = (E, k) => {
    if (k === "lost") return E.lost - E.inFlight;
    if (k === "types") return E.types.size;
    if (k === "first" || k === "last") return typeof E[k] === "number" ? E[k] : -Infinity;
    return E[k];
  };
  const rows = EDGES.slice().sort((a, b) => {
    const k = listSort.key;
    const va = val(a, k);
    const vb = val(b, k);
    const c = typeof va === "number" && typeof vb === "number" ? va - vb : natural(String(va), String(vb));
    return listSort.dir * (c || natural(String(a.from), String(b.from)) || natural(String(a.to), String(b.to)));
  });
  const sortAttr = k => (listSort.key === k ? ` aria-sort="${listSort.dir > 0 ? "ascending" : "descending"}"` : "");
  box.innerHTML =
    `<table class="tlist" aria-label="Connections"><thead><tr>` +
    LIST_COLS.map(
      ([k, l, num]) =>
        `<th scope="col" class="${num ? "n" : ""}"${sortAttr(k)}><button data-sort="${k}">${l}</button></th>`,
    ).join("") +
    `</tr></thead><tbody>${rows.map(listRowHTML).join("")}</tbody></table>`;
  syncTopoList();
}

export function initTopoList() {
  $("topo-list").addEventListener("click", e => {
    const s = e.target.closest("[data-sort]");
    if (s) {
      listSort = { key: s.dataset.sort, dir: listSort.key === s.dataset.sort ? -listSort.dir : 1 };
      renderTopoList();
      $("topo-list").querySelector(`[data-sort="${s.dataset.sort}"]`).focus();
      return;
    }
    const tr = e.target.closest("tbody tr");
    if (tr) setLink(+tr.dataset.e, false);
  });
  $("topo-list").addEventListener("keydown", e => {
    const tr = e.target.closest("tbody tr");
    if (!tr) return;
    let to = null;
    if (e.key === "ArrowDown") to = tr.nextElementSibling;
    else if (e.key === "ArrowUp") to = tr.previousElementSibling;
    else if (e.key === "Home") to = tr.parentElement.firstElementChild;
    else if (e.key === "End") to = tr.parentElement.lastElementChild;
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setLink(+tr.dataset.e, false);
      return;
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      focusView();
      return;
    }
    if (!to) return;
    e.preventDefault();
    tr.tabIndex = -1;
    to.tabIndex = 0;
    to.focus();
  });
  $("topo-list").addEventListener("pointerover", e => {
    const tr = e.target.closest("tbody tr");
    setMapHover(tr ? { e: +tr.dataset.e } : null, null, true);
  });
  $("topo-list").addEventListener("pointerleave", () => setMapHover(null, null, true));
  $("topo-list").addEventListener("focusin", e => {
    const tr = e.target.closest("tbody tr");
    if (tr) setMapHover({ e: +tr.dataset.e }, null, true);
  });
}
