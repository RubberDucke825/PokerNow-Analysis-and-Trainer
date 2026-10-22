# DESIGN.md — pn study visual system ("Graphite")

The `pn` study app (`pn web`, FastAPI + vanilla ES modules, no build step) uses one visual system,
**Graphite**: calm, premium "pro tool". Cool graphite surfaces, a single amber accent, Geist + Geist
Mono, layered surfaces separated by hairlines and soft shadows instead of borders. Dark theme by
default, four-colour suits. It replaced the "Ledger" direction on 2026-10-19.

Everything below lives in CSS under `pn/pn/web/static/`. This file is the summary and the rules;
the per-screen spec is `docs/design/graphite/README.md` (the design handoff) and the rendered
references are `docs/design/graphite/previews/*.dc.html` (serve that folder over HTTP to open them;
they are design references rebuilt from the view source with mock data, not app pages).

## Files

| file | owns |
|---|---|
| `tokens.css` | fonts `@import`, every custom property, light theme and variant overrides |
| `components.css` | topbar, buttons, kbd, segmented control, chips, badges, inputs, panels, data tables, tooltip, sheet, toast, 13×13 grid, legend |
| `app.css` | shell, page heads, /hands, /players, /review, /charts |
| `train.css` | /train: table, seats, action bar, verdict, ranges panel, side tabs |
| `strategy.css` | /strategy library, new-spot form, browser |
| `study.css` | preflop navigator pieces (seat strip, source tag, board list and tiles) |
| `performance.css` | /train/performance |

`index.html`, `js/core.js`, `js/grid.js` and the views define the DOM; the CSS restyles it. When a
view adds markup, reuse the classes and state hooks below before inventing new ones.

## Hard constraints (never change without a decision)

- **Action colours are fixed** (GTO Wizard mapping; players are trained on them): fold `#4a86d9`,
  check/call `#3fa66a`, bets `#e8705f` → `#d4483f` → `#b3302f` → `#922326` (darker = larger),
  all-in `#6e1220`. They live in `--act-*` (tokens.css) and `ACTION_COLORS` in `js/grid.js`. In
  line-breakdown grids black (`#000`) means "not in range".
- **13×13 grid conventions:** action-split cells, opacity/width = share of combos in range, the
  hero hand outlined (`.hero-cell`), a text legend with combo counts.
- **Four-colour suits** by default (`--suit-*`); two-colour is an opt-in variant.
- **Trainer actions map to the solver tree:** Fold / Check / Call plus fixed sizes, no bet slider,
  keyboard shortcuts unchanged (letters, `1`–`9`, `N`/`Space`, `Z`, `G`, `?`).
- **DOM hooks unchanged:** `data-cls`, `data-nav`, `.seg-opt`, `.hero-cell`, `aria-pressed`,
  `aria-selected`, `[style*="--cx"]` seat ring, `--act`, `--cd`, `--p`.
- **Phone width 375 px works with no horizontal scroll**, 16 px gutters.
- **Motion is compositor-only** (transform, opacity) and `prefers-reduced-motion` disables all of it.
- **Light theme stays supported** (`prefers-color-scheme: light` or `html[data-theme="light"]`).
- No framework, no build step, no new dependencies. The only external resource is Google Fonts.

## Tokens (dark, default; all in `tokens.css`)

Colours are oklch. The light theme overrides the same names.

**Surfaces** (hue 264, chroma ≤ 0.01)

| token | value | use |
|---|---|---|
| `--bg-0` | oklch(16.5% 0.006 264) | page |
| `--bg-1` | oklch(18.8% 0.007 264) | side column, sheets, inset wells, inputs |
| `--bg-2` | oklch(21.8% 0.008 264) | panels, seat plates, buttons |
| `--bg-3` | oklch(25.5% 0.009 264) | hover, raised rows, pair cells |
| `--bg-4` | oklch(30% 0.010 264) | pressed, selected segment |
| `--line` | oklch(27.5% 0.008 264) | row rules, grout |
| `--line-2` | oklch(35% 0.010 264) | strong rule, input outline |
| `--hair` | oklch(100% 0 0 / 0.07) | translucent edge on layered surfaces |

**Ink:** `--ink` 95.5% · `--ink-2` 79% · `--ink-3` 66% (labels; ≥ 4.5:1 on bg-0…bg-3) · `--ink-4`
48% (disabled / folded only). Light theme: 21 / 40 / 50 / 68%.

**Accent:** `--amber` oklch(80% 0.115 76) (light: 55% 0.12 68); `--iris` oklch(77% 0.10 286)
(light: 52% 0.14 286). `--accent` = amber. Derived: `--accent-soft` (14% mix, fills),
`--accent-line` (50% mix, outlines), `--accent-fill` / `--accent-fill-ink` (filled buttons; in the
light theme oklch(80% 0.135 78) with dark ink so buttons stay bright), `--accent-ink` (text on
accent). The accent marks hero, focus, the current crumb/node, pressed chips and primary buttons.
It is **not** used for segmented-control selection.

