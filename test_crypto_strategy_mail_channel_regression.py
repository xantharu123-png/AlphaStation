"""Real strategy sender routes by asset channel without promoting watch rows.

Run with scripts/run_offline_tests.py. Routing cases inject an admitted
candidate at the classifier seam; they do not claim CoinGecko observations
are executable trades. Provider, persistence and transport are test doubles.
"""
import ast
from copy import deepcopy
import inspect
import json
import sqlite3

import pytest

import api
from modules import scan_mail_audit, suppression_telemetry
from scripts import collect_server_evidence
from test_crypto_explosion_mail_dispatch import delivery
from test_crypto_manual_filter_audit_20261002 import _coin
from test_stock_strategy_final_revalidation import _patch_strategy_mail_pipeline, _row


def _routing_pipeline(monkeypatch, tmp_path, decision):
    sent, _, _, _ = _patch_strategy_mail_pipeline(monkeypatch)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_format_alert_plan_html", lambda _row: "fixture plan")
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *_a, **_k: 0)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *_a, **_k: decision)
    monkeypatch.setattr(api, "rate_limited_get", lambda *_a, **_k: pytest.fail("unexpected provider I/O"))
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.invalid",
        "ALERT_EMAIL": "crypto-only@example.invalid",
        "ALERT_OPERATOR_WATCH_OPTIN": "1",
    })
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "mail_channel_enabled", lambda _address, channel: channel == "crypto")

    def capture(subject, body, **kwargs):
        # Exercise actual authorization-channel selection, without SMTP,
        # tracker writes or any mutation of a user's alert settings.
        recipients = api._resolve_email_alert_recipients(
            trade_horizon=kwargs["trade_horizon"],
            mail_class=kwargs["mail_class"],
            mail_channel=kwargs["mail_channel"],
        )
        sent.append(dict(subject=subject, body=body, recipients=recipients, **kwargs))
        return True

    monkeypatch.setattr(api, "_send_email_alert", capture)
    return sent


@pytest.mark.parametrize("decision,subject_prefix,mail_class", [
    (None, "Crypto Strategie:", "swing_trade"),
    ({"state": "RED", "layer": "market", "reason_tag": "market_regime_red"},
     "Markt-Regime:", "watch"),
    ({"state": "RED", "layer": "breaker", "state_key": "test-crypto-cell",
      "reason_tag": "regime_cooldown", "watch_cap_seconds": 3600},
     "Performance-Cooldown:", "watch"),
])
def test_crypto_strategy_trade_and_both_watch_seams_use_crypto_consent(
    monkeypatch, tmp_path, decision, subject_prefix, mail_class,
):
    sent = _routing_pipeline(monkeypatch, tmp_path, decision)
    candidate = _row(ticker="AUDIT", Strategy="Channel audit", strategy="Channel audit")
    api._send_strategy_scan_alerts("Channel audit", [candidate], market_type="crypto")
    assert len(sent) == 1
    assert sent[0]["subject"].startswith(subject_prefix)
    assert sent[0]["mail_class"] == mail_class
    assert sent[0]["mail_channel"] == "crypto"
    assert sent[0]["recipients"] == ["crypto-only@example.invalid"]
    if mail_class != "watch":
        assert sent[0]["tracking_scanner"] == "crypto_strategy"


def test_actual_manual_crypto_watch_producer_still_never_calls_transport(monkeypatch, tmp_path):
    persisted = []
    suppressions = []
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [_coin()])
    monkeypatch.setattr(api, "_CG_MARKETS_STATUS", {"source": "fixture", "partial": False})
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *_a: None)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda *_a, **_kw: None)
    monkeypatch.setattr(api, "_scan_control_point", lambda *_a, **_kw: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda _path, rows, **_kw: persisted.extend(rows))
    monkeypatch.setattr(api, "_record_suppression_counts", lambda scanner, counts: suppressions.append((scanner, dict(counts))))
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *_a, **_k: pytest.fail("watch-only producer must not call transport"))
    monkeypatch.setattr(api, "rate_limited_get", lambda *_a, **_k: pytest.fail("unexpected provider I/O"))
    api._crypto_strategy_scan_wrapper("Volume Surge")
    assert api._CRYPTO_STRATEGY_ALERTS_ENABLED is False
    assert len(persisted) == 1
    assert persisted[0]["signal_quality"] == "observe"
    assert persisted[0]["execution_trigger_ok"] is False
    assert persisted[0]["alertable_crypto"] is False
    assert any(scanner == "crypto_strategy" and counts.get("crypto_strategy_watch_only")
               for scanner, counts in suppressions)


