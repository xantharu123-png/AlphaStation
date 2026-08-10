# Alpha Station - PC-Wechsel und Projektuebergabe

**Erstellt:** 11. August 2026

**Repository:** `https://github.com/xantharu123-png/AlphaStation`

**Produktionsserver:** `root@178.104.69.209`, `/home/tradingbot/app`

**Code-Baseline vor dem Dokumenten-Commit:** `1bd9a48` auf `main`

Dieses Dokument bringt einen neuen Windows-PC reproduzierbar auf denselben
Entwicklungsstand. Es ersetzt weder Secrets-Backup noch Server-Backup.

---

## 1. Lesereihenfolge auf dem neuen PC

1. `PROJEKTBIBEL.md` - verbindliche Regeln.
2. `PROJEKTHANDBUCH.md` - Architektur, Historie, aktueller Stand, offene Punkte.
3. dieses Handoff - Installation und Betrieb.
4. `COMMERCIAL_LAUNCH_CHECKLIST.md` - Verkaufsvorbereitung.
5. `deploy/DEPLOY_ANLEITUNG.md` - Produktionsinfrastruktur.

Alte Audit-/Handoff-Dateien sind Belege und Historie, aber nicht automatisch der
aktuelle Produktvertrag.

---

## 2. Was in Git enthalten ist

- Backend, Scheduler, Scanner, Frontend und Tests,
- Deployment-/Health-Skripte,
- SQL-Schema/Migrationen im Code,
- Dokumentation und Auditbelege.

Nicht in Git und separat zu sichern:

- lokale und Server-`.env`, API-, Gmail-, Stripe-, JWT- und Broker-Secrets,
- `data_cache/`, `signal_tracker.sqlite`, Mail-Outbox und Live-Scan-Caches,
- IBKR/TWS-Installation, Zertifikate, nginx-/systemd-Konfiguration,
- Browser-Sessions und lokale IDE-/Codex-Einstellungen.

Secrets niemals in einen Commit, Chat, Screenshot oder eine unverschluesselte
Cloud-Datei kopieren.

---

## 3. Voraussetzungen auf Windows

- Git for Windows,
- Python 3.12 x64,
- Node.js LTS (nur fuer Frontend-Bundle-Aenderungen),
- PowerShell,
- optional OpenSSH Client fuer Serverpruefung,
- Editor/Codex mit Workspace `C:\Users\miros\Desktop\TradingBot`.

Versionen pruefen:

```powershell
git --version
py -3.12 --version
node --version
ssh -V
```

---

## 4. Repository neu einrichten

```powershell
cd C:\Users\miros\Desktop
git clone https://github.com/xantharu123-png/AlphaStation.git TradingBot
cd C:\Users\miros\Desktop\TradingBot
git switch main
git pull --ff-only origin main
git log -1 --oneline
git status --short
```

Erwartung: `main` folgt `origin/main`; keine getrackten lokalen Aenderungen.

Virtuelle Umgebung:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Die historische `.codex_test_venv` ist nicht erforderlich. Auf dem neuen PC kann
`.venv` fuer Entwicklung und Tests verwendet werden.

---

## 5. Secrets sicher uebertragen

Die lokale `.env` darf nur aus einem sicheren Passwortmanager oder verschluesselten
Backup wiederhergestellt werden. Der Server besitzt seine eigene
`/home/tradingbot/app/.env`; sie darf nicht durch die lokale Datei ersetzt werden.

Mindestens pruefen, ohne Werte auszugeben:

- Marktdaten-/Catalyst-/AI-Schluessel,
- `JWT_SECRET`, Auth- und Legacy-Schalter,
- Gmail-Sender/App-Passwort/Empfaenger,
- Stripe Secret, Publishable Key, Price IDs und Webhook Secret,
- `PUBLIC_APP_URL`, CORS und Commercial Flags,
- IBKR Host/Port/Client-ID/Account und AutoTrader-Sicherheitsflags.

