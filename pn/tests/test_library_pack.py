"""tools/library_pack.py: pack the flop library into sha256-checked tar volumes, unpack them
(never over a newer document, index merged and re-keyed) and cut a one-matchup sample; the
committed sample (data/fixtures/library_sample.tar.gz) deals a trainer flop."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from pn.profile import charts
from pn.solve import flops as fl
from pn.train import flop_library as flib
from pn.train import preflop as pre

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "data" / "fixtures" / "library_sample.tar.gz"
BTN_OPEN_BB_CALL = [("LJ", "fold"), ("HJ", "fold"), ("CO", "fold"), ("BTN", "raise"), ("SB", "fold"),
                    ("BB", "call")]
_spec = importlib.util.spec_from_file_location("library_pack", REPO / "tools" / "library_pack.py")
lp = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(lp)  # type: ignore[union-attr]


def _key(i: int) -> str:
    return f"{i:064x}"


def _cache(root: Path, n: int = 12, size: int = 3000) -> Path:
    """n fake documents (gz) + requests and an index with one matchup holding them."""
    root.mkdir(parents=True, exist_ok=True)
    boards = {}
    for i in range(n):
        k = _key(i)
        (root / f"{k}.json.gz").write_bytes(gzip.compress(json.dumps({"i": i, "pad": os.urandom(size).hex()}).encode()))
        (root / f"{k}.toml").write_text(f"# pn solve cache {k}\n")
        boards[f"b{i:02d}"] = {"key": k, "weight": 1}
    (root / lp.INDEX_NAME).write_text(json.dumps({"matchups": {"m1": {
        "label": "srp BB-BTN 100bb: BTN raise, BB call", "depth": 100, "boards": boards},
        "m2": {"label": "alias", "depth": 100, "alias_of": "m1"}}}))  # fmt: skip
    (root / ".solve.lock").write_text("")
    (root / "notes.txt").write_text("not a library file")
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in root.iterdir() if lp.DOC_RE.fullmatch(p.name)}


def test_pack_splits_into_checked_volumes(tmp_path: Path) -> None:
    src = _cache(tmp_path / "cache")
    out = tmp_path / "out"
    man = lp.pack(src, out, part_bytes=40_000)
    parts = man["parts"]
    assert len(parts) > 1 and all(p["bytes"] < 40_000 for p in parts)
    assert (man["documents"], man["boards"], man["matchups"], man["aliases"]) == (12, 12, 1, 1)
    assert "pn_version" in man and "commit" in man
    assert sum(p["files"] for p in parts) == 1 + 24  # index + doc + toml per key; no lock / notes
    sums = (out / lp.SUMS_NAME).read_text().splitlines()
    assert sums == [f"{p['sha256']}  {p['name']}" for p in parts]
    with tarfile.open(out / parts[0]["name"]) as tar:
        assert tar.getnames()[0] == lp.INDEX_NAME
    with pytest.raises(ValueError, match="does not fit"):
        lp.pack(src, tmp_path / "tiny", part_bytes=4_000)


def test_unpack_round_trip_and_tamper(tmp_path: Path) -> None:
    src = _cache(tmp_path / "cache")
    out = tmp_path / "out"
    man = lp.pack(src, out, part_bytes=40_000)
    dest = tmp_path / "dest"
    stats = lp.unpack(out, dest, do_reindex=False)
    assert stats["written"] == 24 and stats["boards_added"] == 12
    assert _files(dest) == _files(src)
    assert json.loads((dest / lp.INDEX_NAME).read_text()) == json.loads((src / lp.INDEX_NAME).read_text())
    bad = out / man["parts"][-1]["name"]
    bad.write_bytes(bad.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="sha256"):
        lp.unpack(out, tmp_path / "dest2", do_reindex=False)


def test_unpack_keeps_newer_documents_and_indexed_boards(tmp_path: Path) -> None:
    src = _cache(tmp_path / "cache", n=3)
    out = tmp_path / "out"
    lp.pack(src, out)
    dest = tmp_path / "dest"
    dest.mkdir()
    newer, older = dest / f"{_key(0)}.json.gz", dest / f"{_key(1)}.json"
    newer.write_bytes(b"mine")  # written after the pack: newer than the archived one
    older.write_bytes(b"old plain")
    os.utime(older, (1, 1))
    (dest / lp.INDEX_NAME).write_text(json.dumps({"matchups": {"m1": {
        "label": "x", "depth": 100, "boards": {"b00": {"key": "mine", "weight": 9}}}}}))  # fmt: skip
    stats = lp.unpack(out, dest, do_reindex=False)
    assert newer.read_bytes() == b"mine" and stats["kept"] == 1
    assert not older.exists() and (dest / f"{_key(1)}.json.gz").exists()  # older plain replaced
    boards = json.loads((dest / lp.INDEX_NAME).read_text())["matchups"]["m1"]["boards"]
    assert boards["b00"]["key"] == "mine" and set(boards) == {"b00", "b01", "b02"}


def test_unpack_skips_unsafe_members(tmp_path: Path) -> None:
    arc = tmp_path / "evil.tar.gz"
    with tarfile.open(arc, "w:gz") as tar:
        for name in ("../escape.json", f"sub/{_key(1)}.toml", f"{_key(2)}.toml"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"ok"))
        link = tarfile.TarInfo(f"{_key(3)}.toml")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    dest = tmp_path / "dest"
    stats = lp.unpack(arc, dest, do_reindex=False)
    assert stats["skipped"] == 3 and stats["written"] == 1
    assert sorted(p.name for p in dest.iterdir()) == [f"{_key(2)}.toml"]
    assert not (tmp_path / "escape.json").exists()


needs_charts = pytest.mark.skipif(
    not (charts.charts_dir() / charts.PC_FILE).exists() or not pre.has_freqs(),
    reason="PokerCoaching charts or GTO Wizard frequencies not present")  # fmt: skip


@needs_charts
def test_sample_takes_the_trainer_matchup(tmp_path: Path) -> None:
    m = lp.sample_matchup(6, 100, "srp", ["BTN", "BB"])
    assert m.label == "srp BB-BTN 100bb: BTN raise, BB call"
    root = tmp_path / "cache"
    root.mkdir()
    boards = {}
    for flop in ("2c2d2h", "3c2c2d", "AhKd2c"):
        key = flib.flop_spot(m, tuple(fl.flop_cards(flop)), 1.0)[2]
        (root / f"{key}.json.gz").write_bytes(gzip.compress(b"{}"))
        boards[flop] = {"key": key, "weight": 4, "accuracy": 1.0}
    (root / lp.INDEX_NAME).write_text(json.dumps({"matchups": {
        m.mid: {"label": m.label, "depth": 100, "pot": "srp", "flops": "all", "boards": boards},
        "other": {"label": "other", "depth": 100, "boards": {"x": {"key": "y"}}}}}))  # fmt: skip
    res = lp.sample(root, tmp_path / "s.tar.gz", table=6, depth=100, line="srp", seats=["BTN", "BB"], boards=2)
    assert (res["matchup"], res["boards"]) == (m.mid, 2)
    with tarfile.open(tmp_path / "s.tar.gz") as tar:
        names = tar.getnames()
        index = json.load(tar.extractfile(lp.INDEX_NAME))  # type: ignore[arg-type]
    assert list(index["matchups"]) == [m.mid] and list(index["matchups"][m.mid]["boards"]) == ["2c2d2h", "3c2c2d"]
    assert "flops" not in index["matchups"][m.mid] and len(names) == 3
    bad = {**boards, "2c2d2h": {**boards["2c2d2h"], "key": "0" * 64}}
    (root / lp.INDEX_NAME).write_text(json.dumps({"matchups": {m.mid: {"label": m.label, "depth": 100, "boards": bad}}}))
    with pytest.raises(ValueError, match="trainer key"):
        lp.sample(root, tmp_path / "t.tar.gz", table=6, depth=100, line="srp", seats=["BTN", "BB"], boards=2)


@needs_charts
@pytest.mark.skipif(not SAMPLE.exists(), reason="committed library sample missing")
def test_committed_sample_deals_a_library_flop(tmp_path: Path) -> None:
    from fake_tree import fake_runner

    from pn.solve import SolveCache
    from pn.train import Config, Trainer

    root = tmp_path / "cache"
    stats = lp.unpack(SAMPLE, root)
    m = lp.sample_matchup(6, 100, "srp", ["BTN", "BB"])
    assert stats["written"] >= 50 and len(flib.FlopIndex(root).boards(m.mid)) == stats["written"] // 2
    t = Trainer(SolveCache(root, runner=fake_runner), background=False)
    v = t.new(Config(seed=1, hero_seat="BTN", drill_share=0))
    for _ in range(40):
        v = _play_preflop(t, v)
        line = [(h["position"], h["token"]) for h in v["history"] if h["street"] == "preflop"]
        if v["street"] == "flop" and line == BTN_OPEN_BB_CALL:
            assert v["solve"]["status"] == "ready" and v["solve"]["backend"] == "texas"
            assert v["solve"]["note"] != "no_library_solve"
            return
        while not v["hand_over"]:
            v = t.act(v["sid"], _passive(v))
        v = t.next(v["sid"])
    pytest.fail("no BTN-raise BB-call flop in 40 hands")


def _passive(v: dict) -> str:
    toks = [o["token"] for o in v["legal_actions"]]
    return next(t for t in ("check", "call", "fold") if t in toks)


def _play_preflop(t, v: dict) -> dict:
    """Hero opens once when it can, then checks / calls / folds."""
    while not v["hand_over"] and v["street"] == "preflop":
        toks = [o["token"] for o in v["legal_actions"]]
        hero = v["hero"]["position"]
        raised = any(h["position"] == hero and h["token"].startswith("raise") for h in v["history"])
        tok = next((x for x in toks if x.startswith("raise")), None) if not raised else None
        v = t.act(v["sid"], tok or _passive(v))
    return v

