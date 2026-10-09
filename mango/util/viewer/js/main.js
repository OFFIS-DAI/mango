// Entry point: sets the views up (in this order), restores what a link or the last visit chose, and draws.
// From then on actions change the state with update() (state.js), and renderAll draws the change.
import { EDGES, FAILS, NODES, WORLD, hasWorldRecs, laneOf, nAgents } from "./model.js";
import { computeRelations } from "./selection.js";
import { flushNow, onRender, pending, state, touch } from "./state.js";
import {
  defaultLanes,
  insertSorted,
  linkFromPage,
  select,
  setFit,
  setLanes,
  setLink,
  setOrder,
  setView,
} from "./ui/actions.js";
import { initButtons } from "./ui/buttons.js";
import { idle } from "./ui/dom.js";
import { lanesBox } from "./ui/elements.js";
import { initFeedback } from "./ui/feedback.js";
import { initFilters, syncFilters } from "./ui/filters.js";
import { initGoto } from "./ui/goto.js";
import { initHeader, updateFailsButton } from "./ui/header.js";
import { initKeys } from "./ui/keys.js";
import { geom, initGeometry, updateGeometry } from "./ui/lanes/geometry.js";
import { flashHeader, initLaneHeader, renderHead } from "./ui/lanes/header.js";
import { initLanes, positionSelRow, renderLanesEmpty, restoreCursor, updateFpill } from "./ui/lanes/interact.js";
import { remeasureLabels } from "./ui/lanes/labels.js";
import { layout, layoutStale, markLayoutStale, relayoutLanes, selShown } from "./ui/lanes/layout.js";
import { initPicker, updatePickButton } from "./ui/lanes/picker.js";
import { computeThread, placePills } from "./ui/lanes/thread.js";
import { initTiles, renderLanes, sizeLanes } from "./ui/lanes/tiles.js";
import { initPanel, renderPanel } from "./ui/panel.js";
import { initPopover } from "./ui/popover.js";
import { drawRail, initRail } from "./ui/rail.js";
import { initTable, renderTable } from "./ui/table.js";
import { firstTopology, initTopoCard } from "./ui/topo/card.js";
import { mapState, renderRoute, renderTopoMeta } from "./ui/topo/highlight.js";
import { initMap } from "./ui/topo/interact.js";
import { initTopoList } from "./ui/topo/list.js";
import { readHash, writeHash } from "./ui/url.js";
import { applyPending, applyView, fitCard, initView, syncLaneTools, updateSelbar, updateStatus } from "./ui/view.js";
import { computeVisibility } from "./visibility.js";

initHeader();
initFilters();
initTable();
initGeometry();
initTiles();
initFeedback();
initPopover();
initPicker();
initLaneHeader();
initLanes();
initMap();
initTopoCard();
initTopoList();
initPanel();
initRail();
initGoto();
initKeys();
initView();
initButtons();

// state keys that decide which records are shown
const FILTERS = ["q", "agent", "cats", "levels", "onlyRelated", "link"];

// Redraws what depends on the state keys changed since the last frame: first what is derived from the
// state, then the controls, the current view (Lanes or Records), the map, the panel, the rail and the URL.
function renderAll(changed) {
  const any = (...keys) => keys.some(k => changed.has(k));
  const filtered = any("view", ...FILTERS) || (changed.has("sel") && state.onlyRelated);
  const rows = filtered || any("sel", "width", "height");
  if (changed.has("fonts")) remeasureLabels();
  if (changed.has("sel")) computeRelations();
  if (filtered) computeVisibility();
  if (filtered || changed.has("lanes")) markLayoutStale();

  if (changed.has("view")) applyView();
  if (any("view", "lanes")) updatePickButton();
  if (any("order", "fit")) syncLaneTools();
  if (any(...FILTERS)) syncFilters();

  if (state.view === "lanes" && nAgents) {
    const relayout = layoutStale || !layout || changed.has("order");
    if (relayout) relayoutLanes();
    const regeo = relayout || !geom || any("fit", "width", "fonts");
    if (regeo) updateGeometry();
    if (relayout || changed.has("sel")) computeThread(changed.has("sel"));
    else if (regeo) placePills();
    if (regeo) {
      renderHead();
      sizeLanes();
    }
    lanesBox.classList.toggle("has-sel", selShown());
    if (regeo || changed.has("sel")) {
      renderLanes(true);
      positionSelRow();
      updateFpill();
    } else if (rows) renderLanes(false);
    renderLanesEmpty();
    if (pending.flashLane >= 0) {
      flashHeader(pending.flashLane);
      pending.flashLane = -1;
    }
    restoreCursor();
  } else if (rows) renderTable(true);
  applyPending();

  // in Records the followed lanes show only in the map and the URL
  const lanesShown = changed.has("lanes") && state.view === "lanes";
  if (any("sel", "view", "order", "lanes", ...FILTERS)) mapState(false);
  if (any("sel", "view", ...FILTERS) || lanesShown) renderPanel();
  if (any("sel", "view", "order", "fit", "width", "height", "theme", "fonts", ...FILTERS) || lanesShown) drawRail();
  if (any("sel", "view", "order", "fit", "lanes", ...FILTERS)) writeHash();
  updateStatus();
  updateFailsButton();
  updateSelbar();
}

// a link opened in the same tab (or Back) changes the hash only
addEventListener("hashchange", () => {
  const hs = readHash();
  setView(hs.view || "records");
  if ((hs.order || "packed") !== state.order) setOrder(hs.order || "packed");
  if (!!hs.fit !== state.fit) setFit(!!hs.fit);
  if (hs.link) setLink(hs.link.e, hs.link.both);
  else if (state.link) setLink(null);
  if (hs.lanes) setLanes(hs.lanes);
  if (hs.sel != null && hs.sel !== state.sel) select(hs.sel, { reveal: true });
});

{
  const hs = readHash();
  // a link without v= is a Records link (a bare #r683), whatever view was used last
  if (location.hash.length > 1) state.view = hs.view || "records";
  if (hs.order) state.order = hs.order;
  if (hs.fit) state.fit = true;
  if (hs.link) state.link = hs.link;
  // without a selection in the link, the first failure is selected
  state.sel = hs.sel != null ? hs.sel : FAILS.length ? FAILS[0] : null;
  state.cursor = state.sel;
  computeRelations();
  if (hs.lanes) {
    state.lanes = hs.lanes;
    // a link to a record in a lane it does not follow opens with that lane added, as navigating there would
    const l = hs.sel != null ? laneOf[hs.sel] : -1;
    if (state.view === "lanes" && l >= 0 && !state.lanes.includes(l) && (l !== WORLD || hasWorldRecs)) {
      state.lanes = insertSorted(state.lanes, l);
      pending.flashLane = l;
    }
  } else if (state.link) {
    const E = EDGES[state.link.e];
    state.lanes = [...new Set([NODES[E.a].lane, NODES[E.b].lane].filter(l => l >= 0))];
  } else if (state.view === "lanes") state.lanes = defaultLanes();
  if (state.link) linkFromPage();
  renderTopoMeta();
  renderRoute();
  applyView();
  pending.reveal = state.sel;
  pending.revealWindow = false;
  fitCard();
  onRender(renderAll);
  touch("view", "lanes", "order", "fit", "link");
  flushNow();
  // the map is drawn once the page is up
  idle(() => {
    firstTopology();
    mapState(true);
  });
}
