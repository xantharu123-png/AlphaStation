# Account-Übergabe – Alpha Station / TradingBot

Stand: **17.10.2026, Europe/Zurich**. Checkout: `C:\Projekt\TradingBot`,
Windows/PowerShell. Auftrag dieser Runde: TODO aktualisieren und eine
verlässliche Übergabe für den anderen Account vorbereiten. Keine neue
Implementierung, Konten-/Aboänderung, Testmail, Order oder Serverinstallation.
Keine Übergabe wurde an einen externen Account oder Dienst versandt.

## 1. Sofort anschließen

1. Obersten Abschnitt von [TODO](../TODO.md) und dieses Dokument lesen.
2. `git status --short`, `git log -5 --oneline` und Index prüfen. Der Checkout
   ist absichtlich stark verändert; WIP/private Belege niemals bereinigen.
3. Als nächstes mit dem Nutzer **IBKR oder andere Optionsdaten** besprechen.
   Die anbieterunabhängigen Claude-Reparaturen wurden bereits umgesetzt und
   veröffentlicht. Diese Arbeit nicht erneut als unerledigt starten.
4. Vor einer Behauptung zur Installation oder Zustellung den aktuellen
   Server-/Versandstand lesen. Hier wurden nur Git und vorhandene lokale
   Prüfbelege neu geprüft, nicht der laufende Server oder das Postfach.

## 2. Verifizierter Git- und Veröffentlichungsstand

| Gate | Beleg am Beginn dieser Dokumentationsrunde |
| --- | --- |
| Arbeitsbranch | `codex/leaps-scanner` |
| Lokaler HEAD | `5c5b319492f5ef951ad46daa8da0ff290c8f18a2` |
| GitHub `main` | Derselbe Hash; am 17.10. frisch mit `git ls-remote` gelesen |
| LEAPS-Quellpaket | `d08b7cb0b94c331aa883723094f2b41ed2ecb434`, 33 abgegrenzte Dateien |
| Veröffentlichungsnachtrag | `5c5b319`, ausschließlich TODO und LEAPS-Prüfbericht |
| Veröffentlichungs-Bundle | `37129d2f1112` |
| Installation dieser Revision | Nicht aktuell bestätigt; Nutzer bekam den normalen Pull-Befehl |
| Optionsanbieter / Aktivierung | Nicht ausgewählt/qualifiziert; keine reale Anbindung aktiviert |

Der erste Push war wegen der 20 LEAPS-Vorgängercommits durch die Tool-Prüfung
blockiert. Der Nutzer hat **d08b7cb einschließlich genau dieser Vorgänger auf
origin/main ausdrücklich freigegeben**. Der normale Push und danach der
Dokumentationsnachtrag waren erfolgreich. Diesen erledigten Blocker nicht
erneut als ausstehende Veröffentlichung behandeln. Kein Force-Push.

Diese Übergabe ist ein reiner Dokumentationsnachtrag auf obiger Produktcodebasis.
Ein anschließender Doku-Commit hat einen anderen HEAD, verändert aber weder
Produktcode noch Bundle und rechtfertigt allein keinen Dienstneustart.

## 3. Erledigte LEAPS-Reparaturen

Details und Grenzen stehen im
[LEAPS-Reparaturbericht](LEAPS_CLAUDE_REPAIRS_2026-10-15.md).

- Delta/IV aus echten Snapshot-Feldern normalisiert; eigene Greek-Präzision,
  ohne Geldgrenzen zu lockern. Fehlende Daten bleiben unbekannt.
- Standard-Call-Terms über reale Kontrakt-/Listingfelder an Discovery gebunden;
  keine neue OCC-/Inode-/Zertifikatskonstruktion.
- Begrenzter 429-Backoff mit Retry-After und automatischer Fortsetzung;
  temporäre Budget-/Prioritätswartezeit getrennt von manueller Pause und 401/403.
- ET-Mitternacht, Probe-Datum, SEC-Checkpoint, Adjustierungsnachweise und
  Fehlerzustand nach Uhr-/Validierungsfehler repariert.
- Alte/verzögerte/zeitversetzte Quotes bekommen kein aktuelles Ranking oder
  preisabhängiges Filter-PASS; erneute Prüfung vor Sortierung/Pagination.
- Zeitwert/Break-even p.a., Delta-Hebel, adjustierte Jahres-HV und separat
  datiertes Optionsvolumen. Keine erfundenen Dividendensummen/Preisziele.
- Standardfilter Delta 0,70–0,85, Mid-Spread höchstens 10 %, OI mindestens 100,
  Aktienreferenz mindestens 10 USD; bewusst leere Filter bleiben leer.
- LEAPS-UI mit Server-Prämienrisiko und tatsächlichen Payoff-Punkten, Chart
  zuerst, Pause auch bei automatischem Warten und getrennten Admin-Rechten.
- Altdatei-Kompatibilität: gespeicherte Schema-1-Quotes ohne die drei neuen
  optionalen Volumenfelder bleiben lesbar; erforderliche/unerwartete Felder
  werden weiter strikt geprüft. Sechs zusätzliche Regressionen enthalten.

