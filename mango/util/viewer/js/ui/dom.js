export const $ = id => document.getElementById(id);

export const store = {
  get(k) {
    try {
      return localStorage.getItem(k);
    } catch (e) {
      return null;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch (e) {
      /* storage unavailable */
    }
  },
};

export const motion = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;

export const isPhone = () => innerWidth <= 640;

export const idle = f =>
  window.requestIdleCallback ? requestIdleCallback(f, { timeout: 1500 }) : setTimeout(() => f(null), 50);

export const announce = text => {
  const el = $("sr");
  el.textContent = "";
  setTimeout(() => {
    el.textContent = text;
  }, 30);
};

// plays the flash animation of an element (again, if it is still running)
export function flash(el, ms) {
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), ms);
}
