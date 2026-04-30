from pathlib import Path

from modules.new_listing_scanner import generate_short_signal


ROOT = Path(__file__).parent


def test_calendar_uses_official_macro_sources_for_may_july_2026():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")

    assert "official_macro_calendar_with_marked_estimates" in api_source
    assert "2026-04-29" in api_source
    assert "2026-05-08" in api_source
    assert "2026-05-12" in api_source
    assert "2026-05-13" in api_source
    assert "2026-05-14" in api_source
    assert "https://www.bls.gov/schedule/news_release/empsit.htm" in api_source
    assert "https://www.bls.gov/schedule/news_release/cpi.htm" in api_source
    assert "https://www.bls.gov/schedule/news_release/ppi.htm" in api_source
    assert "https://www.bea.gov/news/schedule" in api_source
    assert "https://www.census.gov/retail/release_schedule.html" in api_source


def test_short_signal_does_not_reward_a_missed_tp1_as_elite_entry():
    signal = generate_short_signal(
        "TESTUSD",
        {"ath": 100, "current_price": 75},
        exh_score=90,
        exh_details={},
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["tp1_missed"] is True
    assert signal["tp2_missed"] is False
    assert signal["rr1"] == 0
    assert signal["rr_effective"] == signal["rr2"]
    assert signal["grade"] != "S"
    assert "TP1" in signal["timing"]


def test_short_signal_marks_trade_too_late_when_all_targets_are_missed():
    signal = generate_short_signal(
        "TESTUSD",
        {"ath": 100, "current_price": 55},
        exh_score=95,
        exh_details={},
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["tp1_missed"] is True
    assert signal["tp2_missed"] is True
    assert signal["rr_effective"] == 0
    assert signal["timing_quality"] == 0
    assert signal["grade"] == "D"
