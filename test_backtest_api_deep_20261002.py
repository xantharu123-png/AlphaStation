"""Offline reproduction of backtest request/source/persistence defects."""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import api


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "BACKTEST_CACHE", str(tmp_path / "backtest_cache.json"))
    monkeypatch.setattr(api, "BACKTEST_PROGRESS", {})
    monkeypatch.setattr(api, "_BACKTEST_ACTIVE_JOBS", set())
    monkeypatch.setattr(api, "_BACKTEST_ACTIVE_REQUESTS", set())
    monkeypatch.setattr(api, "POLYGON_KEY", "offline")
    return tmp_path


def _report(request):
    return {"ticker": request.ticker, "strategy": request.strategy, "months": request.months,
            "total_trades": 1, "total_decided": 1, "win_rate": 100,
            "trades": [{"pnl_pct": float(request.months), "outcome": "TP2"}],
            "marker": [request.months, request.max_tickers, request.min_price, request.min_volume]}


def _install_engine(monkeypatch):
    monkeypatch.setattr(api, "_run_advanced_scanner_backtest", _report)
    monkeypatch.setattr(api, "_run_crypto_backtest", _report)
    monkeypatch.setattr(api, "_run_backtest", lambda ticker, strategy, months:
                        _report(api.BacktestRequest(ticker=ticker, strategy=strategy, months=months)))


def _cached(request):
    return api.get_backtest_results(ticker=request.ticker, strategy=request.strategy,
                                   months=request.months, max_tickers=request.max_tickers,
                                   min_price=request.min_price, min_volume=request.min_volume)


@pytest.mark.parametrize("field,value", [("months", 0), ("months", -3), ("months", True),
                                        ("months", 25), ("max_tickers", 0),
                                        ("min_price", float("nan")), ("min_price", -1),
                                        ("min_volume", -1), ("min_volume", True)])
def test_invalid_request_parameters_are_rejected(field, value):
    with pytest.raises(ValidationError):
        api.BacktestRequest(**{field: value})


def test_unknown_strategy_rejected_before_any_engine(monkeypatch, isolated):
    calls = []
    monkeypatch.setattr(api, "_run_backtest", lambda *args: calls.append(args) or {})
    with pytest.raises(api.HTTPException) as exc:
        api.run_backtest(api.BacktestRequest(strategy="not-a-real-model"))
    assert exc.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize("changed", ["months", "max_tickers", "min_price", "min_volume"])
def test_full_parameter_identity_survives_reload(monkeypatch, isolated, changed):
    _install_engine(monkeypatch)
    first = api.BacktestRequest(ticker="", strategy="scanner_bi_long", months=3,
                               max_tickers=5, min_price=2, min_volume=100)
    second = first.model_copy(deep=True)
    setattr(second, changed, {"months": 6, "max_tickers": 10, "min_price": 5,
                             "min_volume": 200}[changed])
    api.run_backtest(first)
    api.run_backtest(second)
    restored = _cached(first)
    assert restored["data"]["marker"] == [3, 5, 2, 100]
    assert restored["request"]["ticker"] == ""
    assert restored["request"][changed] == getattr(first, changed)
    assert restored["cache_match"] == "exact_request"
    assert restored["cached_at"]


def test_failed_attempt_preserves_previous_success(monkeypatch, isolated):
    _install_engine(monkeypatch)
    request = api.BacktestRequest(strategy="sma_crossover", months=3)
    succeeded = api.run_backtest(request)
    monkeypatch.setattr(api, "_run_backtest", lambda *args: {"error": "provider_denied"})
    failed = api.run_backtest(request.model_copy(update={"job_id": "second-attempt"}))
    assert failed["error"] == "provider_denied"
    assert _cached(request)["data"]["trades"] == succeeded["trades"]
    assert _cached(request)["data"]["total_trades"] == succeeded["total_trades"]


def test_unmatched_parameters_do_not_load_last_other_study(monkeypatch, isolated):
    _install_engine(monkeypatch)
    request = api.BacktestRequest(strategy="sma_crossover", months=3)
    api.run_backtest(request)
    other = request.model_copy(update={"months": 6})
    restored = _cached(other)
    assert restored["data"]["data_available"] is False
    assert restored["cached_at"] is None


