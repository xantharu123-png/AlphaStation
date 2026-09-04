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

---

## 15. Nachtrag 13.08.2026 - Signal-Pipeline sicher uebernehmen

Der neue Signal-Pipeline-Vertrag steht in `PROJEKTBIBEL.md`, der technische und
forensische Beleg in `AUDIT_SIGNAL_PIPELINE_2026-08-13.md`. Der lokale
Arbeitsstand ist erst dann Produktion, wenn Server-HEAD, API-Revision,
Frontend-Bundle, Services und Health exakt denselben ausgerollten Commit
nachweisen. Lokale Implementierung, gruene Tests oder ein Push sind weder
Produktions- noch Profitabilitaetsbeleg.

### 15.1 Vor jeder Signalbewertung pruefen

- Tracking beginnt erst bei belegter SMTP-DATA-Akzeptanz fuer die konkrete
  Empfaengerkohorte oder dokumentiertem Brokerfill. Scanstart, vorbereitete Mail
  und Vorversandquote sind kein Fill.
- Ohne Brokerfill stammt der First Executable Price aus der ersten realistisch
  ausfuehrbaren Beobachtung ab Akzeptanzzeit: Long zum Ask, Short zum Bid;
  Stop-/BE-Gaps werden symmetrisch inklusive Spread, Slippage und Kosten
  behandelt.
- Daily-Bars benoetigen ein echtes Open. Close-als-Open, laufende Bars oder ein
  lueckenhafter Post-Alert-Pfad duerfen keine erfundene Ausfuehrung erzeugen.
- Delivery-Intent und Empfaenger-Akzeptanzjournal muessen vorhanden sein.
  Unbekannter DATA-Ausgang bleibt quarantainiert; bei Teilannahme umfasst die
  Kohorte nur die im selben Versuch akzeptierten Empfaenger.
- `legacy_open_cohort_unknown` bleibt sichtbar und degradiert. Alte Empfaenger
  werden nicht geraten; Folgeupdates gehen nur an die belegte Ursprungskohorte
  geschnitten mit aktuellem Opt-in.

### 15.2 Zahlen richtig einordnen

Der Mailaudit vom 06.-12.08.2026 fand 16 Update-Digests, 45 Ereigniszeilen und
nach Plan-Dedupe 41 Plaene: 33 terminal, 8 nur mit TP1-offenem Rest. Die
terminalen Ereignisse umfassten 12 positive und 21 negative Ausgaenge, netto
-2,11R. Das ist ein unvollstaendiger Update-Ereignisstrom, keine vollstaendige
Created-/Matured-Kohorte und keine belastbare Trefferprognose.

Die begrenzten Korrekturen lauten ONON -4,65R auf -3,94R, ECO -1,27R auf
-1,00R und CBLL -1,57R auf `NO_FILL`. AURA bleibt konservativ -1,38R;
-1,00R ist ohne eindeutigen Erstmail-/Produktions-DB-Nachweis nur wahrscheinlich.
Die konservative Summe dieser vier Faelle verbessert sich von -8,87R auf
-6,32R (+2,55R, ein entschiedener Verlust weniger). Das beweist keine positive
Erwartung.

Performance wird getrennt nach Created/Matured, gefuellt/entschieden,
`NO_FILL`, `OPEN`, `UNTRACKED` und unresolved ausgewiesen. Unbewiesene
Break-even-Zustellung bleibt `managed_be_unresolved`. Eine Freigabe braucht in
der gemeinsamen Zelle Scanner x Richtung x Horizont x exogenes Marktregime
mindestens 30 vollstaendig beobachtete Entscheidungen, Wilson-95-Prozent-
Intervall und null unresolved Kontrollfaelle.

### 15.3 Historische Reparatur: Dry-Run zuerst

Verbindliches Runbook: `deploy/SIGNAL_TRACKER_REPAIR.md`; Werkzeug:
`scripts/signal_tracker_repair.py`. Niemals Werte aus Mailbetreff oder
Tickernaehe raten.

1. Kandidaten read-only inspizieren und mit Originalmail/Marktdaten eindeutig
   identifizieren.
2. Manifest mit exaktem Vorzustand und externer Evidenz erstellen.
3. Dry-Run pruefen; keine Datenbankaenderung zulassen.
4. Produktionsbackup von Tracker, Zustelljournal und Outbox erstellen und eine
   Vier-Augen-Pruefung durchfuehren.
5. Im Wartungsfenster beide Writer (`tradingbot-api`, `tradingbot-bg`) stoppen;
   nur dann bestaetigten Apply ausfuehren.
6. Services starten und Health, Repair-Audit, Backup-/Manifest-Hash sowie die
   identische Performance-Kohorte vor/nach Repair dokumentieren.

### 15.4 Rollout-Gates

Vor Produktion muessen volle lokale Testsuite, Python-Compile, neu gebautes und
verifiziertes Frontend-Bundle, `git diff --check`, Secret-Diff-Scan und Push
gruen sein. Danach separat nachweisen:

1. Server-HEAD, `origin/main`, API-Revision und Bundle-Hash identisch,
2. API, Background-Service und nginx aktiv; Health ohne unbekannte Zustelllage,
3. Realtime-Quote-Berechtigung und erforderliche Quote-Recency produktiv,
4. unbekannte DATA-Ausgaenge und offene Legacy-Kohorten null oder dokumentiert
   manuell behandelt,
5. Repair nur nach erfolgreichem Dry-Run, Backup und Vier-Augen-Pruefung,
6. neue kausal vollstaendige Forward-Kohorte sammeln; historische Rohdaten
   niemals still umdeuten.

### 15.5 App Store und Google Play sind ein eigener Releasepfad

