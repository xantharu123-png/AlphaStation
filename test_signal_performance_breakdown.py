import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from modules import signal_tracker as st
from scripts import signal_performance_breakdown as script


def test_cli_is_read_only_and_uses_decided_fill_denominator(tmp_path, monkeypatch, capsys):
    path = tmp_path / "archive.sqlite"
    created = (datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE signals (id INTEGER, created_at TEXT,scanner TEXT,mail_class TEXT,status TEXT,"
                     "r_realized REAL,entry_filled_at TEXT,entry_fill_price REAL,entry REAL,stop REAL,tp1 REAL,tp2 REAL,direction TEXT)")
        for ident, status, realized, fill in [(1,"STOP_HIT",-1,100),(2,"OPEN",99,100),(3,"STOP_HIT",50,None)]:
            conn.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ident,created,"bi_long","trade",status,realized,created if fill else None,
                          fill,100,95,110,120,"LONG"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(st,"SIGNAL_DB_PATH",str(path))
    monkeypatch.setattr(sys,"argv",["signal_performance_breakdown.py","--days","365"])
    monkeypatch.setattr(st,"_db_connection",lambda: (_ for _ in ()).throw(AssertionError("migration helper called")))
    assert script.main() == 0
    text = capsys.readouterr().out
    assert "Reifezeit, vollstaendig beobachtet" in text
    assert "-1.00" in text
    assert "+99.00" not in text and "+50.00" not in text
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


AS_OF = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _row(ident=1, **overrides):
    row = dict(id=ident, created_at="2026-08-01T10:00:00+00:00", scanner="bi_long",
               mail_class="trade", status="STOP_HIT", r_realized=-1.0,
               entry_filled_at="2026-08-01T10:01:00+00:00", entry_fill_price=100,
               closed_at="2026-08-03T14:00:00+00:00", entry=100, stop=95, tp1=110,
               tp2=120, direction="LONG", asset_class="stock", strategy="BI",
               trade_horizon="swing", evaluation_horizon_bars=5, market_regime="RISK_ON",
               code_revision="revision_a", evaluation_model_version="model_a",
               fill_evidence_mode="post_alert_quote", origin_evidence="direct_post_send",
               channel="email", outcome_detail="stop_hit")
    row.update(overrides)
    return row


def _report(rows, **kwargs):
    return script.build_evidence_report(rows, {}, days=90, as_of=AS_OF, **kwargs)


def _write_db(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE signals (" + ",".join(
            '"' + col + '"' for col in script.REPORT_COLUMNS
        ) + ", ticker, delivery_recipient_keys_json, execution_context_json)")
        for row in rows:
            columns = list(row)
            conn.execute("INSERT INTO signals (" + ",".join('"' + col + '"' for col in columns)
                         + ") VALUES (" + ",".join("?" for _ in columns) + ")", list(row.values()))


@pytest.mark.parametrize("dimension,new_value", [
    ("scanner", "orb"), ("strategy", "Breakout"), ("asset_class", "crypto"),
    ("direction", "SHORT"), ("trade_horizon", "intraday"),
    ("evaluation_horizon_bars", 8), ("market_regime", "RISK_OFF"),
    ("code_revision", "revision_b"), ("evaluation_model_version", "model_b"),
    ("fill_evidence_mode", "legacy_unknown"), ("origin_evidence", None),
    ("channel", "app"),
])
def test_json_cells_do_not_mix_versions_markets_or_population_evidence(dimension, new_value):
    report = _report([_row(), _row(2, **{dimension: new_value})])
    assert len(report["cells"]) == 2
    assert sum(cell["metrics"]["signals"] for cell in report["cells"]) == 2
    assert report["cost_basis"] == "gross_price_path_no_general_roundtrip_costs"
    assert report["net_expectancy_r"] is None and report["account_pnl_usd"] is None
    assert report["grants_paper_or_live_permission"] is False


def test_missing_identity_stays_unknown_not_long_stock_or_current_revision():
    cell = _report([_row(direction=None, asset_class=None, code_revision=None,
                         evaluation_model_version=None, origin_evidence=None)])["cells"][0]
    assert cell["identity"]["direction"] == "unknown"
    assert cell["identity"]["asset_class"] == "unknown"
    assert cell["identity"]["code_revision"] == "unknown"
    assert cell["identity"]["evaluation_model_version"] == "unknown"
    assert cell["identity"]["origin_evidence"] == "legacy_origin_unknown"
    assert cell["metrics"]["qualified_origin_rows"] == 0


def test_empty_outcomes_are_unknown_not_zero_dollars_or_zero_r():
    report = _report([_row(status="OPEN", r_realized=None, closed_at=None)])
    metrics = report["aggregate_descriptive_only"]
    assert metrics["signals"] == metrics["open"] == 1
    assert metrics["decided_signals"] == 0
    assert metrics["avg_r"] is None and metrics["sum_r"] is None
    assert metrics["win_rate_pct"] is None
    assert report["net_expectancy_r"] is None


@pytest.mark.parametrize("timestamp", script.OUTCOME_TIMESTAMPS)
def test_later_outcome_evidence_is_quarantined_not_a_historical_win(timestamp):
    report = _report([_row(**{timestamp: "2026-10-01T00:00:00+00:00", "r_realized": 20})])
    assert report["cohort"]["included_signals"] == 1
    assert report["cohort"]["quarantined_future_outcome_rows"] == 1
    assert report["cohort"]["rows_in_metrics"] == 0
    assert report["aggregate_descriptive_only"]["sum_r"] is None
    assert report["historical_replay"] is False


def test_trade_shadow_app_and_future_records_are_not_silently_combined():
    report = _report([
        _row(), _row(2, mail_class="shadow", r_realized=100),
        _row(3, status="PENDING_DELIVERY", r_realized=100),
        _row(4, created_at="2026-10-01T00:00:00+00:00", r_realized=100),
    ])
    assert report["cohort"]["rows_in_metrics"] == 1
    assert report["aggregate_descriptive_only"]["sum_r"] == -1
    assert report["filtered_trade_history"]["pending_delivery"] == 1
    assert report["filtered_trade_history"]["future_causal_start"] == 1
    assert report["population_coverage"]["app_signal_population"] == "unavailable_from_this_source"


def test_scanner_filter_applies_to_cohort_counts_not_just_visible_cells():
    report = _report([_row(), _row(2, scanner="orb")], scanner="orb")
    assert report["filtered_trade_history"]["rows"] == 1
    assert report["cohort"]["included_signals"] == 1
    assert report["cells"][0]["identity"]["scanner"] == "orb"


def test_young_open_cohort_not_compared_to_only_fast_losses():
    recent = _row(created_at="2026-09-08T10:00:00+00:00", status="OPEN", r_realized=None,
                  entry_filled_at=None, entry_fill_price=None, closed_at=None)
    mature = _report([recent])
    assert mature["cohort"]["rows_in_metrics"] == 0
    assert mature["cohort"]["excluded_not_mature"] == 1
    provisional = _report([recent], mature_only=False, per_day=True)
    assert provisional["cohort"]["rows_in_metrics"] == 1
    assert provisional["cells"][0]["identity"]["period"] == "2026-09-08"
    assert provisional["aggregate_descriptive_only"]["avg_r"] is None


def test_report_reuses_tracker_math_and_does_not_mutate_rows():
    rows = [_row(), _row(2, r_realized=2, status="TP2_HIT")]
    before = json.dumps(rows, sort_keys=True)
    report = _report(rows)
    reference = st._performance_bucket_for_rows(rows, 90, AS_OF)
    for key in script.REPORT_METRICS:
        assert report["aggregate_descriptive_only"][key] == reference.get(key)
    assert json.dumps(rows, sort_keys=True) == before


def test_json_cli_explicit_path_is_private_read_only_and_strict(tmp_path, monkeypatch, capsys):
    path = tmp_path / "source.sqlite"
    secret = "PRIVATE_RECIPIENT_AND_ACCOUNT_DO_NOT_EXPORT"
    _write_db(path, [_row(delivery_recipient_keys_json=secret, execution_context_json=secret,
                          public_signal_ref="AS1-" + "A" * 20,
                          be_delivery_evidence_key="PRIVATE_RECEIPT"),
                    _row(2, mail_class="shadow", r_realized=999)])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "wrong-default.sqlite"))
    monkeypatch.setattr(st, "_db_connection", lambda: (_ for _ in ()).throw(AssertionError("migration")))
    monkeypatch.setattr(sys, "argv", ["report", "--db", str(path), "--days", "365", "--format", "json"])
    assert script.main() == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert secret not in output and "PRIVATE_RECEIPT" not in output and "AS1-" not in output
    assert "delivery_recipient_keys_json" not in output and "execution_context_json" not in output
    assert report["source_inventory_all_history"]["shadow_rows"] == 1
    assert report["source_inventory_all_history"]["trade_rows"] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (tmp_path / "wrong-default.sqlite").exists()
    rows, _ = script.read_snapshot(path)
    assert "execution_context_json" not in rows[0] and "delivery_recipient_keys_json" not in rows[0]


