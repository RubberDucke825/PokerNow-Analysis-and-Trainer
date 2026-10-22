# Handoff: pn study "Graphite" restyle

## Overview
A restyle of the `pn` poker study app (local FastAPI app, `pn web`, port 8765) toward a calm, premium "pro tool" feel: cool graphite surfaces, one amber accent, Geist + Geist Mono, layered surfaces with hairlines and soft shadows instead of borders. It covers every route in `pn/pn/web/app.py` PAGES: `/train`, `/train/performance`, `/strategy`, `/strategy/new`, `/strategy/{name}`, `/hands`, `/hands/{id}`, `/players`, `/players/{id}`, `/review`, `/charts`.

Chosen defaults (confirmed by the user): **dark theme, amber accent, four-colour suits.** The light theme, iris accent and two-colour deck exist as opt-in variants.

## About the files (read this first)
The handoff's production CSS has been applied: it now lives in `pn/pn/web/static/*.css` (same file names, selectors and token names as the handoff). This directory keeps only the design references and the optional patch.

- `pn/pn/web/static/*.css` is the production CSS described below. `index.html`, `core.js`, `grid.js` and the views were not changed by it.
- `previews/*.dc.html` are **design references**: they rebuild each screen's DOM from the view source (same class names, same data attributes) and load the CSS from `previews/css/`. Use them to see the intended result and to compare against the running app. Do not ship them. Open them in a browser served over HTTP (e.g. `python -m http.server` inside `previews/`); `support.js` and `mock.js` must sit next to them.
- `previews/css-before/` is a copy of the current Ledger CSS, for a Before/After toggle (Tweaks or `?version=before`).

The app is vanilla ES modules, has no build step, and its only external resource is Google Fonts. The new CSS keeps all of that: no new dependencies, and the only change to the font `@import` is the families.

## Fidelity
**High fidelity.** Colours, type, spacing, radii, shadows, states and motion are final and live in the CSS. The mock data in the previews (names, hands, frequencies, grids) is illustrative only.

## Hard constraints that were respected (keep respecting them)
- **Action colours are fixed** (GTO Wizard mapping): fold `#4a86d9`, check/call `#3fa66a`, bets `#e8705f` → `#d4483f` → `#b3302f` → `#922326` (darker = larger), all-in `#6e1220`. They live in `--act-*` and `ACTION_COLORS` in `pn/pn/web/static/js/grid.js`. In the line-breakdown grids, black (`#000`) means "not in range".
- **13×13 grid conventions:** action-split cells, opacity/width = share of combos in range, hero hand outlined (`.hero-cell`), and a text legend with combo counts.
- **Four-colour suits by default** (`--suit-*`), with a two-colour option.
- **Trainer actions map to the solver tree:** Fold / Check / Call + fixed sizes. No bet slider. Keyboard shortcuts unchanged.
- **DOM hooks unchanged:** `data-cls`, `data-nav`, `.seg-opt`, `.hero-cell`, `aria-pressed`, `aria-selected`, `[style*="--cx"]` seat ring, `--act`, `--cd`, `--p`, etc.
- **Light theme + `prefers-reduced-motion`** are both supported.

## Rollout (done; one commit each, check the app between steps)
1. `tokens.css` + `components.css` + `app.css`: every page changes look, nothing should break.
2. `train.css` (+ optional `train_actions.js.diff`).
3. `strategy.css` + `study.css`.
4. `performance.css`.

Then:
- `cd pn && .venv/bin/python -m pytest -q` should pass unchanged (CSS only).
- Copy the database first (`cp <db> /tmp/pn-review.db`), then run `pn web` from the repo root pointing at the copy. The trainer writes to the database.
- Take screenshots at 375, 768 and 1440 wide, in both themes, for every route. Check keyboard play (letters, `1`–`9`, `N`/`Space`, `Z`, `G`, `?`), check focus rings, and check there is no horizontal scroll at 375.

## Design tokens (dark, default; all in `pn/pn/web/static/tokens.css`)
Colours are oklch. The light theme overrides the same names under `prefers-color-scheme: light` and `[data-theme="light"]`.

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

**Ink:** `--ink` 95.5%, `--ink-2` 79%, `--ink-3` 66% (labels, ≥ 4.5:1 on bg-0…bg-3), `--ink-4` 48% (disabled/folded only). Light theme: 21 / 40 / 50 / 68%.

