# LEAPS: Claude-Korrekturen vor der Anbieterentscheidung

Stand: 15.10.2026. Nutzerauftrag: die besprochenen Korrekturen umsetzen;
danach über IBKR oder einen anderen Optionsdatenanbieter entscheiden.
Dieses Dokument trennt lokale Codeprüfung von Datenzugang und Aktivierung.

## Umgesetzt

- Snapshot-Delta und IV werden als dokumentierte Anbieter-Bruchteile übernommen.
  Fehlend bleibt unbekannt; keine erfundene Modellzeit. Ein eigener begrenzter
  Greek-Parser erhält bis zu 36 Nachkommastellen, ohne Geld-Eingabegrenzen zu
  lockern. Die Präzision bleibt auch durch Pricing und Kennzahlen erhalten.
- Normale US-CS-/ADR-Calls werden über tatsächliche Kontrakt-/Listingfelder
  geprüft: Call, OCASPS, American, 100 Aktien, keine Zusatzliefergegenstände,
  passende Identität und USD-Notierung. Der reine Feldproduzent ist an Discovery
  angeschlossen; kein neues OCC-/Inode-Zertifikatsverfahren.
- HTTP 429 erhält automatische Wiederaufnahme: gültiges Retry-After, sonst
  15/30/60/120/300 Sekunden. Der isolierte HTTP-Worker reicht ausschließlich
  den validierten Retry-After-Wert durch, keine sonstigen Antwortheader.
  Nach fünf aufeinanderfolgenden 429 bleibt der Cursor als partiell erhalten;
  die automatische Schleife endet, statt unbegrenzt weiterzufragen.
- Temporäre Prioritäts-/Budgetablehnung erhält Grund und Zeitpunkt. Der
  API-Worker wartet ohne gehaltenen Request-/Owner-Lock und setzt automatisch
  fort. Manuelle Pause bleibt manuell; 401/403 bleiben gesperrt.
- SEC-429 setzt die gespeicherte Untereinheit fort, ohne Aktienquote/Historie
  erneut anzufordern. Adjustierungsnachweise überleben diesen Checkpoint.
- Bestehende Läufe behalten ihr ET-Bewertungsdatum über Mitternacht.
  Zukünftige Starts bleiben unzulässig. Die technische Probe verwendet ET.
  Ein Clock-/Validierungsfehler lässt keinen scheinbar laufenden Zustand
  zurück: Fehler und Pause werden gespeichert, Owner freigegeben und die
  vorherige vollständige Auswertung bleibt erhalten.
- Alter Ask, Zeitversatz und verzögerte Feeds liefern keinen aktuellen
  Vergleich, keine preisabhängige Filterfreigabe und keinen Rang. Bereits
  gespeicherte Ergebnisse werden vor Sortierung und Pagination erneut auf
  Ablauf geprüft. Ausgeschlossene/unprüfbare Zeilen verdrängen keine passenden.
- Ein datierter echter Ask darf weiterhin isolierte Verfallsszenarien liefern,
  aber keine Aktien-P&L/ROI aus einem unpassenden Kurszeitstand. Wiederholte
  Seiten bauen unveränderte Szenariorechnungen nicht erneut auf.
- Deskriptive Kennzahlen: innerer Wert, Zeitwert absolut/monatlich/jährlich,
  erforderlicher jährlicher Aktienanstieg bis zum Break-even, Delta-Hebel,
  Prämienkosten/-verlust, IV/HV-Differenz und Quotient. Jahres-HV verwendet
  252 logarithmische Renditen aus 253 adjustierten, vollständigen und lückenlosen
  Börsensitzungen, Stichprobennenner 251 und Annualisierung mit Wurzel 252.
  Fehlende Adjustierungs-/Historienbelege sperren nur HV. Dividendensummen
  ohne vollständigen belegten Zukunftsplan bleiben unbekannt.