def test_native_explosion_dispatch_codes_are_durable_exportable_and_human_labelled():
    emitted = set()
    for function in (api._crypto_explosion_mail_block_reason,
                     api._send_crypto_explosion_alerts, api._crypto_explosion_wrapper):
        emitted.update(node.value for node in ast.walk(ast.parse(inspect.getsource(function)))
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)
                       and node.value.startswith("crypto_explosion_"))
    assert len(emitted) == 10
    assert emitted <= suppression_telemetry.ALLOWED_SUPPRESSION_REASONS
    assert emitted <= collect_server_evidence.SUPPRESSION_REASONS
    for reason in emitted:
        assert api._stable_suppression_reason(reason) == reason
        label = api._ALERT_SUPPRESSION_LABELS.get(reason)
        assert label and label.startswith("Crypto Long: ")
        assert "_" not in label and label != reason


def test_native_bad_row_counter_is_real_anonymous_persistence_not_transport(monkeypatch, tmp_path):
    database = tmp_path / "suppression.sqlite"
    now = 1_800_000_000
    monkeypatch.setattr(api.time, "time", lambda: now)
    monkeypatch.setattr(suppression_telemetry, "SUPPRESSION_TELEMETRY_DB_PATH", str(database))
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    monkeypatch.setattr(api, "_send_email_alert", lambda *_a, **_k: pytest.fail("invalid rows must not reach SMTP"))
    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", lambda *_a, **_k: pytest.fail("invalid rows must not reach provider"))
    private_marker = "private-recipient@example.invalid"
    with scan_mail_audit.capture(1) as captured:
        assert api._send_crypto_explosion_alerts([private_marker]) is False
    assert captured["reason_occurrences"] == {"crypto_explosion_invalid_row": 1}
    assert captured["transport_events"] == {}
    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT scanner,reason,event_count FROM suppression_buckets").fetchall()
    assert stored == [("crypto_explosion", "crypto_explosion_invalid_row", 1)]
    summary = suppression_telemetry.load_suppression_summary(now=now, db_path=str(database))
    assert summary["available"] and summary["reason_occurrences"] == 1
    assert summary["top_reasons"][0]["reason"] == "crypto_explosion_invalid_row"
    projection = collect_server_evidence._scan_mail_audit_projection(captured)
    assert projection["reason_occurrences"] == {"crypto_explosion_invalid_row": 1}
    for safe_value in (captured, stored, summary, projection, api._EMAIL_SEND_LOG):
        assert private_marker not in json.dumps(safe_value)
    for path in tmp_path.iterdir():
        if path.is_file():
            assert private_marker.encode() not in path.read_bytes()


@pytest.mark.parametrize("mutation,reason", [
    (None, None),
    ("btc_stale", "crypto_explosion_btc_context_unknown_or_stale"),
    ("source_conflict", "crypto_explosion_execution_stale_or_source_unproven"),
    ("closed_candle_stale", "crypto_explosion_execution_stale_or_source_unproven"),
    ("watch_only", "crypto_explosion_watch_only"),
    ("venue_conflict", "crypto_explosion_native_contract_missing_or_conflicting"),
])
def test_actual_native_cache_precheck_explains_gate_without_refresh_send_or_write(
    delivery, monkeypatch, tmp_path, mutation, reason,
):
    row = deepcopy(delivery["row"])
    if mutation == "btc_stale":
        row["btc_context"]["observed_at"] = delivery["now"] - 301
    elif mutation == "source_conflict":
        row["scan_price_source"] = "coingecko:current_price"
    elif mutation == "closed_candle_stale":
        row["execution_candle_timestamp"] = delivery["now"] - 1200
        row["scan_price_observed_at"] = delivery["now"] - 900
    elif mutation == "watch_only":
        row["trade_signal"] = "EXPLOSION_ARMED"
    elif mutation == "venue_conflict":
        row["venue"] = "bybit"
    original = deepcopy(row)
    cache = tmp_path / "cache-native.json"
    monkeypatch.setattr(api, "load_cache_file", lambda *_a, **_k: ([row], None))
    monkeypatch.setattr(api, "rate_limited_get", lambda *_a, **_k: pytest.fail("cache audit cannot access providers"))
    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", lambda *_a, **_k: pytest.fail("cache audit cannot refresh a quote"))
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *_a, **_k: pytest.fail("cache audit cannot refresh candles"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *_a, **_k: pytest.fail("cache audit cannot send"))
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    audit = api._build_alert_audit_for_cache("crypto_explosion", str(cache), read_only=True)
    assert audit["rows_checked"] == 1
    assert audit["evidence_scope"] == "cache_precheck" and audit["delivery_evaluated"] is False
    assert "crash_alertable_now_count" not in audit
    if reason is None:
        assert audit["alertable_now_count"] == 1
        assert audit["mail_status"] == "PRECHECK_PASSED"
        assert audit["suppression_counts"] == {}
    else:
        assert audit["alertable_now_count"] == 0
        assert audit["mail_status"] == "PRECHECK_BLOCKED"
        assert audit["suppression_counts"].get(reason) == 1
    assert row == original
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert delivery["calls"] == [] and delivery["SMTP"].calls == 0
