import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).parent / "scripts" / "signal_tracker_repair.py"
_SPEC = importlib.util.spec_from_file_location("signal_tracker_repair", _SCRIPT)
repair = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(repair)


def _make_db(path: Path, *, include_exit_fields: bool = True) -> None:
    extra = (
        ", exit_fill_price REAL, stop_gap_slippage_r REAL, stop_gap_slippage_pct REAL"
        if include_exit_fields
        else ""
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE signals ("
            "id INTEGER PRIMARY KEY, created_at TEXT, scanner TEXT, ticker TEXT, "
            "strategy TEXT, direction TEXT, status TEXT, outcome_detail TEXT, "
            "entry REAL, entry_fill_price REAL, stop REAL, tp1 REAL, tp2 REAL, "
            "r_realized REAL, r_realized_upper REAL, r_realized_be REAL, "
            "tp1_hit_at TEXT, tp2_hit_at TEXT, stop_hit_at TEXT, closed_at TEXT, "
            "be_activated_at TEXT, be_mail_sent_at TEXT, max_favorable_r REAL, "
            "max_adverse_r REAL, last_eval_at TEXT, eval_fail_count INTEGER, "
            "entry_filled_at TEXT"
            f"{extra})"
        )
        conn.execute(
            "INSERT INTO signals (id, created_at, scanner, ticker, strategy, direction, "
            "status, outcome_detail, entry, entry_fill_price, stop, tp1, tp2, "
            "r_realized, r_realized_upper, r_realized_be, stop_hit_at, closed_at, "
            "max_favorable_r, max_adverse_r, eval_fail_count, entry_filled_at) "
            "VALUES (7, '2026-08-11T13:59:00+00:00', 'stock_strategy', 'CBLL', "
            "'Gap Momentum Long', 'LONG', 'STOP_HIT', 'stop_gap_slippage', "
            "20.41, 20.41, 19.43, 21.98, 22.89, -1.57, -1.57, -1.57, "
            "'2026-08-11T14:00:00+00:00', '2026-08-11T14:00:00+00:00', "
            "0.0, -1.57, 0, '2026-08-11T13:59:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def _manifest(path: Path, *, expected_r=-1.57, updates=None) -> Path:
    default_updates = {
        "status": "NO_FILL",
        "outcome_detail": "stale_price_invalidated_before_entry",
        "r_realized": None,
        "r_realized_upper": None,
        "r_realized_be": None,
        "entry_filled_at": None,
        "entry_fill_price": None,
        "stop_hit_at": None,
        "exit_fill_price": None,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
    }
    selected_updates = updates or default_updates
    before_values = {
        "status": "STOP_HIT",
        "outcome_detail": "stop_gap_slippage",
        "r_realized": expected_r,
        "r_realized_upper": -1.57,
        "r_realized_be": -1.57,
        "entry_filled_at": "2026-08-11T13:59:00+00:00",
        "entry_fill_price": 20.41,
        "stop_hit_at": "2026-08-11T14:00:00+00:00",
        "exit_fill_price": None,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
        "max_favorable_r": 0.0,
        "max_adverse_r": -1.57,
        "entry": 20.41,
    }
    expected = {
        "ticker": "CBLL",
        "scanner": "stock_strategy",
        "created_at": "2026-08-11T13:59:00+00:00",
        "status": "STOP_HIT",
        "entry": 20.41,
        "stop": 19.43,
        "r_realized": expected_r,
        "outcome_detail": "stop_gap_slippage",
    }
    expected.update({key: before_values.get(key) for key in selected_updates})
    payload = {
        "schema_version": 1,
        "repair_id": "cbll-phantom-fill-20260811",
        "corrections": [
            {
                "id": 7,
                "reason": "Snapshot war vor Versand veraltet und der Stop bereits verletzt.",
                "evidence": {
                    "source": "Polygon 5m aggregates plus original Gmail signal",
                    "observed_at": "2026-08-11T13:59:00Z",
                    "summary": "Post-mail market was below stop before any valid entry.",
                },
                "expected": expected,
                "updates": selected_updates,
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(db: Path):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM signals WHERE id=7").fetchone())
    finally:
        conn.close()


def test_dry_run_is_read_only_and_returns_exact_diff(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json")

    result = repair.run_repair(db, manifest)

    assert result["status"] == "dry_run_ok"
    assert result["applied"] is False
    assert result["correction_count"] == 1
    assert result["preview"][0]["changes"]["status"] == {
        "before": "STOP_HIT",
        "after": "NO_FILL",
    }
    assert _row(db)["status"] == "STOP_HIT"


def test_apply_requires_confirmation_backup_and_audit(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json")

    with pytest.raises(repair.RepairError, match="confirm"):
        repair.run_repair(
            db,
            manifest,
            apply=True,
            backup_dir=tmp_path / "backups",
            audit_log=tmp_path / "audit.jsonl",
        )

    assert _row(db)["status"] == "STOP_HIT"
    assert not (tmp_path / "backups").exists()


def test_apply_creates_verified_backup_updates_atomically_and_audits(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json")
    backups = tmp_path / "backups"
    audit = tmp_path / "audit" / "repairs.jsonl"

    result = repair.run_repair(
        db,
        manifest,
        apply=True,
        confirmation=repair.APPLY_CONFIRMATION,
        backup_dir=backups,
        audit_log=audit,
    )

    assert result["status"] == "applied"
    repaired = _row(db)
    assert repaired["status"] == "NO_FILL"
    assert repaired["r_realized"] is None
    assert repaired["entry_fill_price"] is None
    assert repaired["max_favorable_r"] == pytest.approx(0.0)
    assert repaired["max_adverse_r"] == pytest.approx(0.0)
    backup = Path(result["backup"])
    assert backup.is_file()
    assert _row(backup)["status"] == "STOP_HIT"
    records = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["event"] for record in records] == [
        "signal_tracker_repair_prepared",
        "signal_tracker_repair_applied",
    ]
    assert {record["apply_run_id"] for record in records} == {result["apply_run_id"]}
    assert records[-1]["repair_id"] == "cbll-phantom-fill-20260811"
    assert records[-1]["backup_sha256"] == result["backup_sha256"]
    assert len(records[-1]["backup_sha256"]) == 64


def test_before_state_mismatch_blocks_without_backup_or_write(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json", expected_r=-1.0)

    with pytest.raises(repair.RepairError, match="Before-State"):
        repair.run_repair(
            db,
            manifest,
            apply=True,
            confirmation=repair.APPLY_CONFIRMATION,
            backup_dir=tmp_path / "backups",
            audit_log=tmp_path / "audit.jsonl",
        )

    assert _row(db)["r_realized"] == pytest.approx(-1.57)
    assert not (tmp_path / "backups").exists()


def test_manifest_cannot_change_signal_identity_or_plan(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json", updates={"entry": 18.87})

    with pytest.raises(repair.RepairError, match="nicht erlaubte Felder"):
        repair.run_repair(db, manifest)

    assert _row(db)["entry"] == pytest.approx(20.41)


def test_new_exit_field_requires_deployed_schema(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db, include_exit_fields=False)
    manifest = _manifest(tmp_path / "repair.json", updates={"exit_fill_price": 18.875})

    with pytest.raises(repair.RepairError, match="Spalten fehlen"):
        repair.run_repair(db, manifest)


def test_no_fill_cannot_keep_historical_fill_or_gap_metrics(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(
        tmp_path / "repair.json",
        updates={
            "status": "NO_FILL",
            "outcome_detail": "stale_price_invalidated_before_entry",
            "r_realized": None,
            "r_realized_upper": None,
            "r_realized_be": None,
        },
    )

    with pytest.raises(repair.RepairError, match="NO_FILL widerspricht"):
        repair.run_repair(db, manifest)


def test_no_fill_cannot_keep_trade_trajectory(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    updates = _manifest(tmp_path / "base.json")
    payload = json.loads(updates.read_text(encoding="utf-8"))
    correction_updates = payload["corrections"][0]["updates"]
    correction_updates.pop("max_favorable_r")
    correction_updates.pop("max_adverse_r")
    manifest = tmp_path / "repair.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(repair.RepairError, match="Trade-Trajektorie"):
        repair.run_repair(db, manifest)


def test_audit_preflight_failure_prevents_database_write(tmp_path, monkeypatch):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json")

    monkeypatch.setattr(
        repair,
        "_append_audit_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("audit readonly")),
    )

    with pytest.raises(OSError, match="audit readonly"):
        repair.run_repair(
            db,
            manifest,
            apply=True,
            confirmation=repair.APPLY_CONFIRMATION,
            backup_dir=tmp_path / "backups",
            audit_log=tmp_path / "audit.jsonl",
        )

    assert _row(db)["status"] == "STOP_HIT"


def test_audit_log_cannot_alias_database_by_path_or_hardlink(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)
    manifest = _manifest(tmp_path / "repair.json")
    backups = tmp_path / "backups"

    with pytest.raises(repair.RepairError, match="kollidieren"):
        repair.run_repair(
            db,
            manifest,
            apply=True,
            confirmation=repair.APPLY_CONFIRMATION,
            backup_dir=backups,
            audit_log=db,
        )
    assert not backups.exists()

    alias = tmp_path / "audit-hardlink.jsonl"
    try:
        os.link(db, alias)
    except OSError:
        pytest.skip("Hardlinks werden auf diesem Dateisystem nicht unterstuetzt")
    with pytest.raises(repair.RepairError, match="kollidieren"):
        repair.run_repair(
            db,
            manifest,
            apply=True,
            confirmation=repair.APPLY_CONFIRMATION,
            backup_dir=backups,
            audit_log=alias,
        )
    assert not backups.exists()
    assert _row(db)["status"] == "STOP_HIT"


def test_inspect_tickers_is_read_only_and_selective(tmp_path):
    db = tmp_path / "tracker.sqlite"
    _make_db(db)

    assert repair.inspect_tickers(db, ["missing"]) == []
    rows = repair.inspect_tickers(db, ["cbll"])

    assert len(rows) == 1
    assert rows[0]["id"] == 7
    assert rows[0]["ticker"] == "CBLL"
    assert rows[0]["r_realized"] == pytest.approx(-1.57)


def _valid_tp2_after_state():
    return {
        "created_at": "2026-08-11T13:00:00+00:00",
        "direction": "LONG",
        "status": "TP2_HIT",
        "entry": 100.0,
        "entry_filled_at": "2026-08-11T13:05:00+00:00",
        "entry_fill_price": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp1_hit_at": "2026-08-11T13:30:00+00:00",
        "tp2_hit_at": "2026-08-11T14:00:00+00:00",
        "stop_hit_at": None,
        "closed_at": "2026-08-11T14:00:00+00:00",
        "r_realized": 2.0,
        "r_realized_upper": 2.0,
    }


def _valid_untracked_after_state(*, filled=False):
    state = {
        "created_at": "2026-08-11T13:00:00+00:00",
        "direction": "LONG",
        "status": "UNTRACKED",
        "outcome_detail": "eval_failed_5x",
        "entry": 100.0,
        "entry_filled_at": None,
        "entry_fill_price": None,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp1_hit_at": None,
        "tp2_hit_at": None,
        "stop_hit_at": None,
        "closed_at": "2026-08-11T14:00:00+00:00",
        "r_realized": None,
        "r_realized_upper": None,
        "r_realized_be": None,
        "exit_fill_price": None,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
        "be_activated_at": None,
        "be_mail_sent_at": None,
        "be_trigger_at": None,
        "be_exit_fill_price": None,
        "be_exit_at": None,
        "be_exit_evidence_mode": None,
    }
    if filled:
        state.update({
            "entry_filled_at": "2026-08-11T13:05:00+00:00",
            "entry_fill_price": 100.0,
            "outcome_detail": (
                "observation_window_ended_after_fill_without_complete_interval_path"
            ),
        })
    return state


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tp1_hit_at", "2026-08-11T13:30:00+00:00"),
        ("tp2_hit_at", "2026-08-11T13:40:00+00:00"),
        ("stop_hit_at", "2026-08-11T13:40:00+00:00"),
        ("r_realized", -1.0),
        ("r_realized_upper", 1.0),
        ("r_realized_be", 0.0),
        ("exit_fill_price", 95.0),
        ("stop_gap_slippage_r", 0.2),
        ("stop_gap_slippage_pct", 1.0),
        ("be_exit_fill_price", 100.0),
        ("be_exit_at", "2026-08-11T13:45:00+00:00"),
        ("be_exit_evidence_mode", "completed_interval"),
    ],
)
def test_untracked_repair_rejects_terminal_hit_exit_gap_and_be_r_fields(field, value):
    state = _valid_untracked_after_state()
    state[field] = value

    with pytest.raises(repair.RepairError):
        repair._validate_after_state(7, state)


def test_untracked_entry_fill_requires_explicit_post_fill_lost_path():
    state = _valid_untracked_after_state(filled=True)
    repair._validate_after_state(7, state)

    state["outcome_detail"] = (
        "causal_boundary_interval_level_touch_unresolved_after_confirmed_fill"
    )
    repair._validate_after_state(7, state)

    state["outcome_detail"] = "observation_window_ended_without_interval_path"
    with pytest.raises(repair.RepairError, match="Entry-Fill"):
        repair._validate_after_state(7, state)


def test_no_fill_requires_causal_closed_at():
    state = _valid_untracked_after_state()
    state.update({
        "status": "NO_FILL",
        "outcome_detail": "entry_not_reached",
        "closed_at": None,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
    })
    with pytest.raises(repair.RepairError, match="NO_FILL braucht closed_at"):
        repair._validate_after_state(7, state)

    state["closed_at"] = "2026-08-11T12:59:59+00:00"
    with pytest.raises(repair.RepairError, match="kausalen Signalstart"):
        repair._validate_after_state(7, state)


def test_terminal_repair_rejects_close_before_fill_and_conflicting_event():
    state = _valid_tp2_after_state()
    state["closed_at"] = "2026-08-11T13:04:00+00:00"
    state["stop_hit_at"] = "2026-08-11T13:03:00+00:00"

    with pytest.raises(repair.RepairError, match="Abschluss liegt vor Fill"):
        repair._validate_after_state(7, state)


def test_tp2_repair_rejects_negative_or_geometry_inconsistent_r():
    state = _valid_tp2_after_state()
    state["r_realized"] = -99.0
    state["r_realized_upper"] = -99.0

    with pytest.raises(repair.RepairError, match="TP2-R widerspricht"):
        repair._validate_after_state(7, state)


def test_tp2_repair_requires_prior_tp1_event_and_ordering():
    missing = _valid_tp2_after_state()
    missing["tp1_hit_at"] = None
    with pytest.raises(repair.RepairError, match="tp1_hit_at"):
        repair._validate_after_state(7, missing)

    reversed_events = _valid_tp2_after_state()
    reversed_events["tp1_hit_at"] = "2026-08-11T14:01:00+00:00"
    reversed_events["closed_at"] = "2026-08-11T14:02:00+00:00"
    with pytest.raises(repair.RepairError, match="TP1 liegt nach TP2"):
        repair._validate_after_state(7, reversed_events)


def test_normal_stop_without_gap_metrics_validates_without_crashing():
    state = _valid_tp2_after_state()
    state.update({
        "status": "STOP_HIT",
        "tp2_hit_at": None,
        "stop_hit_at": "2026-08-11T14:00:00+00:00",
        "exit_fill_price": 95.0,
        "r_realized": -1.0,
        "r_realized_upper": -1.0,
        "stop_gap_slippage_r": 0.0,
        "stop_gap_slippage_pct": 0.0,
    })

    repair._validate_after_state(7, state)


def test_be_exit_requires_complete_causal_numeric_contract():
    state = _valid_tp2_after_state()
    state.update({
        "be_activated_at": "2026-08-11T13:20:00+00:00",
        "be_mail_sent_at": "2026-08-11T13:25:00+00:00",
        "be_exit_fill_price": "not-a-number",
        "be_exit_at": None,
        "be_exit_evidence_mode": "claimed",
        "r_realized_be": 123.0,
    })

    with pytest.raises(repair.RepairError, match="be_exit_fill_price|BE-Exit"):
        repair._validate_after_state(7, state)


@pytest.mark.parametrize(
    ("direction", "tp1", "tp2", "relation"),
    [
        ("LONG", 111.0, 110.0, "Stop < Entry < TP1 < TP2"),
        ("SHORT", 89.0, 90.0, "TP2 < TP1 < Entry < Stop"),
    ],
)
def test_repair_rejects_reversed_target_order(direction, tp1, tp2, relation):
    state = _valid_tp2_after_state()
    state.update({"direction": direction, "tp1": tp1, "tp2": tp2})
    if direction == "SHORT":
        state.update({
            "stop": 105.0,
            "r_realized": 2.0,
            "r_realized_upper": 2.0,
        })

    with pytest.raises(repair.RepairError, match=relation):
        repair._validate_after_state(7, state)


def test_repair_requires_complete_stop_gap_pair_and_exit_fill():
    state = _valid_tp2_after_state()
    state.update({
        "status": "STOP_HIT",
        "tp2_hit_at": None,
        "stop_hit_at": "2026-08-11T14:00:00+00:00",
        "exit_fill_price": 94.0,
        "r_realized": -1.2,
        "r_realized_upper": -1.2,
        "outcome_detail": "stop_gap_slippage",
        "stop_gap_slippage_r": 0.2,
        "stop_gap_slippage_pct": None,
    })
    with pytest.raises(repair.RepairError, match="gemeinsam gesetzt|stop_gap_slippage_pct"):
        repair._validate_after_state(7, state)

    state["stop_gap_slippage_pct"] = 100.0 / 95.0
    state["exit_fill_price"] = None
    with pytest.raises(repair.RepairError, match="exit_fill_price|Exit-Fill"):
        repair._validate_after_state(7, state)


def test_repair_stop_requires_exit_fill_and_derived_zero_gap_metrics():
    state = _valid_tp2_after_state()
    state.update({
        "status": "STOP_HIT",
        "tp2_hit_at": None,
        "stop_hit_at": "2026-08-11T14:00:00+00:00",
        "exit_fill_price": 95.0,
        "r_realized": -1.0,
        "r_realized_upper": -1.0,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
    })

    with pytest.raises(repair.RepairError, match="stop_gap_slippage"):
        repair._validate_after_state(7, state)


def test_repair_delivery_acceptance_is_earliest_fill_and_event_time():
    state = _valid_tp2_after_state()
    state["delivery_accepted_at"] = "2026-08-11T13:10:00+00:00"
    state["entry_filled_at"] = "2026-08-11T13:05:00+00:00"
    with pytest.raises(repair.RepairError, match="kausalen Signalstart"):
        repair._validate_after_state(7, state)

    state = _valid_tp2_after_state()
    state["delivery_accepted_at"] = "2026-08-11T13:10:00+00:00"
    state["entry_filled_at"] = "2026-08-11T13:10:00+00:00"
    state["last_eval_at"] = "2026-08-11T13:09:00+00:00"
    with pytest.raises(repair.RepairError, match="kausalen Signalstart"):
        repair._validate_after_state(7, state)


def test_repair_rejects_be_mail_before_activation_and_accepts_causal_exit():
    state = _valid_tp2_after_state()
    state.update({
        "be_trigger_at": "2026-08-11T13:20:00+00:00",
        "be_activated_at": "2026-08-11T13:20:00+00:00",
        "be_mail_sent_at": "2026-08-11T13:25:00+00:00",
        "be_exit_fill_price": 100.0,
        "be_exit_at": "2026-08-11T13:30:00+00:00",
        "be_exit_evidence_mode": "completed_interval_open_or_entry_level",
        "r_realized_be": 0.0,
    })
    repair._validate_after_state(7, state)

    state["be_mail_sent_at"] = "2026-08-11T13:19:00+00:00"
    with pytest.raises(repair.RepairError, match="BE-Mail liegt vor"):
        repair._validate_after_state(7, state)
