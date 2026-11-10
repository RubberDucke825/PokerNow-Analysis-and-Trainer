// Tournament preflop navigator (/strategy?mode=mtt&depth=25&path=2,0): Upswing's 8-handed MTT charts at
// 10 / 25 / 50 bb, preflop only (contracts/study_preflop.md, "Tournament charts"). Top: format / depth setup;
// a strip with one card per decision taken (click to go back to it) and the seat to act. Left: the actor's
// 13x13 grid (cell width = share of the hand that reaches the spot); right: Actions tiles. A line that reaches
// the flop or leaves the free chart set ends with a summary; nothing is solved.
// Keyboard: 1-9 choose the nth option, Backspace removes the last step.
import { h, api, pct, setQuery, navigate, errorBox, showTip, hideTip } from "../core.js";
import { renderGrid, actionColors, ALL_CLASSES } from "../grid.js";
import { actionsBlock } from "./strategy_hands.js";

const DEPTHS = [10, 25, 50];
const MAX_KEY_OPTION = 9;
const END_TITLE = { flop: "Preflop over: flop", over: "Hand over", no_chart: "End of the chart set" };

/** Combos of a hand class: pairs 6, suited 4, offsuit 12. */
const classCombos = (cls) => (cls.length === 2 ? 6 : cls[2] === "s" ? 4 : 12);

/**
 * @param {string} label @param {Array<[*, string]>} options @param {*} value @param {(v: *) => void} onPick
 * @returns {HTMLElement}
 */
export const seg = (label, options, value, onPick) => h("div", { class: "seg", role: "group", "aria-label": label },
  options.map(([v, text]) => h("button", { type: "button", class: "seg-opt", "aria-pressed": String(v === value), onclick: () => onPick(v) }, text)));

/** Format switch shared by the cash and tournament navigators. */
export const formatField = (mode) => h("div", { class: "field" }, h("span", {}, "Format"),
  seg("Format", [["cash", "Cash"], ["mtt", "Tournament"]], mode, (v) => {
    if (v !== mode) navigate(v === "mtt" ? "/strategy?mode=mtt" : "/strategy");
  }));

