"""fetch_game_log / fetch_ledger / CSV import with a fake HTTP session (no network)."""

import json

import pytest

from pn.ingest import fetch as F
from pn.ingest.csv_import import game_id_from_filename, read_csv_log
from pn.ingest.parse import parse_messages

GID = "pglTESTgame0001"


class FakeResp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code, self._payload, self.text, self.headers = (
            status,
            payload,
            text,
            headers or {},
        )

    def json(self):
        return self._payload


class FakeServer:
    """Emulates /log: newest first, 50/page, before_at/after_at exclusive; scripted failures."""

    def __init__(self, n, failures=()):
        self.logs = [{"at": f"t{i}", "created_at": str(1000 + i), "msg": f"m{i}"} for i in range(n)]
        self.failures = list(failures)  # statuses to return before serving the next request
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {}))
        assert headers["User-Agent"] == "curl/8.4.0"
        if self.failures:
            return FakeResp(self.failures.pop(0), headers={})
        if url.endswith(".csv"):
            return FakeResp(200, text="player_nickname,player_id,net\n")
        before = int(params.get("before_at", 10**18))
        after = int(params.get("after_at", -1))
        sel = [m for m in self.logs if after < int(m["created_at"]) < before]
        page = sorted(sel, key=lambda m: -int(m["created_at"]))[: F.PAGE_SIZE]
        return FakeResp(200, {"logs": page, "infos": {}})


def test_parse_game_id():
    assert (
        F.parse_game_id("https://www.pokernow.com/games/pglSAMPLE0000000000000000")
        == "pglSAMPLE0000000000000000"
    )
    assert F.parse_game_id("pglSAMPLE0000000000000000") == "pglSAMPLE0000000000000000"
    with pytest.raises(ValueError):
        F.parse_game_id("not a url!")


def test_full_fetch_paginates_and_caches(tmp_path):
    server, sleeps = FakeServer(120), []
    logs = F.fetch_game_log(GID, tmp_path, session=server, sleep=sleeps.append)
    assert [m["created_at"] for m in logs] == [str(1000 + i) for i in range(119, -1, -1)]
    assert server.calls[0] == {} and server.calls[1] == {"before_at": "1070"}
    assert all(s == F.PAGE_DELAY_S for s in sleeps)
    cache = json.loads((tmp_path / f"{GID}.json").read_text())
    assert cache["complete"] and len(cache["logs"]) == 120


def test_429_backoff_is_exponential_and_resumes(tmp_path):
    server, sleeps = FakeServer(60, failures=[429, 429, 429]), []
    logs = F.fetch_game_log(GID, tmp_path, session=server, sleep=sleeps.append)
    assert len(logs) == 60
    assert sleeps[:3] == [1.5, 3.0, 6.0]


def test_retry_after_header_is_honoured(tmp_path):
    server, sleeps = FakeServer(10), []
    server.failures = [429]
    orig = server.get

    def get(url, params=None, headers=None, timeout=None):
        if server.failures:
            server.failures.pop()
            return FakeResp(429, headers={"Retry-After": "7"})
        return orig(url, params, headers, timeout)

    server.get = get
    F.fetch_game_log(GID, tmp_path, session=server, sleep=sleeps.append)
    assert sleeps[0] == 7.0


def test_interrupted_fetch_keeps_progress_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "MAX_RETRIES", 1)
    server = FakeServer(130)
    real_get = server.get
    state = {"n": 0}

    def flaky(url, params=None, headers=None, timeout=None):
        state["n"] += 1
        if state["n"] in (2, 3):  # second page fails twice -> retries exhausted
            return FakeResp(429)
        return real_get(url, params, headers, timeout)

    server.get = flaky
    with pytest.raises(F.FetchError):
        F.fetch_game_log(GID, tmp_path, session=server, sleep=lambda s: None)
    cache = json.loads((tmp_path / f"{GID}.json").read_text())
    assert len(cache["logs"]) == 50 and not cache["complete"]
    server.get = real_get
    server.calls.clear()
    logs = F.fetch_game_log(GID, tmp_path, session=server, sleep=lambda s: None)
    assert len(logs) == 130
    assert {"before_at": "1080"} in server.calls  # resumed below the cached minimum


