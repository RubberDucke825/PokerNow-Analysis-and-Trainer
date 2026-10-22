"""Canonical flops and texture buckets (pn.solve.flops, moved from the removed pn.batch)."""

from __future__ import annotations

import pytest

from pn.solve import flops as fl


def test_canonical_flops_cover_every_raw_flop() -> None:
    flops = fl.canonical_flops()
    assert len(flops) == fl.N_CANONICAL_FLOPS
    assert sum(f.weight for f in flops) == fl.N_RAW_FLOPS
    assert {f.bucket for f in flops} == set(fl.CLASS_KEYS)


@pytest.mark.parametrize("n", [1, 3])
def test_select_flops_keeps_total_weight_and_every_bucket(n: int) -> None:
    sel = fl.select_flops(n)
    assert sum(f.weight for f in sel) == fl.N_RAW_FLOPS
    per_bucket = {k: sum(1 for f in sel if f.bucket == k) for k in fl.CLASS_KEYS}
    assert all(1 <= c <= n for c in per_bucket.values())
    assert all(fl.bucket_of(f.flop) == f.bucket for f in sel)


def test_flop_selection_specs() -> None:
    assert len(fl.parse_flop_selection("all")) == fl.N_CANONICAL_FLOPS
    assert len(fl.parse_flop_selection("2-per-bucket")) <= 2 * len(fl.CLASS_KEYS)
    (a,) = fl.parse_flop_selection("Kd Ah 5h")
    (b,) = fl.parse_flop_selection("AsKh5s")  # same flop up to suit isomorphism
    assert a == b and a.bucket == "twotone_bway"
    with pytest.raises(ValueError):
        fl.parse_flop("AhAh5c")
    with pytest.raises(ValueError):
        fl.select_flops(0)


@pytest.mark.parametrize(("flop", "bucket"), [
    ("7c7d7h", "trips"), ("KcKd5h", "paired_high"), ("5c5d9h", "paired_low"),
    ("Ac8d3h", "rainbow_ahigh"), ("KcQd4h", "rainbow_bway"), ("9c8d6h", "rainbow_connected"),
    ("Tc6d2h", "rainbow_high_dry"), ("8c4d2h", "rainbow_low_dry"), ("Ah7h2d", "twotone_ahigh"),
    ("9h8h6h", "mono_connected"), ("9h8h2h", "mono_low_dry"),
])  # fmt: skip
def test_bucket_examples(flop: str, bucket: str) -> None:
    assert fl.bucket_of(flop) == bucket
