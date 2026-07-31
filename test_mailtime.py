"""Tests fuer modules/mailtime — dualer Mail-Zeitstempel (UTC + Berlin).

AUDIT 2026-07-31: Mail-Bodies zeigten nur UTC, das Postfach zeigt Lokalzeit —
fuer den Empfaenger nicht erkennbar derselbe Moment (BHC-Mail 14:09 UTC =
16:09 MESZ). Zusaetzlich trugen bg_service-Mails UTC-Uhrzeit mit falschem
"CET"-Label.
"""

from datetime import datetime, timezone

from modules.mailtime import berlin_offset_hours, mail_timestamp_dual


def test_offset_mesz_im_sommer():
    # 31.07.2026 liegt klar in der Sommerzeit (MESZ = UTC+2).
    now = datetime(2026, 7, 31, 14, 9, tzinfo=timezone.utc)
    assert berlin_offset_hours(now) == 2


def test_offset_mez_im_winter():
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert berlin_offset_hours(now) == 1


def test_offset_umstellung_maerz():
    # Letzter Sonntag im Maerz 2026 = 29.03., 01:00 UTC.
    assert berlin_offset_hours(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)) == 1
    assert berlin_offset_hours(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == 2


def test_offset_umstellung_oktober():
    # Letzter Sonntag im Oktober 2026 = 25.10., 01:00 UTC.
    assert berlin_offset_hours(datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc)) == 2
    assert berlin_offset_hours(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == 1


def test_dual_timestamp_sommer_format():
    # BHC-Fall: 14:09 UTC muss als 16:09 MESZ erkennbar sein.
    now = datetime(2026, 7, 31, 14, 9, tzinfo=timezone.utc)
    assert mail_timestamp_dual(now) == "31.07.2026 14:09 UTC / 16:09 MESZ"


def test_dual_timestamp_winter_format():
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert mail_timestamp_dual(now) == "15.01.2026 12:00 UTC / 13:00 MEZ"


def test_dual_timestamp_toleriert_naive_utc():
    # Legacy-Aufrufer ohne tzinfo werden als UTC behandelt (kein Crash).
    now = datetime(2026, 7, 31, 14, 9)
    assert mail_timestamp_dual(now) == "31.07.2026 14:09 UTC / 16:09 MESZ"


def test_dual_timestamp_ohne_argument_laeuft():
    stamp = mail_timestamp_dual()
    assert " UTC / " in stamp
    assert ("MESZ" in stamp) or ("MEZ" in stamp)


def test_api_alias_deckt_modul_ab():
    # api._mail_timestamp_dual muss dasselbe Format liefern (Import-Pfad).
    import api

    now = datetime(2026, 7, 31, 14, 9, tzinfo=timezone.utc)
    assert api._mail_timestamp_dual(now) == "31.07.2026 14:09 UTC / 16:09 MESZ"


def test_bg_service_alias_deckt_modul_ab():
    import bg_service

    now = datetime(2026, 7, 31, 14, 9, tzinfo=timezone.utc)
    assert bg_service._mail_timestamp_dual(now) == "31.07.2026 14:09 UTC / 16:09 MESZ"


# ── Zeitstempel-Guard (AUDIT 2026-07-31) ─────────────────────────────────────
# Verhindert Regression: Mail-Bodies mit nacktem UTC- oder falschem CET-Label.
# Erlaubt ist ausschliesslich _mail_timestamp_dual() (dual UTC/MESZ) — die
# einzige Ausnahme sind die lokalen Fallback-Definitionen selbst.

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_REPO = Path(__file__).resolve().parent
_DIRECT_STAMP = re.compile(r'datetime\.now\(\)\.strftime\(["\']%d\.\%m\.\%Y %H:%M["\']\)')


def _offending_lines(filename: str) -> list:
    lines = ( _REPO / filename ).read_text(encoding="utf-8").splitlines()
    hits = []
    for lineno, line in enumerate(lines, start=1):
        if _DIRECT_STAMP.search(line):
            hits.append(f"{filename}:{lineno}: {line.strip()[:90]}")
        if "} CET" in line or " CET<" in line:
            hits.append(f"{filename}:{lineno} (CET-Label): {line.strip()[:90]}")
    return hits


def test_no_direct_utc_or_cet_stamps_in_mail_builders():
    hits = _offending_lines("api.py") + _offending_lines("bg_service.py")
    assert not hits, (
        "Direkte Mail-Zeitstempel gefunden (nur _mail_timestamp_dual ist erlaubt):\n"
        + "\n".join(hits)
    )
