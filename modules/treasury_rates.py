"""Treasury-Yields-Block (FRED DGS2/DGS10/DGS30) — Annotation, kein Gate.

Mess-First (2026-07-30): Der Block liefert Zinsniveau und -dynamik als
Kontext-Annotation fuer Market-Context und Signal-Tracker. Er aendert
bewusst KEIN Scoring und KEIN Gating — ob das Zins-Regime die eigenen
Signale beeinflusst, wird erst aus dem Tracker-Datenbestand ausgewertet
(Phase 2: Regime-Split, sobald n >= 100 entschiedene Signale pro Zelle).

Datenquelle: FRED fredgraph.csv — oeffentlich, kein API-Key noetig,
taegliche Konstantmaturity-Yields in Prozent (Wochenenden/Feiertage = ".").
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2,DGS10,DGS30"
SERIES_IDS = ("DGS2", "DGS10", "DGS30")
MAX_CACHE_OBS = 90  # ~4 Monate Handelstage reichen fuer 20d-Fenster + Puffer

# Erstkalibrierung 2026-07-30, 20-Handelstage-Aenderung der DGS10 in bp.
# +25bp/20d entspricht dem historischen ~schnellen Quartalsmove (vgl. Jul 2026:
# +30bp/Monat). NICHT als Gate verdrahtet — nur Label.
REGIME_MOVE_BP = 10.0
REGIME_FAST_BP = 25.0
STALE_AFTER_DAYS = 4  # FRED hinkt 1 Werktag; >4 Tage = Feiertags-/Abrufproblem

SeriesMap = Dict[str, List[Tuple[str, float]]]


def parse_fred_csv(csv_text: str) -> SeriesMap:
    """Parst fredgraph.csv (DATE + eine Spalte je Serie) in {sid: [(date, value)]}.

    Robust gegen: '.' (Feiertage), leere Zellen, Gross/Klein-Header,
    unsortierte Zeilen. Rueckgabe je Serie aufsteigend sortiert, auf die
    letzten MAX_CACHE_OBS Beobachtungen gekuerzt.
    """
    series: SeriesMap = {sid: [] for sid in SERIES_IDS}
    if not csv_text or not csv_text.strip():
        return series
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            obs_date = (row.get("DATE") or row.get("date") or "").strip()
            if len(obs_date) != 10:
                continue
            for sid in SERIES_IDS:
                raw = (row.get(sid) or "").strip()
                if not raw or raw == ".":
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                series[sid].append((obs_date, value))
    except Exception as exc:
        logger.warning("parse_fred_csv: Fehler (%s) — nutze bisherige Teilserien", exc)
    for sid in SERIES_IDS:
        series[sid] = sorted(series[sid], key=lambda item: item[0])[-MAX_CACHE_OBS:]
    return series


def fetch_fred_csv(url: str = FRED_CSV_URL, timeout: int = 20) -> str:
    """Laedt die FRED-CSV. Wirft bei Netzwerk-/HTTP-Fehlern (Aufrufer fängt)."""
    import requests

    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "AlphaStation/1.0 (treasury rates context)"},
    )
    resp.raise_for_status()
    return resp.text


def _change_bp(obs: List[Tuple[str, float]], days: int) -> Optional[float]:
    """Aenderung in Basispunkten ueber `days` Beobachtungsabstand (Handelstage).

    None, wenn nicht genuegend Beobachtungen — ehrlich statt Schaetzwert.
    """
    if len(obs) < days + 1:
        return None
    return round((obs[-1][1] - obs[-1 - days][1]) * 100.0, 1)


def _regime_from_change(change_20d: Optional[float]) -> Tuple[Optional[str], str]:
    """Regime-Label aus der 20d-Aenderung der DGS10 (Schwellen = Erstkalibrierung)."""
    if change_20d is None:
        return None, "weniger als 21 Beobachtungen — keine 20d-Aenderung"
    if change_20d >= REGIME_FAST_BP:
        return "rising_fast", f"DGS10 20d {change_20d:+.0f}bp >= +{REGIME_FAST_BP:.0f}bp"
    if change_20d >= REGIME_MOVE_BP:
        return "rising", f"DGS10 20d {change_20d:+.0f}bp >= +{REGIME_MOVE_BP:.0f}bp"
    if change_20d <= -REGIME_FAST_BP:
        return "falling_fast", f"DGS10 20d {change_20d:+.0f}bp <= -{REGIME_FAST_BP:.0f}bp"
    if change_20d <= -REGIME_MOVE_BP:
        return "falling", f"DGS10 20d {change_20d:+.0f}bp <= -{REGIME_MOVE_BP:.0f}bp"
    return "stable", f"DGS10 20d {change_20d:+.0f}bp innerhalb +/-{REGIME_MOVE_BP:.0f}bp"


def build_rates_block(
    series: Optional[SeriesMap] = None,
    *,
    source: str = "live",
    fetch_error: Optional[str] = None,
    today=None,
) -> Dict:
    """Baut den Zins-Block. Wirft nie; Missing-Block bei Fehlern/leeren Daten.

    Felder (status == 'ok'):
      as_of, source, stale_days, stale, dgs2/dgs10/dgs30 (Level %),
      change_5d_bp / change_20d_bp (DGS10), dgs30_change_20d_bp,
      curve_10s2s_bp, curve_30s10s_bp, regime (+regime_basis), thresholds.
    """
    if fetch_error:
        return {"status": "missing", "reason": f"FRED-Abruf fehlgeschlagen: {fetch_error}", "regime": None}
    series = series or {}
    dgs10 = list(series.get("DGS10") or [])
    if not dgs10:
        return {"status": "missing", "reason": "Keine FRED-DGS10-Beobachtungen verfuegbar", "regime": None}

    def _latest(sid: str) -> Optional[float]:
        obs = series.get(sid) or []
        return obs[-1][1] if obs else None

    as_of = dgs10[-1][0]
    change_5d = _change_bp(dgs10, 5)
    change_20d = _change_bp(dgs10, 20)
    dgs30 = list(series.get("DGS30") or [])
    change_20d_30 = _change_bp(dgs30, 20) if dgs30 else None
    regime, regime_basis = _regime_from_change(change_20d)

    stale_days: Optional[int] = None
    try:
        ref = today if today is not None else datetime.now(timezone.utc).date()
        if isinstance(ref, datetime):
            ref = ref.date()
        if isinstance(ref, str):
            ref = date.fromisoformat(ref)
        stale_days = (ref - date.fromisoformat(as_of)).days
    except Exception:
        stale_days = None

    d2, d10, d30 = _latest("DGS2"), _latest("DGS10"), _latest("DGS30")
    return {
        "status": "ok",
        "as_of": as_of,
        "source": source,
        "stale_days": stale_days,
        "stale": bool(stale_days is not None and stale_days > STALE_AFTER_DAYS),
        "dgs2": d2,
        "dgs10": d10,
        "dgs30": d30,
        "change_5d_bp": change_5d,
        "change_20d_bp": change_20d,
        "dgs30_change_20d_bp": change_20d_30,
        "curve_10s2s_bp": round((d10 - d2) * 100.0, 1) if d2 is not None and d10 is not None else None,
        "curve_30s10s_bp": round((d30 - d10) * 100.0, 1) if d10 is not None and d30 is not None else None,
        "regime": regime,
        "regime_basis": regime_basis,
        "thresholds": {
            "move_bp": REGIME_MOVE_BP,
            "fast_bp": REGIME_FAST_BP,
            "note": "Erstkalibrierung 2026-07-30 — Annotation, kein Gate",
        },
    }