Nur Schluesselnamen anzeigen:

```powershell
Get-Content .env | Where-Object { $_ -match '^[A-Z0-9_]+=' } | ForEach-Object { ($_ -split '=', 2)[0] }
```

Nie `Get-Content .env` ungefiltert in Chat oder Screenshots ausgeben.

---

## 6. Lokale Abnahme

Compile und Frontend-Vertrag:

```powershell
.\.venv\Scripts\python.exe -m compileall -q api.py bg_service.py modules
.\.venv\Scripts\python.exe scripts\verify_frontend_bundle.py
```

Volle Testsuite:

```powershell
New-Item -ItemType Directory -Force tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest -q --tb=line -p no:cacheprovider --basetemp=tmp\pytest_handoff_20260811
```

Wenn `frontend/index.html` oder Frontend-Quellcode geaendert wurde:

```powershell
node scripts\build_frontend_bundle.js
.\.venv\Scripts\python.exe scripts\verify_frontend_bundle.py
```

Ein lokaler gruener Lauf ist kein Produktionsnachweis.

---

## 7. Optional lokal starten

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Frontend in einem zweiten Terminal:

```powershell
.\.venv\Scripts\python.exe -m http.server 3000 --directory frontend
```

Aufrufen: `http://127.0.0.1:3000`. Lokale Auth-/CORS-/Secret-Konfiguration kann
von Produktion abweichen; daher Fehler nicht durch Abschalten der Security-Gates
"loesen".

---

## 8. Normaler Entwicklungsablauf

```powershell
git status --short
git pull --ff-only origin main
# Code aendern, fokussierte Tests, dann volle Suite
git diff --check
# Nur konkret beabsichtigte Dateien nennen, niemals pauschal alle Dateien stagen.
git add -- path\to\file1 path\to\file2
git commit -m "Kurze praezise Beschreibung"
git push origin main
```

Vor jedem Commit:

- keine `.env`, Datenbank, Caches, Brokerdateien oder Screenshots gestaged,
- keine fremden/unverstandenen Aenderungen zuruecksetzen,
- volle Suite und Bundle-Verifikation gruen,
- bei Tradinglogik Regressionstest und Forward-Messplan vorhanden.

---

## 9. Produktion deployen

Der Server-Auto-Updater beobachtet ausschliesslich `origin/main`. Ein Push kann
automatisch ausgerollt werden, ist aber **kein Beweis**, dass der Rollout erfolgte.

Manuell:

```bash
ssh root@178.104.69.209
cd /home/tradingbot/app
git status --short
git pull --ff-only origin main
bash deploy/safe_deploy.sh
git log -1 --oneline
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/system-health
systemctl status tradingbot-api tradingbot-bg --no-pager -l
```

Produktionsziel:

- `tradingbot-api` und `tradingbot-bg` laufen,
- nginx liefert das statische Frontend,
- ein alter `tradingbot-frontend`-Service ist nur Legacy-Kompatibilitaet und fuer
  kommerziellen Betrieb nicht das Ziel,
- API-Revision, Git-HEAD und Frontend-Bundle-Hash stimmen ueberein.

Auf dem Server niemals normal entwickeln oder committen. Wenn `git pull` wegen
lokaler Serveraenderungen stoppt:

1. `git status --short` und `git diff -- <datei>` sichern/pruefen,
2. Ursache bestimmen (Secret gehoert in `.env`, Codeaenderung gehoert lokal nach Git),
3. Serveraenderung kontrolliert lokal portieren und pushen,
4. erst danach Server-Worktree bereinigen.

Kein `git reset --hard` und kein blindes `git checkout --`.

---

## 10. Produktionsdaten sichern

Vor Rechner-/Servermigration getrennt sichern:

