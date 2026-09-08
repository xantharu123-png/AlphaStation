# Crypto Explosion: Laufzeit, Waechter und Fortschritt

Ausgangsstand: `882d9e63d34e90f094c7bc3395a204c26f4bb605`, `main`.
Der Nutzer bestaetigte diesen Stand auf Hetzner am 08.09.2026 um 09:32 UTC:
API healthy, Bundle `3738379e722f`, drei Dienste aktiv. Nach Auswertung seiner
Logs wurden die folgenden Aenderungen mit "ok machen" beauftragt.

## Nachgewiesener Anlass

Der gelieferte, gefilterte Journald-Ausschnitt enthaelt zwei aeltere erfolgreiche
Laeufe und 28 erfolgreiche Laeufe des API-Prozesses 1920760 vor dem heutigen
Neustart. Letztere prueften jeweils 1000 Handelspaare: 1378,2 bis 1761,6 Sekunden,
Median 1434,8 Sekunden (23m55s). Vier ueberschritten das 25-Minuten-Warnbudget;
drei Warnungen wurden nachweislich durch die Sechs-Stunden-Drossel unterdrueckt.
Kein ERROR/Hartalarm im Ausschnitt. Der Warnung um 06:56 UTC entspricht ein
erfolgreicher Lauf mit 1532,4 Sekunden. Gleiche Journal-Zeitstempel beweisen
wegen Ausgabepufferung keine Ausfuehrungsreihenfolge. Der neue Prozess 1928548
zeigt erst einen Start um 09:46 UTC, noch keinen abgeschlossenen Lauf.

Das beweist zu wenig Warnreserve fuer diese abgeschlossenen Laeufe, nicht die
Abwesenheit saemtlicher Netzprobleme und nicht die Laufzeit der neuen Version.

## Aenderungen und Invarianten

- Weiche Warnung fuer Crypto Explosion nach 35 Minuten. Hartlimit explizit
  weiterhin 75 Minuten, nicht versehentlich 105. BI bleibt bei 60/135 Minuten.
  Dedupe, Sechs-Stunden-Drossel und harte Eskalation bleiben bestehen.
- Gemeinsamer weicher Warntext sagt "dauert laenger als vorgesehen" und
  behauptet keinen nachgewiesenen Netz-Haenger. Ein Neustart ist wegen der
  weichen Warnung allein nicht erforderlich. Krypto-Mails enthalten vorhandenen
  Zaehlerfortschritt und dessen Alter. Statuscode `stuck` bleibt API-kompatibel;
  die Scheduler-Anzeige benennt ihn als Warnbudget-Ueberschreitung.
- Maximal vier Venue-Worker, genau einer je Bybit/Binance/MEXC/Bitget. Auswahl
  und Deckel des bisherigen Universums bleiben erhalten (Standard 1000,
  vorhandene Konfiguration weiterhin 50..1600). Kein unbegrenzter Future-Stau.
  Pro Handelspaar weiterhin 140x5m, 96x15m, 72x4h, Funding und ggf. Spread.
- Originalreihenfolge wird vor der bisherigen Rangfolge wiederhergestellt;
  Thread-Abschlussreihenfolge darf gleich bewertete Treffer nicht umsortieren.
  Keine neuen Score-/Entry-/Stop-/Zielschwellen, kein Aufweichen von BI17/20.
- Der HTTP-Schutz ist auf diese Scan-Worker beschraenkt: ein aktiver Request
  pro Host, mindestens 250ms zwischen Requeststarts. HTTP429/403/418 und
  bekannte Limit-Antworten stoppen die betroffene Venue. Retry-After und
  Bybit-Reset-Zeit werden beachtet, ohne sofortige Retry-Schleife. Cooldowns
  bleiben pro API-Prozess ueber Scans erhalten. Andere Dienste/IP-Nutzer
  teilen ggf. dieselbe Boersenquote; dies ist KEIN globaler IP-Quota-Manager.