- Options-Tagesvolumen und seine Originalzeit/Sitzung sind von Open Interest
  getrennt. Kein Ersetzen fehlender Werte durch Null oder OI.
  Vor Veröffentlichung zusätzlich Altdatei-Kompatibilität reproduziert und
  korrigiert: In Schema 1 gespeicherte Quotes ohne die drei neuen Volumenfelder
  bleiben lesbar; nur diese optionalen Felder dürfen fehlen. Erforderliche
  oder unbekannte Felder werden weiter strikt geprüft. Vier RED-Fälle, danach
  sechs grüne Kompatibilitäts-/Ablehnungsfälle.
- Standardfilter: Delta 0,70–0,85, Mid-Spread höchstens 10 %, OI mindestens 100,
  Aktienreferenz mindestens 10 USD. Bewusst geleerte Filter bleiben leer.
  Standardrang: aufsteigender jährlicher Zeitwert relativ zum Basiswert.
- LEAPS-UI: Server-Prämienkosten/-verlust, kleine Verfallsgrafik aus gelieferten
  Serverpunkten ohne erfundene Verbindungslinien, kompakte Kennzahlen; Chart zuerst.
  Pause ist auch während automatischer Wartezeit möglich. Operative Knöpfe/Rohcodes
  werden Nicht-Admins nicht angezeigt. Keine Änderung der Landingpage-Texte.

## Mathematische Gegenbeispiele

Bei S=100, K=80, Ask=41 und exakt 730 Tagen: innerer Wert 20, Zeitwert 21,
Zeitwert p.a. relativ zu S = 10,5 %, Break-even 121, erforderlicher jährlicher
Aktienanstieg = 10 %. Prämienrisiko eines Standardkontrakts = 4.100 USD vor
unbekannten Kosten. Delta 0,8 ergibt lokalen Hebel 80/41, keine konstante
Hebelwirkung und keine Gewinnwahrscheinlichkeit. Negativer Zeitwert wird als
inkonsistente Preisbasis unrankbar, nicht zum vermeintlichen Schnäppchen.

## Prüfungen

- Neue Gegenfälle jeweils vor ihrer Korrektur rot ausgeführt. Dazu gehören
  Datenpräzision, 429/Retry-After, Budgetwartezeit, Pause, fünfmal 429, Mitternacht,
  Clock-Rollback, alte/gekreuzte/zeitversetzte Quotes, globale Pagination,
  Anbieterfeld-Terms, Adjustierung und SEC-Checkpoint-Wiederaufnahme.
- LEAPS-Zwischenlauf: 1.115 bestanden, eine pytest-Import-Warnung; XML:
  `output/ld/qa-afcbabe76bbb/results.xml`. Keine Aussage über Livezugriff.
- Browser: bestehende reale React-App mit synthetischen HTTP-Grenzen geprüft,
  1440×900 und 390×844. Beide Flows ohne App-/Konsolenfehler, einschließlich
  realer Backend-DTOs, Preisablauf, Stückzahlen, Filter und Rechtewechsel.
  Browser-Plugin nicht vorhanden; vorhandener regulärer Playwright-Harness
  verwendet. Zwei erwartete Tailwind-Hinweise im Backend-DTO-Flow.
- Unabhängiger Abschlussreview: zwei P2-UI-Befunde (falsche Payoff-Interpolation,
  Pause während automatischer Wartezeit) mit RED-Gegenfällen behoben; 41 gezielte
  Tests bestanden. Nachprüfung bestätigt beide geschlossen, kein weiterer
  konkreter Befund im geprüften lokalen Änderungsumfang.
- Frontend gebaut und Quellhash geprüft: `6dbad8f2e7ae`.
- Erster Gesamtlauf wurde bei 39 % kontrolliert für die beiden Review-Korrekturen
  beendet; **kein** vollständiges Prüfergebnis daraus ableiten.
- Projektweiter eingefrorener Gesamtlauf vor der Altdatei-Nachkorrektur: **14.449 PASS,
  7 SKIP, 0 FAIL, 0 ERROR** in 976,11 Sekunden. Darin **1.117 LEAPS-Tests PASS**.
  XML: `output/lg/qa-ee1eff6731ce/results.xml` (14.456 Fälle einschließlich Skips).
  Die einzige Warnung betrifft die bereits erfolgte anyio-Importierung vor dem
  pytest-Assertion-Rewrite. Kein verstecktes Ignore-/Xfail-Paket.
