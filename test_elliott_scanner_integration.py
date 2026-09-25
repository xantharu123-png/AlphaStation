"""Elliott counts stay distinct from executable signals across API consumers."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
import api
from modules import signal_tracker


STRATEGY = "Elliott Wave Muster"


def forged_context(**changes):
    return {"Strategy": STRATEGY, "strategy": STRATEGY, "ticker": "TEST",
            "price": 100.0, "score": 100, "grade": "S", "RVOL": 5,
            "trade_action": "LONG_NOW", "trade_signal": "JETZT_TRADEN",
            "trade_decision": "TRADEABLE", "direction": "LONG",
            "entry": 100, "stop": 95, "tp1": 110, "tp2": 115,
            "elliott": {}, "signal_kind": "pattern_context", **changes}


def test_menu_exposes_separate_manual_pattern_scanner():
    catalog = api.get_public_strategies_for_market("stocks")
    assert STRATEGY in catalog
    assert catalog[STRATEGY]["manual_only"] is True
    assert STRATEGY not in api._AUTO_STOCK_ALERT_STRATEGIES


def test_forged_pattern_cannot_be_trade_signal():
    assert not api._scanner_row_is_trade_signal(forged_context(), "stock_strategy")


def test_pattern_classification_does_not_invent_plan(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Elliott context reached generic trade-level evaluation")
    monkeypatch.setattr(api, "_alert_trade_levels", forbidden)
    state = api._classify_alert_candidate("stock_strategy", forged_context())
    assert state["alertable_now"] is False
    assert state["suppression_reasons"] == ["elliott_pattern_context"]
    assert state["decision"] != "TRADEABLE"


@pytest.mark.parametrize("mail_class", ["trade", "shadow"])
def test_pattern_not_recorded_as_trade_even_with_valid_geometry(tmp_path, monkeypatch, mail_class):
    monkeypatch.setenv("SIGNAL_TRACKER_DB_PATH", str(tmp_path / "tracker.sqlite"))
    assert signal_tracker.record_alert_signals("stock_strategy", [forged_context()], mail_class=mail_class) == 0


def test_pattern_delivery_intent_is_never_send_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_TRACKER_DB_PATH", str(tmp_path / "tracker.sqlite"))
    result = signal_tracker.prepare_alert_delivery_intent("stock_strategy", [forged_context()], "elliott-test")
    assert result["send_allowed"] is False
    assert result["signals"] == []


NOW = datetime(2026, 9, 25, 14, tzinfo=timezone.utc)


@pytest.fixture
def scan_environment(monkeypatch, tmp_path):
    from datetime import timedelta
    from test_elliott_waves import pattern_bars
    from modules import stock_swing_contract as swing
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    history = pattern_bars()
    sessions = []
    cursor = NOW.date() - timedelta(days=1)
    while len(sessions) < len(history):
        if swing.session_close(cursor.isoformat()):
            sessions.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    sessions.reverse()
    for item, session in zip(history, sessions):
        closed = swing.session_close(session)
        item.update(timestamp=(closed - timedelta(hours=6, minutes=30)).isoformat(),
                    close_time=closed.isoformat(), date=session, volume=200_000)
    price = history[-1]["close"]
    observation = {"ticker": "TEST", "day": {"c": price, "v": 200_000},
                   "prevDay": {"c": price + .8}, **swing.metadata(sessions[-1], price)}
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda name: [deepcopy(observation)])
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kwargs: ({"TEST"}, "fixture"))
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *args, **kwargs: deepcopy(history))
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *args: str(tmp_path / "elliott_cache.json"))
    monkeypatch.setattr(api, "_publish_stock_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    def forbidden(*args, **kwargs):
        pytest.fail("Pattern scan reached executable-plan/mail enrichment")
    for name in ("_enrich_stock_strategy_native_plan", "_alert_trade_levels", "_send_strategy_scan_alerts",
                 "_enrich_stock_business_quality_rows"):
        monkeypatch.setattr(api, name, forbidden)
    return observation, history, tmp_path


def test_scan_returns_real_patterns_without_plan_or_mail(scan_environment):
    rows = api._strategy_scan_wrapper(STRATEGY)
    assert len(rows) == 1
    row = rows[0]
    assert row["pattern_count"] >= 1
    assert any(item["family"] == "impulse" for item in row["elliott"]["patterns"])
    assert row["trade_ready"] is False and row["mail_eligible"] is False
    assert not set(row).intersection({"Entry", "entry", "StopLoss", "tp1", "trade_setup", "score", "grade", "Signal_Direction"})
    decorated = api._decorate_scan_results(rows, "strategy_scan", 0)
    visible = api._apply_scanner_visibility_policy("strategy_scan", decorated)
    assert len(visible) == 1
    assert visible[0]["visibility_status"] == "context"
    assert visible[0]["visibility_is_trade_signal"] is False


def test_context_cache_refuses_wrong_strategy_and_tampered_prices(scan_environment):
    row = api._strategy_scan_wrapper(STRATEGY)[0]
    assert api._stock_elliott_row_contract_valid(row, as_of=NOW, expected_strategy=STRATEGY)
    assert not api._stock_elliott_row_contract_valid(row, as_of=NOW, expected_strategy="Momentum Breakout Long")
    changed = deepcopy(row)
    changed["price"] += 1
    assert not api._stock_elliott_row_contract_valid(changed, as_of=NOW)


def test_pattern_reference_must_be_current_completed_session(scan_environment):
    from datetime import timedelta
    row = api._strategy_scan_wrapper(STRATEGY)[0]
    assert not api._stock_elliott_row_contract_valid(row, as_of=NOW + timedelta(days=4))


def test_explicit_context_tag_cannot_be_promoted_after_stripping_report():
    row = forged_context()
    for key in ("Strategy", "strategy", "elliott"):
        row.pop(key)
    assert not api._scanner_row_is_trade_signal(row, "stock_strategy")
    assert signal_tracker.is_elliott_pattern_context(row)


@pytest.mark.parametrize("field,value", [("Ticker", "OTHER"), ("ticker", ""), ("price", True)])
def test_cached_identity_and_price_aliases_are_not_ambiguous(scan_environment, field, value):
    row = api._strategy_scan_wrapper(STRATEGY)[0]
    row[field] = value
    assert not api._stock_elliott_row_contract_valid(row, as_of=NOW)


def test_premarket_and_strategy_sender_do_not_admit_context(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Context reached generic trade/SMTP admission")
    monkeypatch.setattr(api, "_alert_trade_levels", forbidden)
    monkeypatch.setattr(api, "_load_common_stock_universe", forbidden)
    state = api._classify_premarket_candidate("stock_strategy", forged_context())
    assert state["alertable_now"] is False
    monkeypatch.setattr(api, "_classify_alert_candidate", forbidden)
    api._send_strategy_scan_alerts(STRATEGY, [forged_context()])


def test_generic_manual_owner_inherits_heavy_lock_pause_and_weekend_policy():
    from modules.scan_control_policy import capability
    from modules.scan_schedule import is_stock_scan
    key = api._strategy_scan_status_key(STRATEGY)
    assert key == "strat_elliott_wave_muster"
    assert api._is_heavy_stock_worker(key)
    assert capability(key)["resume_policy"] == "continue_if_valid"
    assert is_stock_scan(key)


def test_bad_single_history_keeps_valid_sibling_and_explicit_exclusion(scan_environment, monkeypatch):
    from test_stock_history_isolation import local_error
    observation, history, root = scan_environment
    bad = deepcopy(observation)
    bad["ticker"] = "BAD"
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda name: [bad, deepcopy(observation)])
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kwargs: ({"TEST", "BAD"}, "fixture"))
    def fetch(ticker, *args, **kwargs):
        if ticker == "BAD":
            raise local_error()
        return deepcopy(history)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", fetch)
    rows = api._strategy_scan_wrapper(STRATEGY)
    assert [row["ticker"] for row in rows] == ["TEST"]
    metadata = api.load_cache_metadata(str(root / "elliott_cache.json"))
    assert metadata["diagnostics"]["coverage"] == "complete_with_exclusions"
    assert metadata["diagnostics"]["excluded_data_symbols"] == 1


def test_provider_failure_preserves_previous_final_and_clears_partial(scan_environment, monkeypatch):
    _, _, root = scan_environment
    api._strategy_scan_wrapper(STRATEGY)
    before = (root / "elliott_cache.json").read_bytes()
    events = []
    monkeypatch.setattr(api, "_publish_stock_strategy_attempt", lambda attempt, status, **kwargs: events.append((status, kwargs)))
    def unavailable(*args, **kwargs):
        raise api.StockHistoryDataError("scan_provider_rate_limited", "http_rate_limited")
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", unavailable)
    with pytest.raises(api.ScannerDataError, match="scan_provider_rate_limited"):
        api._strategy_scan_wrapper(STRATEGY)
    assert (root / "elliott_cache.json").read_bytes() == before
    assert not (root / "elliott_cache.json.partial").exists()
    assert events[-1][0] == "error"


def test_no_universe_does_not_become_successful_zero(scan_environment, monkeypatch):
    _, _, root = scan_environment
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda name: [])
    with pytest.raises(api.ScannerDataError, match="scan_data_unavailable"):
        api._strategy_scan_wrapper(STRATEGY)
    assert not (root / "elliott_cache.json").exists()


@pytest.mark.parametrize("reference_error", ["same_session_double_price", "previous_session_history"])
def test_mixed_snapshot_and_pattern_reference_is_excluded(scan_environment, monkeypatch, reference_error):
    observation, history, root = scan_environment
    if reference_error == "same_session_double_price":
        from modules import stock_swing_contract as swing
        observation["day"]["c"] *= 2
        observation.update(swing.metadata(observation["swing_analysis_session"], observation["day"]["c"]))
        reason = "elliott:reference_price_mismatch"
    else:
        history.pop()
        reason = "elliott:history_not_current"
    assert api._strategy_scan_wrapper(STRATEGY) == []
    diagnostic = api.load_cache_metadata(str(root / "elliott_cache.json"))["diagnostics"]
    assert diagnostic["coverage"] == "complete_with_exclusions"
    assert diagnostic["rejected"][reason] == 1


def test_reference_prices_allow_only_legitimate_rounding(scan_environment):
    observation, history, _ = scan_environment
    from modules import stock_swing_contract as swing
    observation["day"]["c"] += .00001
    observation.update(swing.metadata(observation["swing_analysis_session"], observation["day"]["c"]))
    row = api._strategy_scan_wrapper(STRATEGY)[0]
    assert row["elliott_reference_close"] == history[-1]["close"]
    row["elliott_reference_close"] *= 2
    assert not api._stock_elliott_row_contract_valid(row, as_of=NOW)


@pytest.mark.parametrize("has_patterns", [True, False], ids=["pattern-result", "empty-result"])
def test_session_rollover_marks_recent_elliott_cache_stale(scan_environment, monkeypatch, has_patterns):
    """A session-expired cache must not look like a fresh, completed zero scan."""
    observation, history, root = scan_environment
    if not has_patterns:
        from modules import stock_swing_contract as swing
        for bar in history:
            bar.update(open=100.0, high=100.0, low=100.0, close=100.0)
        observation["day"]["c"] = 100.0
        observation.update(swing.metadata("2026-09-24", 100.0))

    class Clock(datetime):
        current = datetime(2026, 9, 25, 20, 14, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    rows = api._strategy_scan_wrapper(STRATEGY)
    expected_count = 1 if has_patterns else 0
    assert len(rows) == expected_count
    before = api.get_scan_results(strategy=STRATEGY, market_type="stocks")
    assert before.count == expected_count
    assert before.data_quality["cache_status"] == "fresh"
    saved = (root / "elliott_cache.json").read_bytes()

    # The new daily session becomes available after the 15-minute delay.
    Clock.current = datetime(2026, 9, 25, 20, 16, tzinfo=timezone.utc)
    after = api.get_scan_results(strategy=STRATEGY, market_type="stocks")
    assert after.count == 0
    assert after.cached_at == before.cached_at
    assert after.cache_age_seconds == 120
    assert after.data_quality["cache_status"] == "stale"
    assert after.diagnostics["warning"] == "elliott_cache_session_stale"
    assert after.diagnostics["elliott_cache_session"] == "2026-09-24"
    assert after.diagnostics["elliott_required_session"] == "2026-09-25"
    assert after.diagnostics["elliott_contract_rejected"] == expected_count
    # Historical scan evidence stays intact; expiry is not a failed scan.
    assert after.diagnostics["coverage"] == "complete"
    assert after.diagnostics["final_results"] == expected_count
    assert after.scan_error is None
    assert any("Elliott" in warning and "veraltet" in warning for warning in after.warnings)
    assert after.warnings == after.data_quality["warnings"]
    assert (root / "elliott_cache.json").read_bytes() == saved


@pytest.mark.parametrize("analysis_as_of", [None, "not-a-date", "2026-09-24T12:00:00Z", "2026-09-25T20:00:00Z"])
def test_empty_elliott_cache_requires_completed_session_metadata(scan_environment, analysis_as_of):
    """Missing, malformed, non-close or future evidence cannot certify zero matches."""
    _, _, root = scan_environment
    api._strategy_scan_wrapper(STRATEGY)
    path = str(root / "elliott_cache.json")
    metadata = api.load_cache_metadata(path)
    metadata["diagnostics"].update(final_results=0, raw_matches_before_special_filter=0,
                                    analysis_as_of=analysis_as_of)
    api.save_cache_file(path, [], metadata=metadata)
    response = api.get_scan_results(strategy=STRATEGY, market_type="stocks")
    assert response.count == 0
    assert response.data_quality["cache_status"] == "stale"
    assert response.diagnostics["warning"] == "elliott_cache_session_unverified"
    assert response.diagnostics["elliott_required_session"] == "2026-09-24"
    assert response.scan_error is None
