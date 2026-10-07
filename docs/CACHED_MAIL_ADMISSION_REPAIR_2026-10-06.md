# Gemeinsame Cache-Freigabe und gezielter LSPD-Beleg

Stand: 06.10.2026, 21:30 Zürich, lokal. Kein Commit/Push, Deployment oder Mailversand.
Vorhandene BI-Start-/Datenkohorten- und Deploy-Retirement-Änderungen bleiben erhalten.

## Nachgewiesene Fehler und Korrekturen

Die App bezeichnete Aktien als freigegeben, obwohl die bereits bestehende
spezifische Qualitätsprüfung des Senders sie ablehnte. Die App nutzte deren
Ergebnis nur für `mail_check`, nicht für den eigentlichen Freigabestatus. Auch
die lesende Admin-Vorprüfung verwendete nur den allgemeinen Klassifizierer.

`_cached_alert_admission_state` verwendet jetzt für beide Ansichten dieselben
bestehenden Auswahlregeln: allgemeine Prüfung, spezifische Aktienqualität,
gültige Tagesreferenz und unveränderten Preis-/Referenzvergleich des Senders.
Es gibt keine neuen Schwellen, synthetischen Levels oder neue Toleranz.
Elliott-Kontext bleibt Kontext. Andere Scanner bekommen keine ungeprüft
übertragene Momentum-Qualitätsschwelle.

Empfänger, Versandzeit, Doppelschutz, finale aktuelle Kurs-/Pfadprüfung und
SMTP bleiben separate Schritte. Eine bestandene Cachevorprüfung behauptet
keinen tatsächlichen Versand oder Postfacheingang. Mail-Cooldown darf den
Chartstatus weiterhin nicht entwerten.

Die ursprünglichen Producer-Felder werden vor einer erneuten Bewertung
wiederhergestellt. Sonst konnte eine vorher abgeleitete `BEOBACHTEN`-Aktion in
der Adminprüfung einen weiteren künstlichen Sperrgrund erzeugen.

Die Kurzfassung zeigt den tatsächlichen Qualitäts-/Datenblocker; Rücktest
bleibt sekundäre Warnung. Der begrenzte Detailblock bleibt begrenzt, aber die
Kurzfassung berücksichtigt auch valide Gründe nach Warnung zwölf. Die ATR-
Sperre heißt präzise „Schwankungsbreite zu gering“, nicht Tageskursänderung.

## RED → GREEN

- Backend: zuerst 30 Fehler/8 bestanden, weitere Grenzfälle mit zwei bzw.
  einem reproduzierten Fehler. Die neue Datei hatte zunächst 57 Fälle; zusammen mit
  bestehenden relevanten Tests 424 bestanden, keine Fehler/Skips:
  `output/mail-fix-qa-c7f9900dc28343f5869a9686bce5f077/results.xml`.
- Frontend: echte JavaScript-Helper per Node ausgeführt. Ursprünglich zehn
  Sperrgründe als bloß „Rücktest offen“; zusätzlich Quellenkonflikt und zwei
  Warnungsgrenzfälle reproduziert. Finale gezielte Prüfung 131 bestanden:
  `output/mail-fix-qa-b1179452807a44f88c1f5642306375cd/results.xml`.
- Reader/Wrapper: Root fand trotz grüner unabhängiger Fixtures einen
  abweichenden JSON-Key. Die Wrapper-Fälle verwenden jetzt echte Reader-
  Projektion statt handgeschriebener Antwort. Erfolg vor Korrektur RED,
  danach 33 bestanden/1 Windows-Symlink-Skip:
  `output/mail-fix-qa-bdb9cfa7aedd4e22b4cd6b8c6cf45c61/results.xml`.
- Diese Testmengen überlappen mit dem Gesamtlauf und dürfen nicht addiert
  als einzigartige Tests bezeichnet werden. Vorhandener anyio-Rewrite-Hinweis.
- Erster Gesamtlauf bewusst unterbrochen, weil noch Grenzregressionen und
  Reader-Korrektur ergänzt wurden. Danach vollständiger isolierter Lauf:
  **11.722 bestanden / 4 fehlgeschlagen / 6 übersprungen**, keine Errors,
  1.559 Sekunden; Root las die abgeschlossene XML:
  `output/mail-fix-qa-7cb256a31e574332a523c1b3a0964a96/results.xml`.
- Zwei Fehler waren 30-Sekunden-Windows-Bash-Timeouts unveränderter Updater-
  Fixtures. Dieselben zwei Tests unverändert separat wiederholt:
  **2 bestanden**, 38,9 Sekunden insgesamt, Timeout nicht erhöht:
  `output/mail-fix-qa-acf662242675495bbf9f21bee29d86dc/results.xml`.