- Ausgeführt über `scripts/run_offline_tests.py -q --tb=no --basetemp
  output/pt-1015b` mit QA-Wurzel `output/lg`. Der Offline-Launcher isoliert
  temporäre Zustände, Fake-Keys, Netzwerk und SMTP. Für lokale Socket-/Prozess-
  Tests war die Windows-Sandboxfreigabe nötig; kein echter Anbieterzugriff.
- Quellhashes von API, LEAPS-Runtime/-API/-Underlying und Frontend nach dem Lauf
  unverändert zum eingefrorenen Startstand. Frontend-Quellbezug erneut geprüft;
  auf LEAPS/API/UI/Tests/Dokumentation begrenztes `git diff --check` mit
  `core.whitespace=cr-at-eol` ist sauber. Der ungefilterte Check meldet bereits
  vorhandene CRLF-/Whitespace-Differenzen in fremdem WIP; nicht pauschal geändert.

### Sieben plattformbedingt ausgelassene Tests

Keine LEAPS-Tests wurden ausgelassen. Die folgenden bestehenden Tests benötigen
Linux-Dateisystemverhalten, POSIX-Rechte oder auf diesem Windows-Host nicht
verfügbare unprivilegierte Symlinks:

- `test_deploy_auto_update::test_auto_update_rejects_symlink_runtime_lock_directory`
- `test_deploy_migration::test_app_streamlit_parent_contract_rejects_symlink`
- `test_deploy_migration::test_physical_layout_recovers_both_app_rename_and_secret_flag_windows`
- `test_deploy_migration::test_regular_file_contract_rejects_symlink_fifo_and_hardlink`
- `test_gap_scan_schedule::test_state_permissions_are_private_on_posix`
- `test_lspd_plan_evidence_reader::test_symlink_cache_is_never_opened`
- `test_momentum_candidate_evidence_reader::test_leaf_symlink_and_directory_are_not_read_as_cache`

Browser-Belege liegen privat außerhalb des Repos unter
`C:/Users/miros/.codex/visualizations/2026/08/11/019fef3d-c9a9-7803-ab66-6631a424c1de/`:
`leaps-20261015-ui-03/` (einschließlich Pause im automatischen Warten) und
`leaps-20261015-real-dto-ui-03/` (finale punktgenaue Grafik).
Der erste Flow enthält 288 protokollierte Anfragen, darunter 44 Szenarioanfragen;
der zweite 16 POSTs mit real erzeugten synthetischen Backend-DTOs. Beide
`results.json` melden `errors: []`; keine Livekurse oder aktuelle Kaufchancen.

## Veröffentlichung auf Nutzerauftrag vom 15.10.2026

Der Nutzer hat Commit, Push und den normalen Server-Pull ausdrücklich beauftragt.
Für die Veröffentlichung wurde der Index unabhängig vom umfangreichen lokalen
WIP aufgebaut: nur LEAPS-Dateien, die eine API-Sortierzeile, der LEAPS-Block im
HTML, die neue oberste TODO-Sektion und dieser Bericht. HTML außerhalb des
LEAPS-Blocks entspricht der Basis; kein fremdes WIP pauschal aufgenommen.

Das Veröffentlichungs-Bundle wurde **aus diesem abgegrenzten Stand** neu gebaut:
`37129d2f1112`. Das zuvor lokal geprüfte Bundle `6dbad8f2e7ae` enthält auch das
übrige lokale WIP und wird deshalb nicht unverändert veröffentlicht.

- Isolierter Veröffentlichungs-Snapshot: **1.155 PASS, 0 FAIL/ERROR/SKIP**;
  darunter 1.123 LEAPS- und 32 Frontend-Vertragstests. XML:
  `output/li/qa-626b05e8c975/results.xml`, 105,54 Sekunden.
