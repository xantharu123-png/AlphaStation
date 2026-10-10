# Getrennte Ausbruch-Beobachtung – 11.10.2026

## Auftrag und tatsächliche Ursache

Der Nutzer hat die getrennte Benachrichtigung für bestätigte Ausbrüche ohne
vollständigen historischen Zielbeleg genehmigt. Anschließend hat er ausdrücklich
„Ja, Beobachtungs-Mails aktivieren“ für seinen eigenen Account bestätigt.

Die angemeldete Live-Prüfung zeigte bei TBBB und PLTR einen bestätigten Ausbruch
zum Tagesabschluss 09.10., ausreichende Scores und Tagesqualität, aber als konkreten
Trade-Mail-Sperrgrund `trade_target_not_structural`. Ohne historischen Gegenlevel
erzeugt der native Builder Projektionsziele; der bestehende Full-Trade-Guard lehnt
Projektions-TP1 ab. Eine bestätigte Beobachtung ist deshalb nicht automatisch ein
vollständiger freigegebener Entry-/Stop-/TP-Plan. Ein allgemeiner SMTP-Ausfall oder
die vollständige wochenlange Ursache ist damit nicht nachgewiesen.

Watchlist-Mails waren im eigenen Account AUS. Nach ausdrücklicher Zustimmung auf AN
gespeichert; Erfolgsmeldung und sichtbare Account-Einstellung verifiziert. Signal-Mails
bleiben AKTIV, Modus Swing und andere Kanäle unverändert. Kein Scan oder Mailversand
wurde dafür gestartet. Lokaler UI-Beleg: `output/watch-optin-full-20261011.jpg`.

## Implementierung

- Neue separate WATCH-Mails im vorhandenen Kanal `stocks_swing` für Momentum
  Breakout Long, Gap Momentum Long und Gap Momentum Short.
- Aktuelle abgeschlossene 1D-Session und numerisch belegter Ausbruch erforderlich.
  Gap-Evidenz ist an Modell, Ticker, Richtung, Zone, Grenzpreis und gespeicherten
  Referenzschluss gebunden. Fehlerhafte Snapshot-Formen werden verworfen.
- Nur der fehlende historische Gegenlevel darf die Trade-Mail blockieren.
  Erforderlich bleiben native kausale Invalidation, ACCEPT ohne Gegenbarriere,
  passende Richtung/Kursbasis und übrige Score-/Qualitäts-/Geometrie-/R:R-Prüfungen.
  Der früh zurückgegebene Projektionsgrund darf keine weiteren Zielfehler verdecken.
- Mail enthält ausschließlich Ticker, Strategie, Richtung, Tagesdatum,
  beobachteten Schluss und Ausbruch-Level. Keine Entry-/Stop-/TP-/R-Werte und
  keine als beobachtet ausgegebene Rücktestbestätigung.
- Kein Trade-Tracker-Eintrag, kein Trade-Intent, kein normaler Trade-Cooldown.
  WATCH-Empfänger und bestehende Kanalpräferenz müssen tatsächlich freigeschaltet sein.
- Normale Signale werden zuerst versucht; Hinweise nutzen nur das tatsächliche
  Restbudget des bestehenden gemeinsamen Senderlimits. Fehlerhafte Hinweise dürfen
  weder bei der Vorprüfung noch beim Versand andere gültige Kandidaten abbrechen.
- Eigene dauerhafte Identität pro Ticker/Strategie/Richtung/Session/Ausbruch-Level.
  Normale Trade-Claims behalten ihre bisherigen 900 Sekunden. Nur Beobachtungs-Claims
  bleiben sieben Tage reserviert: unklare SMTP-Annahme darf auch ohne Outbox und nach
  verlorenem RAM nicht nach 901 Sekunden erneut versendet werden. Ein definitiver
  Nichtversand gibt den eigenen Claim frei; Unklarheit ist kein „gesendet“-Zeitstempel.
