from pathlib import Path

import api
from modules.scanners import _bi_interleave_candidates_by_symbol


def test_bi_scanners_show_candidates_not_signal_only_rows():
    assert "bi_long" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_short" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_long" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS
    assert "bi_short" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS


def test_bi_candidate_order_is_not_alphabet_prefix_biased():
    candidates = ["AAA", "AAB", "AAC", "BAA", "BBB", "CAA", "DDD", "EEE"]
    ordered = _bi_interleave_candidates_by_symbol(candidates)

    assert ordered[:5] == ["AAA", "BAA", "CAA", "DDD", "EEE"]


def test_bi_mail_gate_does_not_become_no_trade(monkeypatch):
    def fake_classify(scanner_name, row, now=None):
        return {
            "score": 69,
            "suppression_reasons": [
                "grade_below_alert_threshold",
                "score_below_alert_threshold",
            ],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", fake_classify)

    state = api._scanner_result_trade_state("bi_long", {"ticker": "RACE"})

    assert state["alertable_now"] is False
    assert state["decision"] == "WATCH"
    assert state["decision_label"] == "Noch nicht bestaetigt"
    assert state["display_reasons"] == [
        "grade_below_alert_threshold",
        "score_below_alert_threshold",
    ]


def test_bi_trade_grade_uses_stricter_ladder(monkeypatch):
    def fake_classify(scanner_name, row, now=None):
        return {
            "score": 74,
            "suppression_reasons": [
                "grade_below_alert_threshold",
                "score_below_alert_threshold",
            ],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", fake_classify)

    row = {"ticker": "G", "price": 32.55, "volume": 2_600_000, "rvol": 0.8}
    api._apply_scanner_result_trade_state(row, "bi_long")

    assert row["grade"] == "B"
    assert row["trade_action"] == "SETUP_NOT_CONFIRMED"
    assert row["signal_label"] == "Noch nicht bestaetigt"
    assert row["scanner_suppression_labels"] == [
        "Setup-Grade -; Freigabe erst ab A",
        "Trade-Score 74; Freigabe erst ab 80",
    ]
    assert row["bi_criteria"]["total"] == 20


def test_bi_explains_setup_grade_and_trade_grade_separately(monkeypatch):
    def fake_classify(scanner_name, row, now=None):
        return {
            "score": 91,
            "suppression_reasons": [
                "grade_below_alert_threshold",
                "rvol_below_alert_threshold",
            ],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", fake_classify)

    row = {
        "ticker": "FRT",
        "BI_Grade": "B",
        "BI_Score": 72,
        "price": 125.96,
        "volume": 1_700_000,
        "rvol": 0.5,
    }
    api._apply_scanner_result_trade_state(row, "bi_long")

    assert row["grade"] == "S"
    assert row["setup_grade"] == "B"
    assert row["trade_action"] == "SETUP_NOT_CONFIRMED"
    assert row["scanner_suppression_labels"] == [
        "Setup-Grade B; Freigabe erst ab A",
        "RVOL 0.5x; benoetigt mindestens 0.7x",
    ]


def test_bi_hard_blocker_stays_no_trade(monkeypatch):
    def fake_classify(scanner_name, row, now=None):
        return {
            "score": 88,
            "suppression_reasons": ["invalid_trade_plan"],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", fake_classify)

    state = api._scanner_result_trade_state("bi_long", {"ticker": "BAD"})

    assert state["decision"] == "NO_TRADE"


def test_bi_frontend_uses_trader_facing_actions_only():
    source = (Path(__file__).resolve().parent / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "BI Kandidatenmodus:" not in source
    assert "KANDIDAT =" not in source
    assert "Status / Kriterien" not in source
    assert "reasonLabels.slice(0, 2)" in source
    assert "NOCH NICHT" in source
