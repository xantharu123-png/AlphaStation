"""Production budgets, bounded parallelism, deterministic results and progress."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

import api
from modules import crypto_scan_runtime as rt, watchdog_log


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_CE_PROGRESS", {})
    monkeypatch.setattr(api, "_CE_RUN_LOCK", threading.Lock())
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(watchdog_log, "WATCHDOG_EVENTS_PATH", str(tmp_path / "watchdog.jsonl"))
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_status", {"crypto_explosion": {"running": False, "last_run": None, "next_run": None, "interval_min": 15}})
    monkeypatch.setattr(api, "_CE_BTC_CONTEXT_CACHE", {"ts": 0, "known": False, "data_status": "missing"})
    monkeypatch.setattr(api, "CRYPTO_EXPLOSION_CACHE", str(tmp_path / "crypto.json"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Unexpected mail")))


def setup_scan(monkeypatch, per_venue=2):
    universe = [{"exchange": venue, "contract": f"{venue}{i}USDT"} for i in range(per_venue) for venue in ("bybit", "binance", "mexc", "bitget")]
    monkeypatch.setattr(api, "_fetch_crypto_explosion_universe", lambda: (universe, {}))
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *_a, **_k: [])
    monkeypatch.setattr(api, "_refresh_crypto_funding", lambda row: row)
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", lambda row, *_a: {
        **row, "execution_candle_timestamp": int(time.time()) - 330,
        "trade_signal": "EXPLOSION_ARMED", "entry_score": 80, "explosion_score": 80,
    })
    return universe


@pytest.mark.parametrize("elapsed", [1532.4, 1761.6, 2099, 2100])
def test_measured_runs_and_35min_boundary_do_not_warn(monkeypatch, elapsed):
    start = time.time() - 5000
    state = {"running": True, "_started_at": start}
    monkeypatch.setitem(api._scan_status, "crypto_explosion", state)
    assert api._scan_watchdog_check("crypto_explosion", now=start + elapsed) is None
    runtime = api._scan_runtime_state("crypto_explosion", state, now_ts=start + elapsed)
    assert runtime["timeout_minutes"] == 35
    assert not runtime["timeout_exceeded"]


def test_soft_warning_and_independent_75min_hard_limit(monkeypatch):
    start, sent = time.time() - 6000, []
    worker = object()
    monkeypatch.setitem(api._scan_status, "crypto_explosion", {"running": True, "_started_at": start})
    monkeypatch.setitem(api._scan_threads, "crypto_explosion", worker)
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kw: sent.append((subject, body)) or True)
    api._ce_progress_update(running=True, checked=430, total=1000)
    assert api._scan_watchdog_check("crypto_explosion", now=start + 2101) == "stuck"
    assert "dauert laenger als vorgesehen" in sent[0][0]
    assert "Budget 35 Min" in sent[0][1]
    assert "belegt keinen Haenger" in sent[0][1]
    assert "430/1000" in sent[0][1]
    assert "Warnung allein ist noch kein Neustart" in sent[0][1]
    assert api._scan_watchdog_check("crypto_explosion", now=start + 4500) is None
    assert api._scan_watchdog_check("crypto_explosion", now=start + 4501) == "stuck_hard"
    assert len(sent) == 2
    assert api._stuck_hard_cap_sec("crypto_explosion") == 4500
    assert api._scan_threads["crypto_explosion"] is worker


def test_four_venue_workers_parallel_but_each_venue_serial_and_ranking_stable(monkeypatch):
    universe = setup_scan(monkeypatch)
    first_calls = threading.Barrier(4)
    lock, seen, active, peaks = threading.Lock(), [], {}, {}
    def fetch(contract, venue, timeframe, count):
        with lock:
            active[venue] = active.get(venue, 0) + 1
            peaks[venue] = max(peaks.get(venue, 0), active[venue])
            seen.append((contract, timeframe, count))
        if contract == f"{venue}0USDT" and timeframe == "5m":
            first_calls.wait(timeout=3)
        with lock:
            active[venue] -= 1
        return []
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fetch)
    rows, stats = api._run_crypto_explosion_scan()
    assert [row["contract"] for row in rows] == [row["contract"] for row in universe]
    assert peaks == {venue: 1 for venue in ("bybit", "binance", "mexc", "bitget")}
    assert len(seen) == len(universe) * 3
    assert set((tf, count) for _, tf, count in seen) == {("5m", 140), ("15m", 96), ("4h", 72)}
    assert stats["venue_workers"] == 4 and stats["chart_checked"] == 8
    assert api._ce_progress_snapshot()["checked"] == 8
    assert all("price" not in original for original in universe)


def test_progress_visible_before_completion_and_wrapper_owns_cache_until_end(monkeypatch):
    setup_scan(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = api._score_crypto_explosion_candidate
    def score(row, *args):
        if row["contract"] == "bybit1USDT":
            entered.set()
            assert release.wait(3)
        return original(row, *args)
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", score)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(api._crypto_explosion_wrapper)
        try:
            assert entered.wait(3)
            progress = api.get_scan_status()["scans"]["crypto_explosion"]["progress"]
            assert 0 < progress["checked"] < progress["total"]
            assert progress["running"] and not running.done()
            with pytest.raises(RuntimeError, match="already running"):
                api._crypto_explosion_wrapper()
        finally:
            release.set()
        running.result(timeout=3)
    assert api._ce_progress_snapshot()["status"] == "done"
    assert not api._ce_progress_snapshot()["running"]


def test_rate_limited_venue_is_not_hammered_or_published_as_complete(monkeypatch):
    setup_scan(monkeypatch, per_venue=3)
    calls = []
    def fetch(contract, venue, **kw):
        calls.append(venue)
        if venue == "bybit":
            raise rt.ScanRequestError("rate_limit", "api.bybit.com")
        return []
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fetch)
    api.save_cache_file(api.CRYPTO_EXPLOSION_CACHE, [{"sentinel": "old"}])
    with pytest.raises(RuntimeError, match="previous cache retained"):
        api._crypto_explosion_wrapper()
    assert calls.count("bybit") == 1
    assert api.load_cache_file(api.CRYPTO_EXPLOSION_CACHE)[0] == [{"sentinel": "old"}]
    assert api._ce_progress_snapshot()["status"] == "error"
    assert not api._CE_RUN_LOCK.locked()


def test_stale_early_candidate_is_removed_before_ranking_not_given_fresh_cache_age(monkeypatch):
    setup_scan(monkeypatch, per_venue=1)
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", lambda row, *_a: {
        **row, "execution_candle_timestamp": int(time.time()) - (1000 if row["exchange"] == "bybit" else 330),
        "trade_signal": "JETZT_TRADEN", "entry_score": 90, "explosion_score": 90,
    })
    rows, stats = api._run_crypto_explosion_scan()
    assert len(rows) == 3
    assert all(row["exchange"] != "bybit" for row in rows)
    assert stats["reason_counts"]["expired_before_publish"] == 1
    assert all(row["execution_data_age_seconds"] >= 30 for row in rows)


def test_shared_btc_context_is_single_flight_and_failure_stays_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **kw: calls.append(1) or [])
    with ThreadPoolExecutor(max_workers=4) as pool:
        contexts = list(pool.map(lambda _: api._get_crypto_btc_context("TEST", 4), range(8)))
    assert len(calls) == 1
    assert all(not ctx["known"] and not ctx["tailwind"] for ctx in contexts)


@pytest.mark.parametrize("active,requested", [("crypto_explosion", "crypto_trade_signals"), ("crypto_trade_signals", "crypto_explosion")])
def test_combined_and_scheduled_engine_cannot_overlap(monkeypatch, active, requested):
    monkeypatch.setitem(api._scan_status, active, {"running": True})
    called = []
    assert api._run_scan_safe(requested, lambda: called.append(True)) is False
    assert called == []


def test_all_request_failures_do_not_replace_previous_good_cache(monkeypatch):
    setup_scan(monkeypatch, per_venue=1)
    def fail(*_a, **_k):
        raise rt.ScanRequestError("transport_error", "test.invalid")
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fail)
    api.save_cache_file(api.CRYPTO_EXPLOSION_CACHE, [{"sentinel": "old"}])
    with pytest.raises(RuntimeError, match="previous cache retained"):
        api._crypto_explosion_wrapper()
    assert api.load_cache_file(api.CRYPTO_EXPLOSION_CACHE)[0] == [{"sentinel": "old"}]


def test_parallel_scan_preserves_real_sequential_scoring_on_identical_data(monkeypatch):
    from test_crypto_explosion_scanner import _bars, _candidate, _btc_context
    monkeypatch.setattr(api.time, "time", lambda: 1_783_500_000.0)
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change, 0.2))
    bars5 = _bars(90, start=9.48, step=0.004, volume=1000, last={"open": 9.93, "high": 9.97, "low": 9.90, "close": 9.95, "volume": 1200})
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900, last={"open": 9.92, "high": 9.98, "low": 9.88, "close": 9.95, "volume": 3600})
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)
    universe = [{**_candidate(), "exchange": venue} for venue in ("bybit", "binance", "mexc", "bitget")]
    monkeypatch.setattr(api, "_fetch_crypto_explosion_universe", lambda: (universe, {}))
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *_a, timeframe, **_k: {"5m": bars5, "15m": bars15, "4h": bars4h}[timeframe])
    monkeypatch.setattr(api, "_refresh_crypto_funding", lambda row: row)
    monkeypatch.setattr(api, "_refresh_crypto_explosion_spread", lambda row: row)
    sequential = [api._score_crypto_explosion_candidate(dict(row), bars5, bars15, bars4h) for row in universe]
    assert all(sequential)
    parallel, stats = api._run_crypto_explosion_scan()
    assert parallel == sequential
    assert stats["chart_checked"] == 4


@pytest.mark.parametrize("endpoint", [api.trigger_crypto_explosion_scan, api.trigger_crypto_trade_signals_scan])
def test_rejected_start_does_not_report_started(monkeypatch, endpoint):
    monkeypatch.setattr(api, "_run_scan_safe", lambda *_a, **_k: False)
    assert endpoint()["status"] == "already_running"