- Konservative Konsequenz: Ein Prozessabbruch mit noch reservierter Beobachtung
  kann diesen einen Hinweis kosten. Er blockiert weder die nächste Session noch
  einen eigenständig qualifizierten Full-Trade. Keine blinde Folgetag-Queue.

## Grenzen

Cup ist nicht in diesem neuen Pfad: sein aktueller Finalizer markiert Projektions-TP1
mit REJECT/causal_structure_missing. Das darf ohne zusätzlichen echten Beleg nicht
als bloß fehlender Gegenlevel gelesen werden. BI, Krypto, weitere Scanner, Full-Trade-
Grenzen und Backtester sind durch diese Änderung nicht allgemein repariert oder
neu freigegeben. Originale vollständige TBBB-/PLTR-Kerzen wurden nicht unabhängig
historisch nachgebaut; keine Allzeithoch-/Providerfehler-Behauptung.

## Prüfung

- TDD: Vor Implementierung fehlt der erwartete native Momentum-Beobachtungsversand.
  Weitere RED-Läufe reproduzierten WATCH-vor-TRADE, ungefangene Fehler, Wiederholung
  nach kurzer Claim-Laufzeit sowie drei missgebildete native Gap-Snapshots.
- Native OHLCV → Produzent → Plan → echter Sender → simuliertes SMTP:
  Momentum, Gap Long und Gap Short, 3 passed; keine persistierten Trade-Zustellungen.
- Unabhängiger Code-Review: zunächst drei Important- und zwei Minor-Funde,
  anschließend zusätzlicher Discovery-Fehler. Alle konkret reproduzierten Funde
  korrigiert; abschließender Review ohne offenen Critical/Important/Minor-Fund.
- Frischer kombinierter Lauf: **170 passed / 0 failed / 0 skipped**,
  1 bekannte AnyIO-Warnung, 111,23 s; darin 89 Grenzfälle. XML
  `output/breakout-notice-final-combined-20261011/qa-2a7b0b830707/results.xml`.
- Atomare Mail-Sperre: **10 passed / 0 failed / 0 skipped**, echte temporäre
  Dedupe-Datei, keine simulierte Sperrantwort. Gewöhnliche Trade-Claims bleiben
  vor 900 Sekunden gesperrt und werden anschließend wieder frei; eine echte
  Sent-Markierung sperrt weiterhin bis zum bestehenden Versand-Cooldown. XML
  `output/breakout-atomic-durable-green-20261011/qa-f0735daf3149/results.xml`.
- Erster vollständiger Lauf: **12.398 passed / 103 failed / 7 skipped**,
  1 bekannte AnyIO-Warnung. Kein Gesamtpass. XML
  `output/breakout-notice-full-final-20261011/qa-ea873238132d/results.xml`.
  Exakter Vergleich gegen die Vorher-Baseline: dieselben 97 Deployment-Fehler
  und sechs zusätzliche Fehler, alle mit Namen und Trace im XML:
  - `test_email_dedupe_atomic::test_api_and_background_service_share_atomic_dedupe_helpers`:
    veraltete Quelltext-Literalprüfung, durch die echte Speicherprüfung oben ersetzt.
  - `test_scanner_history_bi::test_history_bi_all_fixed_assets_remain_present_even_when_source_unavailable`
  - `test_scanner_history_source_freeze::test_unchanged_import_baseline_is_a_copy_not_mutable_global_state`
  - `test_scanner_history_source_freeze::test_crypto_directory_output_carries_all_source_provenance`
  - `test_scanner_history_stock_review::test_stock_replay_missing_one_asset_session_cannot_shrink_every_assets_cohort`
  - `test_scanner_history_stock_review::test_stock_replay_checks_frozen_window_not_just_cohort_symbols`
  Die letzten fünf meldeten korrekt `scanner_calculation_sources_changed_during_replay`:
  Teststart 22:13:43 UTC, reine Zeilenendungsbereinigung in `api.py` um 22:14:05 UTC.
  Die historischen Guards binden rohe Quellbytes beim Import. Sie wurden nicht
  abgeschwächt. Separate Wiederholung der drei History-Suites und atomaren Mail-Sperre:
  **63 passed / 0 failed / 0 skipped**, 1 bekannte AnyIO-Warnung, XML
  `output/breakout-frozen-history-check-20261011/qa-2a426db522fa/results.xml`.
  Import-Hash vorher
  `d5953eaaaf0d422d6093faecb34b188ddfac3f7a3649ef8b77eea6263789dc56`, danach
  `c76898b023edbc90da4448eff778ef92232bdb66c6b2a25302ed548e630adbde`.
