"""Transport failure is not a zero-hit scan. All I/O is isolated or mocked."""
from copy import deepcopy
import json
import threading

import pytest

import api
import modules.scanners as scanners
from test_bi_deep_fixes_scan import _flat_bars, _to_polygon, _contract_result


@pytest.fixture(autouse=True)
def legacy_snapshot_contract(monkeypatch):
    # This suite tests atomic lastTrade/snapshot semantics, not daily plans.
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "realtime")


class Reply:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def stock_io(monkeypatch, tmp_path, rows):
    final = tmp_path / "strategy.json"
    generic = tmp_path / "generic.json"
    final.write_text('{"cached_at":"2026-01-01T00:00:00","results":[{"old":true}]}')
    before = final.read_bytes()
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(final))
    monkeypatch.setattr(api, "STRATEGY_SCAN_CACHE", str(generic))
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: rows)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"TEST"}, "test"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **kw: "")
    monkeypatch.setattr(api, "get_current_trading_session", lambda: ("Regular", "Test"))
    monkeypatch.setattr(api, "_enrich_stock_business_quality_rows", lambda *a: None)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *a: pytest.fail("mail must not run"))
    return final, generic, before


def snapshot(price=10.1):
    return {"ticker": "TEST", "prevDay": {"c": 10.0, "v": 200000},
            "day": {"c": price, "h": 11, "l": 10, "v": 200000}, "lastTrade": {"p": price}}


@pytest.mark.parametrize("status,code", [(401, "scan_provider_unauthorized"), (403, "scan_provider_unauthorized"),
                                         (429, "scan_provider_rate_limited"), (500, "scan_data_unavailable")])
def test_full_snapshot_failure_is_not_movers_only_success(monkeypatch, status, code):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return Reply(status, {"error": "secret-provider-body"})
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    with pytest.raises(scanners.ScannerDataError, match=code) as caught:
        api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    assert len(calls) == 1
    assert caught.value.diagnostics["final_results"] is None
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("payload", [None, {}, {"tickers": None}, {"tickers": [None]},
                                      {"tickers": [{"ticker": 42}]}, ValueError("SECRET")])
def test_invalid_snapshot_payload_is_explicit(monkeypatch, payload):
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: Reply(payload=payload))
    with pytest.raises(scanners.ScannerDataError, match="scan_data_invalid"):
        api._fetch_strategy_snapshot_universe("Momentum Breakout Long")


def test_full_snapshot_success_keeps_broad_universe(monkeypatch):
    monkeypatch.setattr(api, "rate_limited_get", lambda url, **kw: Reply(payload={"tickers": [snapshot()]}))
    rows = api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    assert [row["ticker"] for row in rows] == ["TEST"]
    assert "full" in rows[0]["_sources"]