- Ein mitten im Lauf wegen Rate-Limit abgebrochener ausgewaehlter Batch
  ueberschreibt den vorherigen Cache nicht und wird nicht gesund entwarnt.
  Dasselbe gilt fuer ein leeres Universum oder Abruffehler bei saemtlichen
  ausgewaehlten Handelspaaren. Ein kompletter Datenabruf-Ausfall darf nicht
  als frisch gepruefte leere Signalliste erscheinen.
  Bereits bei der Universumsabfrage fehlende Quellen werden separat markiert;
  die verfuegbaren Quellen koennen weiterhin ihre regulaeren Daten liefern.
  Beobachtete Abruffehler werden gezaehlt und die Abdeckung kenntlich gemacht.
- Gemeinsamer BTC-Kontext hat einen Single-Flight-Lock; fehlender Kontext
  bleibt unbekannt und wird maximal 30 Sekunden negativ gecacht, nicht je
  Kandidat erneut abgefragt. Bestehender positiver TTL bleibt 120 Sekunden.
- Frische wird nach Abschluss aller Venue-Worker erneut aus dem wirklichen
  5m-Kerzenzeitstempel geprueft. In der Wartezeit veraltete Kandidaten werden
  vor Top80-Selektion entfernt, nicht mit einer neuen Cache-Zeit verjuengt.
- Exklusiver Wrapper-Lock umfasst auch Publikation. Der manuelle kombinierte
  Scanner und der eigenstaendige Scheduler duerfen die Long-Engine nicht
  doppelt starten. Der Executor wartet auf alle Worker; kein Timeout startet
  neben einem noch lebenden Worker einen Ersatz.
- Fortschritt ist nur Zaehler-/Laufzeitinformation, keine partielle Signalliste.
  Die bestehende ScanControl zeigt ihn im Krypto-Tab, mit Venue-Zaehlern,
  Fehlerzahl und Zeitpunkt des letzten Fortschritts. Zwischen-Setups werden
  ausdruecklich als vorlaeufig bezeichnet, nicht als fertige Trades.
- Bei der Browserpruefung reproduzierter Datumsfehler behoben: vorhandene
  UTC-/Zeitzonen-Suffixe werden nicht nochmals um `Z` ergaenzt. Ungueltige
  Zeitstempel erscheinen als unbekannt statt `Invalid Date`.

## Primaerquellen fuer Limit-Behandlung

- [Binance USD-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info):
  HTTP429 verlangt Backoff; weitere Verletzungen koennen HTTP418/IP-Bans ergeben.