**Accent:** `--amber` oklch(80% 0.115 76) (light: 55% 0.12 68), `--iris` oklch(77% 0.10 286) (light: 52% 0.14 286). `--accent` = amber by default. Derived tokens:
- `--accent-soft`: 14% mix, for fills
- `--accent-line`: 50% mix, for outlines
- `--accent-fill` / `--accent-fill-ink`: filled buttons. In light theme these are oklch(80% 0.135 78) with dark ink, so buttons stay bright instead of turning brown.
- `--accent-ink`: text on accent.

**Semantics:** `--good` oklch(77% 0.14 152), `--warn` oklch(84% 0.13 94), `--bad` oklch(71% 0.16 27), `--hi`/`--hi-bg` (above GTO, red), `--lo`/`--lo-bg` (below GTO, blue).

**Table:** `--felt-1` oklch(26% 0.012 230) centre → `--felt-2` oklch(20.5% 0.010 240) edge, `--rail` oklch(19% 0.007 264), `--glow` (player-to-act halo), `--felt-shadow`.

**Cards:** `--card-bg` oklch(97.5% 0.004 95). Suits on the card face: `--suit-s`, `--suit-h`, `--suit-d`, `--suit-c`. The `--suit-*-ink` variants are for dark surfaces (deck picker). `--card-back`, `--card-back-line`.

**Grid:** `--cell-bg` oklch(23%), `--cell-pair` oklch(26.5%), `--cell-grout` oklch(15%). Grout is darker than the cells, so the matrix reads as one object.

**Type**
- `--font-body`: "Geist". Used for all text and labels.
- `--font-mono`: "Geist Mono". Used only for numbers in columns (stacks, pots, bb, frequencies, EV, combos) and `kbd`.
- `--font-label` = body.
- Scale: `--text-2xs` 11 · `xs` 12 · `sm` 13 · `base` 14 · `md` 15 · `lg` 22 (page title) · `xl` 28 (verdict) · `num` 32.
- `.label` = 11px / 500 / uppercase / 0.06em tracking / `--ink-3`.
- Body has `font-variant-numeric: tabular-nums`. Headings use −0.015 to −0.035em tracking.

**Space:** `--s1`…`--s7` = 4 · 8 · 12 · 16 · 24 · 32 · 48. `--gutter` clamp(16px, 2.4vw, 32px).

**Radius:** `--radius-sm` 6 (chips, kbd) · `--radius` 8 (controls) · 10 (seat plates, action buttons, verdict) · `--radius-lg` 12 (panels, tables) · `--radius-card` 6 · sheets 14.

**Elevation**
- `--shadow-1`: hair edge + 0 1px 2px. Panels, tables, tiles.
- `--shadow-2`: hair edge + 0 2px 6px −2px + 0 12px 28px −12px. Seats, ticket, popovers.
- `--shadow-3`: hair edge + 0 24px 64px −16px. Sheets, tooltip, toast.
- `--focus`: 2px page-colour gap + 2px accent ring.

**Motion:** `--ease` cubic-bezier(0.22, 1, 0.36, 1); `--dur` 160ms (hover/press); `--dur-2` 220ms (reveals). Only transform and opacity animate.

**Layout:** `--topbar-h` 56 (52 on phones), `--side-w` 400, `--table-max` 860, `--grid-max` 600. Main content is max 1440px.

**Variants (attributes on `<html>`):** `data-theme="light|dark"`, `data-accent="iris"`, `data-suits="2"` (diamonds take the heart colour, clubs the spade colour).

## Components (all in `pn/pn/web/static/components.css` + `pn/pn/web/static/app.css`)
- **Topbar:** sticky, 56px, `--bg-0` at 86% with 14px backdrop blur, inset bottom hairline. Brand: 10px accent square with a 3px accent-soft ring. Nav links are 32px pills (13px/500, `--ink-2`); hover `--bg-2`; current `--bg-3` + hair edge.
- **Buttons:**
  - `.btn`: 36px tall (44 on phones), 0 16px padding, radius 8, 13px/600, `--accent-fill`, inset top highlight.
  - `.btn.ghost`: `--bg-2` + `--shadow-1`, 500 weight; hover `--bg-3`.
  - Disabled: 40% opacity. Active: `translateY(1px)`.