**Semantics:** `--good` oklch(77% 0.14 152) · `--warn` oklch(84% 0.13 94) · `--bad` oklch(71% 0.16 27)
· `--hi`/`--hi-bg` (above GTO, red) · `--lo`/`--lo-bg` (below GTO, blue).

**Table:** `--felt-1` oklch(26% 0.012 230) centre → `--felt-2` oklch(20.5% 0.010 240) edge,
`--rail` oklch(19% 0.007 264), `--glow` (player-to-act halo), `--felt-shadow`.

**Cards:** `--card-bg` oklch(97.5% 0.004 95); suits on the card face `--suit-s/h/d/c`;
`--suit-*-ink` for suits on dark surfaces (deck picker); `--card-back`, `--card-back-line`.

**Grid:** `--cell-bg` oklch(23%), `--cell-pair` oklch(26.5%), `--cell-grout` oklch(15%). Grout is
darker than the cells so the matrix reads as one object.

**Type**
- `--font-body` "Geist": all text and labels. `--font-mono` "Geist Mono": only numbers in columns
  (stacks, pots, bb, frequencies, EV, combos) and `kbd`. `--font-label` = body.
- Scale: `--text-2xs` 11 · `xs` 12 · `sm` 13 · `base` 14 · `md` 15 · `lg` 22 (page title) ·
  `xl` 28 (verdict) · `num` 32.
- `.label` = 11px / 500 / uppercase / 0.06em tracking / `--ink-3`.
- Body has `font-variant-numeric: tabular-nums`; headings track −0.015 to −0.035em.

**Space:** `--s1`…`--s7` = 4 · 8 · 12 · 16 · 24 · 32 · 48; `--gutter` clamp(16px, 2.4vw, 32px).

**Radius:** `--radius-sm` 6 (chips, kbd) · `--radius` 8 (controls) · 10 (seat plates, action
buttons, verdict) · `--radius-lg` 12 (panels, tables) · `--radius-card` 6 · sheets 14.

**Elevation:** `--shadow-1` hair edge + 0 1px 2px (panels, tables, tiles) · `--shadow-2` hair edge +
0 2px 6px −2px + 0 12px 28px −12px (seats, ticket, popovers) · `--shadow-3` hair edge +
0 24px 64px −16px (sheets, tooltip, toast) · `--focus` 2px page-colour gap + 2px accent ring.

**Motion:** `--ease` cubic-bezier(0.22, 1, 0.36, 1); `--dur` 160ms (hover/press); `--dur-2` 220ms
(reveals).

**Layout:** `--topbar-h` 56 (52 on phones), `--side-w` 400, `--table-max` 860, `--grid-max` 600;
main content max 1440px.

**Variants** (attributes on `<html>`): `data-theme="light|dark"`, `data-accent="iris"`,
`data-suits="2"` (diamonds take the heart colour, clubs the spade colour). Nothing sets them today;
to wire a preference later:

```js
const pref = JSON.parse(localStorage.getItem("pn.look") || "{}");
if (pref.suits === 2) document.documentElement.dataset.suits = "2";
if (pref.accent === "iris") document.documentElement.dataset.accent = "iris";
```

## Components (`components.css`, `app.css`)

- **Topbar:** sticky 56px, `--bg-0` at 86% with 14px backdrop blur, inset bottom hairline. Brand is a
  10px accent square with a 3px accent-soft ring. Nav links are 32px pills (13/500, `--ink-2`);
  hover `--bg-2`; current `--bg-3` + hair edge.
- **Buttons:** `.btn` 36px (44 on phones), 0 16px padding, radius 8, 13/600, `--accent-fill`, inset
  top highlight. `.btn.ghost` `--bg-2` + `--shadow-1`, 500 weight, hover `--bg-3`. Disabled 40%
  opacity; active `translateY(1px)`.
- **kbd:** 20px tall, min-width 20, radius 5, Geist Mono 11/500, ink at 10%. `.kbd-soft` (bg-3),
  `.kbd-inv` (on accent fill).
- **Segmented control `.seg`:** recessed `--bg-1` track, 3px padding, radius 10; options 30px,
  radius 7, 13/500 sentence case; pressed = raised `--bg-4` (light: `--bg-2`) + hair + 1px shadow.
- **Chips:** 30px pill, `--bg-2` + hair; pressed = `--accent-soft` fill + `--accent-line` ring.
- **Badges, tags, verdicts, arch, job chips:** 20–24px pills, `currentColor` at 13%, 11/600 caps.
- **Inputs:** 36px (44 on phones), `--bg-1`, radius 8, inset 1px `--line-2`; hover ring `--ink-4`;
  focus ring `--accent` + 3px `--accent-soft`; invalid ring `--bad`. Selects get a CSS chevron.
