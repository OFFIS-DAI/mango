import { CATS, CAT_LABEL, levelColor } from "../core/schema.js";
import { nf } from "../core/util.js";
import {
  AGENTS,
  EDGES,
  catCount,
  keySet,
  levelCount,
  levelIx,
  nRecords,
  prebuildText,
  presentCats,
  presentLevels,
  reverseEdge,
} from "../model.js";
import { state } from "../state.js";
import { setFilter } from "./actions.js";
import { $, isPhone } from "./dom.js";
import { html } from "./html.js";

const agentSel = $("agent");

const cut32 = a => {
  a = String(a);
  return a.length > 32 ? a.slice(0, 31) + "…" : a;
};

const catsEl = $("cats");

const levelsEl = $("levels");

export const q = $("q");

let qTimer = 0;

// field names come from the data, so new fields are filterable without changes here
const fieldKeys = [...keySet].filter(k => !k.startsWith("__") && k !== "timestamp").sort();

const fieldsOpen = !isPhone();

const linkChip = $("linkchip");

// the controls show the filters of the state, whichever action set them
export function syncFilters() {
  if (q.value !== state.q && document.activeElement !== q) q.value = state.q;
  if (agentSel.value !== state.agent) agentSel.value = state.agent;
  $("only-related").checked = state.onlyRelated;
  for (const b of catsEl.querySelectorAll("[data-cat]")) b.setAttribute("aria-pressed", state.cats.has(b.dataset.cat));
  for (const b of levelsEl.querySelectorAll("[data-level]"))
    b.setAttribute("aria-pressed", state.levels.has(b.dataset.level));
  renderLinkChip();
}

function renderLinkChip() {
  const lk = state.link;
  linkChip.hidden = !lk;
  if (!lk) {
    linkChip.innerHTML = "";
    return;
  }
  const E = EDGES[lk.e];
  const rev = reverseEdge(lk.e);
  const bothTitle = rev < 0 ? "No messages the other way" : "Also show the reverse direction";
  linkChip.innerHTML =
    html`<span class="lc-t" title="Connection filter">${E.from} ${lk.both ? "⇄" : "→"} ${E.to}</span>` +
    html`<button data-act="both" aria-pressed="${lk.both}" ${rev < 0 ? "disabled" : ""} title="${bothTitle}" aria-label="Both directions">⇄</button>` +
    `<button data-act="clear-link" title="Clear the connection filter" aria-label="Clear the connection filter">✕</button>`;
}

export function initFilters() {
  agentSel.innerHTML =
    `<option value="">All</option>` +
    AGENTS.map(a => html`<option value="${a}" title="${a}">${cut32(a)}</option>`).join("");
  agentSel.addEventListener("change", () => setFilter({ agent: agentSel.value }));
  catsEl.innerHTML = presentCats
    .map(
      c =>
        html`<button class="chip" style="--k: var(--c-${c})" data-cat="${c}" aria-pressed="${state.cats.has(c)}">` +
        html`<span class="dot"></span>${CAT_LABEL[c]} <span>${nf(catCount[CATS.indexOf(c)])}</span></button>`,
    )
    .join("");
  catsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    const c = b.dataset.cat;
    const cats = new Set(state.cats);
    cats.has(c) ? cats.delete(c) : cats.add(c);
    setFilter({ cats });
  });
  levelsEl.innerHTML =
    `<label>Level</label>` +
    presentLevels
      .map(
        l =>
          html`<button class="chip" style="--k: ${levelColor(l)}" data-level="${l}" aria-pressed="true">` +
          html`<span class="dot"></span>${l} <span>${nf(levelCount[levelIx.get(l)])}</span></button>`,
      )
      .join("");
  levelsEl.addEventListener("click", e => {
    const b = e.target.closest("[data-level]");
    if (!b) return;
    const l = b.dataset.level;
    const levels = new Set(state.levels);
    levels.has(l) ? levels.delete(l) : levels.add(l);
    setFilter({ levels });
  });
  q.addEventListener("focus", prebuildText);
  q.addEventListener("input", () => {
    clearTimeout(qTimer);
    if (nRecords > 20000) qTimer = setTimeout(() => setFilter({ q: q.value }), 150);
    else setFilter({ q: q.value });
  });
  $("only-related").addEventListener("change", e => setFilter({ onlyRelated: e.target.checked }));
  $("fields").innerHTML =
    `<button class="fdis" id="fields-t" aria-expanded="${fieldsOpen}" aria-controls="fields-l">` +
    `<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M3.2 1.5 6.8 5 3.2 8.5" fill="none" stroke="currentColor"` +
    ` stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>Fields <span>${nf(fieldKeys.length)}</span></button>` +
    `<span id="fields-l"${fieldsOpen ? "" : " hidden"}>` +
    fieldKeys.map(k => html`<button data-key="${k}">${k}</button>`).join(" ") +
    `</span>`;
  $("fields").addEventListener("click", e => {
    const t = e.target.closest("#fields-t");
    if (t) {
      const open = t.getAttribute("aria-expanded") !== "true";
      t.setAttribute("aria-expanded", open);
      $("fields-l").hidden = !open;
      return;
    }
    const b = e.target.closest("[data-key]");
    if (!b) return;
    q.value = (q.value.trim() ? q.value.trim() + " " : "") + b.dataset.key + "=";
    q.focus();
  });
}
