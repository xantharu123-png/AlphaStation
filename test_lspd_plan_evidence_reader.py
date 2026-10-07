"""Offline contract tests for the narrowly scoped, stdlib-only LSPD reader."""
import copy
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


READER_PATH = Path(__file__).parent / "scripts" / "collect_lspd_plan_evidence.py"
WRAPPER_PATH = READER_PATH.with_suffix(".ps1")
STAMP = "2026-10-05T20:00:00Z"
ZONE = "lz_0123456789abcdef"
PRIVATE = "never-export-this-secret@example.invalid"


@pytest.fixture
def reader():
    # Missing reader is the intended RED failure, not an import/collection error.
    assert READER_PATH.is_file(), "The bounded LSPD evidence reader is not implemented"
    spec = importlib.util.spec_from_file_location("lspd_reader_fixture", READER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload():
    """Hand-written fixture follows the native serializers, not reader helpers."""
    evidence = {
        "source_family": "horizontal_swing", "source_name": "confirmed_swing_high",
        "timeframe": "1D", "lower": 13.4, "upper": 13.4,
        "observed_at": "2026-09-29T20:00:00Z", "confirmed_at": STAMP,
        "data_cutoff_at": STAMP, "strength": 1.4, "projection_only": False,
        "independence_key": "horizontal_swing:1D",
        "provenance": {"pivot_index": 20, "confirmation_bar_index": 22,
                       "pivot_left": 2, "pivot_right": 2, "touch_count": 1,
                       "role_hint": "resistance", "private_note": PRIVATE},
    }
    history = {"model": "connected_role_boundary_v2", "zone_id": ZONE,
               "lower": 13.4, "upper": 13.6, "membership_confirmed_at": STAMP,
               "confirmed_at_by_direction": {"LONG": "2026-10-02T20:00:00Z"}}
    proof = {
        "model": "break_reclaim_close_hold_v2", "state": "RECLAIM_PENDING",
        "reason": "completed_retest_missing", "direction": "LONG", "zone_id": ZONE,
        "boundary": 13.6, "zone_confirmed_at": "2026-10-02T20:00:00Z",
        "timeframe": "1D", "as_of": STAMP, "break_closed_at": STAMP,
        "last_completed_at": STAMP, "last_completed_close": 13.8,
        "hold_bars_required": 1, "hold_bars_observed": 0,
        "retest_required": True, "retest_observed": False, "completed_bars_used": 4,
        "reclaim_history": history, "secret": PRIVATE,
    }
    zone = {
        "zone_id": ZONE, "lower": 13.4, "upper": 13.6, "reference": 13.5,
        "side_at_reference": "resistance", "sources": ["confirmed_swing_high"],
        "independent_sources": 1, "independent_structural_sources": 1,
        "touch_count": 1, "confirmed_at": STAMP, "break_state": "intact",
        "strength": 1.4, "origin_roles": ["resistance"], "projection_only": False,
        "quality_flags": [], "reclaim_history": history,
        "break_reclaim_evidence": proof, "evidence": [evidence], "private": PRIVATE,
    }
    row = {
        "ticker": "LSPD", "Ticker": "LSPD", "score": 83, "price": 13.1,
        "stock_swing_contract_version": 1, "stock_swing_mode": "completed_daily_swing",
        "swing_analysis_session": "2026-10-05", "swing_reference_close": 13.1,
        "swing_data_delay_seconds": 900, "swing_timeframe": "1D", "trade_horizon": "swing",
        "scan_price_observed_at": STAMP, "scan_price_source": "polygon_completed_1d_swing",
        "price_observed_at": STAMP, "price_source": "polygon_completed_1d_swing",
        "price_mode": "swing_reference_close", "price_session": "COMPLETED_US_SESSION",
        "fill_evidence_verified": False, "History_Bars": 70,
        "native_plan_status": "built", "native_plan_reason": "native_structure_plan",
        "native_plan_diagnostics": {"status": "built", "reason": "native_structure_plan",
                                    "private": PRIVATE},
        "trade_setup": {"entry": 13.1, "stop": 12.8, "tp1": 13.4, "tp2": 14.0,
                        "risk": .3, "rr_tp1": 1.0, "rr_tp2": 3.0, "atr": .4,
                        "direction": "LONG", "structure_status": "WAIT_BREAK_RECLAIM",
                        "structure_reason": "first_opposing_barrier_before_minimum_rr",
                        "barrier_gate": "BREAK_RECLAIM_REQUIRED", "barrier_gate_active": True,
                        "stop_zone_id": ZONE, "stop_zone_low": 12.7,
                        "tp1_zone_id": ZONE, "tp1_confirmed_at": STAMP,
                        "nearest_barrier": {"zone_id": ZONE, "zone_low": 13.4,
                                            "zone_high": 13.6, "price": 13.4,
                                            "confirmed_at": STAMP, "source": PRIVATE},
                        "warnings": [PRIVATE]},
        "level_structure": {"model": "causal_level_zones_v2", "symbol": "LSPD",
                            "asset_class": "stock", "horizon": "swing", "as_of": STAMP,
                            "current_price": 13.1, "zones": [zone],
                            "atr_by_timeframe": {"1D": .4},
                            "completed_bar_counts": {"1D": 70, "4H": 40}, "quality_flags": []},
        "email": PRIVATE, "provider_key": PRIVATE,
    }
    return {"cache_version": 18, "timestamp": 1791230700.0, "partial": False,
            "results": [{"ticker": "OTHER", "email": PRIVATE}, row],
            "diagnostics": {"coverage": "complete", "secret": PRIVATE}}


def write_cache(tmp_path, data=None):
    path = tmp_path / "strategy_momentum_breakout_long_cache.json"
    path.write_text(json.dumps(payload() if data is None else data), encoding="utf8")
    return path


def test_exact_unique_lspd_projection_preserves_native_zone_evidence(reader, tmp_path):
    path = write_cache(tmp_path)
    raw, snapshot = reader.read_cache(path)
    result = reader.project_payload(raw, snapshot)
    assert result["status"] == "ok"
    assert result["lspd"]["ticker"] == "LSPD"
    assert result["lspd"]["row_index"] == 1
    assert result["cache"]["rows_total"] == 2
    assert result["cache"]["cache_version"] == 18
    assert result["cache"]["size_bytes"] == path.stat().st_size
    assert len(result["cache"]["sha256"]) == 64
    assert result["lspd"]["swing_reference"]["swing_analysis_session"] == "2026-10-05"
    assert result["lspd"]["trade_setup"]["entry"] == 13.1
    zone = result["lspd"]["level_structure"]["zones"][0]
    assert zone["lower"] == 13.4 and zone["upper"] == 13.6
    assert zone["origin_roles"] == ["resistance"]
    assert zone["confirmed_at"] == "2026-10-05T20:00:00+00:00"
    assert zone["evidence"][0]["provenance"]["pivot_index"] == 20
    assert zone["break_reclaim_evidence"]["state"] == "RECLAIM_PENDING"
    assert zone["reclaim_history"]["confirmed_at_by_direction"]["LONG"] == "2026-10-02T20:00:00+00:00"
    assert result["raw_daily_prefix"]["status"] == "not_stored_in_final_result_cache"
    serialized = json.dumps(result, allow_nan=False)
    assert PRIVATE not in serialized and "OTHER" not in serialized


@pytest.mark.parametrize("field", ["ticker", "Ticker"])
def test_either_exact_public_ticker_alias_selects_lspd(reader, tmp_path, field):
    data = payload()
    data["results"][1].pop("ticker" if field == "Ticker" else "Ticker")
    result = reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))
    assert result["lspd"]["ticker"] == "LSPD"