def _snapshot_feed(monkeypatch, full, gainers=(), losers=()):
    feeds = {"tickers": full, "gainers": gainers, "losers": losers}
    calls = []
    def fetch(url, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        assert endpoint in feeds, "unexpected provider endpoint"
        calls.append(endpoint)
        return Reply(payload={"status": "OK", "tickers": deepcopy(list(feeds[endpoint]))})
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    return calls


@pytest.mark.parametrize("supplement_trade", [{}, None, {"p": 0}])
def test_sparse_movers_never_erase_full_snapshot_trade(monkeypatch, supplement_trade):
    full = snapshot()
    full["lastTrade"]["t"] = 200
    before = deepcopy(full)
    calls = _snapshot_feed(monkeypatch, [full], [{"ticker": "TEST", "lastTrade": supplement_trade}])
    rows = api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    assert rows == [{**before, "_sources": ["full", "gainers"]}]
    assert full == before
    assert calls == ["tickers", "gainers", "losers"]


def test_conflicting_movers_never_mix_full_snapshot_market_objects(monkeypatch):
    full = {**snapshot(), "lastTrade": {"p": 10.1, "t": 200},
            "lastQuote": {"p": 10.09, "P": 10.11, "t": 201}, "updated": 202}
    conflicting = {"ticker": "TEST", "lastTrade": {"p": 9, "t": 100},
                   "lastQuote": {"p": 8.9, "P": 9.1, "t": 101},
                   "day": {"c": 9, "v": 1}, "prevDay": {"c": 8}, "updated": 102,
                   "supplement_only": "not_part_of_full_snapshot"}
    _snapshot_feed(monkeypatch, [full], [conflicting], [{"ticker": "TEST", "day": {"c": 12}, "updated": 300}])
    rows = api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    assert rows == [{**full, "_sources": ["full", "gainers", "losers"]}]


def test_movers_add_new_tickers_and_keep_first_snapshot_atomic(monkeypatch):
    full = snapshot()
    gainer = {**snapshot(12), "ticker": "NEW_GAINER", "updated": 200}
    loser = {**snapshot(8), "ticker": "NEW_LOSER", "updated": 201}
    repeated = {"ticker": "NEW_GAINER", "lastTrade": None, "updated": 300}
    _snapshot_feed(monkeypatch, [full], [gainer], [loser, repeated])
    rows = api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    assert rows == [{**full, "_sources": ["full"]},
                    {**gainer, "_sources": ["gainers", "losers"]},
                    {**loser, "_sources": ["losers"]}]


def test_unpriced_full_snapshot_cannot_borrow_movers_trade(monkeypatch, tmp_path):
    full = snapshot()
    del full["lastTrade"]
    _snapshot_feed(monkeypatch, [full], [{"ticker": "TEST", "lastTrade": {"p": 10.1, "t": 200}}])
    rows = api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    final, generic, before = stock_io(monkeypatch, tmp_path, rows)
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable"):
        api._strategy_scan_wrapper("Momentum Breakout Long", send_email=False)
    assert rows == [{**full, "_sources": ["full", "gainers"]}]
    assert final.read_bytes() == before
    assert not generic.exists()
    assert not final.with_suffix(".json.partial").exists()


@pytest.mark.parametrize("provider_status,code", [("NOT_AUTHORIZED", "scan_provider_unauthorized"),
                                                 ("RATE_LIMITED", "scan_provider_rate_limited"),
                                                 ("ERROR", "scan_data_invalid")])
def test_http200_error_body_is_not_success(monkeypatch, tmp_path, provider_status, code):
    payload = {"status": provider_status, "tickers": [snapshot()], "results": []}
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: Reply(payload=payload))
    with pytest.raises(scanners.ScannerDataError, match=code):
        api._fetch_strategy_snapshot_universe("Momentum Breakout Long")
    final, before = bi_io(monkeypatch, tmp_path, Reply(payload=payload))
    with pytest.raises(scanners.ScannerDataError, match=code):
        scanners._bi_background_scan("fake", candidates=["TEST"])
    assert final.read_bytes() == before


@pytest.mark.parametrize("rows", [[], [{"ticker": "TEST", "prevDay": {"c": 10}, "day": {"c": 11}}]])
def test_empty_or_unpriced_stock_scan_preserves_last_good(monkeypatch, tmp_path, rows):
    final, generic, before = stock_io(monkeypatch, tmp_path, rows)
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable"):
        api._strategy_scan_wrapper("Momentum Breakout Long", send_email=False)
    assert final.read_bytes() == before
    assert not generic.exists()
    assert not final.with_suffix(".json.partial").exists()


def test_valid_stock_zero_remains_success(monkeypatch, tmp_path):
    final, generic, before = stock_io(monkeypatch, tmp_path, [snapshot()])
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: pytest.fail("below 2% must not fetch history"))
    assert api._strategy_scan_wrapper("Momentum Breakout Long", send_email=False) == []
    payload = json.loads(final.read_text())
    assert payload["results"] == [] and final.read_bytes() != before and generic.exists()
    assert payload["diagnostics"]["coverage"] == "complete"
    assert payload["diagnostics"]["rejected"]["change_filter"] == 1


def test_strict_stock_history_failure_does_not_change_optional_enrichment_contract(monkeypatch):
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: None)
    def strict_unavailable(*args, **kwargs):
        raise api.StockHistoryDataError("scan_data_unavailable", "connection_failure")
    monkeypatch.setattr(api, "fetch_stock_daily_history_strict", strict_unavailable)
    cache = {}
    assert api._fetch_strategy_daily_history("TEST", 70, cache) == []
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable"):
        api._fetch_strategy_daily_history("TEST", 70, cache, True)


def bi_io(monkeypatch, tmp_path, reply):
    final = tmp_path / "bi_long.json"
    monkeypatch.setattr(scanners, "_BI_CACHE_FILE", str(tmp_path / "bi_{direction}.json"))
    monkeypatch.setattr(scanners, "_BI_PROGRESS_FILE", str(tmp_path / "progress_{direction}.json"))
    monkeypatch.setattr(scanners, "_bi_clear_stop", lambda *a: None)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda *a: False)
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: reply)
    final.write_text('{"cached_at":"2026-01-01T00:00:00","results":[{"old":true}]}')
    return final, final.read_bytes()


@pytest.mark.parametrize("status,code", [(401, "scan_provider_unauthorized"), (403, "scan_provider_unauthorized"),
                                         (429, "scan_provider_rate_limited"), (500, "scan_data_unavailable")])