- Wiederholung auf unveränderten Quellen: **12.404 passed / 97 failed / 7 skipped**,
  1 bekannte AnyIO-Warnung, 663,07 s; **kein Gesamtpass**. XML
  `output/breakout-notice-frozen-full-20261011/qa-8f5b343e5834/results.xml`.
  Exakter Vergleich sortierter `classname::name` gegen die Vorher-Baseline:
  keine zusätzlichen oder weggefallenen Fehler. Unverändert Deployment
  Auto-Update 55, Migration 10, Retirement 5, Security-Reaudit 27. Die WindowsApps-/
  WSL-Bash-Aufrufe können die übergebenen Windows-Pfade nicht öffnen; diese
  vorhandenen Deployment-/Testharness-Probleme wurden hier nicht umgebaut.
  Alle 89 neuen Ausbruch-Grenzfälle und drei neuen nativen Produzentenfälle
  bestanden; API-Quellhash vor/nach diesem Lauf unverändert.
- Separater Git-Index-Abzug exportiert: **180 passed / 0 failed / 0 skipped**,
  1 bekannte AnyIO-Warnung, 116,91 s. XML
  `output/breakout-release-index-20261011/qa/qa-f8bde43536ef/results.xml`.
  Dieser Abzug enthält den vollständigen benötigten Git-Stand, aber nur sieben
  vorgemerkte Änderungen; kein privates `output/` und kein übriges WIP im Commit.
  API stimmt roh mit dem Index-Blob überein; die LF-Testdateien stimmen nach
  regulärer Git-CRLF-Checkout-Konvertierung mit ihren Index-Blobs überein.
- Zusätzlicher Abzug über Git-Archiv: nochmals **180 passed / 0 failed / 0 skipped**,
  1 bekannte AnyIO-Warnung, 97,98 s. XML
  `output/breakout-release-index-20261011/qa-raw/qa-3dce3e1e4434/results.xml`.
  Derselbe unveränderte Inhalt und dieselben Fälle, keine zusätzliche Abdeckung;
  auch Git-Archiv nutzt unter `core.autocrlf=true` die normale Checkout-Konvertierung.

Externe Netzwerkverbindungen und echtes SMTP sind in allen Offline-Läufen gesperrt;
Testadressen sind `.invalid`, Auth-/Tracker-/Outbox-/Dedupe-Daten temporär isoliert.
Ein vor dem letzten Patch angehaltener Gesamtlauf und ein während einer Teständerung
gestarteter kombinierter Lauf mit alter Text-Erwartung zählen nicht als finaler Pass.

## Veröffentlichung und offene Live-Prüfung

Feature-Commit `7be76b50b518359fb8cfa1d1ce66878fe5fa1685` ist erfolgreich nach
`origin/main` gepusht. Nur sieben Mail-Code-/Test-/Übergabedateien veröffentlicht.
Privates `output/` und die übrigen übernommenen Änderungen bleiben lokal und unberührt.

Frischer tatsächlicher Healthbeleg: healthy, `47d0b9ed93c2`, Bundle `8f8a6c0c5bae`,
Serverzeit `2026-10-10T22:38:09.186826` ohne expliziten Offset. Feature dort noch nicht
installiert; keine echte neue Beobachtungs-Mail oder reguläre Signal-Mail im Postfach
nachgewiesen. Keine Testmail, kein Replay, Pull, Neustart oder Produktions-Codeänderung
durch diesen Arbeitsschritt. Nach Veröffentlichung muss die Serverrevision und danach
die nächste qualifizierte reguläre Zustellung geprüft werden.
