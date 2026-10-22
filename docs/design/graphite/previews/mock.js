// Mock data + helpers for the pn redesign previews. Mirrors the app's own markup builders
// (core.js card(), grid.js renderGrid/cellBackground/actionColors, train_table.js seat ring) so the
// preview DOM matches what the views emit, and the real CSS files in css/ (or css-before/) style it.

export const RANKS = "AKQJT98765432";
export const AC = { fold: "#4a86d9", check: "#3fa66a", call: "#3fa66a", r1: "#e8705f", r2: "#d4483f", r3: "#b3302f", r4: "#922326", allin: "#6e1220" };
const GLYPH = { s: "♠", h: "♥", d: "♦", c: "♣" };

export const card = (c) => ({ cls: `card suit-${c[1]}`, text: c[0] + GLYPH[c[1]], key: c });
export const cards = (s) => (s ? s.split(" ").filter(Boolean).map(card) : []);

export function param(name) { try { return new URLSearchParams(location.search).get(name); } catch { return null; } }

const FILES = ["tokens", "components", "app", "train", "strategy", "study", "performance"];
export function applyEnv({ theme = "dark", accent = "amber", suits = "4", version = "after" }) {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-accent", accent);
  if (String(suits) === "2") root.setAttribute("data-suits", "2"); else root.removeAttribute("data-suits");
  const dir = version === "before" ? "css-before" : "css";
  for (const f of FILES) {
    let l = document.getElementById(`pn-css-${f}`);
    if (!l) { l = document.createElement("link"); l.rel = "stylesheet"; l.id = `pn-css-${f}`; document.head.appendChild(l); }
    const href = new URL(`${dir}/${f}.css`, location.href).href;
    if (l.href !== href) l.href = href;
  }
}

/** Preview-only: emulate a viewport width inside a narrower pane by rewriting the media queries and zooming the body. */
export async function emulate(width, height = 900, version = "after") {
  const dir = version === "before" ? "css-before" : "css";
  const pane = window.innerWidth;
  const conds = (w) => (q, op, px) => { const v = Number(px); return op === "max" ? (w <= v ? "(min-width: 0px)" : "(max-width: 0px)") : (w >= v ? "(min-width: 0px)" : "(max-width: 0px)"); };
  for (const f of FILES) {
    const l = document.getElementById(`pn-css-${f}`);
    let css = await (await fetch(new URL(`${dir}/${f}.css`, location.href))).text();
    css = css.replace(/\((max|min)-width:\s*(\d+)px\)/g, (m, op, px) => conds(width)(m, op, px)).replace(/100vh/g, height + "px");
    let s = document.getElementById(`pn-emu-${f}`);
    if (!s) { s = document.createElement("style"); s.id = `pn-emu-${f}`; document.head.appendChild(s); }
    s.textContent = css;
    if (l) l.disabled = true;
  }
  document.body.style.width = width + "px";
  document.body.style.zoom = String(Math.min(1, pane / width));
}

// deterministic noise
export function noise(seed) { let t = (seed * 2654435761) >>> 0; t ^= t >>> 15; t = Math.imul(t, 2246822519) >>> 0; t ^= t >>> 13; t >>>= 0; return (t % 10000) / 10000; }

export function gridClass(i, j) { return i === j ? RANKS[i] + RANKS[j] : i < j ? RANKS[i] + RANKS[j] + "s" : RANKS[j] + RANKS[i] + "o"; }
export const combosOf = (i, j) => (i === j ? 6 : i < j ? 4 : 12);
/** 0..1 preflop-ish strength */
export function strength(i, j) {
  const hi = 12 - Math.min(i, j), lo = 12 - Math.max(i, j);
  let s = (hi * 1.6 + lo) / 31.2;
  if (i === j) s = 0.48 + (hi / 12) * 0.52;
  else { if (i < j) s += 0.07; s -= Math.max(0, (hi - lo - 4)) * 0.025; }
  return Math.max(0, Math.min(1, s));
}