Dieses Repository belegt derzeit die responsive Web-App, aber keine native
iOS-/Android-Huelle, kein Xcode-/Gradle-Projekt und kein Release-Signing. Fuer
eine Store-Einreichung muessen Architektur, echtes Geraete-QA, Store-IAP,
Privacy-/Trackingangaben, Entwicklerkonten, Signierung und Store-Metadaten
separat aufgebaut und nachgewiesen werden. Ein Frontend-Bundle oder ein
erfolgreicher Server-Rollout schliesst diese Gates nicht.

### 15.6 Reproduzierbare lokale Abnahme vom 13.08.2026

Auf diesem PC wurde der finale Arbeitsbaum in einer isolierten Testumgebung
abgenommen: 1768/1768 Tests bestanden, 47 geaenderte/neue Python-Dateien
kompilierten, das Frontend-Bundle wurde als `a6c74874a925` neu gebaut und
verifiziert, JavaScript-Syntax, `git diff --check` und Secret-Musterscan waren
gruen. Der unabhaengige Endaudit meldete P0 0, P1 0 und P2 0.

Die reale Browserpruefung nutzte 1440 x 1100 und 390 x 844 Pixel. Es gab keinen
horizontalen Overflow und keine Konsolenfehler; die lokale Tailwind-Runtime
meldet lediglich ihre bekannte allgemeine Production-Build-Warnung. Das
Smart-Money-Radar bestand einen DOM-Injection-Gegenversuch. Screenshots liegen
lokal unter `output/playwright/` und sind absichtlich durch `.gitignore` vom
Repository ausgeschlossen.

Die lokale reale Mail-Outbox enthielt bei Abschluss 0 aktive
`pending/sending/delivering/uncertain` Eintraege. Dies ist kein Beleg fuer den
Produktionsserver. Nach Clone/PC-Wechsel muessen Bundle-Hash und Tests erneut
reproduziert und danach Server-HEAD, API-Revision, Services, Health,
Quote-Berechtigung und Zustelljournale separat geprueft werden.

### 15.7 Aktueller Git-/Serverblocker

Lokaler Implementierungscommit: `e9cba06`. `origin/main` stand beim letzten
Abgleich weiterhin auf `9987c7f`, weil auf diesem PC kein GitHub-HTTPS-
Schreib-Credential, kein `gh`-Login und kein GitHub-SSH-Schluessel vorhanden
war. Keine Zugangsdaten in Dateien oder Befehlszeilen eintragen; den Zugriff
ueber den vorgesehenen Credential-Manager bzw. einen autorisierten SSH-Key
wiederherstellen und danach den Remote-Hash explizit pruefen.

Produktion meldete oeffentlich noch Revision `de4e7cfac0ec` und Bundle
`c0b3b13a6c86`; die neue Landing-Copy war nicht live. Der Server-SSH-Versuch
scheiterte mangels uebertragenem privaten Schluessel. Daher wurden bewusst kein
Pull, kein Repair und kein Neustart versucht. Nach Wiederherstellung der
Zugaenge gelten weiterhin Backup-, Realtime-Quote-, Legacy-Kohorten-, Health-
und Vier-Augen-Gates aus 15.4.

## 16. Scanner-Remediation 04./05.09.2026

Aktuelle fachliche Referenz: `AUDIT_REMEDIATION_2026-09-04.md`. Die alten
Commit-/Servermeldungen in Abschnitt 15 sind historische Momentaufnahmen,
nicht der aktuelle Git- oder Deploymentstatus. Basis dieses Pakets war
`47eca9ac05ecbf599e85a16658f2a8d367feb3c5` auf `main`.

- BI bleibt strikt mindestens 17/20, jetzt Contract `stock-bi-20-v2`; keine
  unter-17-Watchlist, kein solches Tracking und keine solche Mail.
- Shared-Level-Grenzen, Struktur-Stop, gerichtete Preisrundung, Fibonacci-
  Chronologie, fertige Kerzen, RVOL-Bezug und venuegleiche Crypto-Messwerte
  wurden mit Gegenbeispielen und Regressionen korrigiert.
- Tracker `causal-legs-extrema-v2`: keine spaeteren Gewinne nach vollem Exit,
  keine Post-Exit-Extrema. Rohhistorie und Produktionsdaten wurden nicht
  automatisch repariert. Backtestmodell und echte Versand-/Fillpfade bleiben
  getrennte Modelle, keine Netto-Kontorendite.
- Alle 20 Scheduler-/Cache-Eintraege sowie 13 oeffentliche Aktien- und 11
  generische Crypto-Strategien sind im Audit eingeordnet. Inventur, tiefer
  technischer Test und empirische Profitabilitaet sind getrennt ausgewiesen.
- Reales Frontend wurde mit ausschliesslich synthetischen lokalen Antworten
  auf Desktop und 390x844 geprueft. Auswahl-Snapshot/Richtung/20 BI-Faktoren
  und Unknown-Zustaende stimmen im geprueften Ablauf.
- `output/` und Mailarchive bleiben privat/lokal. Keine Server-Aenderung,
  echte Mail, Order, produktive DB-Migration oder Cron-Aktivierung ausgefuehrt.
  Additive Tracker-Schemafelder sind im Code enthalten und werden erst beim
  jeweiligen Runtime-Start angelegt; vor dem Server-Rollout DBs sichern.

Finale Tests, Git-Abnahme und offene Rollout-Gates stehen im Auditbericht.
Nach serverseitigem Pull sind Revision, Bundle, Services und Health separat
zu pruefen. Nach Wechsel auf v2 kann die BI-Liste bis zum naechsten gueltigen
Scan korrekt leer sein. Positive neue Netto-Erwartung ist noch nachzuweisen;
alte Archivverluste sind keine Messung der neuen Version.