- **kbd:** 20px tall, min-width 20, radius 5, Geist Mono 11/500, ink at 10% on transparent. Variants: `.kbd-soft` (bg-3) and `.kbd-inv` (on accent fill).
- **Segmented control (`.seg`):** recessed `--bg-1` track, 3px padding, radius 10. Options are 30px tall, radius 7, 13px/500 sentence case. Pressed option: raised `--bg-4` (light: `--bg-2`) + hair edge + 1px shadow. The accent is not used for selection.
- **Chips:** 30px pill, `--bg-2` + hair. Pressed: `--accent-soft` fill + `--accent-line` ring.
- **Badges, tags, verdicts, arch, job chips:** 20–24px pills. Fill is `currentColor` at 13%, with 11px/600 caps text.
- **Inputs:** 36px (44 on phones), `--bg-1`, radius 8, inset 1px `--line-2`. Hover ring `--ink-4`; focus ring `--accent` + 3px `--accent-soft`; invalid ring `--bad`. Selects get a CSS-gradient chevron.
- **Panels:** `--bg-2`, radius 12, `--shadow-1`, 24px padding (16 on phones). A table inside a panel loses its own surface.
- **Data tables:**
  - The wrapper is the surface (`--bg-2`, radius 12).
  - Cells: 11px 14px padding, 1px `--line` row rules, no vertical rules, last row has no rule.
  - Head: sans 11/500 caps `--ink-3`, `--line-2` underline.
  - Numbers: mono, right-aligned. Hover row: `--bg-3`.
  - `td.stat.hi`/`.lo`: tinted backgrounds.
- **Tooltip `.tip`:** `--bg-3`, radius 10, `--shadow-3`. **Sheet:** `--bg-2`, radius 14, scrim at 60% with 4px blur. **Toast:** `--bg-3`, radius 10, `--shadow-3`.
- **13×13 grid:**
  - Layout: 2px gap (1px in `.gw` and on phones), grout `--cell-grout`, radius 8 with `overflow: hidden`.
  - Cell names: Geist 600 at clamp(7px…12px), −0.02em.
  - `.gw` cell names: white with a three-layer dark halo (light theme: ink with a white halo).
  - Hero cell: 2px accent outline + 3px inner dark ring.
  - Pinned grid: other cells at 40% opacity.
- **Legend:** 10px swatches (radius 3), mono 12/500 counts, no-wrap items.

## Screens
### /train (`train.css`)
- **Page head:** "Trainer" label + h1 "Play", RNG badge (40px, mono 22/600 accent), "Performance" link, ghost "New session" (armed state: bad tint + ring). Under it, `page-meta` rolls in mono 12 separated by 3px dots.
- **Surface:** `.train-surface` is radius 12 with a hair edge. `.two-col` = table column + 400px side column. At ≥ 1101px the side column is sticky (top 56px, own scroll, sticky tab bar) and the table is capped at `calc((100vh − 400px) × 2)` wide. The goal is that table, action bar and verdict fit on one screen. Below 1100px the side column stacks under the table.
- **Table:**
  - Felt: radial `--felt-1` → `--felt-2`, inner vignette, 10px `--rail` ring + hairline, soft drop shadow.
  - Pot: pill with "POT" in 11px caps and the amount in mono 15/600.
  - Board cards deal in with a 220ms fade-up and a 50ms stagger.
  - Cards on the table size with the table (container units): 28–44px wide, aspect 5:7. Hero cards are larger (34–50px).
- **Seats:**
  - Plate: `--bg-2`, radius 10, `--shadow-2`. Position pill 24px (`--bg-3`, 11px/600); name `--ink-2`; stack mono 13/500.
  - Hero: accent ring + accent position pill.
  - To act: steady neutral halo (`--glow`) — 1px ring, 4px soft ring and 28px blur. The hero's halo uses the accent instead.
  - Folded: 42% opacity, no shadow, action label hidden.
  - All-in: red ring + red stack.
  - Recent action: 20px pill, 11px caps, coloured by kind. Bet tag: 24px pill with a 14px chip dot. Dealer: 22px light disc with "D". Face-down cards: two overlapping 22×30 backs.
- **Action bar:**
  - Each button is 56px tall, radius 10, filled with `--act` at 15% over `--bg-2`, with a 42% `--act` inset ring. Label is 15/600, the bb amount (`.act-sub`) mono 12/500 `--ink-2`, and the kbd sits at the right, tinted by `--act`.
  - Hover raises the fill to 24%. `.is-pressed` uses a 30% fill, a 2px `--act` ring and a 4px halo; the other buttons get `.is-dimmed` at 32% opacity.
  - Fold (matched by `[aria-keyshortcuts~="f" i]` or `.act-fold`) is transparent with a `--line-2` ring and `--ink-2` text.
  - Phones: 52px tall, 3 + 2 grid for five actions, 2 × 2 for four, kbd hidden.