def test_bi_history_provider_failure_preserves_final(monkeypatch, tmp_path, status, code):
    final, before = bi_io(monkeypatch, tmp_path, Reply(status, {"error": "SECRET"}))
    with pytest.raises(scanners.ScannerDataError, match=code):
        scanners._bi_background_scan("fake", candidates=["TEST"] * 10)
    assert final.read_bytes() == before
    progress = json.loads((tmp_path / "progress_long.json").read_text())
    assert progress["status"] == "error"
    assert progress["diagnostics"]["data_failures"] == 1
    assert progress["diagnostics"]["final_results"] is None
    assert "SECRET" not in json.dumps(progress)


@pytest.mark.parametrize("payload,code", [
    (None, "scan_data_invalid"), ({}, "scan_data_invalid"), ({"results": "bad"}, "scan_data_invalid"),
    ({"results": [None]}, "scan_data_incomplete"),
])
def test_bi_invalid_feed_not_technical_rejection(monkeypatch, tmp_path, payload, code):
    final, before = bi_io(monkeypatch, tmp_path, Reply(payload=payload))
    with pytest.raises(scanners.ScannerDataError, match=code):
        scanners._bi_background_scan("fake", candidates=["TEST"])
    assert final.read_bytes() == before


def test_bi_local_geometry_error_is_explicit_data_exclusion_not_technical_rejection(monkeypatch, tmp_path):
    payload = {"results": [{"t": 1, "o": 2, "h": 1, "l": 1, "c": 2, "v": 1}]}
    final, before = bi_io(monkeypatch, tmp_path, Reply(payload=payload))
    scanners._bi_background_scan("fake", candidates=["TEST"])
    result = json.loads(final.read_text())
    d = result["diagnostics"]
    assert d["coverage"] == "complete_with_exclusions"
    assert d["excluded_data_symbols"] == d["data_failures"] == 1
    assert d["valid_data_symbols"] == d["analyzed"] == 0
    assert result["results"] == [] and d["rejected"] == {}


def test_bi_under17_is_legitimate_zero_with_separate_funnel(monkeypatch, tmp_path):
    final, before = bi_io(monkeypatch, tmp_path, Reply(payload={"results": _to_polygon(_flat_bars())}))
    result = _contract_result((False, 80, 100, [], 80, "A", 0, 0), green=16)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **kw: result)
    scanners._bi_background_scan("fake", candidates=["TEST"])
    payload = json.loads(final.read_text())
    assert payload["results"] == [] and final.read_bytes() != before
    funnel = payload["diagnostics"]
    assert funnel["coverage"] == "complete" and funnel["analyzed"] == 1
    assert funnel["data_failures"] == 0 and funnel["final_results"] == 0
    assert funnel["rejected"] == {"indicator_or_hard_gate_contract": 1}


def test_bi_valid_short_history_is_explicit_filter_not_transport_failure(monkeypatch, tmp_path):
    final, _ = bi_io(monkeypatch, tmp_path, Reply(payload={"results": _to_polygon(_flat_bars(n=20))}))
    scanners._bi_background_scan("fake", candidates=["TEST"])
    funnel = json.loads(final.read_text())["diagnostics"]
    assert funnel["data_failures"] == 0
    assert funnel["rejected"] == {"insufficient_completed_history": 1}


def test_bi_failed_second_page_is_not_partial_universe_success(monkeypatch, tmp_path):
    final, before = bi_io(monkeypatch, tmp_path, None)
    replies = iter([Reply(payload={"results": [{"ticker": "TEST"}], "next_url": "https://example.invalid/page2"}), Reply(403)])
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: next(replies))
    with pytest.raises(scanners.ScannerDataError, match="scan_provider_unauthorized"):
        scanners._bi_background_scan("fake")
    assert final.read_bytes() == before


@pytest.mark.parametrize("reference_rows", [[], [{"ticker": "WARRANTW"}]])
def test_bi_empty_validated_broad_universe_never_falls_back_to_movers(monkeypatch, tmp_path, reference_rows):
    final, before = bi_io(monkeypatch, tmp_path, None)
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if "/reference/" in url:
            return Reply(payload={"status": "OK", "results": reference_rows})
        if "/snapshot/" in url:
            return Reply(payload={"status": "OK", "tickers": [{"ticker": "TEST"}]})
        return Reply(payload={"status": "OK", "results": _to_polygon(_flat_bars())})
    monkeypatch.setattr(scanners, "rate_limited_get", fetch)
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable"):
        scanners._bi_background_scan("fake")
    assert len(calls) == 1
    assert final.read_bytes() == before
    progress = json.loads((tmp_path / "progress_long.json").read_text())
    assert progress["status"] == "error"
    assert progress["diagnostics"]["coverage"] == "incomplete"
    assert progress["diagnostics"]["final_results"] is None


