# Nachprüfung: gespeicherte Scan-Freigabe und Chartkurs

Datum: 29.09.2026. Basis: `5acd7cf3468116a0fdf8437659be4baaac4f59f2`.

## Verifizierter Stand der Vorarbeiten

- Health von Hetzner, gelesen am 29.09., Serverzeit `19:20:35.336808`:
  `healthy`, Revision `5acd7cf34681`, Frontend `22c113cb3556`.
  Der zuvor offene Rollout dieser Version ist damit belegt.
- Die bisherigen Preisbasis-/Wyckoff-Korrekturen sind im Checkout enthalten.
  Ihre aktuellen Regressionstests wurden zusammen mit den nachfolgenden
  Ergänzungen ausgeführt. Frühere vollständige Testläufe stehen im
  [ursprünglichen Prüfbericht](CHART_PRICE_WYCKOFF_AUDIT_2026-09-28.md).

## Verbliebener Fehler und Korrektur

Die Tabelle bezeichnete eine gespeicherte Freigabe als „Signal freigegeben“.
Die Seitenleiste verglich dagegen den passenden Chartkurs mit den gespeicherten
Planleveln. Ein Kurs vor dem Entry führte zu „Einstieg noch nicht erreicht“ und
`WAIT_FOR_TRIGGER`. Auch nach einem früheren Überschreiten des Entry wurde so
ein nicht belegter historischer Ablauf behauptet.

- Tabelle und gemeinsame Kandidatenpräsentation: **„Im Scan freigegeben“**.
  Der bestehende Datenstand bleibt separat sichtbar. Ein Referenzschlussdatum
  wird nicht als tatsächlicher Scan-Zeitpunkt ausgegeben.
- Seitenleiste: **„Chartkurs unter Entry“** (Long) bzw. **„Chartkurs über Entry“**
  (Short), ohne behauptete Erstberührung oder frische Trigger-Freigabe.
  Der reine Kursvergleich bleibt `WATCH_ONLY`, interner Zustand
  `price_before_entry` statt `entry_pending`.
- Stop und TP1: „Chartkurs am/unter …“ bzw. „am/über …“ berücksichtigt auch
  exakte Gleichheit. Kein Text behauptet eine ausgeführte Stop-/Zielorder.
- Entry, Stop, Ziele, aktuelle Kursquelle und R-Berechnung bleiben unverändert.
  Kein Backend-/Scanner-/SMTP-Code geändert, keine Gate-Lockerung.
  1D-Strukturreminder und die getrennte Mail-/App-Auswahl bleiben verfügbar.

## Nachweise

1. Neue Regressionen vor der Korrektur: **20 fehlgeschlagen, 88 bestanden**.
   Reproduziert wurden beide Richtungen, frühere Kurse jenseits des Entry,
   fehlende Historie, Stop-/Zielgleichheit und die gemeinsame Tabellen-/Sidebaransicht.
2. Dieselben beiden Testdateien danach: **108 bestanden**.
3. Erweiterte Nachprüfung: **1.452 bestanden** in 57,29 s. Ein pytest-Hinweis
   zur bereits importierten Bibliothek `anyio`, keine fehlgeschlagenen Tests.
   Ausgeführt wurden alle `test_frontend*.py` und `test_wyckoff*.py` im
   Projektroot sowie `test_reminder_controls_behavior.py`,
   `test_trade_reminder_ui.py`, `test_scanner_structure_reminders.py` und
   `test_crypto_reminder_capability.py` mit dem isolierten Launcher:

   ```powershell
   & '.\.codex_pytest_env\Scripts\python.exe' -B tmp/offline_mail_fix_tests_20260925.py -q --tb=short <Testdateien>
   ```

   Er verwendet ausschließlich temporäre Testdaten und Fake-Konfiguration;
   externe Provider- und SMTP-Verbindungen sind gesperrt.
4. Lokaler Browser: tatsächliches neu gebautes Frontend, synthetische VIAV-
   Daten, alle Requests abgefangen. Long/Short jeweils 1440×900 und 390×900.
   Tabellen- und Planüberschriften, geschlossene historische Bewertung,
   separater Wyckoff-Schalter, vorhandene Reminder-Steuerung und Seitenbreite
   geprüft. **Keine Konsolenfehler, kein Seitenleistenüberlauf.**
   Sichtprüfung der Desktop-/Mobilbilder durchgeführt.
   Die Playwright-CLI war nicht offline verfügbar; verwendet wurde die bereits
   installierte Playwright-Bibliothek mit Chromium, ohne Paketinstallation.
5. Bundle neu gebaut und geprüft: **`00c5288ac5f0`**. JS-Syntax und `git diff --check`
   erfolgreich. Private Testartefakte unter `output/playwright/scan-price-basis-20260929/`
   und `output/mail-fix-qa-e572292e0c974679a292449da63a63d5/`, nicht für GitHub.

## Weiterhin offen

- Deployment **dieser** Nachkorrektur; diese Sitzung änderte Hetzner nicht.
- Exakter Replay der acht alten VIAV-Wyckoff-Strukturen ohne deren eingefrorene
  Originalkerzen weiterhin nicht möglich. Synthetische UI-Daten ersetzen ihn nicht.
- Neue vollständige Scannerläufe und tatsächliche Signal-Mailzustellung nach
  den früheren Reparaturen sind hier nicht frisch nachgewiesen. Ein Health-Check
  oder ein UI-Status ist kein Empfangsnachweis.

Die vorhandene Übergabe wurde erhalten und in [TODO.md](../TODO.md) aktualisiert.