- Ein alter Sichtbarkeitstest verlangte ausdrücklich die gerade beseitigte
  grüne Freigabe trotz abgelehnter spezifischer Qualität. Root aktualisierte
  ihn auf den genehmigten gemeinsamen Auswahlvertrag und ergänzte die
  Nichtmutation des Producer-Objekts; Mail-Vorprüfung bleibt gesperrt.
- Der Registry-Guard fand drei ungültige Datenzustände ohne explizites
  Decision-Mapping. Sechs neue echte App-/Admin-Consumer-Fälle reproduzierten
  `WATCH` statt `NO_TRADE`. Exakt drei vorhandene Gründe in der bestehenden
  NO_TRADE-Menge registriert, keine Whitelist oder Senderänderung. Neue
  Backenddatei jetzt **63 Fälle**; gezielt **242 bestanden**, inklusive
  Registry und aktualisiertem Sichtbarkeitstest:
  `output/mail-fix-qa-51c2e5805e47446582cef24542d4b125/results.xml`.
- Erneuter breiter Produktlauf auf diesem letzten Stand abgeschlossen:
  **11.565 bestanden / 2 übersprungen / 0 Fehler oder Errors**, 472,5 Sekunden.
  Root las die abgeschlossene XML und bestätigte danach alle drei Quellhashes:
  `output/mail-fix-qa-4ee1ba82632248c6bb17f82cb0591cff/results.xml`.
  Nur die vier unveränderten `test_deploy*.py`-Dateien sind hierbei explizit
  ausgenommen; deren erster Gesamtlauf samt zwei isolierten Wiederholungen
  ist oben getrennt ausgewiesen. Kein vollständiger grüner Repo-Lauf behauptet.
  Skips betreffen POSIX-Dateirechte und nicht verfügbare unprivilegierte
  Windows-Symlinks, nicht übersprungene fachliche Scanner-/Mailprüfungen.

Der Testlauncher isoliert Datenbanken/Cache und Fake-Zugangsdaten, verbietet
externes Netzwerk und SMTP. Keine echten Handels-/Testmails aus Tests.

Der erste abgeschlossene vollständige Lauf wurde nach dem letzten Bundlebau mit eingefrorenem Quellstand
gestartet; erneute SHA-256-Prüfung während des Laufs unverändert:

| Datei | SHA-256 |
| --- | --- |
| `api.py` | `DD4F141D10BC658A7C79867264EC009648EBA5E59702698F156A8434FBC7A8F2` |
| `frontend/index.html` | `010B9D9DF935B036B1B11699B134D78F8E6EFF0BFBD2DB486E120B40956FFFC7` |
| `frontend/app.bundle.js` | `D252B345E19D2C8A302927D1AD770F7B37729DF9D7A8F0315F92C6B37CEF6F0F` |

HEAD bleibt `e83bb1d6abdd34a2bd08f2395170fd444f015790`. Die Hashes bezeichnen
lokales WIP, nicht eine bereits veröffentlichte oder deployte Revision.

Unabhängige finale Leseprüfung am identischen API-Hash: keine konkreten
P1/P2-Regressionsbefunde. BI/Krypto bleiben beim bisherigen Klassifizierer,
Elliott bleibt Kontext. Producer-Wiederherstellung betrifft nur Score/Grade/
Aktion, nicht Kurs-/Bestätigungsdaten. App ignoriert weiter transportbezogene
Sperren; Admin/Mailvorprüfung behalten sie. Sender/Claims/SMTP unverändert.

Nach den drei expliziten NO_TRADE-Registrierungen lautet der API-Hash
`F198AC728D405CF8C59E24051AC010A0C6BCAD12C7B370B7551733BB269A7530`.
HTML und Bundle blieben unverändert. Der anschließende Produktlauf bezieht
sich auf diesen letzten Quellstand, nicht auf den ersten API-Hash.

## Renderprüfung

Frontend-Bundle frisch erzeugt: `ba7e64a7b792`.
Desktop und 390×844 über den internen Browser geprüft, mit klar als QA
benannten synthetischen Zeilen und einem GET-only-Localhost-Server. Die
Kurzfassung und ausgeklappte Mail-/Qualitätsdetails stimmen überein; gültige
Freigabe mit ausstehendem Rücktest bleibt grün. Keine neuen langen Textblöcke.
Lokale QA meldet 0 Upstream-Anfragen, 0 SMTP und 0 Schreibversuche. Keine
JavaScript-Fehler; vorhandener Tailwind-Runtime-Warnhinweis. Viewport zurückgesetzt.

Screenshots liegen außerhalb des Repos:

