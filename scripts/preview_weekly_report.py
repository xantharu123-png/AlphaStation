#!/usr/bin/env python3
"""Preview der Wochenreport-Mail mit echten Daten (AUDIT 2026-07-24).

Rendert bg_service._build_weekly_report_mail mit der echten
load_performance_summary — dieselbe Datenbasis und derselbe Code-Pfad wie
der Freitags-Job, aber OHNE Versand. Schreibt weekly_report_preview.html
ins Repo-Root und prueft, ob die T1/Kalibrier-Elemente (Ø R 50/50-Spalte,
Wilson-Fusstext) im Body landen.

Usage (Server, im App-Verzeichnis):
    venv/bin/python3 scripts/preview_weekly_report.py [--days 7]
    # HTML danach lokal ansehen:
    #   scp root@SERVER:/home/tradingbot/app/weekly_report_preview.html .

Exit 0 = Mail rendert mit allen neuen Elementen (oder leere Woche),
1 = Elemente fehlen trotz Signalen (Alt-Stand?) / Fehler.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Verdikt-Logik lebt zentral in modules/signal_tracker.py (wird auch vom
# Wochenreport-Alarm in bg_service genutzt) — hier nur noch konsumieren.
from modules.signal_tracker import scanner_verdict as _verdict  # noqa: E402


def _fmt_signed(value, digits=2):
    return f"{value:+.{digits}f}" if isinstance(value, (int, float)) else "—"


def _print_scanner_table(summary):
    """Scanner-Abrechnung direkt im Terminal (kein scp/Browser noetig)."""
    total = summary.get("total") or {}
    per_scanner = summary.get("per_scanner") or {}
    rows = sorted(per_scanner.items(),
                  key=lambda kv: float((kv[1] or {}).get("sum_r") or 0.0),
                  reverse=True)
    rows.append(("GESAMT", total))

    header = (f"{'Scanner':<18} {'Sig':>4} {'Entsch':>6} {'Hit%':>6} "
              f"{'KI95':>11} {'ØR':>7} {'ØR5050':>8} {'ΣR':>8}  Verdikt")
    print("\nScanner-Abrechnung (nach Σ R, GESAMT-Zeile unten):")
    print(header)
    print("-" * len(header))
    counts = {"behalten": 0, "beobachten": 0, "abschalten": 0}
    for name, bucket in rows:
        bucket = bucket or {}
        wilson = bucket.get("win_rate_wilson_95") or {}
        ki = (f"{wilson['lower_pct']:.0f}–{wilson['upper_pct']:.0f}%"
              if isinstance(wilson.get("lower_pct"), (int, float)) else "—")
        win = bucket.get("win_rate_pct")
        hit = f"{win:.0f}%" if isinstance(win, (int, float)) else "—"
        verdict, why = _verdict(bucket)
        if name != "GESAMT":
            counts[verdict] = counts.get(verdict, 0) + 1
        print(f"{name:<18} {bucket.get('signals', 0):>4} "
              f"{bucket.get('decided_signals', 0):>6} {hit:>6} {ki:>11} "
              f"{_fmt_signed(bucket.get('avg_r')):>7} "
              f"{_fmt_signed(bucket.get('avg_r_managed_50_50')):>8} "
              f"{_fmt_signed(bucket.get('sum_r'), 1):>8}  "
              f"{verdict} ({why})")
    print(f"\nVerdikt: {counts.get('behalten', 0)} behalten | "
          f"{counts.get('beobachten', 0)} beobachten | "
          f"{counts.get('abschalten', 0)} abschalten")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7,
                        help="Fenster wie der Freitags-Job (Default 7)")
    args = parser.parse_args()

    try:
        import bg_service
    except Exception as exc:  # noqa: BLE001 - Preview soll alles abfangen
        print(f"FAIL: bg_service nicht importierbar: {exc}")
        return 1
    if not getattr(bg_service, "load_performance_summary", None):
        print("FAIL: load_performance_summary nicht verfuegbar "
              "(modules/signal_tracker.py fehlt?)")
        return 1

    summary = bg_service.load_performance_summary(days=args.days)
    subject, body_html = bg_service._build_weekly_report_mail(summary)

    out = REPO_ROOT / "weekly_report_preview.html"
    out.write_text(body_html, encoding="utf-8")

    total = summary.get("total") or {}
    n_signals = total.get("signals", 0) or 0
    print(f"Betreff: {subject}")
    print(f"Signale: {n_signals} | entschieden: "
          f"{total.get('decided_signals', '?')} | "
          f"Ø R 50/50: {total.get('avg_r_managed_50_50')} | "
          f"Wilson: {total.get('win_rate_wilson_95')}")

    failures = []
    if n_signals > 0:
        # Nur pruefbar, wenn die Woche Tabellen rendert (keine Leere-Woche-Mail)
        for label, needle in (
            ("Ø R 50/50-Spalte", "Ø R 50/50"),
            ("Wilson-Fusstext", "KI = Wilson-95%-Intervall"),
            ("50/50-Semantik im Fusstext", "50/50-Managements"),
        ):
            ok = needle in body_html
            print(("  OK   " if ok else "  FAIL ") + label)
            if not ok:
                failures.append(label)
    else:
        print("  INFO leere Woche — Lebenszeichen-Mail gerendert "
              "(Tabellen-Check entfaellt; ggf. --days 30 probieren)")

    _print_scanner_table(summary)

    print(f"\nHTML geschrieben: {out}")
    print("Zum Ansehen: scp root@SERVER:"
          "/home/tradingbot/app/weekly_report_preview.html . "
          "und im Browser oeffnen")

    if failures:
        print(f"\nFAIL ({len(failures)}) — Mail rendert ohne die neuen "
              "Elemente (git pull + Restart?)")
        return 1
    print("\nPASS — Wochenreport rendert korrekt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