- **Waiting / solving line (`.train-wait`):** a 56px `--bg-2` bar with a breathing accent dot and a 2px accent sweep along the bottom (1.6s, transform only).
- **Hand over:** "Next hand" is the primary button, 56px. During the auto-deal countdown, `.next-drain` shrinks with scaleX over `--cd` and draws a 3px line along the bottom edge. Next to it, the auto-deal toggle is a 56px ghost tile.
- **Inline verdict:**
  - Box: 52px min height, radius 10, background = grade colour at 7% over `--bg-2`, with a 30% grade ring and a 3px grade rule on the left.
  - Content: status pill (grade at 16%), chosen action 15/600, detail 13 `--ink-2`, then the tools ("Retry spot" with an accent ring, "Why →") as 36px ghost buttons.
  - Grade colours: `.lvl-ok` = good, `.lvl-warn` = warn, `.lvl-bad` = bad.
- **Ranges panel (`.tr-ranges`):** `--bg-1` well, radius 12, hair edge. The head has "Ranges at this decision" and a mono meta line. Below it, a `.seg` switches Now / previous-street line / This street. The two grids sit side by side, up to 22rem each at ≥ 1101px, each with a `.legend` showing combo counts; the taken action's legend item becomes a pill. Line steps are 30px chips separated by "›".
- **Side tabs:** "Feedback / Spot importance / Progress", as 36px pill tabs (selected = `--bg-3` + hair). The feedback tab shows a grade dot. Panels fade up 4px on switch.
- **Feedback tab:**
  - "This hand" pills: 32px, pressed = accent-line ring.
  - Head: label "street · pos · class", status pill, chosen action 28/600, best-action text, frequency in mono 22 coloured by grade.
  - RNG bar: 10px tall, 2px gaps between segments, plus an ink marker with a mono number tag.
  - Frequency table: 36px rows; the chosen row is `--bg-3` radius 8; the best action is in accent.
  - Then the grid toggle and "This session" totals.
- **Spot importance tab:** slider + `.seg`, then 44px leak rows. Pinned row: `--accent-soft` fill + 3px accent bar.
- **Progress tab:** KPI tiles in a 3-column grid (2px gaps on `--bg-2`), sparkline in a `--bg-2` well, compact table.

### /train/performance (`performance.css`)
- **KPIs:** tiles on `--bg-2`, 2px gaps, radius 12; the main KPI is in accent at 32px.
- **Trend chart:** `--bg-2` well, 10rem tall; closeness line in accent, blunder line in bad.
- **Heatmap:** `--bg-1` well, 3px cell spacing, cells radius 6 and 3rem tall. Five-step ramp: bad 55% / bad 32% / warn 28% / good 38% / good 82%, each mixed into `--bg-2` in oklab. Thin samples (fewer than 20 graded) are striped; empty cells are outlined. Selected cell: accent outline + 3px accent-soft halo.
- **Detail panel:** a `--bg-2` panel with 52px spot rows (closeness bar, best/mixed/blunder split bar, ghost "Drill this spot").

### /strategy library, /strategy/new, /strategy/{name} (`strategy.css`)
- **Library:** `.lib-panel` with filter bar (`--bg-1`) and 64px rows. On hover a 3px accent bar grows on the left. Cards are 26×36, street tags are tinted pills, exploitability is coloured good/warn/bad, and "Delete" is a ghost button that arms red.
- **New spot:**
  - Steps are in one `--bg-2` panel, numbered with 28px circular mono badges.
  - The range editor's fill uses `--accent-fill` at 80%.
  - Board slots: dashed slots replaced by inset hairline slots. Deck picker cards are 32px (44 on phones), and the pressed card gets an accent ring + soft fill.
  - Sticky "ticket" on the right (`--shadow-2`, radius 12): a mini felt holding the board, `t-row`s with mono values, a 48px "Solve spot" button, and solve progress below it (status chip, three mono stats, spark).