@pytest.mark.parametrize("symbol", ["lspd", " LSPD", "LSPD ", "LSPD.A", PRIVATE])
def test_no_fuzzy_symbol_match_can_export_another_row(reader, tmp_path, symbol):
    data = payload()
    data["results"][1].update(ticker=symbol, Ticker=symbol)
    with pytest.raises(reader.EvidenceError, match="lspd_not_found"):
        reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))


@pytest.mark.parametrize("shape", [[], {}, {"results": {}}, {"results": [None]},
                                   {"results": [], "partial": True}])
def test_bad_cache_shapes_fail_closed(reader, tmp_path, shape):
    with pytest.raises(reader.EvidenceError):
        reader.project_payload(*reader.read_cache(write_cache(tmp_path, shape)))


def test_duplicate_lspd_rows_are_not_silently_chosen(reader, tmp_path):
    data = payload()
    data["results"].append(copy.deepcopy(data["results"][1]))
    with pytest.raises(reader.EvidenceError, match="lspd_not_unique"):
        reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))


def test_conflicting_ticker_aliases_fail_closed(reader, tmp_path):
    data = payload()
    data["results"][1]["Ticker"] = "OTHER"
    with pytest.raises(reader.EvidenceError, match="ticker_alias_conflict"):
        reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))


@pytest.mark.parametrize("field", ["trade_setup", "native_plan_diagnostics", "level_structure"])
def test_malformed_lspd_nested_shape_is_not_presented_as_empty_evidence(reader, tmp_path, field):
    data = payload()
    data["results"][1][field] = [PRIVATE]
    with pytest.raises(reader.EvidenceError, match="lspd_shape_invalid"):
        reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))