@pytest.mark.parametrize("accepted", [True, False])
def test_manual_bi_acknowledges_acceptance(monkeypatch, accepted):
    monkeypatch.setattr(api, "POLYGON_KEY", "fake")
    monkeypatch.setattr(api, "_scan_status", {"bi_long": {}})
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_run_scan_safe", lambda *a, **kw: accepted)
    result = api.trigger_bi_scan(api.BIScanRequest(direction="long"))
    assert result["accepted"] is accepted
    assert result["status"] == ("started" if accepted else "busy")
    if not accepted:
        assert result["reason"] == "start_not_accepted"
        assert result["run_id"] is None


@pytest.mark.parametrize("accepted", [True, False])
def test_manual_stock_acknowledges_acceptance(monkeypatch, accepted):
    monkeypatch.setattr(api, "POLYGON_KEY", "fake")
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_run_scan_safe", lambda *a, **kw: accepted)
    result = api.run_scan(api.ScanRequest(strategy="Momentum Breakout Long", market_type="stocks"), None)
    assert result["accepted"] is accepted
    assert result["status"] == ("started" if accepted else "busy")
    if not accepted:
        assert result["reason"] == "start_not_accepted"
        assert result["run_id"] is None


def test_public_data_errors_are_safe():
    for code in scanners.ScannerDataError.CODES:
        assert api._public_scan_error_code(code) == code
    assert api._public_scan_error_code("unknown-provider-SECRET") == "scan_failed"


def test_bi_wrapper_does_not_mail_or_leave_partial_on_data_error(monkeypatch):
    removed = []
    monkeypatch.setattr(api, "_scan_cache_revision", lambda *a: (1, 1))
    monkeypatch.setattr(api, "_remove_partial_cache", lambda path: removed.append(path))
    monkeypatch.setattr(api, "_check_and_alert", lambda *a: pytest.fail("incomplete scans never mail"))
    def broken(*a, **kw):
        raise scanners.ScannerDataError("scan_provider_rate_limited")
    monkeypatch.setattr(api, "_bi_background_scan", broken)
    with pytest.raises(scanners.ScannerDataError):
        api._bi_background_scan_wrapper("long")
    assert removed == [api.BI_CACHE_LONG, api.BI_CACHE_LONG]


def test_worker_failure_preserves_last_success_and_exposes_safe_attempt(monkeypatch):
    name = "test_scan_data_outcome"
    state = {"running": False, "last_run": "previous-success", "next_run": None}
    monkeypatch.setitem(api._scan_status, name, state)
    entered = threading.Event()
    release = threading.Event()
    def broken():
        entered.set()
        assert release.wait(2)
        raise scanners.ScannerDataError("scan_provider_unauthorized", {"coverage": "incomplete", "final_results": None})
    assert api._run_scan_safe(name, broken) is True
    assert entered.wait(2)
    worker = api._scan_threads[name]
    run_id = state["last_run_id"]
    assert api._run_scan_safe(name, broken) is False
    release.set()
    worker.join(2)
    assert not worker.is_alive() and state["running"] is False
    assert state["last_run"] == "previous-success" and state["last_run_id"] == run_id
    assert state["last_error"] == "scan_provider_unauthorized"
    assert state["last_attempt_diagnostics"]["final_results"] is None


def test_bi_get_keeps_success_funnel_separate_from_failed_attempt(monkeypatch):
    final_funnel = {"coverage": "complete", "total": 100, "analyzed": 80, "final_results": 0}
    attempt = {"coverage": "incomplete", "checked": 1, "data_failures": 1, "final_results": None}
    state = {"running": False, "last_error": "scan_provider_unauthorized", "last_attempt_diagnostics": attempt,
             "last_run_id": "attempt-2", "last_attempt_at": "2026-09-08T14:00:00", "last_run": "2026-09-08T12:00:00"}
    monkeypatch.setitem(api._scan_status, "bi_long", state)
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **kw: ([], "2026-09-08T12:00:00", {"diagnostics": final_funnel}, False))
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *a: rows)
    result = api.get_bi_results("long")
    assert result.scan_error == "scan_provider_unauthorized"
    assert result.scan_run_id == "attempt-2"
    assert result.diagnostics["funnel"] == final_funnel
    assert result.diagnostics["attempt_diagnostics"] == attempt
    assert result.scan_last_completed_at == "2026-09-08T12:00:00"
