import api


def _bar(close, volume=1_000_000, high=None, low=None, open_=None):
    high = close * 1.012 if high is None else high
    low = close * 0.988 if low is None else low
    open_ = close * 0.997 if open_ is None else open_
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _cup_handle_bars(last_close=101.7, last_volume=2_400_000):
    bars = []
    # Left side of the cup.
    for i in range(28):
        close = 100 - 24 * (i / 27)
        bars.append(_bar(close, volume=1_050_000))
    # Rounded bottom, not a single V-bar.
    for i in range(26):
        close = 75 + 2.0 * abs((i - 13) / 13)
        bars.append(_bar(close, volume=820_000))
    # Right side returns near the old lip.
    for i in range(36):
        close = 77 + 22.5 * (i / 35)
        bars.append(_bar(close, volume=1_150_000))
    # Handle stays in the upper half and contracts volume.
    handle = [98.8, 97.2, 95.5, 94.0, 94.8, 95.6, 96.7, 97.5, 98.3]
    for close in handle:
        bars.append(_bar(close, volume=650_000))
    bars.append(_bar(last_close, volume=last_volume, high=max(last_close * 1.01, 102.2), low=99.8))
    return bars


def test_cup_handle_detector_returns_actionable_long_levels():
    setup = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)

    assert setup is not None
    assert setup["entry"] < 101.7
    assert setup["stop_loss"] < setup["entry"] < setup["tp1"] < setup["tp2"]
    assert setup["risk_reward"] >= 1.8
    assert setup["live_rr_ratio"] >= 1.4
    assert setup["score"] >= 80


def test_cup_handle_detector_rejects_chased_breakout():
    setup = api._detect_cup_handle_breakout(_cup_handle_bars(last_close=112.0), current_price=112.0)

    assert setup is None


def test_cup_handle_strategy_filter_marks_confirmed_breakout_as_trade_now(monkeypatch):
    # M-Cup&Handle-Fix: Waehrend offener US-Session wird CONFIRMED designgemaess
    # auf INTRADAY_UNCONFIRMED/BEOBACHTEN downgegradet (unfertige Tageskerze).
    # Dieser Test prueft den Confirmed-Pfad NACH Tagesschluss => Session mocken,
    # sonst ist der Test tageszeitabhaengig flaky.
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *a, **k: {"allowed": False, "session": "US_CLOSED", "reason": "unit-test market closed"},
    )
    monkeypatch.setattr(
        api,
        "_fetch_long_latest_intraday_state",
        lambda ticker: {"latest_bar_change_pct": 0.22, "latest_bar_close_pos": 0.82},
    )
    candidate = {
        "ticker": "CUPX",
        "price": 101.7,
        "Dollar_Volume": 8_000_000,
        "base_score": 82,
        "score": 82,
        "_daily_bars": _cup_handle_bars(),
    }
    strat = {"min_dollar_volume": 2_000_000}

    enriched = api._apply_cup_handle_strategy_filter(candidate, strat)

    assert enriched is not None
    assert enriched["trade_signal"] == "JETZT_TRADEN"
    assert enriched["trade_action"] == "LONG_NOW"
    assert enriched["entry_status"] == "BREAKOUT_CONFIRMED"
    assert enriched["pattern_type"] == "cup_handle_breakout"
    assert enriched["score"] >= 80
    assert enriched["grade"] in {"S", "A"}
    assert enriched["long_entry_quality"] == "TRADEABLE"


def test_cup_handle_strategy_filter_rejects_missing_fresh_5m_trigger(monkeypatch):
    monkeypatch.setattr(api, "_fetch_long_latest_intraday_state", lambda ticker: {})
    candidate = {
        "ticker": "CUPX",
        "price": 101.7,
        "Dollar_Volume": 8_000_000,
        "base_score": 82,
        "score": 82,
        "_daily_bars": _cup_handle_bars(),
    }

    assert api._apply_cup_handle_strategy_filter(candidate, {"min_dollar_volume": 2_000_000}) is None


def test_cup_handle_detector_rejects_missing_breakout_volume():
    bars = _cup_handle_bars()
    for bar in bars[-30:]:
        bar["volume"] = 0

    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


def test_cup_handle_strategy_is_visible_in_stock_menu():
    strategies = api.get_public_strategies_for_market("stocks")

    assert "Cup and Handle Breakout" in strategies
    assert strategies["Cup and Handle Breakout"]["needs_cup_handle"] is True