- **Browser:**
  - Line strip on `--bg-1`: 10px-radius cards; the current node gets an accent ring + 3px soft halo; the taken action has a 2px accent bar.
  - Underline tabs (selected = 2px ink underline) with a mono combo count. Filter chips are accent-soft pills.
  - GW grid up to 780px wide, legend below.
  - Right column: action tiles keep the solid `--act` fill (radius 10, 80px tall, mono 24 frequency, white text with a dark shadow; selected = 2px ink ring), stacked bar, equity panel (mono 24 figures, 8px split bar, chart in a `--bg-2` well), hand-class pair rows (36px; the active row is accent-soft with a 3px bar), meta row.

### /hands, /hands/{id}, /players, /players/{id}, /review, /charts (`app.css`, `study.css`)
- **Hands:** filters (label caps over 36px selects), pager, data table with mono net coloured pos/neg, and "SD" badge.
- **Hand replay:** board strip panel, players table, then one section per street: header with h3 15/600, 26×36 cards, mono pot; 40px action rows coloured by kind; grade chips.
- **Players:** matrix table with sticky first column and stat cells (mono value + n; hi/lo tint), plus archetype pills.
- **Player profile:** panels for Exploits (tips), By position (matrix + group rows), Leaks (`--bg-1` cards with a 3px hi/lo inset rule; the number is coloured hi/lo), and Archetype scores (6px bars).
- **Review:** inline form; Blunders panel with a big mono count (32px, bad) and grade tables; deviations table with verdict pills and `linkish` reason toggles.
- **Charts / preflop navigator:** chart block with grid + legend and a range-text form. `study.css` covers the seat strip, the source tag (accent pill; GTO Wizard/chart variants are neutral), the end card (inset 2px accent top), the board list (48px rows) and the board tiles.

## Interactions & behaviour
All behaviour is unchanged and lives in the existing JS. The CSS only restyles these states:
- `:hover`, `:active`
- `:focus-visible` (accent ring everywhere)
- `[aria-pressed]`, `[aria-selected]`, `[aria-current]`, `[disabled]`, `[hidden]`
- `.is-pressed`, `.is-dimmed`, `.is-counting`, `.is-acting`, `.is-folded`, `.is-allin`, `.is-hero`, `.is-selected`, `.has-pin`, `.is-pinned`, `.is-drilled`, `.armed`, `.is-armed`
- `[data-status]` on `.job-panel`

**Responsive:** ≤ 1100 stacks the two-column surfaces; ≤ 900 compacts the library and performance rows; ≤ 700 switches to the phone drawer nav, 44px targets, full-width segments and 3:4 table; ≤ 480 hides grid sub-labels.

**Reduced motion:** every transition and animation is disabled.

## Optional JS
- `train_actions.js.diff` (in this directory) adds an `act-<kind>` class to trainer action buttons (one line; uses `actionKind` from `grid.js`). The CSS already matches fold by its F shortcut, so this is only for robustness.
- Variant switches (only needed if you want iris or a two-colour deck later):
  ```js
  const pref = JSON.parse(localStorage.getItem("pn.look") || "{}");
  if (pref.suits === 2) document.documentElement.dataset.suits = "2";
  if (pref.accent === "iris") document.documentElement.dataset.accent = "iris";
  ```

## Known caveats
- Cell names on the lighter action colours (`#3fa66a`, `#e8705f`) are only legible thanks to the dark halo; plain white alone would be about 3:1. The action hues are fixed by constraint.
- In the light theme, `.gw` cell names are ink with a white halo, and on the darkest bet reds they read worse than white would. Consider forcing white names on `.gw` grids in both themes, as `.tr-line` already does.
- The previews are rebuilt from the view source with mock data, not captured from `pn web`. Verify against the real app using the rollout steps.

## Assets
No images or icons. Fonts: Geist and Geist Mono via Google Fonts `@import` in `tokens.css` (this replaces Instrument Sans + Space Mono). Suit glyphs are the Unicode characters ♠ ♥ ♦ ♣ already emitted by `core.js`.

## Files
```
(production CSS: pn/pn/web/static/
  tokens.css components.css app.css train.css strategy.css study.css performance.css)
train_actions.js.diff     optional
previews/             design references (serve over HTTP)
  Design System.dc.html   tokens + components
  Train.dc.html           /train: ?state=act|pressed|solving|best|villain|blunder|over &seats=6|8 &tab=feedback|leaks|progress
  Strategy.dc.html        ?view=library|new|browser
  Screens.dc.html         ?view=hands|hand|players|player|review|charts|performance
  Responsive.dc.html      375 / 768 / 1440 side by side
  css/  css-before/  mock.js  support.js
  (all previews also take ?mode=light, ?acc=iris, ?suits=2, ?version=before)
```
