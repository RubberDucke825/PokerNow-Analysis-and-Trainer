"""Golden tests for the solver core. A DIFF HERE MEANS SOLVER BEHAVIOUR OR CACHE KEYS CHANGED: the
TexasSolver command file decides what is solved, and the cache key (sha256 of the canonical spot)
names every cached document, so a changed key silently invalidates every solve in every cache
and the flop library (189k documents) along with it. Do not regenerate unless the change is
intended; then re-key or re-solve the library in the same change (`pn library reindex`, fill).
Regenerate: PN_UPDATE_GOLDEN=1 uv run pytest tests/test_solver_golden.py.

The spot is the README's `pn solve new --seats BTN,BB --board Kd2cKh --line srp --hero BTN`,
under each solve preset. Library flop keys per matchup are pinned separately in
fixtures/hu_flop_6max_100bb_keys.json (test_library_alias, test_library_8max_200bb).
Needs no solver binary."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pn.solve import build_spot, docstore, spot_key
from pn.solve.request import SOLVE_PRESETS, apply_preset, new_request, parse_board
from pn.solve.texas import command_text

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
KEYS = GOLDEN / "readme_spot_keys.json"
UPDATE = os.environ.get("PN_UPDATE_GOLDEN") == "1"
PRESETS = ("trainer", "review", "library")


def _spot(preset: str | None) -> dict:
    req = new_request(["BTN", "BB"], 100, board=parse_board("Kd2cKh"), hero="BTN", line="srp")
    spot = build_spot(req)
    return apply_preset(spot, preset)[0] if preset else spot


def _check(path: Path, text: str) -> None:
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"{path.name} changed: solver input differs (see module doc)"


def test_presets_are_the_pinned_ones() -> None:
    assert set(SOLVE_PRESETS) == set(PRESETS)


@pytest.mark.parametrize("preset", PRESETS)
def test_command_text(preset: str) -> None:
    _check(GOLDEN / f"readme_spot_{preset}.txt", command_text(_spot(preset))[0])


def test_cache_keys() -> None:
    keys = {name: spot_key(_spot(None if name == "none" else name)) for name in ("none", *PRESETS)}
    _check(KEYS, json.dumps(keys, indent=1, sort_keys=True) + "\n")
    assert len(set(keys.values())) == len(keys)  # presets never share a key


@pytest.mark.parametrize("gz", ["1", "0"])
def test_document_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gz: str) -> None:
    monkeypatch.setenv("PN_CACHE_GZIP", gz)
    doc = json.loads((Path(__file__).parent / "fixtures" / "adapter_hu_flop_out.json").read_text())
    key = spot_key(_spot("trainer"))
    path = docstore.dump(tmp_path, key, doc)
    assert path.name == f"{key}{'.json.gz' if gz == '1' else '.json'}"
    assert docstore.read(tmp_path, key) == doc and docstore.doc_files(tmp_path) == {key: path}
    if gz == "1":
        assert path.read_bytes()[4:8] == b"\0\0\0\0"  # gzip mtime 0: same document, same bytes
        assert docstore.load(path) == doc