export async function studyMttView(q) {
  const depth = Number(q.get("depth"));
  const st = {
    depth: DEPTHS.includes(depth) ? depth : DEPTHS[0],
    path: (q.get("path") || "").split(",").filter((s) => /^\d+$/.test(s)).map(Number),
    sel: -1,
  };
  const root = h("div", { class: "page study study-mtt" });
  const setupBox = h("div", { class: "study-setup" });
  const body = h("div", { class: "study-body" });
  root.append(setupBox, body);
  let data = null;
  let gridBox = null;
  let seq = 0;

  const setPath = (path) => { st.path = path; st.sel = -1; return load(); };
  const reset = () => setPath([]);
  const choose = (k) => { if (data && data.options[k]) setPath([...st.path, k]); };

  function drawSetup() {
    setupBox.replaceChildren(formatField("mtt"),
      h("div", { class: "field" }, h("span", {}, "Stack"),
        seg("Stack depth", DEPTHS.map((d) => [d, `${d} bb`]), st.depth, (v) => { if (v !== st.depth) { st.depth = v; reset(); } })),
      h("div", { class: "field" }, h("span", {}, "Table"), h("span", { class: "muted" }, "8-handed, antes")),
      h("div", { class: "study-links" },
        h("button", { type: "button", class: "btn ghost", onclick: reset, disabled: !st.path.length }, "Reset line")));
  }

  async function load() {
    const mine = ++seq;
    setQuery({ mode: "mtt", depth: st.depth, path: st.path.join(",") || null });
    drawSetup();
    let d;
    try {
      d = await api(`/api/study/mtt?depth=${st.depth}&path=${st.path.join(",")}`);
    } catch (e) {
      if (mine !== seq) return;
      data = null;
      body.replaceChildren(errorBox(e), h("p", {}, h("button", { type: "button", class: "btn", onclick: reset }, "Reset line")));
      return;
    }
    if (mine !== seq) return;
    data = d;
    draw();
  }

  // ---------------------------------------------------------------- strip
  function stepCard(step, i) {
    return h("div", { class: "ln-card st-seat" },
      h("span", { class: "ln-head" }, h("span", { class: "pos-tag" }, step.actor)),
      h("ul", { class: "ln-acts" }, h("li", {}, h("button", { type: "button", class: "ln-act is-taken", "aria-pressed": "true",
        title: `Back to ${step.actor}'s decision`, onclick: () => setPath(st.path.slice(0, i)) }, h("span", {}, step.label)))));
  }
  function actorCard() {
    return h("div", { class: "ln-card st-seat is-current", "aria-current": "step" },
      h("span", { class: "ln-head" }, h("span", { class: "pos-tag" }, data.actor)),
      h("ul", { class: "ln-acts" }, data.options.map((o) => h("li", {}, h("button", { type: "button", class: "ln-act",
        title: `${data.actor}: ${o.label}`, onclick: () => choose(o.k) }, h("span", {}, o.label), h("span", { class: "mono" }, pct(o.freq, 1)))))));
  }
  const strip = () => h("nav", { class: "line-strip study-strip", "aria-label": "Line" },
    data.steps.map(stepCard), data.actor ? actorCard() : null);

  // ---------------------------------------------------------------- grid
  function paintGrid() {
    if (!gridBox || !data.actor) return;
    const toks = data.options.map((o) => o.token);
    const colors = actionColors(toks);
    const cells = {};
    for (const cls of ALL_CLASSES) {
      const dist = data.cells[cls] || {};
      const segs = toks.map((t, k) => ({ freq: dist[t] || 0, color: colors[k] }));
      const one = st.sel >= 0 ? segs[st.sel] : null;
      cells[cls] = { weight: data.reach[cls], segments: one ? [one, { freq: 1 - one.freq, color: "transparent" }] : segs,
        sub: one ? String(Math.round(100 * one.freq)) : null,
        label: data.options.map((o, k) => `${o.label} ${pct(segs[k].freq)}`).join(", ") };
    }
    const tip = (cls) => {
      const dist = data.cells[cls] || {};
      return h("div", {}, h("b", {}, cls), data.reach[cls] < 1 ? h("span", { class: "muted" }, ` · ${pct(data.reach[cls])} reach here`) : null,
        h("ul", { class: "tip-list" }, data.options.map((o, k) =>
          h("li", {}, h("span", { style: { color: colors[k] } }, o.label), h("span", { class: "mono" }, pct(dist[o.token] || 0, 1))))));
    };
    gridBox.replaceChildren(renderGrid(cells, { fill: "full", class: "gw", caption: `${data.actor} strategy by hand class`,
      onFocus: (cls, el) => showTip(el, tip(cls)), onBlur: hideTip }));
  }
  function select(k) {
    if (st.sel === k) return;
    st.sel = k;
    root.querySelectorAll(".act-tile").forEach((el, i) => { el.classList.toggle("is-sel", i === k); el.setAttribute("aria-pressed", String(i === k)); });
    const all = root.querySelector(".act-all");
    if (all) all.setAttribute("aria-pressed", String(k < 0));
    paintGrid();
  }

  // ---------------------------------------------------------------- columns
  function leftActor() {
    gridBox = h("div", { class: "grid-box" });
    return [h("header", { class: "study-head" }, h("h2", {}, `${data.actor} to act`),
      h("span", { class: "src-tag src-upswing", title: data.node }, `Upswing MTT ${data.depth}bb · ${data.title}`)), gridBox];
  }
  function rightActor() {
    const toks = data.options.map((o) => o.token);
    const freqs = data.options.map((o) => o.freq);
    const combos = ALL_CLASSES.reduce((s, cls) => s + classCombos(cls) * data.reach[cls], 0);
    const node = { actions: data.options.map((o) => ({ token: o.token, amount: null, name: o.label })) };
    return actionsBlock({ node, bb: 1, colors: actionColors(toks), freqs, combosPer: freqs.map((f) => f * combos), sel: st.sel,
      onSelect: select, onPlay: choose,
      keys: [h("kbd", {}, "1"), "–", h("kbd", {}, String(Math.min(MAX_KEY_OPTION, toks.length))), " choose ", h("kbd", {}, "⌫"), " back"] });
  }
  function endSummary(end) {
    return [h("header", { class: "study-head" }, h("h2", {}, END_TITLE[end.kind] || "Preflop over")),
      h("section", { class: "end-card", "aria-label": "End of preflop" },
        h("p", { class: "end-line" }, `${end.actor}: ${end.label}`),
        h("p", { class: "note" }, end.note))];
  }

  function draw() {
    gridBox = null;
    if (!data.actor) st.sel = -1;
    const left = data.end ? endSummary(data.end) : leftActor();
    const right = data.end ? [] : [rightActor()];
    body.replaceChildren(h("div", { class: "strat-surface" }, strip(),
      h("div", { class: "two-col" }, h("div", { class: "main-col" }, left), h("div", { class: "side-col" }, right))));
    paintGrid();
  }

  const onKey = (e) => {
    if (!root.isConnected) { document.removeEventListener("keydown", onKey); return; }
    if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^[1-9]$/.test(e.key)) { e.preventDefault(); choose(+e.key - 1); }
    else if (e.key === "Backspace" && st.path.length) { e.preventDefault(); setPath(st.path.slice(0, -1)); }
  };
  document.addEventListener("keydown", onKey);

  await load();
  return root;
}
