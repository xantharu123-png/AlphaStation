"""Regressionstests Infrastruktur-Fixes BI- + Biotech-Scanner (Audits 10.06.2026).

Beweist:
- K-1: bg-BI-Pfad ruft den ECHTEN Scan (modules.scanners._bi_background_scan)
  und schreibt einen NICHT-leeren Cache; der ImportError-Fallback ist weg.
- N:   Cache-Frische — bg mailt nie aus einem >2h alten Cache;
  api.load_cache_file setzt max_age_hours durch (leer + Stale-Metadatum).
- H-3: PRIORITY_WATCH/WATCH_FOR_TRIGGER sind wieder sichtbare Signal-Rows;
  NEAR_BINARY_EVENT/AVOID_NEWS_RISK bleiben Kontext.
- H-2: Binary-Event <= T-3 => Suppression `near_binary_event` + rote
  Warnzeile im Plan-HTML; T-10 bleibt clean mailbar.
- M-4: synthetische Biotech-Level sind mailbar + gelabelt (synthetic-Flag),
  estimated-Level anderer Scanner bleiben hart geblockt.
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Session-unabhaengig: Repo-Root (Verzeichnis dieser Datei) in sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api
import bg_service
import modules.scanners as scanners_mod
from modules.trade_levels import normalize_alert_trade_levels


BI_LONG_CACHE = "/tmp/bi_cache_long.json"
BIOTECH_CACHE = "/tmp/alpha_biotech_cache.json"


# ── Gemeinsame Fixtures/Helpers (Muster: test_mail_gates_bg.py) ─────────────

def _bg_setup(monkeypatch, tmp_path, cache_file, payload):
    """bg-Mail-Test-Setup: Cache schreiben, Dedupe isolieren, Versand aufzeichnen."""
    isolated_cache = tmp_path / Path(cache_file).name
    with isolated_cache.open("w", encoding="utf-8") as f:
        json.dump(payload, f)
    monkeypatch.setattr(bg_service, "_alert_cache_path", lambda _name: str(isolated_cache))
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(
        bg_service,
        "_has_open_equivalent_trade_safe",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600, raising=False)
    monkeypatch.setattr(
        bg_service, "_fetch_long_latest_intraday_state", lambda *a, **k: {}, raising=False
    )
    monkeypatch.setattr(
        bg_service,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
        raising=False,
    )
    sent_mails = []

    def _recorder(subject, body_html, secrets, mail_class="trade"):
        sent_mails.append({"subject": subject, "body": body_html, "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent_mails


def _bi_row(ticker="GOOD", **overrides):
    row = {
        "ticker": ticker,
        "BI_Grade": "S",
        "BI_Score": 120,
        "Preis": 10.05,
        "current_price": 10.05,
        "RVOL": 2.5,
        "direction": "long",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 11.0,
        "TP2": 11.8,
        "Name": "Test Corp",
        "latest_bar_change_pct": 0.4,
        "latest_bar_close_pos": 0.8,
    }
    row.update(overrides)
    return row


def _biotech_row(ticker="BIOX", **overrides):
    """Saubere, mailbare Biotech-Row (Grade A, native Level, frisches Timing)."""
    row = {
        "Ticker": ticker,
        "ticker": ticker,
        "Grade": "A",
        "Score": 88,
        "Preis": 10.05,
        "current_price": 10.05,
        "RVOL": 2.0,
        "direction": "long",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 11.0,
        "TP2": 11.8,
        "Name": "Bio Test Corp",
        "latest_bar_change_pct": 0.4,
        "latest_bar_close_pos": 0.8,
    }
    row.update(overrides)
    return row


# ── K-1: bg-BI-Pfad ruft den echten Scan und schreibt NICHT-leeren Cache ────

def test_k1_bg_run_bi_scan_writes_nonempty_cache(monkeypatch, tmp_path):
    """End-to-End (gemockter Netz-Scan): 2 Kandidaten => Cache mit 2 results.

    Der Mock ersetzt nur die Netz-Schicht (_bi_background_scan) und nutzt die
    ECHTE Cache-Save-Funktion des Scan-Owners — beweist die K-1-Verdrahtung
    bg -> modules.scanners inkl. Kandidaten-Durchreichung und Cache-Format.
    """
    monkeypatch.setattr(
        scanners_mod, "_BI_CACHE_FILE", str(tmp_path / "bi_cache_{direction}.json")
    )
    calls = {}

    def _fake_scan(poly_key, direction="long", candidates=None):
        calls["poly_key"] = poly_key
        calls["direction"] = direction
        calls["candidates"] = candidates
        rows = [
            {"Ticker": c["Ticker"], "BI_Score": 90 - i, "BI_Grade": "A"}
            for i, c in enumerate(candidates)
        ]
        scanners_mod._bi_cache_save(
            rows, direction=direction, checked=len(candidates),
            total=len(candidates), detail="unit-test",
        )

    monkeypatch.setattr(scanners_mod, "_bi_background_scan", _fake_scan)
    candidates = [
        {"Ticker": "AAA", "Name": "A Corp", "Preis": 12.0, "Change%": 1.0,
         "RVOL": 2.0, "Volume": 1_000_000, "DollarVol": 12_000_000},
        {"Ticker": "BBB", "Name": "B Corp", "Preis": 25.0, "Change%": -0.5,
         "RVOL": 1.4, "Volume": 800_000, "DollarVol": 20_000_000},
    ]

    bg_service._bg_run_bi_scan("long", {"POLYGON_KEY": "pk-test"}, candidates=candidates)

    cache_path = tmp_path / "bi_cache_long.json"
    assert cache_path.exists(), "K-1: Cache-Datei wurde nicht geschrieben"
    cache = json.loads(cache_path.read_text())
    assert len(cache["results"]) == 2, "K-1: Cache muss 2 results enthalten (nicht 0!)"
    assert cache["count"] == 2
    assert calls["poly_key"] == "pk-test"
    assert calls["direction"] == "long"
    assert [c["Ticker"] for c in calls["candidates"]] == ["AAA", "BBB"]


def test_k1_importerror_fallback_is_gone():
    """grep-Pin: weder Phantom-Import noch divergenter Direkt-Fallback existieren."""
    src = Path(bg_service.__file__).with_suffix(".py").read_text(encoding="utf-8")
    assert "_bi_background_scan_standalone" not in src, (
        "K-1: Phantom-Import ist zurueck — bg wuerde wieder in den Fallback laufen"
    )
    assert "_run_bi_analysis_direct" not in src, (
        "K-1: divergenter Fallback (Schwelle 85/75, falsches Level-Modell) ist zurueck"
    )
    assert not hasattr(bg_service, "_run_bi_analysis_direct")
    assert hasattr(bg_service, "_bg_run_bi_scan")
    assert "_bi_background_scan" in src, "K-1: echte Scan-Verdrahtung fehlt"


def test_bg_keeps_last_good_cache_visible_during_replacement(monkeypatch, tmp_path):
    cache = tmp_path / "bi.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": [{"ticker": "OLD"}]}))
    monkeypatch.setitem(bg_service._SCAN_CACHE_MAP, "bi_long", str(cache))

    bg_service._clear_scan_cache("bi_long")

    assert cache.exists()
    assert json.loads(cache.read_text())["results"][0]["ticker"] == "OLD"


def test_bi_partial_cache_never_replaces_last_final(monkeypatch, tmp_path):
    final_path = tmp_path / "bi_long.json"
    final_path.write_text(json.dumps({"results": [{"ticker": "OLD"}]}), encoding="utf-8")
    monkeypatch.setattr(scanners_mod, "_BI_CACHE_FILE", str(tmp_path / "bi_{direction}.json"))

    scanners_mod._bi_cache_save(
        [{"ticker": "PARTIAL"}], direction="long", partial=True, checked=5, total=10
    )
    assert json.loads(final_path.read_text(encoding="utf-8"))["results"][0]["ticker"] == "OLD"
    partial_path = Path(f"{final_path}.partial")
    assert json.loads(partial_path.read_text(encoding="utf-8"))["partial"] is True

    scanners_mod._bi_cache_save(
        [{"ticker": "FINAL"}], direction="long", partial=False, checked=10, total=10
    )
    final_payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert final_payload["results"][0]["ticker"] == "FINAL"
    assert final_payload["partial"] is False
    assert not partial_path.exists()


def test_biotech_partial_cache_never_replaces_last_final(monkeypatch, tmp_path):
    final_path = tmp_path / "biotech.json"
    final_path.write_text(json.dumps({"results": [{"ticker": "OLD"}]}), encoding="utf-8")
    monkeypatch.setattr(scanners_mod, "_biotech_cache_file", lambda: str(final_path))

    scanners_mod._biotech_cache_save(
        [{"ticker": "PARTIAL"}], partial=True, checked=2, total=8
    )
    assert json.loads(final_path.read_text(encoding="utf-8"))["results"][0]["ticker"] == "OLD"
    partial_path = Path(f"{final_path}.partial")
    assert json.loads(partial_path.read_text(encoding="utf-8"))["partial"] is True

    scanners_mod._biotech_cache_save(
        [{"ticker": "FINAL"}], partial=False, checked=8, total=8
    )
    final_payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert final_payload["results"][0]["ticker"] == "FINAL"
    assert final_payload["partial"] is False
    assert not partial_path.exists()


def test_systemic_candidate_failures_abort_scanner_publish():
    with pytest.raises(RuntimeError, match="systemischer Analysefehler"):
        scanners_mod._raise_on_systemic_analysis_failures("BI long", 20, 18)

    scanners_mod._raise_on_systemic_analysis_failures("BI long", 20, 15)


def test_bg_final_publish_contract_rejects_partial_and_accepts_done(tmp_path):
    cache = tmp_path / "bi.json"
    progress = tmp_path / "progress.json"
    started_at = time.time()
    cache.write_text(json.dumps({
        "timestamp": started_at,
        "direction": "long",
        "partial": True,
        "results": [{"ticker": "PARTIAL"}],
    }))
    progress.write_text(json.dumps({"timestamp": started_at, "status": "running"}))

    try:
        bg_service._require_final_scanner_publish(
            "bi_long", str(cache), None, str(progress), started_at, direction="long"
        )
    except RuntimeError as exc:
        assert "partieller" in str(exc)
    else:
        raise AssertionError("Partieller BI-Cache wurde als final akzeptiert")

    cache.write_text(json.dumps({
        "timestamp": time.time(),
        "direction": "long",
        "partial": False,
        "results": [],
    }))
    progress.write_text(json.dumps({"timestamp": time.time(), "status": "done"}))
    payload = bg_service._require_final_scanner_publish(
        "bi_long", str(cache), None, str(progress), started_at, direction="long"
    )
    assert payload["results"] == []


def test_api_final_publish_contract_rejects_partial_bi_cache(monkeypatch, tmp_path):
    cache = tmp_path / "bi.json"
    progress = tmp_path / "progress.json"
    cache.write_text(json.dumps({
        "timestamp": time.time(),
        "direction": "long",
        "partial": True,
        "results": [{"ticker": "PARTIAL"}],
    }))
    progress.write_text(json.dumps({"timestamp": time.time(), "status": "running"}))
    monkeypatch.setitem(api.SCAN_CACHE_MAP, "bi_long", str(cache))
    monkeypatch.setitem(api._SCAN_PROGRESS_MAP, "bi_long", str(progress))

    try:
        api._require_fresh_scan_cache("bi_long", None)
    except RuntimeError as exc:
        assert "partial" in str(exc)
    else:
        raise AssertionError("API akzeptierte einen partiellen BI-Cache")


def test_api_final_publish_contract_rejects_stopped_biotech(monkeypatch, tmp_path):
    cache = tmp_path / "biotech.json"
    progress = tmp_path / "progress.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": []}))
    progress.write_text(json.dumps({"timestamp": time.time(), "status": "stopped"}))
    monkeypatch.setitem(api.SCAN_CACHE_MAP, "biotech", str(cache))
    monkeypatch.setitem(api._SCAN_PROGRESS_MAP, "biotech", str(progress))

    try:
        api._require_fresh_scan_cache("biotech", None)
    except RuntimeError as exc:
        assert "stopped" in str(exc)
    else:
        raise AssertionError("Gestoppter Biotech-Scan wurde als final akzeptiert")


def test_k1_mail_footer_uses_v33_grade_ladder():
    """Fusszeilen-Pin: Alt-Leiter (Score >=113/99) raus, V3.3-Leiter drin."""
    src = Path(bg_service.__file__).with_suffix(".py").read_text(encoding="utf-8")
    assert "Score ≥113" not in src, "Alt-Grade-Leiter (113/99) noch in der Mail-Fusszeile"
    assert "Score ≥99" not in src
    assert "85 + 4 Smart-Money-Fires" in src
    assert "71 + 3 Fires" in src


# ── N: Cache-Frische ────────────────────────────────────────────────────────

def test_n_bg_stale_cache_blocks_mails(monkeypatch, tmp_path):
    """Cache 3h alt => 2h-Gate blockt JEDE Mail (auch fuer perfekte Rows)."""
    sent = _bg_setup(monkeypatch, tmp_path, BI_LONG_CACHE, {
        "results": [_bi_row("GOOD")],
        "timestamp": time.time() - 3 * 3600,
    })
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == [], "N: Mail aus 3h altem Cache — Frische-Gate wirkungslos"


def test_n_bg_fresh_cache_never_bypasses_api_mail_owner(monkeypatch, tmp_path):
    """Auch ein frischer BG-Cache darf keinen unsicheren Entry-Wire senden."""
    sent = _bg_setup(monkeypatch, tmp_path, BI_LONG_CACHE, {
        "results": [_bi_row("GOOD")],
        "timestamp": time.time(),
    })
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_n_load_cache_file_enforces_max_age(tmp_path):
    """api.load_cache_file: explizites max_age_hours => leer + Stale-Metadatum;
    Default (None) = Anzeige-Modus liefert weiterhin alles + cached_at."""
    cache = tmp_path / "stale.json"
    old_iso = (datetime.now() - timedelta(hours=3)).isoformat()
    cache.write_text(json.dumps({"cached_at": old_iso, "results": [{"ticker": "OLD"}]}))

    rows, cached_at = api.load_cache_file(str(cache), max_age_hours=2)
    assert rows == [], "N: 3h alter Cache muss bei max_age=2 leer zurueckkommen"
    assert cached_at == old_iso, "N: Stale-Metadatum (cached_at) muss erhalten bleiben"

    rows_display, cached_at_display = api.load_cache_file(str(cache))
    assert rows_display == [{"ticker": "OLD"}], "Anzeige-Modus (Default) darf stale zeigen"
    assert cached_at_display == old_iso

    rows_ok, _ = api.load_cache_file(str(cache), max_age_hours=4)
    assert rows_ok == [{"ticker": "OLD"}], "Cache juenger als max_age muss durchkommen"


# ── H-3: REST-Endpoint — handelbare Modes sichtbar, Kontext bleibt Kontext ──

def _h3_row(mode):
    return {
        "ticker": "BIOX",
        "Grade": "A",
        "Score": 88,
        "RVOL": 2.0,
        "Bio_Trade_Mode": mode,
        "Bio_Risk_Flags": [],
        "trade_health": {"decision": "TRADEABLE"},
    }


def test_h3_priority_watch_and_trigger_rows_visible():
    """Grade-A-Row mit PRIORITY_WATCH/WATCH_FOR_TRIGGER => wieder Signal-Row."""
    assert api._scanner_row_is_trade_signal(_h3_row("PRIORITY_WATCH"), "biotech") is True
    assert api._scanner_row_is_trade_signal(_h3_row("WATCH_FOR_TRIGGER"), "biotech") is True


def test_h3_context_modes_stay_hidden():
    """NEAR_BINARY_EVENT (neu) und AVOID_NEWS_RISK bleiben Kontext (kein Signal)."""
    assert api._scanner_row_is_trade_signal(_h3_row("NEAR_BINARY_EVENT"), "biotech") is False
    assert api._scanner_row_is_trade_signal(_h3_row("AVOID_NEWS_RISK"), "biotech") is False
    assert api._scanner_row_is_trade_signal(_h3_row("WATCHLIST"), "biotech") is False


# ── H-2: Binary-Event-Gate + Warnzeile ──────────────────────────────────────

def test_h2_t2_row_suppressed_and_plan_html_warns():
    """T-2 (Readout in 2 Tagen): hartes Mail-Gate + rote Warnzeile im Plan."""
    row = _biotech_row(BPIQ_Catalysts=[{"name": "PDUFA", "days_until": 2}])
    state = api._classify_alert_candidate("biotech", row)
    assert "near_binary_event" in state["suppression_reasons"]
    assert state["alertable_now"] is False

    plan_html = api._format_alert_plan_html(row)
    assert "Binäres Event in 2d" in plan_html
    assert "Stop schützt NICHT über ein Gap" in plan_html


def test_h2_t10_row_not_binary_gated_and_no_warning():
    """T-10: kein Binary-Gate, keine Warnzeile (Run-up-Fenster ist handelbar)."""
    row = _biotech_row(BPIQ_Catalysts=[{"name": "PDUFA", "days_until": 10}])
    state = api._classify_alert_candidate("biotech", row)
    assert "near_binary_event" not in state["suppression_reasons"]
    assert "Binäres Event" not in api._format_alert_plan_html(row)


def test_h2_bg_biotech_entries_are_api_owned(monkeypatch, tmp_path):
    """Biotech-Entry-Mails duerfen den API-Revalidator nicht umgehen."""
    sent = _bg_setup(monkeypatch, tmp_path, BIOTECH_CACHE, {
        "results": [
            _biotech_row("TMI2", Bio_Risk_Flags=["near_binary_event"],
                         BPIQ_Catalysts=[{"days_until": 2}]),
            _biotech_row("TPLU", BPIQ_Catalysts=[{"days_until": 10}]),
        ],
        "timestamp": time.time(),
    })
    bg_service._check_and_alert_scan_results("biotech", {"POLYGON_KEY": ""})
    assert sent == []


# ── M-4: Synthetik-Level ehrlich klassifizieren ─────────────────────────────

def test_m4_normalize_levels_synthetic_flag():
    """levels['synthetic'] feuert fuer Flag + Alt-Source-Prefix; native/estimated
    bleiben unveraendert (Gate-Kompatibilitaet — estimated-Sperre NICHT ausgeloest)."""
    flagged = _biotech_row(Trade_Setup_Synthetic=True)
    levels = normalize_alert_trade_levels(flagged, price_fallback=10.05)
    assert levels["synthetic"] is True
    assert levels["estimated"] is False, "M-4: synthetic darf das estimated-Gate nicht ausloesen"
    assert levels["native"] is True, "M-4: native bleibt True (minimal-invasive Variante)"

    legacy = _biotech_row(Trade_Setup_Source="biotech_daily_structure")
    assert normalize_alert_trade_levels(legacy, price_fallback=10.05)["synthetic"] is True

    plain_native = _bi_row("NATV")
    levels_native = normalize_alert_trade_levels(plain_native, price_fallback=10.05)
    assert levels_native["synthetic"] is False
    assert levels_native["native"] is True


def test_m4_synthetic_biotech_bg_row_cannot_bypass_api_owner(monkeypatch, tmp_path):
    """Auch die Biotech-Ausnahme wird ausschliesslich im API-Pfad versandt."""
    sent = _bg_setup(monkeypatch, tmp_path, BIOTECH_CACHE, {
        "results": [_biotech_row("SYNT", Trade_Setup_Synthetic=True,
                                 Trade_Setup_Source="biotech_daily_structure")],
        "timestamp": time.time(),
    })
    bg_service._check_and_alert_scan_results("biotech", {"POLYGON_KEY": ""})
    assert sent == []

    # Label-Quelle ist das Flag, nicht nur der Source-Prefix (api + bg):
    flag_only = _biotech_row("SYNT", Trade_Setup_Synthetic=True)
    assert "Struktur-Level" in api._format_alert_plan_html(flag_only)
    assert "Struktur-Level" in bg_service._format_alert_plan_html(flag_only)
    native_row = _biotech_row("NATV")
    assert "Struktur-Level" not in api._format_alert_plan_html(native_row)
    assert "Struktur-Level" not in bg_service._format_alert_plan_html(native_row)


def test_m4_estimated_levels_still_blocked(monkeypatch, tmp_path):
    """Estimated-Gate unveraendert: Row ohne native Stop/TP (Levels wuerden
    geschaetzt) bleibt im bg- UND api-Pfad geblockt."""
    row = _biotech_row("ESTM")
    for key in ("Entry", "StopLoss", "TP1", "TP2"):
        row.pop(key, None)
    sent = _bg_setup(monkeypatch, tmp_path, BIOTECH_CACHE, {
        "results": [row], "timestamp": time.time(),
    })
    bg_service._check_and_alert_scan_results("biotech", {"POLYGON_KEY": ""})
    assert sent == [], "M-4: estimated-Row wurde gemailt — Sperre ausgehebelt"

    levels = normalize_alert_trade_levels(dict(row), price_fallback=10.05)
    assert levels["estimated"] is True
    assert levels["synthetic"] is False
