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
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402


def _frontend_source() -> str:
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _as_admin(monkeypatch, captured):
    monkeypatch.setattr(
        api, "_require_admin",
        lambda authorization: ({"email": "admin@x.com"}, "admin@x.com"),
    )
    monkeypatch.setattr(
        api, "load_performance_summary",
        lambda days=90: captured.append(days) or {"days": days, "signals": 5},
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

    assert captured == [1, 365, 90]


def test_days_default_is_30(monkeypatch):
    captured = []
    _as_admin(monkeypatch, captured)

    # Direkter Aufruf ohne days: FastAPI uebergibt das Query-Objekt nicht,
    # der Endpoint faellt kontrolliert auf den Default 30 zurueck.
    api.api_signal_performance(authorization="Bearer admin-token")
    assert captured == [30]

    # HTTP-Seite: Query-Default ist ebenfalls 30.
    sig = inspect.signature(api.api_signal_performance)
    assert sig.parameters["days"].default.default == 30


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
        lambda days=90: {"days": days, "signals": 2},
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
    assert "{ label: 'STOP', cls: 'bg-red-100 text-red-700' }" in source
    assert "{ label: 'EXPIRED', cls: 'bg-gray-100 text-gray-600' }" in source

    assert (
        "Auswertung: First-Touch konservativ (Stop vor Ziel), TP1 = halbe Position. Crypto stündlicher Spot-Check."
        in source
    )

    assert "[7, 30, 90].map" in source
    assert "const [days, setDays] = useState(30);" in source
    assert "Performance-Ansicht benötigt Pro-Plan" in source
