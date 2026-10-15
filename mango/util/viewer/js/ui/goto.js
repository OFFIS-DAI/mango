import { ROW_H } from "../core/lanes-layout.js";
import { ABSOLUTE, bandOf, bandTime, bandTimesAll, time0, timeLabel } from "../model.js";
import { state } from "../state.js";
import { $, announce, flash, motion } from "./dom.js";
import { focusView, gutterEl, lanesBox, lhead, recBox, rowsEl } from "./elements.js";
import { html } from "./html.js";
import { layout } from "./lanes/layout.js";
import { renderLanes } from "./lanes/tiles.js";
import { closePopover, showPopover } from "./popover.js";
import { TABLE_ROW_H, renderTable } from "./table.js";
import { visList } from "../visibility.js";

let gotoEl = null;

function closeGoto(refocus) {
  if (gotoEl) closePopover(refocus);
}

function parseGoto(s) {
  s = s.trim().toLowerCase();
  let m = /^(\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?$/.exec(s);
  if (m) {
    const tod = +m[1] * 3600 + +m[2] * 60 + (m[3] ? +m[3] : 0) + (m[4] ? +m[4] : 0);
    if (!ABSOLUTE) return time0 + tod;
    let t = Math.floor(time0 / 86400) * 86400 + tod;
    if (t < time0 - 1e-9) t += 86400;
    return t;
  }
  m = /^\+?\s*((?:\d+(?:\.\d+)?\s*(?:d|h|min|m|ms|s|us|µs)\s*)+)$/.exec(s);
  if (!m) return null;
  const unit = { d: 86400, h: 3600, min: 60, m: 60, s: 1, ms: 1e-3, us: 1e-6, µs: 1e-6 };
  let t = 0;
  for (const p of m[1].matchAll(/(\d+(?:\.\d+)?)\s*(d|h|min|ms|m|s|us|µs)/g)) t += +p[1] * unit[p[2]];
  return time0 + t;
}

function gotoTime(t) {
  if (state.view === "lanes" && layout) {
    let b = layout.bandKey.findIndex(k => bandTime(k) >= t - 1e-9);
    if (b < 0) b = layout.bandKey.length - 1;
    if (b < 0) return false;
    lanesBox.scrollTop = layout.bandRow[b] * ROW_H;
    renderLanes(false);
    const el = gutterEl.querySelector(`.bl[data-b="${b}"]`);
    if (el) flash(el, 1300);
    announce(`Went to ${timeLabel(bandTime(layout.bandKey[b]))}`);
    return true;
  }
  let p = 0;
  while (p < visList.length && bandTime(bandOf[visList[p]]) < t - 1e-9) p++;
  if (p >= visList.length) p = visList.length - 1;
  if (p < 0) return false;
  recBox.scrollTop = p * TABLE_ROW_H;
  renderTable(false);
  const td = rowsEl.querySelector(`tr[data-i="${visList[p]}"] td`);
  if (td && td.animate && motion())
    td.animate([{ background: "var(--accent-soft)" }, { background: "transparent" }], { duration: 1200 });
  announce(`Went to ${timeLabel(bandTime(bandOf[visList[p]]))}`);
  return true;
}

export function openGoto(anchor) {
  if (gotoEl) {
    closeGoto(true);
    return;
  }
  const gotoAnchor = anchor || document.activeElement;
  gotoEl = document.createElement("div");
  gotoEl.className = "pop goto";
  gotoEl.setAttribute("role", "dialog");
  gotoEl.setAttribute("aria-label", "Go to time");
  const ex = ABSOLUTE
    ? timeLabel(bandTimesAll[Math.min(bandTimesAll.length - 1, bandTimesAll.length >> 1)] || time0)
    : "+1h";
  const help = ABSOLUTE
    ? "A time of day in UTC (HH:MM[:SS]) or a duration from the first record (+1h06m, +3720s)."
    : "A duration from the first record: +1h06m, +3720s or H:MM.";
  gotoEl.innerHTML =
    html`<h3>Go to time</h3><input type="text" id="goto-q" placeholder="${ex} or +1h06m" aria-label="Time" autocomplete="off">` +
    html`<p>${help}</p><p class="err" id="goto-err" role="alert"></p>`;
  showPopover(gotoEl, {
    anchor: gotoAnchor,
    room: 160,
    onClose: () => {
      gotoEl = null;
    },
  });
  const inp = gotoEl.querySelector("input");
  inp.focus();
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      const t = parseGoto(inp.value);
      if (t == null) {
        gotoEl.querySelector("#goto-err").textContent = "Not a time: use 07:06 or +1h06m";
        return;
      }
      closeGoto(false);
      gotoTime(t);
      focusView();
    }
  });
}

export function initGoto() {
  // the time column of either view opens it
  $("thead").addEventListener("click", e => {
    if (e.target.closest("#th-time")) openGoto(e.target.closest("#th-time"));
  });
  lhead.addEventListener("click", e => {
    if (e.target.closest("#corner")) openGoto(e.target.closest("#corner"));
  });
}