- `/home/tradingbot/app/.env` verschluesselt,
- `data_cache/` einschliesslich Tracker-/Outbox-Datenbank,
- nginx-/TLS-Konfiguration,
- systemd Units/Overrides und Timer/Cron fuer Auto-Update,
- relevante Journald-/Deploy-Logs,
- Stripe-Webhook-/Domain-Konfiguration im Anbieterportal,
- IBKR Paper-Konfiguration und dokumentierte Account-/Client-ID-Zuordnung.

Das Git-Repository ersetzt diese Sicherung nicht.

---

## 11. Erste Produktionspruefung nach dem PC-Wechsel

```bash
cd /home/tradingbot/app
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
systemctl is-active tradingbot-api tradingbot-bg nginx
bash deploy/health_check.sh
journalctl -u tradingbot-api -n 80 --no-pager
journalctl -u tradingbot-bg -n 80 --no-pager
```

Danach in der App pruefen:

1. Login und `/api/auth/me`,
2. System Health/Scheduler ohne parallele Doppelruns,
3. Aktien- und Krypto-Scan inklusive Fortschritt und frischem Cache,
4. Mailstatus, Outbox und Testmail,
5. persoenliche Position markieren und Folge-/Stop-Mail-Filter,
6. Reminder auf echten Trigger/Retest,
7. Paper AutoTrader bleibt deaktiviert/Kill-Switch aktiv, bis der DU-Soak erfolgt.

---

## 12. Aktueller fachlicher Stand

Implementiert:

- Aktien-, ORB-, BI-, Biotech-, Penny-, Bear-/Crash- und Krypto-Scanner,
- getrennte Setup-/Timing-/Business-Quality-Gates,
- strukturbezogene Entry-/Stop-/TP-Plaene inklusive VRVP/S/R/ATR/Fib,
- Multi-Timeframe-Barrieren und Post-Pump-Rejection-Guard,
- persistente Mail-Outbox, Reminder, Dedupe und persoenliche Positionsfilter,
- Forward-Tracker, Wochenreport und ehrlicher Backtest-/Ambiguitaetsvertrag,
- revisionsgebundener Auto-Deploy,
- geschuetzter IBKR-Paper-AutoTrader V2 mit Kill-Switch und Reconciliation.

Noch nicht als produktiv/empirisch bewiesen:

- aktueller Server-Rollout dieses Dokumentenstands,
- profitable Erwartung je Scanner/Regime; Stichproben weiter sammeln,
- realer mehrtaegiger IBKR-DU-Paper-Soak; Live bleibt blockiert,
- vollstaendige Commercial-Freigabe inklusive Recht, Steuer, Datenlizenzen,
  HTTPS und Live-Stripe.

---

## 13. Sofortige Prioritaeten fuer die naechste Sitzung

1. Exakten `main`-Commit lokal und auf dem Server abgleichen.
2. Volle Suite und Frontend-Bundle auf dem neuen PC reproduzieren.
3. Scheduler, Outbox, persoenliche Folge-/Stop-Mails und aktuelle Scan-Caches live
   verifizieren.
4. Forward-Kohorten je Scanner/Regime auf mindestens 30 entschiedene Signale bringen.
5. IBKR Paper in kontrollierten Stufen testen: Connect/Reconcile, Review ohne
   Transmit, kleiner Bracket-Order-Test, Partial Fill/Reject, Restart/OCA/Kill-Switch.
6. Erst danach Commercial Launch oder Live-Automation neu bewerten.

---

## 14. Uebergabe-Kurzfassung

- **Verbindliche Regeln:** `PROJEKTBIBEL.md`
- **Aktueller Gesamtstand:** `PROJEKTHANDBUCH.md`
- **Setup neuer PC:** dieses Dokument
- **Git-Quelle:** `origin/main`
- **Produktionsquelle:** exakter Server-HEAD plus Health-/Bundle-Nachweis
- **Secrets/Live-Daten:** separat und verschluesselt, nie aus Git
- **Tradingstatus:** Analyse- und Paper-Infrastruktur vorhanden; keine
  Gewinnzusage, keine Live-AutoTrader-Freigabe
