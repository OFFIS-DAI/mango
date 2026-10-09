export const natural = new Intl.Collator(undefined, { numeric: true }).compare;

export const esc = s =>
  String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export const fmt = v => (v !== null && typeof v === "object" ? JSON.stringify(v) : String(v));

export const nf = n => n.toLocaleString("en-US");

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export const plural = (n, one, many) => `${nf(n)} ${n === 1 ? one : many || one + "s"}`;

export const aidOf = s => {
  const m = /aid=(['"])(.*?)\1/.exec(s == null ? "" : String(s));
  return m ? m[2] : null;
};

export const median = arr => {
  if (!arr.length) return 0;
  const s = Float64Array.from(arr).sort();
  return s.length % 2 ? s[s.length >> 1] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

export const lowerBound = (arr, v) => {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (arr[m] < v) lo = m + 1;
    else hi = m;
  }
  return lo;
};

export const round1 = v => Math.round(v * 10) / 10;
