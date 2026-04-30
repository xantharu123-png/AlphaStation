from pathlib import Path

from modules.new_listing_scanner import generate_short_signal


ROOT = Path(__file__).parent


def test_calendar_uses_official_macro_sources_for_full_2026_core_calendar():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")

    assert '@app.get("/api/system-health")' in api_source
    assert '@app.get("/api/risk-policy")' in api_source
    assert "official_macro_calendar_with_marked_estimates" in api_source
    assert "2026-04-29" in api_source
    assert "2026-05-08" in api_source
    assert "2026-05-12" in api_source
    assert "2026-05-13" in api_source
    assert "2026-05-14" in api_source
    assert "2026-09-04" in api_source
    assert "2026-09-11" in api_source
    assert "2026-09-16" in api_source
    assert "2026-12-04" in api_source
    assert "2026-12-10" in api_source
    assert "2026-12-15" in api_source
    assert "2026-12-16" in api_source
    assert "2026-12-23" in api_source
    assert "https://www.bls.gov/schedule/news_release/empsit.htm" in api_source
    assert "https://www.bls.gov/schedule/news_release/cpi.htm" in api_source
    assert "https://www.bls.gov/schedule/news_release/ppi.htm" in api_source
    assert "https://www.bea.gov/news/schedule" in api_source
    assert "https://www.census.gov/retail/release_schedule.html" in api_source
    assert "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/" in api_source
    assert "2026-08-03" in api_source
    assert "2026-12-03" in api_source
    assert "for month in []:" not in api_source


def test_crash_monitor_claims_match_backend_factors():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "_load_common_stock_universe" in api_source
    assert '"common_stock_filtered": True' in api_source
    assert '"status": "error"' in api_source
    assert "stale data must not be treated as a fresh success" in api_source
    assert "Put/Call Ratio" not in frontend_source
    assert "VIX Termstruktur" not in frontend_source
    assert "Sektor-Divergenzen" not in frontend_source
    assert "Common-Stock-Marktbreite" in frontend_source


def test_calendar_includes_major_exchange_hours_and_holiday_status():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "EXCHANGE_CALENDARS_2026" in api_source
    assert '"exchanges": _build_exchange_calendar_status()' in api_source
    assert "NYSE / Nasdaq" in api_source
    assert "London Stock Exchange" in api_source
    assert "Xetra / Frankfurt" in api_source
    assert "Tokyo Stock Exchange" in api_source
    assert "Hong Kong Exchange" in api_source
    assert "2026-11-27" in api_source
    assert "2026-09-22" in api_source
    assert "Boersenzeiten & Feiertage" in frontend_source
    assert "Naechster Feiertag" in frontend_source


def test_scanner_quality_and_safe_deploy_are_present():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    deploy_script = (ROOT / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    assert "def _decorate_scan_results" in api_source
    assert '"why_in"' in api_source
    assert '"exclusion_policy"' in api_source
    assert "RISK_POLICY" in api_source
    assert '"$PYTHON" -m py_compile' in deploy_script
    assert "python3 -m venv" in deploy_script
    assert '"$PYTHON" -m pip install -r requirements.txt' in deploy_script
    assert '"$PYTHON" -m pytest' in deploy_script
    assert "/api/system-health" in deploy_script
    assert "systemctl restart" in deploy_script


def test_gitignore_keeps_generated_runtime_files_out_of_commits():
    gitignore = (ROOT / ".gitignore").read_text(encoding="ascii")

    assert "__pycache__/" in gitignore
    assert "*.pyc" in gitignore
    assert "chrome-debug-profile*/" in gitignore
    assert "tmp_*.html" in gitignore


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