- `C:/Users/miros/.codex/visualizations/2026/08/11/019fef3d-c9a9-7803-ab66-6631a424c1de/mail-admission-desktop.jpg`
- `C:/Users/miros/.codex/visualizations/2026/08/11/019fef3d-c9a9-7803-ab66-6631a424c1de/mail-admission-mobile.jpg`

## Konkreter LSPD-Beleg eingegangen und geprüft

Privater Export `output/profitability/lspd-plan-evidence-20261006T191129Z.json`
ist eingegangen. SHA-256:
`DD7C42DC8BEA23FFA666D3D96B4EAB81D7329A8C87EDF85900C5C698584AEF96`.
Nicht veröffentlichen. Root und zwei unabhängige Prüfer lasen die nativen
Plan-/Zonenfelder; es wurde kein später geladener Chart als Originalbeleg benutzt.

Referenz `2026-10-05T20:00:00Z`, Entry 10,21, Stop 10,04, Risiko 0,17,
TP1 10,23 und TP2 10,26. Die gespeicherten R-Werte sind arithmetisch richtig:
0,02/0,17 = 0,1176R, 0,05/0,17 = 0,2941R; 50/50-Mittel gerundet 0,21R.

106 eindeutige native Zonen mit 269 Evidenzzeilen. Keine zukünftigen
Bestätigungszeiten oder abweichenden Cutoffs gefunden. Stop-/TP-Zonen-IDs,
Bounds und Bestätigungszeiten entsprechen jeweils genau einer nativen Zone.

| Rolle im Plan | Native Grenzen | Zustand am Referenzschluss |
| --- | --- | --- |
| Stopzone | 10,07109–10,15891 | Unter Entry; aktueller LONG-Break bestätigt |
| Erste Gegenbarriere | 10,13109–10,22891 | Entry 10,21 innerhalb; nicht reclaimed |
| Zweite Zielzone | 10,19109–10,26391 | Entry ebenfalls innerhalb |

Die erste Gegenbarriere enthält bestätigte historische Support- und Resistance-
Pivots. Das ist durch den vorhandenen Connected-Role-Vertrag erlaubt und für sich
kein fehlerhafter Widerstand. `select_stop_and_barrier` auf den gespeicherten
Zonen reproduziert `WAIT_BREAK_RECLAIM`, `entry_overlaps_opposing_barrier`,
Barrierenraum 0R. Der bestätigte Ausbruch über die tiefere Stopzone beweist
keinen Ausbruch durch diese höhere Zone. Ein fehlender Rücktest ist hier nicht
die alleinige Sperre. Kein belegter falscher LSPD-Ausschluss; keine Lockerung.

Der neue Reader liest ausschließlich die eindeutige LSPD-Zeile aus dem
Momentum-Cache im `/proc/<API-PID>/root/tmp`-Namensraum. Stabile PID/Startzeit,
begrenzter kohärenter regulärer File-Read, feste Feldallowlists, keine Konto-
oder Zugangsdaten. PowerShell streamt den geprüften Reader über SSH und
legt nur lokal eine neue private Ergebnisdatei an; kein Überschreiben.

Originale Tageskerzen werden ausdrücklich als nicht im Ergebniscache
gespeichert ausgewiesen. Eine begrenzte Suche in bestehenden Cache-/Evidenz-
pfaden fand den ursprünglichen Same-as-of-Prefix nicht. `_daily_bars` wird
vor dem Speichern entfernt, der Run-Cache ist nur im Speicher. Herkunft,
interne Konsistenz und Auswahl wurden geprüft; die ursprüngliche vollständige
OHLCV-/VRVP-Neuberechnung ist damit nicht belegt. Kein Providerabruf, Server-
Schreibzugriff, Pull/Neustart/Scanstart oder erneuter Export erforderlich.

### Begrenzte Rundungsnachprüfung

Native konservative LONG-TP1-Rundung ergab bei der LSPD-Zone 10,22; der spätere
VRVP-Adapter übernimmt im überlappenden Fall den normal gerundeten Barrieren-
preis 10,23, auch wenn `vrvp_applied=False` bleibt. Dies erklärt die Abweichung,
ist aber kein Nachweis eines fehlerhaften ACCEPT-/Mailgates.

Vier Offline-Komponentenproben: LONG/SHORT außerhalb der Gegenbarriere behalten
die konservativen Targets und identisches R:R durch `protected_precedes_barrier`;
zwei überlappende Fälle runden anders, bleiben jedoch WAIT und nicht
einstiegsberechtigt. Expliziter Vertrag in `api.py` (`_round_trade_price_directional`)
und `test_audit_structural_invalidation.py`. Keine Quelländerung aus diesen Proben.
Grundlage `output/systemic-mail-audit-20261006-chronology-4c33/probe.py`,
vier Anschlussproben inline, Ergebnis `ASSERTIONS_OK_4_CASES`. Synthetische
bestätigte Zonenevidenz/leer gültiges Profil, kein vollständiger Scannerreplay.