def test_incremental_refresh_fetches_only_newer(tmp_path):
    server = FakeServer(120)
    F.fetch_game_log(GID, tmp_path, session=server, sleep=lambda s: None)
    server.logs += [{"at": "x", "created_at": str(2000 + i), "msg": f"new{i}"} for i in range(70)]
    server.calls.clear()
    logs = F.fetch_game_log(GID, tmp_path, session=server, sleep=lambda s: None)
    assert len(logs) == 190 and logs[0]["created_at"] == "2069"
    assert all(c.get("after_at") == "1119" for c in server.calls)
    assert len(server.calls) == 2  # 50 + 20, never re-pages the old history


def test_403_raises_with_hint(tmp_path):
    server = FakeServer(5, failures=[403])
    with pytest.raises(F.FetchError, match="User-Agent"):
        F.fetch_game_log(GID, tmp_path, session=server, sleep=lambda s: None)


def test_fetch_ledger_writes_csv(tmp_path):
    path = F.fetch_ledger(
        GID, tmp_path, session=FakeServer(0, failures=[429]), sleep=lambda s: None
    )
    assert path.name == f"ledger_{GID}.csv" and path.read_text().startswith("player_nickname")


def test_csv_import_matches_json_shape(tmp_path):
    csv_path = tmp_path / f"poker_now_log_{GID}.csv"
    rows = [
        ("-- ending hand #1 --", "2026-10-01T00:00:09.000Z", "109"),
        ('"b @ bid0000001" collected 0.50 from pot', "2026-10-01T00:00:08.000Z", "108"),
        ('Uncalled bet of 0.25 returned to "b @ bid0000001"', "2026-10-01T00:00:07.000Z", "107"),
        ('"a @ aid0000001" folds', "2026-10-01T00:00:06.000Z", "106"),
        ("Your hand is 10♥, 2♣", "2026-10-01T00:00:05.000Z", "105"),
        ('"b @ bid0000001" posts a big blind of 0.50', "2026-10-01T00:00:04.000Z", "104"),
        ('"a @ aid0000001" posts a small blind of 0.25', "2026-10-01T00:00:03.000Z", "103"),
        (
            'Player stacks: #1 "a @ aid0000001" (10.00) | #2 "b @ bid0000001" (10.00)',
            "2026-10-01T00:00:02.000Z",
            "102",
        ),
        (
            '-- starting hand #1 (id: abc) No Limit Texas Hold\'em (dealer: "a @ aid0000001") --',
            "2026-10-01T00:00:01.000Z",
            "101",
        ),
        ("Game Config Changes\n* Max Time Bank: 10s » 90s", "2026-10-01T00:00:00.000Z", "100"),
    ]
    import csv

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entry", "at", "order"])
        w.writerows(rows)
    msgs = read_csv_log(csv_path)
    assert game_id_from_filename(csv_path) == GID
    assert msgs[0]["created_at"] == "109" and msgs[-1]["msg"].startswith("Game Config Changes\n")
    (h,) = parse_messages(msgs, game_id=GID, source_kind="pokernow_csv", hero_id="aid0000001")
    a = next(p for p in h["players"] if p["player_id"] == "aid0000001")
    assert a["cards"] == ["Th", "2c"] and a["cards_source"] == "hero_export"
    assert (
        h["net"] == {"aid0000001": -25, "bid0000001": 25} and h["source"]["kind"] == "pokernow_csv"
    )


def test_csv_import_rejects_wrong_columns(tmp_path):
    bad = tmp_path / "x.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="missing columns"):
        read_csv_log(bad)