export function cellBackground(segments, weight, full) {
  const total = segments.reduce((s, x) => s + x.freq, 0);
  if (!total || weight <= 0) return {};
  let acc = 0; const stops = [];
  for (const s of segments) {
    if (s.freq <= 0) continue;
    const a = (100 * acc) / total; acc += s.freq; const b = (100 * acc) / total;
    stops.push(`${s.color} ${a.toFixed(2)}% ${b.toFixed(2)}%`);
  }
  const image = `linear-gradient(to right, ${stops.join(", ")})`;
  if (full) return { backgroundImage: image, backgroundSize: `${Math.min(100, 100 * weight).toFixed(1)}% 100%`, backgroundPosition: "left", backgroundRepeat: "no-repeat" };
  return { backgroundImage: image, backgroundSize: `100% ${Math.max(4, Math.min(100, 100 * weight)).toFixed(1)}%`, backgroundPosition: "bottom", backgroundRepeat: "no-repeat" };
}

/** fn(i, j, cls) -> {weight, segments, sub?} | null. Returns {rows, totals (per segment index combos), combos}. */
export function grid(fn, { full = true, hero = null, selected = null } = {}) {
  const rows = []; const totals = []; let combos = 0;
  for (let i = 0; i < 13; i++) {
    const cells = [];
    for (let j = 0; j < 13; j++) {
      const name = gridClass(i, j);
      const c = fn(i, j, name);
      const kind = i === j ? "pair" : i < j ? "suited" : "offsuit";
      const k = ["cell", kind, c && c.weight > 0 ? "" : "empty", hero === name ? "hero-cell" : "", selected === name ? "is-selected" : ""].filter(Boolean).join(" ");
      if (c && c.weight > 0) {
        const tot = c.segments.reduce((s, x) => s + x.freq, 0) || 1;
        c.segments.forEach((s, x) => { totals[x] = (totals[x] || 0) + combosOf(i, j) * c.weight * (s.freq / tot); });
        combos += combosOf(i, j) * c.weight;
      }
      cells.push({ key: name, name, cls: k, style: c && c.weight > 0 ? cellBackground(c.segments, c.weight, full) : {}, sub: c && c.sub ? c.sub : "" });
    }
    rows.push({ key: "r" + i, cells });
  }
  return { rows, totals, combos };
}

/** split a hand into action frequencies by strength bands; acts = [{color, band:[lo,hi]}] */
export function bands(i, j, acts, jitter = 0.12, seed = 1) {
  const s = strength(i, j) + (noise(i * 13 + j + seed * 977) - 0.5) * jitter;
  const segs = acts.map((a) => {
    const [lo, hi] = a.band; const mid = (lo + hi) / 2, half = (hi - lo) / 2 + 0.06;
    return { color: a.color, freq: Math.max(0, 1 - Math.abs(s - mid) / half) };
  });
  if (!segs.some((x) => x.freq > 0)) segs[s < 0.5 ? 0 : segs.length - 1].freq = 1;
  return segs;
}

export const fmt = (n, d = 1) => Number(n).toFixed(d);
export const pct = (n, d = 0) => `${(n * 100).toFixed(d)}%`;

/** seat ring per train_table.js: hero idx 0 bottom, clockwise */
export function ring(seats, button) {
  const n = seats.length; const out = [];
  seats.forEach((s, idx) => {
    const ang = Math.PI / 2 + (2 * Math.PI * idx) / n;
    const pos = (r, a = ang) => ({ "--cx": (Math.cos(a) * r).toFixed(4), top: `${(50 + 46 * r * Math.sin(a) * 0.92).toFixed(2)}%` });
    const cls = ["seat", s.hero && "is-hero", s.folded && "is-folded", s.acting && "is-acting", s.allin && "is-allin"].filter(Boolean).join(" ");
    out.push({
      key: s.pos, cls, style: pos(1), pos: s.pos, name: s.hero ? "You" : s.name, stack: fmt(s.stack),
      cards: s.cards ? cards(s.cards) : [], hasCards: !!s.cards, back: !s.cards && !s.folded,
      act: s.act || "", actCls: s.act ? `seat-act act-${s.act.split(" ")[0].replace("-", "")}` : "", hasAct: !!s.act,
      dealer: s.pos === button, dealerStyle: pos(0.74, ang + 0.42),
      bet: s.bet ? fmt(s.bet) : "", hasBet: !!s.bet, betStyle: pos(0.57),
    });
  });
  return out;
}