## Aktuelle lesende Livekontrolle

06.10., 21:17–21:26 Zürich, bestehende Anmeldung. Admin: 1 Swing- und 1
Kryptoempfänger, 0 SMTP-Annahmen, 50 Auslassungen, 0 Fehler/0 wartend im
begrenzten letzten-50-/API-Start-/24h-Fenster. Dies ist kein Wochenprotokoll.
Die generische alte Admin-Vorprüfung zeigt Aktienstrategien 46 Kandidaten/1
bestandene Vorprüfung. Aktuelles Momentum ECHO: Score 83/Grade A, App
„Im Scan freigegeben“, Tagesqualität 70/96. Die geöffneten Details sperren
Signal-Mail ausdrücklich wegen `<78`, nicht wegen fehlendem Rücktest.
Das bestätigt die lokal korrigierte App-/Admin-/Senderabweichung auf dem
noch unveränderten Server; keine tatsächliche Signalzustellung bewiesen.
Kein Scan, Testmail, Kontoänderung oder Deploy. Temporärer Agent-Tab wird geschlossen.

## Abschlussgrenzen / nächste Schritte

1. Produktlauf, unveränderte finale Hashes und nativen LSPD-Beleg abgeschlossen;
   vollständigen Lauf und isolierte Deployment-Wiederholungen getrennt belassen.
2. Ursprünglicher OHLCV-/VRVP-Prefix für einen vollständigen LSPD-Replay fehlt;
   keinen neuen mathematischen Defekt oder vollständige Levelrichtigkeit behaupten.
3. Geprüfte lokale Änderungen separat veröffentlichen/deployen. User-retired
   `safe_deploy.sh` nicht wiederherstellen, keine Installationsmigration.
4. Ein wirklich freigegebenes Signal durch finale Revalidierung, SMTP und
   Postfach nachweisen. Technische Testmail wurde früher als angekommen
   bestätigt; diese Genehmigung nicht für weitere Mails wiederverwenden.

Methodik: Systematic Debugging, testgetriebene Entwicklung und
frontend-testing-debugging; Root las Source/Diffs und Reader selbst, prüfte
Regressionen und gerenderte Ansichten. Grüne Tests ersetzen nicht Live-Evidenz.

## Veröffentlichungsnachtrag 07.10.2026

Nutzer hat Commit und Push freigegeben. Das begrenzte Paket enthält zusätzlich
das bereits geprüfte BI-Startfeedback und gemeinsame Aktien-Datenfehlerbudget
(eigene Berichte/Regressionstests). Private `output/`-Exporte, Testdatenbanken
und separate lokale Deploy-/Commercial-Änderungen bleiben ausgeschlossen.
Kein Installer, Safe-Deploy oder Serverneustart wird durch die Veröffentlichung
ausgeführt. Vor Push wird der genaue Git-Snapshot gesondert geprüft; vorherige
Voll-Läufe des größeren lokalen WIP werden nicht als identischer Release-Snapshot
ausgegeben. Veröffentlichung und tatsächliche Zustellung bleiben getrennte Gates.

07.10., 07:54 Zürich: ausgewählte Git-Index-Produkt-/Testdateien in separaten
Snapshot exportiert und geprüft: **11.569 bestanden / 2 Windows-Skips / 0 Fehler
oder Errors**, XML-Dauer 452,5s. `output/release-verification-20261007/qa-8b465e7207f5/results.xml`.
Vier unveränderte `test_deploy*.py`-Dateien weiterhin ausdrücklich ausgenommen;
Commerce-/Kalendertests diesmal die unveränderten HEAD-Versionen, nicht das
ausgeschlossene Retirement-WIP. Skipgründe dieselben POSIX-/Symlinkbedingungen.
Offline-Harness sperrt echtes Netzwerk/SMTP und benutzt ausschließlich Fake-
Konfiguration. Runtime und Tests danach unverändert; spätere Dokukorrekturen
anonymisieren persönliche Konto-Präferenzen und ergänzen dieses Ergebnis.

Unabhängige Veröffentlichungskontrolle bestätigt keine Abhängigkeit von
ausgeschlossenem WIP und einen bytegleichen in-memory-Babel-Neubau des Bundles.
Stage enthält genau 16 freigegebene Pfade, keine privaten `output/`-Originale,
Zugangsdaten oder echten Empfängeradressen. `git diff --cached --check` sauber.
API-Gitblob `8c5bbf19d930f3b11771e2858712c6a67094f300` ist bytegleich zwischen
Index, Produktarbeitsstand und QA-Snapshot. Kein Serverupdate/Testversand.
