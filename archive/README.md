# Archiv — nicht mehr produktiv, nur Referenz

Diese Dateien wurden am 24.07.2026 (Audit `AUDIT_GESAMT_2026-07-24.md`, Befund D2)
aus dem Projekt-Root hierher verschoben. Sie werden vom laufenden System
**nicht mehr referenziert** und dienen nur noch als historische Referenz.
Löschbar nach eigener Prüfung — die Git-Historie behält alles.

| Ordner | Inhalt | Warum archiviert |
|---|---|---|
| `legacy_streamlit/` | `scanner.py` (19k Zeilen Streamlit-UI), `start_service.sh`, `data_fetchers.py.bak` | Streamlit-UI ist seit FastAPI/React-Umstellung deaktiviert (siehe `deploy/DEPLOY_ANLEITUNG.md`). **Achtung:** `test_audit_fixes_bg.py` extrahiert per AST reine Helper aus `scanner.py` (Parität mit Kopien in `bg_service.py`) — Pfad ist angepasst, Datei bitte nicht löschen. `start_service.sh` startete noch die alte UI; Produktion läuft über systemd. |
| `backups/` | `backup_before_refactor.zip`, `cowork_backup_20260404.tar.gz`, `backup_pre_sanierung_20260610.tar.gz`, `backup_before_refactor/` | Manuelle Sicherungen aus Refactor-Phasen. Git ist die Sicherung. |
| `legacy_frontend/` | `index.html` (456 KB, Stand 04.04.) | Uralte monolithische Frontend-Variante; ausgeliefert wird ausschließlich `frontend/index.html` (siehe `api.py` `_FRONTEND_DIR`). |
| `design_mockups/` | UI-Design-Explorationen (HTML) | Entwurfsphase April 2026, längst überholt. |
| `dev_artifacts/` | `changes.patch`, `h`, `gitignore.txt`, Screenshots, `tmp_*` | Arbeitsreste ohne Produktivbezug. |
| `audits_old/` | Audit- und Handoff-Dokumente Jan–Apr 2026 | Fachlich überholt durch die Audits ab Juni 2026; im Root verbleiben die aktuellen. |
