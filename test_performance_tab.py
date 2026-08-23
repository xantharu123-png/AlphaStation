"""Tests fuer den Performance-Tab (Track-Record des Signal-Trackers).

API (api.py):
- Tab-Gate 'signal-performance' in _TAB_GATES registriert; Pro-Liste in
  SCANNER_TABS_BY_PLAN explizit erweitert (Elite/Trial = Kunden-Tabs, Basic
  bleibt gesperrt) — Map-Pin + Source-Pin analog test_audit_fixes_api H-11.
- days-Clamp 1..365 + Default 30 (auch bei direktem Funktionsaufruf, wo
  FastAPI-Query-Parsing nicht greift).
- Admin bleibt erlaubt (bestehende _require_admin-Logik, Muster aus
  test_signal_tracker_api); eingeloggter Nicht-Admin mit gueltigem Token
  kommt durch — den Plan-Check (Pro/Elite) erzwingt die
  commerce_auth_gate-Middleware via _TAB_GATES serverseitig.

Frontend (frontend/index.html, String-Pins wie test_frontend_unit_labels):
- Tab-Registrierung (allTabs + Mega-Menue 'Analyse' + Render-Switch).
- Spalten Hit-Rate / Ø R / Σ R + Zeilen-Toenung nach Σ R.
- Status-Badges (OPEN blau / TP gruen / STOP rot / EXPIRED grau),
  Hinweiszeile zur Auswertungs-Methodik, 7/30/90-Zeitraum-Schalter,
  403-Zustand "Performance-Ansicht benötigt Pro-Plan".
- AUDIT 2026-07-24: Ø R 50/50 (Managed-R), Wilson-KI an der Hit-Rate,
  Stichproben-Warnung (<30 entschieden) und 50/50-R bei letzten Signalen.
"""
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402


