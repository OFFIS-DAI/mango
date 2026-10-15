// What the buttons written into the page do, by their data-act; one listener serves them all.
import { pending, state } from "../state.js";
import { clearFilters, ensureLane, followChain, setLink, toggleLane } from "./actions.js";
import { motion } from "./dom.js";
import { panel } from "./elements.js";
import { openPicker } from "./lanes/picker.js";

const BUTTONS = {
  "clear-filters": () => clearFilters(),
  follow: () => followChain(),
  pick: () => openPicker(),
  lane: b => toggleLane(+b.dataset.l),
  both: () => setLink(state.link.e, !state.link.both),
  "clear-link": () => setLink(null),
  "add-lane": () => {
    if (state.sel == null) return;
    ensureLane(state.sel, false);
    pending.reveal = state.sel;
  },
  details: () => panel.scrollIntoView({ behavior: motion() ? "smooth" : "auto", block: "start" }),
};

export function initButtons() {
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (b && !b.disabled && BUTTONS[b.dataset.act]) BUTTONS[b.dataset.act](b);
  });
}