def test_old_unidentified_cache_is_not_relabelled_exact(monkeypatch, isolated):
    legacy = isolated / "backtest_AAPL_sma_crossover.json"
    original = {"cached_at": "2026-09-01", "results": {"months": 6, "total_trades": 4}}
    legacy.write_text(json.dumps(original), encoding="utf-8")
    restored = _cached(api.BacktestRequest(months=3))
    assert restored["data"]["data_available"] is False
    assert json.loads(legacy.read_text(encoding="utf-8")) == original


def test_running_job_id_cannot_be_reused(monkeypatch, isolated):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def engine(*args):
        entered.set()
        assert release.wait(5)
        return {"total_trades": 0}
    monkeypatch.setattr(api, "_run_backtest", engine)
    request = api.BacktestRequest(job_id="same-job")
    def worker():
        try:
            api.run_backtest(request.model_copy(deep=True))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(api.HTTPException) as exc:
            api.run_backtest(request.model_copy(deep=True))
        assert exc.value.status_code == 409
    finally:
        release.set()
        thread.join(6)
    assert not thread.is_alive()
    assert errors == []


@pytest.mark.parametrize("exit_price,positive", [(100.204, True), (100.196, False)])
def test_indicator_trade_keeps_net_profit_sign_after_fees(exit_price, positive):
    trade = api._make_trade("2026-01-01", 100, "2026-01-02", exit_price)
    assert (trade["pnl_pct"] > 0) is positive
    assert trade["pnl_pct"] != 0
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == exit_price


class _Response:
    status_code = 200
    def __init__(self, body):
        self.body = body
    def json(self):
        return self.body


@pytest.fixture
def clocked_provider(monkeypatch):
    cutoff = datetime(2026, 4, 10, 23, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff if tz else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    bars = []
    day = datetime(2025, 8, 1, tzinfo=timezone.utc)
    while day < cutoff:
        if day.weekday() < 5:
            bars.append({"t": int(day.timestamp() * 1000), "o": 100, "h": 102,
                         "l": 98, "c": 100, "v": 1_000_000})
        day += timedelta(days=1)
    return bars


@pytest.mark.parametrize("status", ["ERROR", "NOT_AUTHORIZED"])
def test_http200_provider_error_is_not_successful_study(monkeypatch, clocked_provider, status):
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": status, "results": clocked_provider}))
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert result.get("error")


def test_daily_source_explicit_adjustment_and_bounded_dates(monkeypatch, clocked_provider):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"status": "OK", "results": list(reversed(clocked_provider))})
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    api._run_backtest("TEST", "sma_crossover", 3)
    assert calls[0][1]["params"]["adjusted"] == "true"
    assert "2099-12-31" not in calls[0][0]
    assert calls[0][1].get("timeout")


def test_daily_pagination_does_not_ignore_remaining_history(monkeypatch, clocked_provider):
    calls = []
    descending = list(reversed(clocked_provider))
    def fetch(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _Response({"status": "OK", "results": descending[:80],
                              "next_url": "https://api.polygon.io/v2/aggs/ticker/TEST/range/1/day/2025-01-01/2026-04-10?cursor=next"})
        return _Response({"status": "OK", "results": descending[80:]})
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert len(calls) == 2
    assert result["model_provenance"]["input_bars"] == len(descending)


def test_foreign_pagination_host_is_not_requested(monkeypatch, clocked_provider):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return _Response({"status": "OK", "results": clocked_provider,
                          "next_url": "https://evil.invalid/steal"})
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert result.get("error")
    assert len(calls) == 1


def test_conflicting_duplicate_daily_rows_are_not_silently_selected(monkeypatch, clocked_provider):
    rows = list(clocked_provider)
    rows.append({**rows[80], "c": 101})
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": rows}))
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert result.get("error")


@pytest.mark.parametrize("bad", [{"volume": None}, {"volume": True}, {"volume": -1},
                                  {"open": True}, {"high": float("nan")}])
def test_crypto_input_does_not_invent_missing_or_invalid_ohlcv(bad):
    row = {"date": "2026-01-01", "open": 100, "high": 102, "low": 98,
           "close": 100, "volume": 1000, **bad}
    assert api._normalize_crypto_bars([row]) == []


def test_crypto_history_rejects_internal_missing_day(monkeypatch):
    now = datetime.now(timezone.utc).date()
    rows = [{"date": (now - timedelta(days=70-i)).isoformat(), "open": 100,
             "high": 102, "low": 98, "close": 100, "volume": 1000} for i in range(70)]
    rows.pop(35)
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *args, **kwargs: rows)
    bars, exchange = api._validated_exchange_daily_crypto_bars({"symbol": "test", "current_price": 100}, 90)
    assert bars == []
    assert exchange is None


