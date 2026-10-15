import { $, announce } from "./dom.js";
import { html } from "./html.js";

let toastTimer = 0;

let toastPaused = false;

let toastActions = [];

const toastEl = $("toast");

// the toast is not a live region: it speaks once through the status region, in the words of `spoken` when given
export function toast(text, actions, spoken) {
  clearTimeout(toastTimer);
  toastActions = actions || [];
  toastEl.innerHTML = html`<span>${text}</span>${toastActions.map((a, k) => html`<button data-k="${k}">${a.label}</button>`)}`;
  toastEl.hidden = false;
  announce(spoken || text);
  const arm = () => {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      if (!toastPaused) toastEl.hidden = true;
      else arm();
    }, 5000);
  };
  arm();
}

export const tip = $("tip");

let tipAct = null;

export function setTipAction(f) {
  tipAct = f;
}

export function showTip(markup, rect, near, interactive) {
  tip.innerHTML = markup;
  tip.hidden = false;
  tip.classList.toggle("act", !!interactive && !!tipAct);
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  let x = rect.left + (near ? 12 : 0);
  let y = rect.bottom + (near ? 12 : 8);
  if (x + w > innerWidth - 8) x = Math.max(8, innerWidth - 8 - w);
  if (y + h > innerHeight - 8) y = Math.max(8, rect.top - 8 - h);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

export function hideTip() {
  tip.hidden = true;
  tipAct = null;
  tip.classList.remove("act");
}

export function initFeedback() {
  toastEl.addEventListener("click", e => {
    const b = e.target.closest("button[data-k]");
    if (!b) return;
    toastEl.hidden = true;
    toastActions[+b.dataset.k].run();
  });
  for (const [ev, v] of [
    ["pointerenter", true],
    ["pointerleave", false],
    ["focusin", true],
    ["focusout", false],
  ]) {
    toastEl.addEventListener(ev, () => {
      toastPaused = v;
    });
  }
  tip.addEventListener("click", e => {
    const b = e.target.closest("[data-tact]");
    if (b && tipAct) tipAct(b.dataset.tact);
  });
}
