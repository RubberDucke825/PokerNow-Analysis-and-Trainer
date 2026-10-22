# Range Format

Text (Pio/GTO Wizard compatible):
`AA,KK,QQ:0.5,AKs,AQo:0.25,T9s-54s,A5s+,22+`
- Hand classes: `AA` (pair), `AKs`, `AKo`, `AK` (= AKs+AKo). Ranges `T9s-54s` (same gap down), `A5s+` (kicker up), `22+`.
- Optional `:w` weight 0..1 (default 1). Weight applies to every combo in the class.
- Specific combos allowed: `AhKh:0.7`.
- Whitespace ignored. Later entries override earlier for the same combo.

Dense JSON form: `{"weights": [f32 x 1326]}` indexed by `pn.ranges.combo_index` (contracts/README.md). Blocked combos (board/dead cards) have weight 0.

Python: `pn.ranges.parse(text) -> dict[str,float]` (combo string -> weight), `pn.ranges.to_text(d)`, `pn.ranges.to_dense(d) -> list[float]`, `pn.ranges.from_dense(weights)`. `data/fixtures/combo_index.json` lists the 1326 combo strings in index order.