def _frontend_source() -> str:
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _evaluate_status_badge(signal: dict) -> dict:
    """Execute the real frontend classifier without compiling the whole SPA."""
    source = _frontend_source()
    match = re.search(
        r"(const statusBadge = \(sig\) => \{.*?^\s*\};)\r?\n\r?\n\s*const total",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "statusBadge classifier not found in frontend source"
    script = (
        f"{match.group(1)}\n"
        f"process.stdout.write(JSON.stringify(statusBadge({json.dumps(signal)})));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _evaluate_model_cell_evidence(segment: dict) -> dict:
    """Execute the real per-model-cell denominator classifier."""
    source = _frontend_source()
    match = re.search(
        r"(const modelCellEvidence = \(segment\) => \{.*?^\s*\};)\r?\n\r?\n\s*const total",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "modelCellEvidence classifier not found in frontend source"
    script = (
        f"{match.group(1)}\n"
        "process.stdout.write(JSON.stringify("
        f"modelCellEvidence({json.dumps(segment)})));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _evaluate_landing_scroll(target_exists: bool) -> dict:
    """Execute the real null-safe landing-page scroll helper."""
    source = _frontend_source()
    match = re.search(
        r"(const scrollToLandingSection = \(targetId\) => \{.*?^\};)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "scrollToLandingSection helper not found"
    script = (
        "const calls = []; const warnings = [];\n"
        "global.document = { getElementById: () => "
        + (
            "({ scrollIntoView: (options) => calls.push(options) })"
            if target_exists
            else "null"
        )
        + " };\n"
        "global.console = { warn: (...parts) => warnings.push(parts.join(' ')) };\n"
        f"{match.group(1)}\n"
        "let result = null; let error = null;\n"
        "try { result = scrollToLandingSection('features'); } "
        "catch (exc) { error = String(exc && exc.message || exc); }\n"
        "process.stdout.write(JSON.stringify({ result, error, calls, warnings }));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _evaluate_boot_runtime_error(root_has_children: bool) -> dict:
    """Execute boot.js with a rendered or still-empty root and emit one error."""
    boot_source = (ROOT / "frontend" / "boot.js").read_text(encoding="utf-8")
    child_count = 1 if root_has_children else 0
    script = f"""
const vm = require('vm');
const listeners = {{}};
const appended = [];
const logs = [];
const root = {{ children: Array.from({{ length: {child_count} }}) }};
const body = {{
  contains: (node) => appended.includes(node),
  appendChild: (node) => appended.push(node),
}};
const document = {{
  body,
  addEventListener: () => {{}},
  createElement: (tag) => ({{
    tag,
    id: '',
    style: {{}},
    children: [],
    appendChild(node) {{ this.children.push(node); }},
  }}),
  getElementById: (id) => id === 'root'
    ? root
    : appended.find((node) => node.id === id) || null,
  querySelector: () => null,
}};
const fakeConsole = {{ error: (...parts) => logs.push(parts.join(' ')) }};
const window = {{
  console: fakeConsole,
  addEventListener: (name, handler) => {{ listeners[name] = handler; }},
  setTimeout: () => {{}},
}};
const context = {{ window, document, console: fakeConsole }};
vm.createContext(context);
vm.runInContext({json.dumps(boot_source)}, context, {{ filename: 'boot.js' }});
listeners.error({{ message: 'runtime boom', target: window }});
process.stdout.write(JSON.stringify({{
  overlay: Boolean(document.getElementById('boot-error-overlay')),
  logs,
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _as_admin(monkeypatch, captured):
    monkeypatch.setattr(
        api, "_require_admin",
        lambda authorization: ({"email": "admin@x.com"}, "admin@x.com"),
    )
    monkeypatch.setattr(
        api, "load_performance_summary",
        lambda days=90, mature_only=True: captured.append((days, mature_only))
        or {"days": days, "signals": 5, "mature_only": mature_only},
    )


# ── 1) Gate-Map: Endpoint als Tab-Gate fuer Pro+Elite registriert ──


def test_tab_gate_registered_for_signal_performance():
    gates = dict(api._TAB_GATES)
    assert gates.get("/api/signal-performance") == "signal-performance"

    # No runtime mutation: plan access is defined once in modules/auth.py.
    api_src = (ROOT / "api.py").read_text(encoding="utf-8")
    assert 'SCANNER_TABS_BY_PLAN["pro"].append("signal-performance")' not in api_src

    if api.HAS_AUTH:
        assert "signal-performance" in api.SCANNER_TABS_BY_PLAN["pro"]
        assert "signal-performance" in api.SCANNER_TABS_BY_PLAN["elite"]
        assert "signal-performance" in api.SCANNER_TABS_BY_PLAN["trial"]
        assert "signal-performance" not in api.SCANNER_TABS_BY_PLAN["basic"]


# ── 2) days-Clamp 1..365 + Default 30 ──


def test_days_query_clamped_to_1_and_365(monkeypatch):
    captured = []
    _as_admin(monkeypatch, captured)

    api.api_signal_performance(days=0, authorization="Bearer admin-token")
    api.api_signal_performance(days=9999, authorization="Bearer admin-token")
    api.api_signal_performance(days=90, authorization="Bearer admin-token")

    assert captured == [(1, True), (365, True), (90, True)]


def test_days_default_is_30(monkeypatch):
    captured = []
    _as_admin(monkeypatch, captured)

    # Direkter Aufruf ohne days: FastAPI uebergibt das Query-Objekt nicht,
    # der Endpoint faellt kontrolliert auf den Default 30 zurueck.
    api.api_signal_performance(authorization="Bearer admin-token")
    assert captured == [(30, True)]

    # HTTP-Seite: Query-Default ist ebenfalls 30.
    sig = inspect.signature(api.api_signal_performance)
    assert sig.parameters["days"].default.default == 30
    assert sig.parameters["mature_only"].default.default is True


# ── 3) Zugriff: Admin weiter erlaubt, eingeloggter User kommt durch ──


def test_admin_remains_allowed(monkeypatch):
    captured = []
    _as_admin(monkeypatch, captured)

    result = api.api_signal_performance(days=7, authorization="Bearer admin-token")

    assert result["days"] == 7
    assert result["signals"] == 5


def test_logged_in_non_admin_allowed_anonymous_stays_403(monkeypatch):
    monkeypatch.setattr(
        api, "load_performance_summary",
        lambda days=90, mature_only=True: {
            "days": days,
            "signals": 2,
            "mature_only": mature_only,
        },
    )
    monkeypatch.setattr(api, "HAS_AUTH", True)
    # Gueltiger Token eines Nicht-Admins: _require_admin lehnt ab (403),
    # der Fallback akzeptiert den eingeloggten User — Pro/Elite-Gating
    # passiert serverseitig in der Middleware (_TAB_GATES).
    monkeypatch.setattr(
        api, "verify_token",
        lambda token: {"email": "pro-user@example.com", "plan": "pro"} if token == "pro-token" else None,
        raising=False,
    )

    result = api.api_signal_performance(days=30, authorization="Bearer pro-token")
    assert result["days"] == 30

    # Ohne Token bleibt der bestehende 403-Kontrakt erhalten.
    with pytest.raises(api.HTTPException) as exc_info:
        api.api_signal_performance(days=30, authorization=None)
    assert exc_info.value.status_code == 403


# ── 4) Frontend: Tab-Registrierung ──


def test_frontend_signal_performance_tab_registered():
    source = _frontend_source()

    assert "{ id: 'signal-performance', label: 'Performance' }" in source
    assert "Track-Record: Hit-Rate & R-Bilanz aller Signale" in source
    assert "activeTab === 'signal-performance' && <SignalPerformanceTab" in source
    assert "function SignalPerformanceTab" in source
    assert "mature_only=${matureOnly}" in source
    assert "Reife Kohorte" in source
    assert "Alle vorläufig" in source
    assert "Performance pro kausaler Modellzelle" in source
    assert "Horizont / Regime" in source
    assert "fill_evidence_mode" in source


def test_frontend_all_public_copy_is_paper_only():
    """Public product copy must not promise executable live broker orders."""
    source = _frontend_source()

    for stale_claim in (
        "Full Auto (Orders gehen live)",
        "Bracket Order via Broker-Anbindung",
        "Broker</div><div style={{ fontSize: 12, color: '#64748b' }}>Integration",
        "Semi / Full",
        "automatisiert oder semi-automatisiert Orders über eine Broker-Anbindung platzieren kann",
        "Automatisiert oder semi-automatisiert platzierte Orders",
        "führt Orders basierend auf algorithmischen Signalen aus",
        "Full-Auto-Modus",
        "Semi-Auto-Modus",
    ):
        assert stale_claim not in source

    for paper_only_truth in (
        "Paper-/Simulationsmodus",
        "Echte Broker-Orders und Live-Trading sind technisch blockiert.",
        "Simulierter Bracket-Order-Plan",
        "Paper Broker",
        "ausschließlich eine Paper-Broker-Simulation",
        "übermittelt keine echten Orders",
    ):
        assert paper_only_truth in source


def test_frontend_landing_scroll_targets_are_unique_and_null_safe():
    """Every public scroll CTA reaches one stable ID and missing IDs never crash."""
    source = _frontend_source()

    for target in ("features", "pricing", "demo-preview"):
        section = re.search(
            rf'<section\s+id="{re.escape(target)}"[^>]*>',
            source,
        )
        assert section is not None, target
        assert "{...{id:" not in section.group(0), target
        assert source.count(f'id="{target}"') == 1, target
        assert f"scrollToLandingSection('{target}')" in source

    assert _evaluate_landing_scroll(True) == {
        "result": True,
        "error": None,
        "calls": [{"behavior": "smooth", "block": "start"}],
        "warnings": [],
    }
    missing = _evaluate_landing_scroll(False)
    assert missing["result"] is False
    assert missing["error"] is None
    assert missing["calls"] == []
    assert len(missing["warnings"]) == 1


def test_frontend_copy_never_turns_backtests_or_paper_execution_into_live_release():
    """Backtests and every Auto-Trader label remain explicitly Paper-only."""
    source = _frontend_source()

    for misleading in (
        "Live moeglich mit Risk-Limit",
        "Backtest Freigabe",
        "Backtests & Live-Healthchecks",
        "Live-Ausführungskontrolle",
        "Automatisierte Trade-Ausführung",
        "optional für Broker-Anbindungen",
    ):
        assert misleading not in source

    assert re.search(r"(?<!Paper )Auto-Trader \(Elite\)", source) is None

    for paper_truth in (
        "Paper Auto-Trader (Elite)",
        "Backtest Paper-Evidenz",
        "Paper-Testkandidat mit Risk-Limit",
        "Backtests & Paper-Healthchecks",
        "Automatisierte Paper-Ausführung",
        "optional für Paper-Broker-Anbindungen",
    ):
        assert paper_truth in source


def test_boot_errors_are_fatal_only_until_the_app_has_rendered():
    """Late runtime errors stay logged without covering a working application."""
    boot_failure = _evaluate_boot_runtime_error(False)
    assert boot_failure["overlay"] is True
    assert any("runtime boom" in entry for entry in boot_failure["logs"])

    late_failure = _evaluate_boot_runtime_error(True)
    assert late_failure["overlay"] is False
    assert any("runtime boom" in entry for entry in late_failure["logs"])


# ── 5) Frontend: Tabelle (Hit-Rate / Ø R / Σ R) + Zeilen-Toenung ──


def test_frontend_performance_table_columns_and_row_tinting():
    source = _frontend_source()

    for col in (
        ">Scanner</th>", ">Signale</th>", ">TP1</th>", ">TP2</th>",
        ">Stop</th>", ">Offen</th>", ">Hit-Rate</th>", ">Ø R</th>",
        ">Σ R</th>", ">Alerts/Tag</th>",
    ):
        assert col in source, col

    # Zeilen mit Σ R > 0 gruen, < 0 rot getoent
    assert "(s.sum_r ?? 0) > 0 ? 'bg-green-50' : (s.sum_r ?? 0) < 0 ? 'bg-red-50'" in source


# ── 6) Frontend: Status-Badges, Hinweiszeile, 7/30/90, 403-Zustand ──


def test_frontend_status_badges_hint_period_switch_and_403():
    source = _frontend_source()

    assert "{ label: 'OPEN', cls: 'bg-blue-100 text-blue-700' }" in source
    assert "{ label: 'TP2', cls: 'bg-green-100 text-green-700' }" in source
    stop_badge = _evaluate_status_badge({"status": "STOP_HIT"})
    assert stop_badge["label"] == "STOP"
    assert stop_badge["cls"] == "bg-red-100 text-red-700"
    expired_badge = _evaluate_status_badge({"status": "EXPIRED"})
    assert expired_badge["label"] == "EXPIRED"
    assert expired_badge["cls"] == "bg-gray-100 text-gray-600"

    assert "Standard ist die vollständig beobachtete Versandkohorte." in source
    assert "vollständigen chronologischen Intervallen" in source

    assert "[7, 30, 90].map" in source
    assert "const [days, setDays] = useState(30);" in source
    assert "Performance-Ansicht benötigt Pro-Plan" in source


@pytest.mark.parametrize(
    ("status", "expected_label", "expected_class"),
    [
        ("STOP_HIT", "STOP", "bg-red-100 text-red-700"),
        ("EXPIRED", "EXPIRED", "bg-gray-100 text-gray-600"),
    ],
)
def test_terminal_negative_status_stays_negative_after_prior_tp1(
    status, expected_label, expected_class
):
    """A prior TP1 touch is history, never a green terminal outcome."""
    badge = _evaluate_status_badge(
        {"status": status, "tp1_hit_at": "2026-08-11T15:05:00+00:00"}
    )

    assert badge["label"] == expected_label
    assert badge["cls"] == expected_class
    assert badge["progressLabel"] == "TP1 zuvor erreicht"
    assert "badge.progressLabel" in _frontend_source()


def test_frontend_model_cells_show_resolved_and_unresolved_denominators():
    """A cell with unresolved evidence is visible and explicitly blocked."""
    evidence = _evaluate_model_cell_evidence(
        {
            "managed_be_decided_signals": 30,
            "managed_be_unresolved": 4,
            "managed_be_sample_reliable": True,
            "managed_be_win_rate_pct": 60.0,
            "managed_be_win_rate_wilson_95": {
                "lower_pct": 42.0,
                "upper_pct": 75.0,
            },
            "avg_r_managed_50_50_be": 0.45,
            "sum_r_managed_50_50_be": 13.5,
        }
    )

    assert evidence == {
        "family": "managed_be",
        "familyLabel": "Managed 50/50+BE",
        "resolved": 30,
        "unresolved": 4,
        "winRatePct": 60.0,
        "winRateWilson95": {"lower_pct": 42.0, "upper_pct": 75.0},
        "avgR": 0.45,
        "sumR": 13.5,
        "sampleReliable": True,
        "metricsComplete": True,
        "releaseAllowed": False,
        "denominatorLabel": "30 resolved / 4 unresolved",
        "releaseLabel": "Nicht freigegeben - 4 unresolved",
    }
    source = _frontend_source()
    assert "const cellEvidence = modelCellEvidence(s);" in source
    assert "{cellEvidence.denominatorLabel}" in source
    assert "{cellEvidence.releaseLabel}" in source


def test_frontend_model_cell_uses_be_fields_when_managed_fields_are_absent():
    """The BE denominator is the safe fallback for transitional summaries."""
    evidence = _evaluate_model_cell_evidence(
        {
            "be_decided_signals": 9,
            "be_unresolved": 2,
            "be_sample_reliable": False,
            "be_win_rate_pct": 55.6,
            "be_win_rate_wilson_95": {"lower_pct": 25.0, "upper_pct": 80.0},
            "avg_r_be": 0.1,
            "sum_r_be": 0.9,
            "decided_signals": 99,
        }
    )

    assert evidence == {
        "family": "be",
        "familyLabel": "BE-only",
        "resolved": 9,
        "unresolved": 2,
        "winRatePct": 55.6,
        "winRateWilson95": {"lower_pct": 25.0, "upper_pct": 80.0},
        "avgR": 0.1,
        "sumR": 0.9,
        "sampleReliable": False,
        "metricsComplete": True,
        "releaseAllowed": False,
        "denominatorLabel": "9 resolved / 2 unresolved",
        "releaseLabel": "Nicht freigegeben - 2 unresolved",
    }


def test_frontend_model_cell_partial_managed_payload_does_not_mix_denominators():
    """A partial managed cohort must not hide unresolved BE evidence."""
    evidence = _evaluate_model_cell_evidence(
        {
            "managed_be_unresolved": 0,
            "managed_be_win_rate_pct": 99.0,
            "avg_r_managed_50_50_be": 9.9,
            "be_decided_signals": 9,
            "be_unresolved": 0,
            "be_sample_reliable": True,
            "be_win_rate_pct": 44.4,
            "be_win_rate_wilson_95": {"lower_pct": 18.0, "upper_pct": 73.0},
            "avg_r_be": -0.2,
            "sum_r_be": -1.8,
            "win_rate_pct": 88.8,
        }
    )

    assert evidence == {
        "family": "be",
        "familyLabel": "BE-only",
        "resolved": 9,
        "unresolved": 0,
        "winRatePct": 44.4,
        "winRateWilson95": {"lower_pct": 18.0, "upper_pct": 73.0},
        "avgR": -0.2,
        "sumR": -1.8,
        "sampleReliable": True,
        "metricsComplete": True,
        "releaseAllowed": True,
        "denominatorLabel": "9 resolved / 0 unresolved",
        "releaseLabel": "",
    }


def test_frontend_model_cell_be_fallback_respects_sample_reliability():
    """Reliability belongs to the same denominator pair selected for display."""
    evidence = _evaluate_model_cell_evidence(
        {
            "be_decided_signals": 30,
            "be_unresolved": 0,
            "be_sample_reliable": False,
            "be_win_rate_pct": 50.0,
            "be_win_rate_wilson_95": {"lower_pct": 33.0, "upper_pct": 67.0},
            "avg_r_be": 0.2,
            "sum_r_be": 6.0,
        }
    )

    assert evidence == {
        "family": "be",
        "familyLabel": "BE-only",
        "resolved": 30,
        "unresolved": 0,
        "winRatePct": 50.0,
        "winRateWilson95": {"lower_pct": 33.0, "upper_pct": 67.0},
        "avgR": 0.2,
        "sumR": 6.0,
        "sampleReliable": False,
        "metricsComplete": True,
        "releaseAllowed": False,
        "denominatorLabel": "30 resolved / 0 unresolved",
        "releaseLabel": "Nicht freigegeben - kleine Stichprobe",
    }


def test_frontend_model_cell_missing_unresolved_fails_closed():
    """A legacy row may not silently turn unknown evidence into zero gaps."""
    evidence = _evaluate_model_cell_evidence({"decided_signals": 12})

    assert evidence == {
        "family": "unresolved",
        "familyLabel": "Evidenzfamilie unbekannt",
        "resolved": 12,
        "unresolved": None,
        "winRatePct": None,
        "winRateWilson95": None,
        "avgR": None,
        "sumR": None,
        "sampleReliable": None,
        "metricsComplete": False,
        "releaseAllowed": False,
        "denominatorLabel": "12 resolved / unresolved unbekannt",
        "releaseLabel": "Nicht freigegeben - unresolved unbekannt",
    }


@pytest.mark.parametrize(
    ("reliability_present", "reliability", "expected_label"),
    [
        (False, None, "Nicht freigegeben - Reliability unbekannt"),
        (True, None, "Nicht freigegeben - Reliability unbekannt"),
        (True, False, "Nicht freigegeben - kleine Stichprobe"),
    ],
)
def test_frontend_model_cell_reliability_must_be_explicitly_true(
    reliability_present, reliability, expected_label
):
    """Missing, null and false reliability all block release."""
    segment = {
        "managed_be_decided_signals": 30,
        "managed_be_unresolved": 0,
        "managed_be_win_rate_pct": 60.0,
        "managed_be_win_rate_wilson_95": {
            "lower_pct": 42.0,
            "upper_pct": 75.0,
        },
        "avg_r_managed_50_50_be": 0.45,
        "sum_r_managed_50_50_be": 13.5,
    }
    if reliability_present:
        segment["managed_be_sample_reliable"] = reliability

    evidence = _evaluate_model_cell_evidence(segment)

    assert evidence["sampleReliable"] is reliability
    assert evidence["releaseAllowed"] is False
    assert evidence["releaseLabel"] == expected_label


def test_frontend_model_cell_consumers_use_only_the_selected_family():
    """Every displayed model metric must come from modelCellEvidence."""
    source = _frontend_source()
    table = re.search(
        r"Performance pro kausaler Modellzelle.*?</table>",
        source,
        flags=re.DOTALL,
    )
    assert table is not None
    rendered = table.group(0)

    for selected_metric in (
        "cellEvidence.familyLabel",
        "cellEvidence.denominatorLabel",
        "cellEvidence.releaseLabel",
        "cellEvidence.winRatePct",
        "cellEvidence.winRateWilson95",
        "cellEvidence.avgR",
        "cellEvidence.sumR",
        "cellEvidence.sampleReliable",
    ):
        assert selected_metric in rendered

    for mixed_metric in (
        "s.managed_be_win_rate_pct",
        "s.win_rate_pct",
        "s.managed_be_win_rate_wilson_95",
        "s.managed_be_sample_reliable",
        "s.avg_r_managed_50_50_be",
        "s.sum_r_managed_50_50_be",
    ):
        assert mixed_metric not in rendered



# ── 7) Frontend: Managed-R, Wilson-KI, Stichproben-Warnung (AUDIT 2026-07-24) ──


def test_frontend_managed_r_wilson_and_reliability_visible():
    source = _frontend_source()

    # Kopf-Karte + Scanner-Spalte Ø R 50/50
    assert "'Ø R 50/50'" in source
    assert ">Ø R 50/50</th>" in source
    assert "avg_r_managed_50_50" in source

    # Wilson-KI an der Hit-Rate (Kopf-Hint + Scanner-Zelle)
    assert "win_rate_wilson_95" in source
    assert "95%-KI:" in source

    # Stichproben-Warnung bei < 30 entschiedenen Signalen
    assert "sample_reliable === false" in source
    assert "decided_signals" in source

    # 50/50-R bei den letzten Signalen + Methodik-Fusszeile
    assert "50/50: {fmtR(sig.r_managed_50_50)}" in source
    assert "Wilson-95%-Konfidenzintervall" in source
    assert "managed_be_unresolved" in source
    assert "sperren die Managed-BE-Freigabe" in source
    assert "zeigten MFE >= +1R" in source
    assert "trackerbasierte BE-Gegenrechnung" in source
    assert "seit 30.07. im Tracker" in source
    assert "erreichten +1R" not in source
    assert "Einstand-Regel ab +1R (live)" not in source