## 4. Prüfbelege – historisch, nicht heute erneut ausgeführt

Am 17.10. wurden die vorhandenen XML-/JSON-Belege lesend gegengeprüft:

| Beleg vom 15.10. | Ergebnis und tatsächlicher Umfang |
| --- | --- |
| `output/lh/qa-d20590f0cc11/results.xml` | 14.455 PASS, 7 SKIP, 0 FAIL/ERROR; gesamte damalige Arbeitskopie **inklusive geerbtem WIP**, darunter 1.123 LEAPS-Tests |
| `output/li/qa-626b05e8c975/results.xml` | 1.155 PASS, 0 SKIP/FAIL/ERROR; isolierter Veröffentlichungsstand, darunter 1.123 LEAPS- und 32 Frontend-Vertragstests |
| `leaps-20261015-publish-ui-03/results.json` | 1440×900/390×844, 288 Anfragen/44 Szenarioanfragen, alle Checks true, `errors: []` |
| `leaps-20261015-publish-dto-ui-01/results.json` | Beide Viewports, 16 POSTs, alle Checks true, `errors: []`; synthetische Inputs durch echte Discovery-/Store-/API-DTOs |

Browserbelege liegen unter
`C:/Users/miros/.codex/visualizations/2026/08/11/019fef3d-c9a9-7803-ab66-6631a424c1de/`.
Screenshots sind vorhanden. Erwartete Tailwind-Hinweise und absichtliche 403/409
sind im Harness getrennt erfasst. Sieben Skips betreffen Plattform-/POSIX-/
Symlink-Verhalten, keine LEAPS-Tests. Eine bekannte anyio-Import-Warnung.

**Nicht behaupten:** 14.455 Tests nur des veröffentlichten Standes, reale
Quotes/Lizenzen/Nachtleistung, Linux-Abnahme oder sichere Gewinnquote. Den
isolierten 1.155er-Lauf nicht als Vollsuite des gesamten Projekts ausgeben.
Abgebrochene Läufe und frühe Browser-FAILs bleiben als historische Belege
erhalten; deren Ursachen und Korrekturen stehen im Reparaturbericht.

API-/BG-Tests nur über den vorhandenen Offline-Launcher mit privatem QA-Pfad,
Fake-Keys und gesperrten externen Sockets/SMTP ausführen. Nicht zur Übergabe
erneut ungefragt einen großen Testlauf oder echte Provider-Probe starten.

## 5. Tatsächlich nächste Schritte

### A. Betreiber-Sicherheit bleibt ein separater dringender Punkt

Der Abschnitt vom 14.10. in TODO dokumentiert exponierte alte Provider-
Schlüssel. Tatsächliche Rotation und aktuelle Serververwendung sind am 17.10.
nicht geprüft. Betreiber soll Widerruf/Rotation erledigen bzw. belegen; keine
alten Keys für Probes verwenden, Schlüssel ausgeben, Geschichte umschreiben
oder Konto-/Repo-Sichtbarkeit ohne Auftrag ändern. Private Security-Berichte
nicht in das öffentliche Repository übertragen.

### B. Serverstand nur lesen, bevor erneut aktualisiert wird

Serverinstallation des veröffentlichten LEAPS-Stands ist offen, nicht
nachweislich fehlgeschlagen. Die früheren Health-Angaben vom 12./14.10. sind
historisch. Im Server-Terminal wäre folgender Check ausschließlich lesend:

