  const tsCache = new Map();
  function parseTs(s) {
    if (typeof s !== "string") return NaN;
    if (s.length > 20 && s[19] === "." && s[s.length - 1] === "Z") {
      const head = s.slice(0, 19);
      let base = tsCache.get(head);
      if (base === undefined) { base = Date.parse(head + "Z") / 1000; tsCache.set(head, base); }
      return base + Number("0" + s.slice(19, -1));
    }
    return Date.parse(s) / 1000;
  }
  const wallOf = i => parseTs(RECORDS[i].timestamp);
  const W0 = N ? wallOf(0) : NaN;

  const CLOCK = nAgentSim >= 0.8 * nAgentRecs && simMax > simMin ? "sim" : "wall";
  const raw = CLOCK === "sim" ? T : Float64Array.from({ length: N }, (_, i) => wallOf(i));
  const tt = new Float64Array(N), inferred = new Uint8Array(N);
  {
    const prevK = new Float64Array(N), nextK = new Float64Array(N);
    let p = NaN;
    for (let i = 0; i < N; i++) { prevK[i] = p; if (!isNaN(raw[i])) p = raw[i]; }
    p = NaN;
    for (let i = N - 1; i >= 0; i--) { nextK[i] = p; if (!isNaN(raw[i])) p = raw[i]; }
    for (let i = 0; i < N; i++) {
      if (!isNaN(raw[i])) { tt[i] = raw[i]; continue; }
      inferred[i] = 1;
      let e = Infinity;
      if (idOf[i] >= 0) eachEffect(idOf[i], j => { if (!isNaN(raw[j]) && raw[j] < e) e = raw[j]; });
      const lo = prevK[i], hi = nextK[i];
      if (e !== Infinity) tt[i] = Math.min(Math.max(e, isNaN(lo) ? e : lo), isNaN(hi) ? e : hi);
      else tt[i] = !isNaN(lo) ? lo : !isNaN(hi) ? hi : 0;
    }
  }
  const BUCKETS = [1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4, 1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
    1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const sortedT = Float64Array.from(tt).sort();
  const distinctAt = q => {
    let n = 0, last = NaN;
    for (let i = 0; i < sortedT.length; i++) {
      const v = q ? Math.floor(sortedT[i] / q) : sortedT[i];
      if (v !== last) { n++; last = v; }
    }
    return n;
  };
  let Q = 0;
  if (distinctAt(0) > N / 3) Q = BUCKETS.find(q => distinctAt(q) <= N / 3) || 3600;
  const key = Q ? Float64Array.from(tt, v => Math.floor(v / Q)) : tt;
  const ktime = k => Q ? k * Q : k;
  const bandTimesAll = [];
  {
    let last = NaN;
    for (let i = 0; i < sortedT.length; i++) {
      const v = Q ? Math.floor(sortedT[i] / Q) : sortedT[i];
      if (v !== last) { bandTimesAll.push(ktime(v)); last = v; }
    }
  }
  const median = arr => {
    if (!arr.length) return 0;
    const s = Float64Array.from(arr).sort();
    return s.length % 2 ? s[s.length >> 1] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
  };
  const MED_STEP = median(bandTimesAll.slice(1).map((v, k) => v - bandTimesAll[k]).filter(d => d > 0));
  const TMIN = N ? sortedT[0] : 0, TMAX = N ? sortedT[N - 1] : 0;
  const ABS = TMIN >= 1e8;
  const T0 = bandTimesAll.length ? bandTimesAll[0] : 0;
  const UNITS = [60, 1, 1e-3, 1e-6];
  const sameAt = (a, b, u) => Math.floor(a / u + 1e-9) === Math.floor(b / u + 1e-9);
  // a few neighbouring bands may share a label at this unit; those labels get the next finer unit
  const UNIT = UNITS.find(u => {
    let clash = 0;
    for (let k = 1; k < bandTimesAll.length; k++) if (sameAt(bandTimesAll[k], bandTimesAll[k - 1], u)) clash++;
    return clash <= 0.1 * (bandTimesAll.length - 1);
  }) || 1e-6;
  const UNIT_IX = UNITS.indexOf(UNIT);
  const gutterWidth = () => (isPhone() ? [52, 60, 88, 112] : [64, 72, 100, 124])[UNIT_IX] + (ABS ? 0 : 6);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function timeParts(t, unit = UNIT) {
    const v = ABS ? t : t - T0;
    const neg = v < 0, a = Math.abs(v);
    const us = Math.round(a * 1e6), sec = Math.floor(us / 1e6), frac = us - sec * 1e6;
    const h = ABS ? Math.floor(sec / 3600) % 24 : Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    const parts = [(ABS ? pad(h) : (neg ? "−" : "+") + h) + ":", pad(m)];
    if (unit <= 1) parts[1] += ":", parts.push(pad(s));
    if (unit === 1e-3) parts.push("." + pad(Math.floor(frac / 1000), 3));
    if (unit === 1e-6) parts.push("." + pad(frac, 6));
    return parts;
  }
  const dayOf = t => Math.floor(t / 86400);
  const dateLabel = t => { const d = new Date(Math.floor(t) * 1000); return `${MONTHS[d.getUTCMonth()]} ${pad(d.getUTCDate())}`; };
  const timeLabel = t => timeParts(t).join("");
  const finer = (t, prev) => {
    let u = UNIT;
    while (prev != null && u > 1e-6 && sameAt(t, prev, u)) u = UNITS[UNITS.indexOf(u) + 1];
    return u;
  };
  function timeBits(t, prev) {
    const u = finer(t, prev), parts = timeParts(t, u);
    let same = 0;
    if (prev != null && (!ABS || dayOf(prev) === dayOf(t))) {
      const pp = timeParts(prev, u);
      while (same < parts.length - 1 && pp[same] === parts[same]) same++;
    }
    return { date: ABS && prev != null && dayOf(prev) !== dayOf(t) ? dateLabel(t) : "", same: parts.slice(0, same).join(""), rest: parts.slice(same).join("") };
  }
  // elide: a narrow gutter shows an ellipsis for the unchanged part, since the corner cell has the full time
  function timeHTML(t, prev, elide) {
    const b = timeBits(t, prev);
    return (b.date ? `<span class="dt">${b.date}</span>` : "") + (b.same ? `<span class="m">${elide ? "…" : b.same}</span>` : "") + b.rest;
  }
  // compact drops the trailing " min" of "+1 h 06 min" where space is short (the corner cell)
  function dur(d, compact) {
    const a = Math.abs(d), sgn = d < 0 ? "−" : "+";
    if (a < 1e-3) return `${sgn}${Math.round(a * 1e6)} µs`;
    if (a < 1) return `${sgn}${Math.round(a * 1e3)} ms`;
    if (a < 60) return `${sgn}${a < 10 && Math.round(a * 10) % 10 ? a.toFixed(1) : Math.round(a)} s`;
    if (a < 3600) return `${sgn}${Math.round(a / 60)} min`;
    if (a < 86400) {
      const m = Math.round(a / 60);
      return `${sgn}${Math.floor(m / 60)} h ${pad(m % 60)}${compact ? "" : " min"}`;
    }
    const h = Math.round(a / 3600);
    return `${sgn}${Math.floor(h / 24)} d ${h % 24} h`;
  }
  const recTime = i => (inferred[i] ? "≈" : "") + timeLabel(ktime(key[i]));