@pytest.mark.parametrize("bad_schema", [False, True])
def test_missing_or_wrong_database_is_not_successful_zero_performance(tmp_path, monkeypatch, capsys, bad_schema):
    path = tmp_path / "missing.sqlite"
    if bad_schema:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
    monkeypatch.setattr(sys, "argv", ["report", "--db", str(path), "--format", "json"])
    assert script.main() == 2
    captured = capsys.readouterr()
    assert not captured.out and "nicht verfuegbar" in captured.err
    assert path.exists() is bad_schema


def test_valid_empty_database_has_no_measured_outcome(tmp_path):
    path = tmp_path / "empty.sqlite"
    _write_db(path, [])
    rows, inventory = script.read_snapshot(path)
    report = script.build_evidence_report(rows, inventory, days=30, as_of=AS_OF)
    assert report["source_inventory_all_history"]["all_rows"] == 0
    assert report["cells"] == []
    assert report["aggregate_descriptive_only"]["sum_r"] is None
    assert report["net_expectancy_r"] is None


def test_valid_smtp_and_be_evidence_survives_static_column_allowlist(tmp_path):
    from test_signal_causal_execution import _row as causal_row
    row = _row(**causal_row(evaluation_horizon_bars=5, closed_at="2026-08-27T14:00:00+00:00"),
               origin_evidence="smtp_acceptance",
               delivery_accepted_at="2026-08-24T14:00:00+00:00",
               public_signal_ref="AS1-" + "B" * 20)
    assert st._has_trade_qualified_origin(row)
    # BI's strategy-specific maturity may exceed a supplied five-bar hint.
    # Compare only once its actual shared observation horizon has elapsed.
    observed = AS_OF + timedelta(days=30)
    before = st._performance_bucket_for_rows([row], 90, observed)
    assert before["avg_r_managed_50_50_be"] is not None
    path = tmp_path / "smtp.sqlite"
    _write_db(path, [row])
    rows, inventory = script.read_snapshot(path)
    report = script.build_evidence_report(rows, inventory, days=90, as_of=observed)
    metrics = report["aggregate_descriptive_only"]
    for key in script.REPORT_METRICS:
        assert metrics[key] == before.get(key)
    assert metrics["qualified_origin_rows"] == 1
    assert "fr1_" not in json.dumps(report)
