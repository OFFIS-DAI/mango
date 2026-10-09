// The current Lanes layout, rebuilt when the followed lanes, the filters or the order change.
import { laneOf, layoutLanes } from "../../model.js";
import { state } from "../../state.js";
import { vis } from "../../visibility.js";

export let layout = null;
export let layoutStale = true;
export function markLayoutStale() {
  layoutStale = true;
}
export function relayoutLanes() {
  layout = layoutLanes(state.lanes || [], state.order === "file", state.cats.has("task"), vis);
  layoutStale = false;
}
export const colOfRec = i => layout.col[laneOf[i]];

export const selShown = () =>
  state.sel != null &&
  vis[state.sel] === 1 &&
  (state.view !== "lanes" || !layout || layout.col[laneOf[state.sel]] >= 0);

// the record under the pointer in Lanes
export let hovRec = -1;
export function setHovRec(i) {
  hovRec = i;
}