- **Panels:** `--bg-2`, radius 12, `--shadow-1`, 24px padding (16 on phones). A table inside a
  panel loses its own surface.
- **Data tables:** the wrapper is the surface (`--bg-2`, radius 12). Cells 11px 14px, 1px `--line`
  row rules, no vertical rules, last row unruled. Head 11/500 caps `--ink-3` with a `--line-2`
  underline. Numbers mono, right-aligned. Hover row `--bg-3`. `td.stat.hi/.lo` tinted.
- **Tooltip `.tip`:** `--bg-3`, radius 10, `--shadow-3`. **Sheet:** `--bg-2`, radius 14, scrim 60%
  with 4px blur. **Toast:** `--bg-3`, radius 10, `--shadow-3`.
- **13×13 grid:** 2px gap (1px in `.gw` and on phones), grout `--cell-grout`, radius 8 with
  `overflow: hidden`. Cell names Geist 600 at clamp(7px…12px), −0.02em. `.gw` names are white with a
  three-layer dark halo (light theme: ink with a white halo). Hero cell: 2px accent outline + 3px
  inner dark ring. Pinned grid: other cells at 40%.
- **Legend:** 10px swatches (radius 3), mono 12/500 counts, no-wrap items.

Per-screen details (trainer table, seats, action bar, verdict, ranges panel, side tabs; strategy
library/new/browser; hands, players, review, charts; performance) are in
`docs/design/graphite/README.md` under "Screens".

## State hooks the CSS styles

All behaviour lives in the existing JS; the CSS only restyles these states:
`:hover`, `:active`, `:focus-visible` (accent ring everywhere), `[aria-pressed]`, `[aria-selected]`,
`[aria-current]`, `[disabled]`, `[hidden]`, `.is-pressed`, `.is-dimmed`, `.is-counting`,
`.is-acting`, `.is-folded`, `.is-allin`, `.is-hero`, `.is-selected`, `.has-pin`, `.is-pinned`,
`.is-drilled`, `.armed`, `.is-armed`, `[data-status]` on `.job-panel`. Trainer action buttons also
carry `act-<kind>` (fold/check/call/raise/allin, from `actionKind` in `grid.js`).

**Responsive:** ≤ 1100 stacks the two-column surfaces; ≤ 900 compacts library and performance rows;
≤ 700 switches to the phone drawer nav, 44px targets, full-width segments and a 3:4 table;
≤ 480 hides grid sub-labels.

## Adding UI

1. Pick an existing surface level (`--bg-1` well, `--bg-2` panel, `--bg-3` raised) and the matching
   shadow; do not draw borders, use `--hair` or `--line`.
2. Text is Geist; only columnar numbers and kbd are Geist Mono. Labels use `.label`.
3. One accent. Semantic colour (`--good/--warn/--bad`, `--hi/--lo`, `--act-*`) carries meaning;
   never use it decoratively.
4. Animate transform and opacity only, with `--ease` and `--dur`/`--dur-2`, and leave the
   `prefers-reduced-motion` block intact.
5. Check 375 wide (no horizontal scroll), keyboard focus rings and the light theme.

## Verifying a style change

```bash
cd pn && .venv/bin/python -m pytest -q          # CSS changes must leave the suite unchanged
cp data/pn.sqlite /tmp/pn-review.db             # the trainer writes to the database
PN_DB=/tmp/pn-review.db pn/.venv/bin/pn web     # from the repo root, port 8765
```

Then screenshot every route (`/train`, `/train/performance`, `/strategy`, `/strategy/new`,
`/strategy/{name}`, `/hands`, `/hands/{id}`, `/players`, `/players/{id}`, `/review`, `/charts`) at
375, 768 and 1440 wide in both themes; check keyboard play, focus rings and that nothing scrolls
horizontally at 375.

## Known caveats

- Cell names on the lighter action colours (`#3fa66a`, `#e8705f`) rely on the dark halo for
  legibility; plain white alone is about 3:1. The hues are fixed by constraint.
- In the light theme `.gw` cell names are ink with a white halo and read worse than white would on
  the darkest bet reds. Forcing white names on `.gw` grids in both themes (as `.tr-line` does) is an
  open option.

## History

- 2026-10-07 "Ledger" (Instrument Sans + Space Mono, felt greens, brass accent); its handoff is not kept in the repository.
- 2026-10-19 "Graphite" (this document): `docs/design/graphite/`. The pre-Graphite CSS is kept in
  `docs/design/graphite/previews/css-before/` for a before/after comparison.
