import { clamp, nf, plural } from "../../core/util.js";
import {
  AGENTS,
  WORLD,
  degreeOf,
  hasWorldRecs,
  laneFailed,
  laneName,
  laneOf,
  laneTotal,
  nAgents,
} from "../../model.js";
import { pathOfSel } from "../../selection.js";
import { state } from "../../state.js";
import { insertSorted, setLanes, setView, toggleLane, undoLanes, withWorld } from "../actions.js";
import { $ } from "../dom.js";
import { on } from "../events.js";
import { toast } from "../feedback.js";
import { html, raw } from "../html.js";
import { closePopover, showPopover } from "../popover.js";

const pickBtn = $("pick");

export function updatePickButton() {
  const n = (state.lanes || []).filter(l => l !== WORLD).length;
  pickBtn.innerHTML =
    `<span class="lg">Lanes <b>${nf(n)}</b> of ${nf(nAgents)}</span><span class="sh"><b>${nf(n)}</b>/${nf(nAgents)}</span>` +
    ` <span class="caret" aria-hidden="true">▾</span>`;
  pickBtn.setAttribute("aria-label", `Lanes: following ${nf(n)} of ${plural(nAgents, "agent")}`);
}

let picker = null;

function closePicker(refocus) {
  if (picker) closePopover(refocus);
}

export function openPicker() {
  if (picker) {
    closePicker(true);
    return;
  }
  if (state.view !== "lanes") setView("lanes", { keepLanes: true });
  picker = document.createElement("div");
  picker.className = "pop";
  picker.setAttribute("role", "dialog");
  picker.setAttribute("aria-label", "Follow agents");
  const nFailing = AGENTS.filter((a, l) => laneFailed[l]).length;
  picker.innerHTML =
    `<h3>Follow agents</h3><input type="search" id="pk-q" placeholder="Search agents…" aria-label="Search agents" autocomplete="off">` +
    `<div class="pbtn"><button class="tool" data-pk="all">All</button><button class="tool" data-pk="none">None</button>` +
    `<button class="tool" data-pk="chain" ${state.sel == null ? "disabled" : ""}>Chain of selection</button>` +
    `<button class="tool" data-pk="fail" ${nFailing ? "" : "disabled"}>With failures (${nFailing})</button></div>` +
    `<ul class="plist" id="pk-list" role="listbox" aria-multiselectable="true" aria-label="Agents" tabindex="0"></ul>`;
  showPopover(picker, {
    anchor: pickBtn,
    room: 200,
    keep: pickBtn,
    onClose: () => {
      picker = null;
      pickBtn.setAttribute("aria-expanded", "false");
    },
  });
  pickBtn.setAttribute("aria-expanded", "true");
  const qi = picker.querySelector("#pk-q");
  const list = picker.querySelector("#pk-list");
  let active = 0;
  let items = [];
  const ids = (hasWorldRecs ? [WORLD] : []).concat(AGENTS.map((a, l) => l));
  const itemHTML = (l, k, lanes) => {
    const f = laneFailed[l];
    const p = l === WORLD ? 0 : degreeOf(l);
    const meta =
      html`${nf(laneTotal[l])}${l === WORLD ? " records" : ""}` +
      (f ? html` · <b>${nf(f)} failed</b>` : "") +
      (l === WORLD ? "" : ` · ${plural(p, "partner")}`);
    const cls = (l === WORLD ? "world " : "") + (k === active ? "act" : "");
    return (
      html`<li role="option" id="pk-${l}" data-l="${l}" aria-selected="${lanes.includes(l)}" class="${cls}">` +
      html`<span class="cb"></span><span class="pn">${laneName(l)}</span><span class="pm">${raw(meta)}</span></li>`
    );
  };
  const render = () => {
    const t = qi.value.trim().toLowerCase();
    items = ids.filter(l => !t || laneName(l).toLowerCase().includes(t));
    active = clamp(active, 0, Math.max(0, items.length - 1));
    const lanes = state.lanes || [];
    list.innerHTML =
      items.map((l, k) => itemHTML(l, k, lanes)).join("") ||
      `<li class="pm" aria-disabled="true">No agent matches</li>`;
    if (items.length) list.setAttribute("aria-activedescendant", "pk-" + items[active]);
    list.querySelector("li.act")?.scrollIntoView({ block: "nearest" });
  };
  const addMatches = () => {
    const add = items.filter(l => !(state.lanes || []).includes(l));
    if (!add.length) return;
    const prev = (state.lanes || []).slice();
    let ls = prev;
    for (const l of add) ls = insertSorted(ls, l);
    setLanes(ls);
    toast(`Added ${plural(add.length, "lane")}`, [undoLanes(prev)]);
  };
  render();
  qi.focus();
  qi.addEventListener("input", () => {
    active = 0;
    render();
  });
  qi.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      addMatches();
      render();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      list.focus();
    }
  });
  let typed = "";
  let typedAt = 0;
  const typeAhead = ch => {
    const now = performance.now();
    typed = now - typedAt < 800 ? typed + ch.toLowerCase() : ch.toLowerCase();
    typedAt = now;
    // the same letter again steps to the next agent starting with it
    const q = [...typed].every(c => c === typed[0]) ? typed[0] : typed;
    const from = q.length > 1 ? active : active + 1;
    for (let n = 0; n < items.length; n++) {
      const k = (from + n) % items.length;
      if (laneName(items[k]).toLowerCase().startsWith(q)) {
        active = k;
        return true;
      }
    }
    return false;
  };
  list.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp")
      active = clamp(active + (e.key === "ArrowDown" ? 1 : -1), 0, items.length - 1);
    else if (e.key === "Home" || e.key === "End") active = e.key === "Home" ? 0 : items.length - 1;
    else if ((e.key === " " || e.key === "Enter") && items.length) toggleLane(items[active], true);
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && items.length) typeAhead(e.key);
    else return;
    e.preventDefault();
    render();
  });
  list.addEventListener("click", e => {
    const li = e.target.closest("li[data-l]");
    if (!li) return;
    active = items.indexOf(+li.dataset.l);
    toggleLane(+li.dataset.l, true);
    render();
  });
  picker.addEventListener("click", e => {
    const b = e.target.closest("[data-pk]");
    if (!b || b.disabled) return;
    const prev = (state.lanes || []).slice();
    const k = b.dataset.pk;
    if (k === "all") setLanes(ids.slice());
    else if (k === "none") setLanes([]);
    else if (k === "chain") {
      const out = [];
      for (const i of pathOfSel()) if (!out.includes(laneOf[i])) out.push(laneOf[i]);
      setLanes(out.includes(WORLD) ? [WORLD, ...out.filter(l => l !== WORLD)] : out);
    } else if (k === "fail") setLanes(withWorld(AGENTS.map((a, l) => l).filter(l => laneFailed[l])));
    toast("Lanes changed", [undoLanes(prev)]);
    render();
  });
}

export function initPicker() {
  on("leaving-view", () => closePicker(false));
  pickBtn.addEventListener("click", openPicker);
}