- [Bybit Rate Limit Rules](https://bybit-exchange.github.io/docs/v5/rate-limit):
  `retCode=10006`, Reset-Header; nach einem HTTP403-Frequenzblock mindestens
  zehn Minuten pausieren. Keine proprietaeren oder privaten Endpunkte benutzt.

## Abnahme und Grenzen

Lokale Offline-Tests und synthetische Desktop-/Mobil-Browserpruefung; keine
echten Boersenabrufe, Mails, Orders, produktiven Datenbanken oder Serveraktionen.
Gezielter Zwischenstand: 265 Tests bestanden.

Finale Abnahme (08.09.2026):

- Erste volle Suite: 3518 bestanden, vier Windows-/Linux-Contract-Skips,
  478,96 Sekunden. JUnit lokal: `tmp/crypto_runtime_full_20260908.xml`.
- Danach Datums- und Komplettausfall-Gegenproben zunaechst rot reproduziert,
  korrigiert und alle nicht-deploy Tests erneut ausgefuehrt: **3368 bestanden**,
  null Fehler/Skips, 115,46 Sekunden.
  JUnit lokal: `tmp/crypto_runtime_final_20260908.xml`.
- Die unveraenderten drei Deployment-Testdateien und ihre Skripte wurden
  nicht nochmals ausgefuehrt: zuvor 161 bestanden, vier plattformspezifische
  Skips. Zusammengesetzter Abnahmestand daher 3529 PASS + vier Skips,
  ausdruecklich kein einzelner finaler Vollsuite-Lauf dieser Groesse.
- Gegenproben: 35-/75-Minuten-Grenzen, Mail-Dedupe/Recovery, maximal vier
  parallel aktive Venues, eine Anfrage je Host, Abbruch/Cooldown, Erhalt
  vorheriger Caches bei Ausfall, Frische vor Publikation, Fortschritt vor
  Abschluss, Engine-Exklusivitaet einschliesslich Publikation und identische
  Ergebnisse des echten Scorers bei identischen sequentiellen/parallelen
  Eingabekerzen. Keine gelockerten Scoregrenzen fuer den Test.
- Python-Compile fuer alle neun geaenderten/neuen Python-Dateien,
  JavaScript-Syntax, Frontend-Build/Verifier und `git diff --check` bestanden.
  Finaler Bundle-Quellmarker: **`938257a0d05f`**.
- Echtes Chromium mit synthetischem Loopback-Fixture: 1440x1000 und 390x844.
  Sichtbar: 430/1000, 43 Prozent, vorlaeufige Setupzahl, Venue-Zaehler,
  deaktivierter Startknopf und Warnbudget-Hinweis. Keine fertige Kandidatenliste
  aus Zwischenresultaten. Zeitangaben gueltig; Mobilbreite/Seitenbreite 390/390;
  keine ungefangenen JavaScript-Fehler. Bekannte Tailwind-CDN-Buildwarnung,
  im ersten Fixture-Aufruf ein nicht produktiver favicon-404.
  Browsernachweis mit Playwright-Skill: gecachte native Windows-CLI, da der
  Bash-Wrapper hier nicht startete. Keine Installation oder Paketaktualisierung.

Finale Screenshots, bewusst nur lokal unter
`output/playwright/crypto-runtime-20260908/.playwright-cli/`:
`page-2026-09-08T10-27-44-466Z.png` (Desktop),
`page-2026-09-08T10-28-12-490Z.png` (Warnstatus),
`page-2026-09-08T10-28-41-588Z.png` (Mobil).

SHA-256 der abgenommenen lokalen Laufzeitdateien:

| Datei | SHA-256 |
| --- | --- |
| api.py | 09997109b48cb36c7ade4bba80370d8f3839af3efe5370b2ca0262bfd67e38f0 |
| modules/crypto_scan_runtime.py | 0a5b8444410ddef7314fddb1fd6464e4ba37bfbe5a127a610a907ef1be317b88 |
| modules/new_listing_scanner.py | 9be42f83057b401fdc6259d1e7a7a6bc951dfa2f7757553bcc3eb47e5eadd89a |
| frontend/index.html | c030f62229d6a5508e20196ceced0f3d3d12712bda341c03318ed16a1435cca3 |
| frontend/app.bundle.js | aaccc748486f9d68821571f563bdb7ddb7bbc04a1fd804e545b0c765010c8832 |

Die echte Beschleunigung muss auf Hetzner gemessen werden. Gleichmaessig ueber
vier Boersen verteilte I/O-Arbeit kann parallel laufen; bei Konzentration auf
eine einzige Boerse oder langsamen Endpunkten bleibt diese Venue der Engpass.
Weder lokale Tests noch weniger Warnungen beweisen eine bessere Trefferquote.
Keine Dependency-/DB-Migration, kein Cron- oder Berechtigungswechsel.

Vor dem Commit zeigte der autorisierte lesende Remote-Abgleich weiter
`882d9e63d34e90f094c7bc3395a204c26f4bb605` auf `origin/main`.
Ein nachfolgender GitHub-Push ist kein Server-Rollout. Nach manuellem Pull
muessen API/BG neu gestartet und Revision/Bundle/Services/Health geprueft
werden. Die naechsten produktiven Laufzeiten und Progress-/ERROR-Zeilen
sind separat auszuwerten; keine Zusage einer bestimmten Beschleunigung,
Mailanzahl oder besseren Trefferquote aus diesem Laufzeitpaket.
