import { nf } from "../core/util.js";
import {
  CLOCK,
  FAILS,
  RECORDS,
  TMAX,
  TMIN,
  dur,
  failOf,
  nAgents,
  nEvRecv,
  nHandler,
  nRecords,
  nRecv,
  nSched,
} from "../model.js";
import { state } from "../state.js";
import { failPos, nextFail, select } from "./actions.js";
import { $ } from "./dom.js";
import { html } from "./html.js";

const wallSpan = nRecords ? Date.parse(RECORDS[nRecords - 1].timestamp) - Date.parse(RECORDS[0].timestamp) : NaN;

const simSpan = CLOCK === "sim" ? TMAX - TMIN : 0;

const showSim = CLOCK === "sim" && simSpan > 0 && simSpan > (10 * wallSpan) / 1000;

let failsBtn = null;

export function updateFailsButton() {
  const s = state.sel;
  const k = s != null && failOf[s] ? failPos(s) : -1;
  failsBtn.innerHTML =
    k >= 0
      ? `error <b>${nf(k + 1)}</b> of ${nf(FAILS.length)}`
      : `<b>${nf(FAILS.length)}</b> ${FAILS.length === 1 ? "error" : "errors"}`;
}

export function initHeader() {
  $("stats").innerHTML =
    [
      [nRecords, "records"],
      [nAgents, "agents"],
      [nRecv, "messages"],
      [nEvRecv, "events"],
      [nSched, "tasks"],
      [nHandler, "handler calls"],
    ]
      .map(([n, l]) => html`<span><b>${nf(n)}</b> ${l}</span>`)
      .join("") +
    (isNaN(wallSpan) ? "" : html`<span><b>${nf(Math.round(wallSpan))}</b> ms</span>`) +
    (showSim ? html`<span><b>${dur(simSpan).slice(1)}</b> simulated</span>` : "") +
    `<button id="fails" ${FAILS.length ? "" : "disabled"} title="Select the next failure or error (e, Shift: previous)"></button>`;
  failsBtn = $("fails");
  failsBtn.addEventListener("click", e => {
    if (e.shiftKey) nextFail(true);
    else if (state.sel != null && failOf[state.sel]) nextFail(false);
    else select(FAILS[0], { reveal: true });
  });
}