def test_known_field_names_do_not_allow_arbitrary_strings_or_boolean_numbers(reader, tmp_path):
    data = payload()
    row = data["results"][1]
    row["swing_analysis_session"] = PRIVATE
    row["price_source"] = PRIVATE
    row["trade_setup"].update(entry=True, stop=float("inf"), direction=PRIVATE)
    row["level_structure"]["zones"][0].update(zone_id=PRIVATE, sources=[PRIVATE], origin_roles=[PRIVATE])
    evidence = row["level_structure"]["zones"][0]["evidence"][0]
    evidence.update(independence_key=PRIVATE, source_name=PRIVATE)
    evidence["provenance"].update(role_hint=PRIVATE, session_closed_at=PRIVATE, pivot_left=True)
    result = reader.project_payload(*reader.read_cache(write_cache(tmp_path, data)))
    assert PRIVATE not in json.dumps(result, allow_nan=False)
    assert "entry" not in result["lspd"]["trade_setup"]
    assert "stop" not in result["lspd"]["trade_setup"]


def test_duplicate_json_keys_are_rejected_before_projection(reader, tmp_path):
    path = write_cache(tmp_path)
    path.write_text('{"results": [], "results": [{"ticker":"LSPD"}]}', encoding="utf8")
    with pytest.raises(reader.EvidenceError, match="invalid_json"):
        reader.read_cache(path)


def test_symlink_cache_is_never_opened(reader, tmp_path):
    target = write_cache(tmp_path)
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Local OS cannot create an unprivileged symlink")
    with pytest.raises(reader.EvidenceError, match="not_regular"):
        reader.read_cache(alias)