```bash
sudo -u tradingbot git -C /home/tradingbot/app rev-parse HEAD
curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Health liegt am API-Port 8000; 404 am statischen Frontend-Port 3000 ist kein
Mailausfall. `healthy` beweist weder Scannerfreigaben noch SMTP/Postfach.
Normalen Pull/Neustart nur bei tatsächlich nötigem Update und nach Ende
laufender Scans; kein Deploy-Skript, Besitzerwechsel oder Installationsumbau.

### C. Optionsdaten zuerst unter Berücksichtigung von IBKR entscheiden

Bereits geklärt: **vorhandenes IBKR-Konto**, **Stocks Starter**, **nur echte
Bid/Ask**, kein Richtpreis-/Last-/Mid-Ersatz und keine Abo-Buchung durch den Agenten.
Vorhandene TWS-/Paper-AutoTrader-Vorbereitung ist kein Beweis einer Options-
Marktdatenanbindung oder bestätigter Datenrechte. Datenlesen vom Orderpfad trennen.

Offen sind tatsächliche Optionen- UND Basiswertrechte, Quellzeiten/Greeks/OI,
12–24-Monats-Abdeckung, Nachtpreise/Pacing und erlaubtes Publikum. Die
Preisrecherche in TODO ist vom 14.10. und kein neu geprüfter Tarif oder
Lizenznachweis. Erst danach echten Leseadapter auswählen/anschließen.

Danach: datierte Market Cap ≥2 Mrd. USD, 20-Tage-Dollarvolumen ≥20 Mio. USD,
Watchlist-/Scanner-Union, Wochen-Referenzcache und tatsächlicher Nacht-Schlussstand;
schließlich BG-Nachtlauf mit gemeinsamer Last-/Prioritätssteuerung. Kein
erfundener Budgetwert, keine neue Owner-Hierarchie und keine ungenehmigte
Aktivierung. Echte Probe/Admin-Lauf später separat autorisieren.

### D. Andere Reparaturen, Mail, BI und historische Leistung nicht vergessen

Die breiten Claude-Audit-Reparaturen vom 13.10. liegen überwiegend weiter als
lokales WIP vor. Sie wurden **nicht pauschal durch den LEAPS-Push mitveröffentlicht**.
Maßgeblich: [Reparaturinventar](CLAUDE_AUDIT_REMEDIATION_2026-10-13.md) und
TODO-Abschnitt „Claude-Audit-Reparaturen lokal verifiziert“.

- Eigenes Veröffentlichungspaket/Abhängigkeiten von geerbtem WIP abgrenzen und
  nach neuer QA veröffentlichen, nicht den gesamten Dirty Tree committen.
- Technische Testmail, Crash-/Wochenmails, FET sowie TBBB/PLTR sind historisch
  tatsächlich angekommen. Keine pauschale Behauptung „nie eine Mail“.
- Reguläre rechtzeitige Schlusskursmail nach der Zeitkorrektur und eine
  vollständige Aktien-Trade-Mail bleiben separate aktuelle Postfachgates.
  Keine weitere Testmail, künstliche Signal-Freigabe oder Lockerung der Grenzen.
- BI-v9/150-Bar-Live- und historische Abnahme erst nach passender Veröffentlichung;
  vorhandenen BI-Lauf/alte v8-Studien nicht als nie durchgeführt darstellen oder
  heimlich auf v9 umetikettieren. Fehltermine/Lebenszyklen und alte −47,5R-
  Wochenkohorte bleiben eigene Evidenzaufgaben, keine belegte Kontoperformance.
- FET-15×-Verlust: reale Fills, Marginmodus, Stop-Order und Liquidationsbelege
  fehlen. Doppelte Warnung/ATR-Weitergabe haben lokales WIP; nicht aus alter
  Mailformulierung allein eine Verlustursache oder Live-Reparatur behaupten.

## 6. WIP und feste Nutzergrenzen

- Arbeitskopie umfasst unter anderem API/BG, Scanner/BI, Mail/Tracker, Frontend,
  Deployment-/Windows-Testhilfe, Dokumente sowie ungetrackte Module/Tests.
  Kein Reset, Clean, Stash-Verwerfen, Broad-Staging oder privates `output/` pushen.
- `frontend/index.html` und das lokale Bundle enthalten zusätzlich fremdes WIP.
  Publiziertes Bundle `37129d2f1112` und lokales Bundle sind nicht gleichzusetzen.
  Erneut nur zum genehmigten Quellstand passend bauen/veröffentlichen.
- `deploy/safe_deploy.sh` ist im Arbeitsbaum absichtlich gelöscht, in Git noch
  vorhanden. Nicht aus HEAD wiederherstellen oder aus einer alten TODO heraus
  installieren. Diese Übergabe veröffentlicht keine Deployment-Änderungen.
- Keine ungefragten Landingpage-Texte, Konten-, Abos-, Mail-/Scannergrenzen oder
  Broker-/Installationsänderungen. Titel „Ausbruch bestätigt“ nicht erneut in
  „Watchlist/Beobachtung“ umbenennen. Ein solcher Hinweis ist trotzdem kein
  vollständiger Handelsplan und keine ausgeführte Order.
- Fehlender Rücktest ist nicht generell ein Ausschluss. Cup braucht seine echte
  Struktur; BI bleibt mindestens 17/20. Keine fehlenden Strukturlevel erfinden.
- Nach Arbeitsblöcken TODO aktualisieren. Git-Publish, Installation, reale
  Datenqualität, Signalversand, SMTP-Annahme und Postfachzustellung getrennt halten.

## 7. Kopierbarer Einstieg für den anderen Account

> Arbeite in `C:\Projekt\TradingBot` nahtlos weiter. Lies zuerst den obersten
> Abschnitt von TODO.md und docs/HANDOFF_ACCOUNT_2026-10-17.md. Produktcodebasis
> ist veröffentlicht: LEAPS-Paket d08b7cb mit Doku 5c5b319. Bewahre sämtliches
> Dirty/Untracked-WIP und private Belege. Kläre als nächstes die Optionsdaten-
> Entscheidung mit vorhandenem IBKR-Konto und ausschließlich echten Bid/Ask.
> Server-/Postfachstand zuerst lesen, nicht behaupten oder blind neu starten.
> Andere lokale Claude-Reparaturen nicht mit dem LEAPS-Release verwechseln.
> Keine neuen Abos, Orders, Testmails oder Landingpage-Umschreibungen. Aktualisiere
> TODO nach Abschluss mit wirklicher QA und getrennten Veröffentlichungs-/Livegates.
