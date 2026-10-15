import { laneOf } from "../model.js";
import { state } from "../state.js";
import { followChain, goCause, goEffect, nextFail, select, setFit, setOrder, setView, toggleLane } from "./actions.js";
import { $, announce } from "./dom.js";
import { hideTip, tip } from "./feedback.js";
import { q } from "./filters.js";
import { openGoto } from "./goto.js";
import { html, raw } from "./html.js";
import { openPicker } from "./lanes/picker.js";
import { closePopover, openPopover } from "./popover.js";
import { focusMap, toggleWide } from "./topo/card.js";

const keysDlg = $("keys");

// Every keyboard shortcut, in the sections of the ? sheet, which is written from this table. Those with
// `run` are handled here (the Lanes ones in Lanes only); the others by the view or map that has the focus.
// `mod` is a modifier shown before the keys; `shown` replaces the keys on the sheet.
const SHORTCUTS = [
  [
    "Anywhere",
    [
      {
        keys: ["/"],
        does: "focus the filter",
        run: () => {
          q.focus();
          q.select();
        },
      },
      { keys: ["v"], does: "Records / Lanes", run: () => setView(state.view === "lanes" ? "records" : "lanes") },
      { keys: ["e", "E"], does: "next / previous failure", run: k => nextFail(k === "E") },
      { keys: ["[", "]"], does: "go to the cause / the first effect", run: k => (k === "[" ? goCause() : goEffect()) },
      { keys: ["f"], does: "follow the selection's chain in Lanes", run: () => followChain() },
      {
        keys: ["t"],
        does: "go to a time",
        run: () => openGoto(state.view === "lanes" ? $("corner") : $("th-time") || $("tab-records")),
      },
      { keys: ["g", "G"], does: "focus the map / widen it", run: k => (k === "g" ? focusMap() : toggleWide()) },
      { keys: ["Esc"], does: "close, then clear the selection" },
      { keys: ["?"], does: "this sheet", run: () => keysDlg.showModal() },
    ],
  ],
  [
    "Records",
    [
      { keys: ["↑", "↓"], does: "move the cursor row" },
      { keys: ["Home", "End", "PgUp", "PgDn"], does: "jump" },
      { keys: ["Enter", "Space"], does: "select or deselect" },
    ],
  ],
  [
    "Lanes",
    [
      { keys: ["↑", "↓"], does: "previous / next record in the lane" },
      { keys: ["←", "→"], does: "nearest record in the next lane" },
      { keys: ["Home", "End", "PgUp", "PgDn"], does: "jump within the lane" },
      { keys: ["Enter", "Space"], does: "select or deselect" },
      { keys: ["a"], does: "choose lanes", run: () => openPicker() },
      { keys: ["o"], does: "Packed / File order", run: () => setOrder(state.order === "file" ? "packed" : "file") },
      { keys: ["z"], does: "Fit all lanes", run: () => setFit(!state.fit) },
      {
        keys: ["x"],
        does: "stop following the cursor's lane",
        run: () => {
          if (state.cursor != null && state.lanes.includes(laneOf[state.cursor])) toggleLane(laneOf[state.cursor]);
        },
      },
      { mod: "Alt", keys: ["←", "→"], does: "move the cursor's lane" },
    ],
  ],
  [
    "Map",
    [
      { shown: "arrows", does: "nearest agent in that direction" },
      { keys: ["Home"], does: "the hub" },
      { keys: ["Enter"], does: "filter or follow; on a connection: show its messages" },
      { mod: "Shift", keys: ["Enter"], does: "follow the agent and its partners" },
      { keys: ["c", "C"], does: "next / previous connection" },
      { keys: ["Esc"], does: "back to the agent, then to the view" },
    ],
  ],
];

// key -> the shortcut it runs, with the view it is limited to
const RUN = new Map();
for (const [section, list] of SHORTCUTS)
  for (const s of list)
    if (s.run) for (const k of s.keys) RUN.set(k, { run: s.run, view: section === "Lanes" ? "lanes" : null });

function sheetHTML() {
  const kbd = keys => keys.map(k => html`<kbd>${k}</kbd>`).join(" ");
  const dt = s => s.shown ?? (s.mod ? html`<kbd>${s.mod}</kbd>+` : "") + kbd(s.keys);
  return SHORTCUTS.map(
    ([section, list]) =>
      html`<section><h3>${section}</h3><dl>${list.map(s => html`<dt>${raw(dt(s))}</dt><dd>${s.does}</dd>`)}</dl></section>`,
  ).join("");
}

export function initKeys() {
  keysDlg.querySelector(".kc").innerHTML = sheetHTML();
  $("keys-close").addEventListener("click", () => keysDlg.close());
  keysDlg.addEventListener("click", e => {
    if (e.target === keysDlg) keysDlg.close();
  });
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented) return;
    const t = e.target;
    const typing = t.closest && t.closest("input, select, textarea");
    if (e.key === "Escape") {
      if (keysDlg.open) return;
      if (openPopover()) {
        closePopover(true);
        return;
      }
      if (!tip.hidden) {
        hideTip();
        return;
      }
      if (typing) return;
      if (state.sel != null) {
        select(null);
        announce("Selection cleared");
      }
      return;
    }
    // letters inside the lane picker, a popover or the touch tooltip belong to it, not to the view behind
    if (
      typing ||
      e.ctrlKey ||
      e.metaKey ||
      e.altKey ||
      keysDlg.open ||
      (t.closest && t.closest(".pop, .tip, [role=listbox]"))
    )
      return;
    const shortcut = RUN.get(e.key);
    if (!shortcut || (shortcut.view && shortcut.view !== state.view)) return;
    shortcut.run(e.key);
    e.preventDefault();
  });
}
