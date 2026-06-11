"""Waechter-Test: bg_service.py MUSS als Skript einen Entrypoint haben.

Hintergrund (Live-Vorfall 11.06.2026): Der `if __name__ == "__main__"`-Block
wurde von einer Fremd-KI entfernt (Commit 2a78cb5). `python3 bg_service.py`
endete seither nach dem Import mit Exit 0 -> systemd-Restart-Schleife
("Deactivated successfully", Counter > 3500) -> KEIN Background-Scan und
KEIN bg-Alert lief jemals, waehrend `import bg_service` (alle Smoke-Tests!)
weiter funktionierte. Diese Tests verhindern eine Wiederholung.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg_service.py")


def test_bg_service_has_main_entrypoint_calling_run_service():
    src = open(BG_PATH, encoding="utf-8").read()
    assert 'if __name__ == "__main__":' in src, (
        "bg_service.py hat keinen __main__-Block — systemd wuerde wieder in die "
        "Exit-0-Restart-Schleife laufen!"
    )
    main_block = src.split('if __name__ == "__main__":', 1)[1]
    assert "run_service()" in main_block, "__main__-Block ruft run_service() nicht auf"
    assert "run_once()" in main_block, "__main__-Block bietet 'once' nicht an"


def test_bg_service_executes_main_block_as_script():
    """Subprocess-Beweis: Der Block LAEUFT (nicht nur vorhanden) — unbekanntes
    Kommando zeigt die Hilfe statt still mit dem Import zu enden."""
    result = subprocess.run(
        [sys.executable, BG_PATH, "zeige_hilfe_bitte"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=os.path.dirname(BG_PATH),
    )
    combined = result.stdout + result.stderr
    assert "Befehle: start | once" in combined, (
        "bg_service.py fuehrt den __main__-Block nicht aus — vermutlich wieder "
        "entfernt/verschoben"
    )


def test_default_without_argument_targets_run_service():
    """Source-Pin: ohne Argument ist 'start' der Default (die systemd-Unit
    startet teils ohne Argument)."""
    src = open(BG_PATH, encoding="utf-8").read()
    main_block = src.split('if __name__ == "__main__":', 1)[1]
    assert 'else "start"' in main_block
