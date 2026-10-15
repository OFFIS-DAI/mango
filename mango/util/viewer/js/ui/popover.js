// A small dialog below the button that opened it (the lane picker, go to time). One is open at a time:
// a click outside closes it, Escape closes it and gives the focus back to that button.
import { clamp } from "../core/util.js";

let current = null;

// el: the dialog; anchor: what it opens below and gives the focus back to; room: the height kept free
// for it above the bottom of the window; keep: an element whose presses leave it open (its toggle);
// onClose: runs when it closes, however that happens
export function showPopover(el, { anchor, room, keep, onClose }) {
  closePopover(false);
  document.body.append(el);
  const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: 20, bottom: 80 };
  el.style.left = clamp(r.left, 8, innerWidth - Math.min(320, innerWidth - 16) - 8) + "px";
  el.style.top = Math.min(r.bottom + 6, innerHeight - room) + "px";
  el.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    closePopover(true);
  });
  current = { el, anchor, keep, onClose };
}

export function closePopover(refocus) {
  if (!current) return;
  const { el, anchor, onClose } = current;
  current = null;
  el.remove();
  if (onClose) onClose();
  if (refocus && anchor && anchor.isConnected) anchor.focus();
}

export const openPopover = () => (current ? current.el : null);

export function initPopover() {
  document.addEventListener("pointerdown", e => {
    if (current && !current.el.contains(e.target) && !(current.keep && current.keep.contains(e.target)))
      closePopover(false);
  });
}