- Sechs zusätzliche Altdatei-Regressionen sind enthalten. Ein unabhängiger
  Nachreview bestätigt die enge Decoder-Korrektur sowie API-/HTML-/TODO-
  Commitgrenzen und den passenden Quellhash des Veröffentlichungs-Bundles.
- Derselbe abgegrenzte Build besteht beide realen React-Browserflüsse bei
  1440×900 und 390×844: `leaps-20261015-publish-ui-03/` und
  `leaps-20261015-publish-dto-ui-01/` im oben genannten privaten Belegordner.
  288 Anfragen/44 Szenarioanfragen bzw. 16 POSTs, jeweils `errors: []`.
- Zwei vorherige Browserläufe sind als FAIL-Belege erhalten: `publish-ui-01`
  traf das Playwright-Rennen `pauseAt(Date.now())` gegen die laufende Uhr;
  `publish-ui-02` prüfte den noch sichtbaren Pause-Knopf vor dem nachfolgenden
  Status-/React-Update. Im Harness wird nun der echte Disabled-Zustand abgewartet
  und die laufende virtuelle Uhr mit `fastForward` statt rückwärts anfälligem
  `pauseAt` verwendet. Kein Produktfehler wurde dafür durch Wiederholen verdeckt.
- Neuer projektweiter Offline-Nachlauf nach der Altdatei-Korrektur:
  **14.455 PASS, 7 SKIP, 0 FAIL/ERROR**, 1.313,09 Sekunden; darin 1.123 LEAPS-Tests.
  XML: `output/lh/qa-d20590f0cc11/results.xml`. Dieselben sieben oben genannten
  plattformbedingten Skips und dieselbe anyio-Import-Warnung; keine LEAPS-Skips.
  Dieser projektweite Lauf betrifft die gesamte Arbeitskopie inklusive
  geerbtem WIP. Der getrennte 1.155er-Lauf und beide Browserflüsse prüfen
  ausdrücklich den isolierten Veröffentlichungsstand. Ein projektweiter
  Gesamtlauf nur dieses isolierten Stands wird damit nicht behauptet.

## Bewusst noch offen — nach der Anbieterentscheidung

1. Vorhandenes IBKR-Konto berücksichtigen; tatsächliche Options-/Basiswertrechte,
   echte Bid/Ask/Greeks/OI, Nachtpreise und zulässiges Publikum feststellen.
   Stocks Starter und die Wahl „nur echte Bid/Ask“ sind bereits bekannt.
2. Den gewählten realen Leseadapter anschließen. Keine Zugangsdaten erfunden,
   keine aktuell oder früher veröffentlichten Schlüssel für Probes benutzt.
3. Anbieterabhängige Universumsanreicherung (Watchlist/Scanner-Treffer,
   datierte Market Cap mindestens 2 Mrd. USD und 20-Tage-Dollarvolumen mindestens
   20 Mio. USD), wöchentlicher Referenzcache und tatsächlicher Nacht-Schlussstand.
   Diese Daten nicht aus Preis, OI oder unvollständigen Facts erraten.
4. Darauf aufbauend den wirklichen BG-Nachtlauf mit gemeinsamer Laststeuerung
   verbinden. Kein neuer Scheduler oder Provider-/Rechtebeleg wurde vorgetäuscht.
   Die bisherige Admission-/Store-Implementierung wurde nicht blind gelöscht
   und keine zusätzliche Owner-/Authority-/Checkpoint-Hierarchie aufgebaut.
5. Erst dann gesondert erlaubte technische Probe und echter Admin-Lauf.
   Persönliches Datenabo ist kein Nachweis einer Kunden-Anzeigelizenz.

Keine Abobuchung, Brokerverbindung, Orders, Signal-Mails, Konto-/Serveränderung,
Installationsmigration oder Wiederherstellung des gelöschten Deployskripts.
Stand ist lokal, nicht committet/gepusht und nicht auf Hetzner installiert.
Vorhandenes fremdes WIP bleibt erhalten.
