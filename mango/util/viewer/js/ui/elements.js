// The fixed elements of the page (page.html) that several modules use.
import { state } from "../state.js";
import { $ } from "./dom.js";

export const recBox = $("records-box");

export const rowsEl = $("rows");

export const tableEl = $("table");

export const lanesBox = $("lanes");

export const lanesPanel = $("lanes-panel");

export const lhead = $("lhead");

export const gutterEl = $("gutter");

export const plane = $("plane");

export const lanesIn = $("lanes-in");

export const panel = $("panel");

export function focusView() {
  if (state.view === "lanes") {
    const el = state.cursor != null ? plane.querySelector(`.rec[data-i="${state.cursor}"]`) : null;
    (el || lanesBox).focus({ preventScroll: true });
  } else {
    const el = state.cursor != null ? rowsEl.querySelector(`tr[data-i="${state.cursor}"]`) : null;
    (el || recBox).focus({ preventScroll: true });
  }
}
