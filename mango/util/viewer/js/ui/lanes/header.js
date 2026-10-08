import { nf, plural } from "../../core/util.js";
import { CLOCK, NODES, UNIT, WORLD, laneFailed, laneName, laneOf, laneTotal } from "../../model.js";
import { pending, state } from "../../state.js";
import { setLanes, toggleLane } from "../actions.js";
import { announce, flash, motion } from "../dom.js";
import { lanesBox, lanesIn, lhead } from "../elements.js";
import { emit, on } from "../events.js";
import { html, raw } from "../html.js";
import { geom } from "./geometry.js";
import { layout } from "./layout.js";

export function renderHead() {
  const g = geom;
  lhead.style.height = g.headH + "px";
  const clock = CLOCK === "sim" ? "Sim time" : "Wall time";
  let h =
    `<button class="corner${UNIT < 1 ? " fine" : ""}" id="corner" style="width:${g.gw}px" title="Go to a time (t)">` +
    `<span class="ck"><span class="lg">${clock}</span><span class="sh">Time</span></span>` +
    `<span class="ct" id="corner-t"></span><span class="cd" id="corner-d"></span></button>`;
  layout.lanes.forEach((l, c) => {
    const tot = laneTotal[l];
    const v = layout.laneVis[l];
    const f = laneFailed[l];
    const meta =
      (v === tot ? (f && g.W < 190 ? nf(tot) : plural(tot, "record")) : `${nf(v)} of ${nf(tot)}`) +
      (f ? html` · <b>${nf(f)} failed</b>` : "");
    const cls =
      "lh" +
      (l === WORLD ? " world" : "") +
      (g.labels ? "" : " vert") +
      ((state.fit || g.W < 148) && g.labels && l !== WORLD ? " fitw" : "");
    const title = laneName(l) + (l === WORLD ? ": records without an agent" : "");
    h +=
      html`<div class="${cls}" data-l="${l}" style="width:${g.w[c]}px;--ll:${g.life[c] - g.x[c]}px" title="${title}">` +
      html`<span class="nm">${laneName(l)}</span>` +
      (l === WORLD
        ? ""
        : html`<span class="mt">${raw(meta)}</span>` +
          html`<button class="x" data-x="${l}" aria-label="Stop following ${laneName(l)}" title="Stop following (x)">✕</button>`) +
      `</div>`;
  });
  lhead.innerHTML = h;
  lanesBox.setAttribute("aria-label", "Agent lanes: " + layout.lanes.map(laneName).join(", "));
}

let drag = null;

const dropLine = document.createElement("div");

const dropIndex = cx => {
  const x = cx - lanesBox.getBoundingClientRect().left + lanesBox.scrollLeft - geom.gw;
  let at = 0;
  while (at < layout.nC && geom.x[at] + geom.w[at] / 2 < x) at++;
  return at;
};

const endDrag = () => {
  if (!drag) return;
  const d = drag;
  drag = null;
  d.el.classList.remove("drag");
  dropLine.remove();
  if (!d.on || d.at == null) return;
  const lanes = state.lanes.slice();
  const from = lanes.indexOf(d.l);
  let to = d.at;
  if (to > from) to--;
  if (to === from) return;
  lanes.splice(from, 1);
  lanes.splice(to, 0, d.l);
  setLanes(lanes);
  announce(`Moved ${laneName(d.l)}`);
};

export function moveLane(dir) {
  if (state.cursor == null || !state.lanes) return;
  const lanes = state.lanes.slice();
  const from = lanes.indexOf(laneOf[state.cursor]);
  const to = from + dir;
  if (from < 0 || to < 0 || to >= lanes.length) return;
  lanes.splice(from, 1);
  lanes.splice(to, 0, laneOf[state.cursor]);
  setLanes(lanes);
  pending.focus = state.cursor;
  announce(`Moved ${laneName(laneOf[state.cursor])} to position ${to + 1}`);
}

export function flashHeader(l) {
  const el = lhead.querySelector(`.lh[data-l="${l}"]`);
  if (el) flash(el, motion() ? 650 : 1200);
}

// the header of an agent hovered in the map lights up
function markHoveredLane(h) {
  lhead.querySelectorAll(".lh.peek").forEach(el => el.classList.remove("peek"));
  const lane = h && h.n != null ? NODES[h.n].lane : -1;
  if (lane >= 0) lhead.querySelector(`.lh[data-l="${lane}"]`)?.classList.add("peek");
}

export function initLaneHeader() {
  on("map-hover", markHoveredLane);
  lhead.addEventListener("click", e => {
    const x = e.target.closest("[data-x]");
    if (x) {
      toggleLane(+x.dataset.x);
    }
  });
  dropLine.className = "dropline";
  lhead.addEventListener("pointerdown", e => {
    const h = e.target.closest(".lh");
    if (!h || e.target.closest("button") || e.button !== 0) return;
    drag = { l: +h.dataset.l, x: e.clientX, on: false, el: h, id: e.pointerId };
  });
  lhead.addEventListener("pointermove", e => {
    const h = e.target.closest(".lh");
    emit("peek-lane", h ? +h.dataset.l : null);
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.on && Math.abs(e.clientX - drag.x) > 5) {
      drag.on = true;
      drag.el.classList.add("drag");
      lhead.setPointerCapture(e.pointerId);
      lanesIn.append(dropLine);
    }
    if (!drag.on) return;
    const at = dropIndex(e.clientX);
    const x = at < layout.nC ? geom.x[at] : geom.contentW;
    dropLine.style.left = geom.gw + x + "px";
    dropLine.style.height = Math.min(lanesIn.offsetHeight, lanesBox.scrollTop + lanesBox.clientHeight) + "px";
    drag.at = at;
  });
  lhead.addEventListener("pointerup", endDrag);
  lhead.addEventListener("pointercancel", endDrag);
  lhead.addEventListener("pointerleave", () => {
    if (!drag) emit("peek-lane", null);
  });
}