def test_oversized_cache_is_rejected_before_loading_json(reader, tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_BYTES", 32)
    path = write_cache(tmp_path)
    with pytest.raises(reader.EvidenceError, match="too_large"):
        reader.read_cache(path)


def test_same_path_replacement_between_stat_and_open_fails_coherence(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"results": []}', encoding="utf8")
    original_open = reader.os.open
    def racing_open(name, flags, *args, **kwargs):
        os.replace(replacement, path)
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, "open", racing_open)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_pathname_replacement_during_read_fails_coherence(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"results": []}', encoding="utf8")
    original_lstat = Path.lstat
    calls = []
    # NTFS prevents replacing an open descriptor. Return the real second file's
    # metadata at the post-read pathname check to exercise Linux rename safety.
    def racing_lstat(named, *args, **kwargs):
        if named == path:
            calls.append(named)
            if len(calls) > 1:
                return original_lstat(replacement, *args, **kwargs)
        return original_lstat(named, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", racing_lstat)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_changed_open_file_metadata_fails_coherence(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    original_fstat = reader.os.fstat
    calls = []
    def racing_fstat(descriptor):
        if calls:
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        calls.append(descriptor)
        return original_fstat(descriptor)
    monkeypatch.setattr(reader.os, "fstat", racing_fstat)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_service_pid_change_discards_otherwise_good_cache(reader, tmp_path, monkeypatch):
    root = tmp_path / "namespace"
    (root / "tmp").mkdir(parents=True)
    write_cache(root / "tmp")
    pids = iter([71, 72])
    monkeypatch.setattr(reader, "service_pid", lambda: next(pids))
    monkeypatch.setattr(reader, "process_start_ticks", lambda pid: 12345)
    monkeypatch.setattr(reader, "process_root", lambda pid: root)
    with pytest.raises(reader.EvidenceError, match="service_changed"):
        reader.collect()


def test_recycled_pid_start_time_discards_otherwise_good_cache(reader, tmp_path, monkeypatch):
    root = tmp_path / "namespace"
    (root / "tmp").mkdir(parents=True)
    write_cache(root / "tmp")
    starts = iter([12345, 12346])
    monkeypatch.setattr(reader, "service_pid", lambda: 71)
    monkeypatch.setattr(reader, "process_start_ticks", lambda pid: next(starts))
    monkeypatch.setattr(reader, "process_root", lambda pid: root)
    with pytest.raises(reader.EvidenceError, match="service_changed"):
        reader.collect()


def test_collect_only_uses_verified_process_namespace(reader, tmp_path, monkeypatch):
    root = tmp_path / "namespace"
    (root / "tmp").mkdir(parents=True)
    path = write_cache(root / "tmp")
    monkeypatch.setattr(reader, "service_pid", lambda: 71)
    monkeypatch.setattr(reader, "process_start_ticks", lambda pid: 12345)
    monkeypatch.setattr(reader, "process_root", lambda pid: root)
    result = reader.collect()
    assert result["api"] == {"unit": "tradingbot-api.service", "pid": 71, "start_ticks": 12345}
    assert result["cache"]["sha256"]
    assert result["lspd"]["ticker"] == "LSPD"
    assert path.read_text(encoding="utf8") == json.dumps(payload())


def test_stdin_cli_is_standalone_and_sanitizes_failure():
    assert READER_PATH.is_file(), "The standalone evidence CLI is not implemented"
    completed = subprocess.run([os.sys.executable, "-I", "-", "unexpected-argument"],
                               input=READER_PATH.read_text(encoding="utf8"), text=True,
                               capture_output=True, timeout=10)
    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["status"] == "unavailable"
    assert result["reason"] == "unsupported_arguments"
    assert completed.stderr == ""


@pytest.mark.parametrize("scenario", ["success", "ssh_failure", "invalid_json", "wrong_shape", "existing_file"])
def test_powershell_export_requires_valid_reply_and_never_overwrites(reader, tmp_path, scenario):
    assert WRAPPER_PATH.is_file(), "The safe local LSPD export wrapper is not implemented"
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed on this test host")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(READER_PATH, scripts / READER_PATH.name)
    shutil.copy2(WRAPPER_PATH, scripts / WRAPPER_PATH.name)
    export = tmp_path / "output" / "profitability" / "lspd-plan-evidence-20261006T123456Z.json"
    # Only SSH is external/faked: feed the real reader projection to its real
    # PowerShell consumer, so a handwritten reply cannot hide schema drift.
    response = reader.project_payload(*reader.read_cache(write_cache(tmp_path)))
    assert response["raw_daily_prefix"]["provider_fetch_performed"] is False
    if scenario == "wrong_shape":
        response["read_only"] = "true"
    reply = tmp_path / "fixture-reply.json"
    reply.write_text("not json" if scenario == "invalid_json" else json.dumps(response), encoding="utf8")
    if scenario == "existing_file":
        export.parent.mkdir(parents=True)
        export.write_text("preserved", encoding="utf8")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    code = """
$ErrorActionPreference = 'Stop'
function Get-Date { [datetime]::SpecifyKind([datetime]'2026-10-06T12:34:56', [DateTimeKind]::Utc) }
function ssh {
    if (($args -join '|') -ne '-T|-o|StrictHostKeyChecking=yes|-o|ConnectTimeout=10|root@178.104.69.209|/usr/bin/python3 -I -') { throw 'Unexpected SSH contract' }
    $pipedSource = @($input) -join "`n"
    if (-not $pipedSource.Contains('def collect():')) { throw 'Collector source not streamed' }
    $global:LASTEXITCODE = EXIT_CODE
    Get-Content -Raw -LiteralPath REPLY_PATH
}
try { & WRAPPER_PATH; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
""".replace("EXIT_CODE", "7" if scenario == "ssh_failure" else "0").replace(
        "REPLY_PATH", quote(reply)).replace("WRAPPER_PATH", quote(scripts / WRAPPER_PATH.name))
    encoded = base64.b64encode(code.encode("utf-16le")).decode("ascii")
    completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-EncodedCommand", encoded],
                               text=True, capture_output=True, timeout=30)
    if scenario == "success":
        assert completed.returncode == 0, completed.stdout + completed.stderr
        saved = json.loads(export.read_text(encoding="utf8"))
        assert saved == response
        assert saved["lspd"]["ticker"] == "LSPD"
        assert saved["lspd"]["trade_setup"]["entry"] == 13.1
        assert PRIVATE not in export.read_text(encoding="utf8")
    else:
        assert completed.returncode != 0
        if scenario == "existing_file":
            assert export.read_text(encoding="utf8") == "preserved"
        else:
            assert not export.exists()