def test_rule_warmup_is_not_part_of_requested_test_window(monkeypatch, clocked_provider):
    observed = []
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": clocked_provider}))
    monkeypatch.setattr(api, "evaluate_backtest_rule_signal",
                        lambda bars, index, rule: observed.append(bars[index]["date"]) or None)
    api._run_backtest("TEST", "Momentum Breakout Long", 3)
    assert observed
    assert all(day >= "2026-01-10" for day in observed)


@pytest.mark.parametrize("missing", ["o", "h", "l", "v"])
def test_shared_backtest_source_never_substitutes_missing_fields(monkeypatch, missing):
    from modules import data_fetchers as df
    row = {"t": 1767312000000, "o": 100, "h": 102, "l": 98, "c": 100, "v": 1000}
    row.pop(missing)
    monkeypatch.setattr(df, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": [row]}))
    result = df.fetch_backtest_daily_data("offline", "TEST", "2026-01-01", "2026-01-05")
    assert result == []
    assert result.data_quality["excluded_invalid_bars"] == 1


def test_shared_backtest_source_preserves_completion_flags(monkeypatch):
    from modules import data_fetchers as df
    row = {"t": 1767312000000, "o": 100, "h": 102, "l": 98, "c": 100, "v": 1000,
           "is_closed": True, "final": False}
    monkeypatch.setattr(df, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": [row]}))
    result = df.fetch_backtest_daily_data("offline", "TEST", "2026-01-01", "2026-01-05")
    assert result[0]["is_closed"] is True
    assert result[0]["final"] is False
    assert result.data_quality["status"] == "PARTIAL"
    assert result.data_quality["explicitly_incomplete_bars"] == 1
    assert result.data_quality["completed_daily_bars_only"] is False


def test_shared_backtest_source_fetches_all_pages(monkeypatch):
    from modules import data_fetchers as df
    rows = [{"t": day, "o": 100, "h": 102, "l": 98, "c": 100, "v": 1000}
            for day in (1767312000000, 1767571200000)]
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return _Response({"status": "OK", "results": rows[:1],
                          "next_url": "https://api.polygon.io/v2/aggs/ticker/TEST/range/1/day/2026-01-01/2026-01-05?cursor=next"}) if len(calls) == 1 else _Response({"status": "OK", "results": rows[1:]})
    monkeypatch.setattr(df, "rate_limited_get", fetch)
    result = df.fetch_backtest_daily_data("offline", "TEST", "2026-01-01", "2026-01-05")
    assert len(calls) == 2
    assert len(result) == 2


@pytest.mark.parametrize("payload", [{"months": True}, {"months": "3"}, {"max_tickers": True},
                                      {"max_tickers": 3}, {"min_volume": True}, {"min_price": True},
                                      {"min_price": "NaN"}, {"min_price": "5.0"}])
def test_actual_http_body_rejects_coerced_parameters(payload):
    import asyncio
    from fastapi import FastAPI
    validation_app = FastAPI()
    @validation_app.post("/validate")
    def validate(request: api.BacktestRequest):
        return request.model_dump()
    messages = []
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/validate", "raw_path": b"/validate",
             "query_string": b"", "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("offline", 80)}
    asyncio.run(validation_app(scope, receive, send))
    assert next(message["status"] for message in messages if message["type"] == "http.response.start") == 422


def test_omitted_profile_filters_and_explicit_zero_are_distinct(monkeypatch, isolated):
    _install_engine(monkeypatch)
    omitted = api.run_backtest(api.BacktestRequest(ticker="", strategy="scanner_bi_long", max_tickers=5))
    explicit = api.run_backtest(api.BacktestRequest(ticker="", strategy="scanner_bi_long", max_tickers=5,
                                                   min_price=0, min_volume=0))
    assert omitted["request"]["min_price"] == 5
    assert omitted["request"]["min_volume"] == 200000
    assert explicit["request"]["min_price"] == 0
    assert explicit["request"]["min_volume"] == 0
    assert omitted["request"] != explicit["request"]


def test_identical_request_new_job_cannot_overlap(monkeypatch, isolated):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def engine(*args):
        entered.set()
        assert release.wait(5)
        return {"total_trades": 0, "trades": []}
    monkeypatch.setattr(api, "_run_backtest", engine)
    request = api.BacktestRequest(job_id="original")
    def worker():
        try:
            api.run_backtest(request)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(api.HTTPException) as exc:
            api.run_backtest(request.model_copy(update={"job_id": "new-browser-view"}))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "backtest_request_already_running"
    finally:
        release.set()
        thread.join(6)
    assert not thread.is_alive()
    assert errors == []
    assert api._BACKTEST_ACTIVE_REQUESTS == set()


def test_corrupt_v2_cache_is_rejected_without_overwrite(monkeypatch, isolated):
    request = api.BacktestRequest()
    path = api._backtest_v2_cache_path(api._backtest_request_identity(request))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(api.HTTPException) as exc:
        _cached(request)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "backtest_cache_invalid"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_persistence_failure_does_not_destroy_previous_report(monkeypatch, isolated):
    _install_engine(monkeypatch)
    request = api.BacktestRequest()
    first = api.run_backtest(request)
    path = api._backtest_v2_cache_path(first["request"])
    previous = path.read_bytes()
    monkeypatch.setattr(api, "save_cache_file", lambda *args: (_ for _ in ()).throw(OSError("offline disk failure")))
    fresh = api.run_backtest(request)
    assert fresh["cache_saved"] is False
    assert fresh["cached_at"] is None
    assert fresh["trades"] == first["trades"]
    assert path.read_bytes() == previous


def test_trade_display_cap_is_disclosed_without_changing_totals(monkeypatch, isolated):
    monkeypatch.setattr(api, "_run_backtest", lambda *args: {"total_trades": 200,
                        "trades": [{"pnl_pct": 1}] * 50, "unresolved": 70,
                        "open_trades": [{"pnl_pct": None}] * 50})
    result = api.run_backtest(api.BacktestRequest())
    assert result["trades_total"] == 200
    assert result["trades_returned"] == 50
    assert result["trades_truncated"] is True
    assert result["open_trades_total"] == 70
    assert result["open_trades_returned"] == 50
    assert result["open_trades_truncated"] is True


def test_crypto_explicitly_open_historical_candle_is_not_validated(monkeypatch):
    now = datetime.now(timezone.utc).date()
    rows = [{"date": (now - timedelta(days=70-i)).isoformat(), "open": 100,
             "high": 102, "low": 98, "close": 100, "volume": 1000} for i in range(70)]
    rows[35].update(is_closed=True, final=False)
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *args, **kwargs: rows)
    bars, exchange = api._validated_exchange_daily_crypto_bars({"symbol": "test", "current_price": 100}, 90)
    assert bars == []
    assert exchange is None


def test_crypto_all_missing_series_is_not_successful_zero_study(monkeypatch):
    monkeypatch.setattr(api, "_crypto_backtest_universe", lambda _: [{"id": "test", "symbol": "tst"}])
    monkeypatch.setattr(api, "_validated_exchange_daily_crypto_bars", lambda *args, **kwargs: ([], None))
    result = api._run_crypto_backtest(api.BacktestRequest(strategy="crypto_early_mover_long", max_tickers=5))
    assert result["data_quality"]["unavailable_tickers"] == ["TST"]
    assert result["verdict"]["status"] == "data_incomplete"
    assert result["verdict"]["tradable"] is False


def test_crypto_short_coverage_cannot_claim_complete_requested_period(monkeypatch):
    now = datetime.now(timezone.utc).date()
    rows = [{"date": (now - timedelta(days=45-i)).isoformat(), "open": 100,
             "high": 102, "low": 98, "close": 100, "volume": 1000} for i in range(45)]
    monkeypatch.setattr(api, "_crypto_backtest_universe", lambda _: [{"id": "test", "symbol": "tst"}])
    monkeypatch.setattr(api, "_validated_exchange_daily_crypto_bars", lambda *args, **kwargs: (rows, "binance"))
    result = api._run_crypto_backtest(api.BacktestRequest(strategy="crypto_early_mover_long", months=12, max_tickers=5))
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["data_quality"]["insufficient_warmup_tickers"] == ["TST"]
    assert result["verdict"]["status"] == "data_incomplete"


def test_classic_invalid_unused_bar_remains_report_quality(monkeypatch, clocked_provider):
    rows = list(clocked_provider)
    rows[10] = {**rows[10], "o": None}
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": rows}))
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["data_quality"]["excluded_invalid_bars"] == 1
    assert result["verdict"]["status"] == "data_incomplete"


def test_classic_missing_study_tail_is_not_complete_zero_study(monkeypatch, clocked_provider):
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": clocked_provider[:-7]}))
    result = api._run_backtest("TEST", "sma_crossover", 3)
    assert result["data_quality"]["status"] == "PARTIAL"
    assert "2026-04-10" in result["data_quality"]["missing_expected_sessions"]
    assert result["verdict"]["status"] == "data_incomplete"


def test_shared_fetcher_current_session_without_flags_is_not_completed(monkeypatch):
    from modules import data_fetchers as df
    cutoff = datetime(2026, 10, 2, 17, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff if tz else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(df, "datetime", Clock)
    rows = [{"t": int(datetime(2026, 10, day, tzinfo=timezone.utc).timestamp() * 1000),
             "o": 100, "h": 102, "l": 98, "c": 100, "v": 1000} for day in (1, 2)]
    monkeypatch.setattr(df, "rate_limited_get", lambda *args, **kwargs:
                        _Response({"status": "OK", "results": rows}))
    result = df.fetch_backtest_daily_data("offline", "TEST", "2026-10-01", "2026-10-02")
    assert [row["date"] for row in result] == ["2026-10-01"]
    assert result.data_quality["excluded_open_future_sessions"] == 1
    assert result.data_quality["completed_daily_bars_only"] is True


def test_indicator_entry_never_substitutes_next_observed_for_missing_session():
    assert api._indicator_entry_on_next_open(0, ["2026-04-10", "2026-04-14"], [100, 120]) is None


@pytest.mark.parametrize("dates", [["2026-04-10", "2026-04-13"],
                                    ["2026-01-16", "2026-01-20"]])
def test_indicator_next_session_accepts_weekend_and_holiday_gap(dates):
    trade = api._indicator_entry_on_next_open(0, dates, [100, 120])
    assert trade["entry_price"] == 120
    assert trade["entry_date"] == dates[1]


def test_indicator_missing_exit_session_stays_unresolved():
    position = {"entry_date": "2026-04-10", "entry_price": 100, "dir": "long"}
    assert api._indicator_exit_on_next_open(position, 0, ["2026-04-10", "2026-04-14"], [100, 120]) is None
    assert position["missing_expected_sessions"] == ["2026-04-13"]
    assert api._indicator_exit_on_next_open(position, 1, ["2026-04-10", "2026-04-14", "2026-04-15"], [100, 120, 130]) is None
    open_trade = api._indicator_unresolved_trade(position, ["2026-04-10", "2026-04-14", "2026-04-15"])
    assert open_trade["pnl_pct"] is None
    assert open_trade["outcome"] == "UNRESOLVED"
    assert open_trade["missing_expected_sessions"] == ["2026-04-13"]


@pytest.mark.parametrize("broken", ["result_request", "trade_array", "nonfinite_metric"])
def test_v2_result_payload_must_match_identity_and_be_json_safe(monkeypatch, isolated, broken):
    _install_engine(monkeypatch)
    request = api.BacktestRequest()
    first = api.run_backtest(request)
    path = api._backtest_v2_cache_path(first["request"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if broken == "result_request":
        payload["results"]["request"]["months"] += 1
    elif broken == "trade_array":
        payload["results"]["trades"] = "not-a-trade-array"
    else:
        payload["results"]["avg_pnl"] = float("nan")
    path.write_text(json.dumps(payload), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(api.HTTPException) as exc:
        _cached(request)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "backtest_cache_invalid"
    assert path.read_bytes() == original
