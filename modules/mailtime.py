"""Dualer Mail-Zeitstempel: UTC + Europa/Berlin ohne tzdata-Dependency.

AUDIT 2026-07-31 (BHC-Mail): Mail-Bodies zeigten nur "... 14:09 UTC",
waehrend das Postfach die lokale Empfangszeit (16:09 MESZ) anzeigt — fuer
den Empfaenger nicht erkennbar derselbe Moment. Jede Mail traegt deshalb
beide Zeiten. Die EU-Sommerzeitregel (letzter Sonntag im Maerz/Oktober,
01:00 UTC) ist hier fest implementiert, damit weder Windows-Hosts ohne
tzdata-Paket noch minimale Server-Images eine Zeitzonen-Datenbank brauchen.
"""

from datetime import date, datetime, timedelta, timezone


def _last_sunday(year: int, month: int) -> date:
    """Letzter Sonntag des Monats (EU-DST-Stichtage: Maerz/Oktober)."""
    day = date(year, month, 31) if month == 10 else date(year, month, 31)
    # Maerz und Oktober haben beide 31 Tage — generisch vom Monatsende laufen.
    while day.weekday() != 6:  # 6 = Sonntag
        day -= timedelta(days=1)
    return day


def berlin_offset_hours(now_utc: datetime) -> int:
    """UTC-Offset von Europe/Berlin in Stunden (2 = MESZ, 1 = MEZ)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    start = datetime(now_utc.year, 3, _last_sunday(now_utc.year, 3).day, 1, 0, tzinfo=timezone.utc)
    end = datetime(now_utc.year, 10, _last_sunday(now_utc.year, 10).day, 1, 0, tzinfo=timezone.utc)
    return 2 if start <= now_utc < end else 1


def mail_timestamp_dual(now_utc: datetime = None) -> str:
    """Zeitstempel 'TT.MM.JJJJ HH:MM UTC / HH:MM MESZ' (bzw. MEZ im Winter)."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    offset = berlin_offset_hours(now)
    label = "MESZ" if offset == 2 else "MEZ"
    berlin = now + timedelta(hours=offset)
    return f'{now.strftime("%d.%m.%Y %H:%M")} UTC / {berlin.strftime("%H:%M")} {label}'
