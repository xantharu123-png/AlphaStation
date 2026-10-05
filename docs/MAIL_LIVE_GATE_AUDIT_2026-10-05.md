# Signal-Mailkontrolle nach dem Betreiberupdate – 05.10.2026

## Verifizierter Serverstand

- API-Health um 13:37 UTC: `healthy`, Revision `43180ffea286`,
  Frontend-Bundle `67924e9f894a`. Der Betreiber hat das vorige Paket aktualisiert.
- `43180ff` enthält den Codecommit `f46413c` mit BI-Backtest-Diagnosen und
  PREPARED-Recovery. Hier kein Pull, Neustart, Scanstart, Testmail oder Settings-Write.
- Die bereits früher bestätigte technische Testmail beweist nicht den heutigen
  Signalweg. Eine neue echte Signalannahme/Postfachzustellung wurde nicht beobachtet.

## Heutige App-Befunde

Die angemeldete Admin-Mailansicht wurde um 15:38 Zürich rein lesend geprüft:
ein Swing- und ein Kryptoempfänger, 0 SMTP-Annahmen, 50 ausgelassene Entscheidungen,
0 gemeldete Versandfehler und 0 wartende Mails. Das sind maximal 50 RAM-Ereignisse
seit API-Start innerhalb von 24 Stunden, **kein vollständiger Tagesnachweis**.
Die sichtbaren Ereignisse reichen ungefähr von 11:27 bis 15:33 Zürich.

Nachprüfung um 16:07 Zürich: weiterhin 0 SMTP-Annahmen, 50 Auslassungen,
0 gemeldete Versandfehler und 0 wartend. Zwei neue generische Entscheidungen
um 16:06 sind auch nach Beginn der US-Handelssitzung sichtbar. Ihre Herkunft
und der ursprüngliche Grund sind aus der RAM-Ansicht nicht rekonstruierbar;
die Uhrzeit allein beweist keinen Datenproviderfehler. Admin → System Logs
zeigt 0 Zeilen / „Keine Logs vorhanden“. Dieser UI-Endpunkt liest eine separate
Scannerlogdatei, nicht das systemd-Journal; deshalb ersetzt er keine fehlende
Protokollstelle.

Die Scanner-Vorprüfung hat keine aktuellen mailfähigen Pläne ergeben. Sie liest
nur gespeicherte Kandidaten und ist weder Scannerabschluss noch SMTP-Versuch:
37 Aktienstrategie-Kandidaten, 13 Gap-Long- und 3 Gap-Short-Kandidaten wurden geprüft;
jeweils 0 freigegeben. Sichtbare Aktienentscheidungen nennen niedrigen Trade-Score,
Tagesqualität unter 78/96, bereits berührtes/nahes TP1 oder dünne 20T-Dollar-Liquidität.
Bei Krypto dominieren Watch-/Retest-Zustand, Score/Grade und fehlender BTC-Kontext.

### DAC: konkrete vorgelagerte Blockade

- Momentum-Kandidat: Schlusskurs 165,15, Trade-Score 88/S, Tagesqualität 87/96.
- Gespeicherter Plan: Entry 165,15, Stop 162,67, TP1 170,10, TP2 174,23.
- Die App nennt beide Ziele ausdrücklich Projektionen ohne bestätigte Gegenbarriere.
- Die Zahlen entsprechen dem echten Projektionspfad: `165,15 × 1,03 = 170,1045`
  und `165,15 × 1,055 = 174,23325`. Die Zielqualität wird
  `PROJECTION_ONLY_NO_CONFIRMED_BARRIER`; die Mailregel liefert
  `trade_target_not_structural`.
- Der fehlende Rücktest ist **nicht** diese Blockade. Score und Tagesqualität allein
  geben einen nicht strukturell belegten Zielplan nicht frei.
- Ob ein echter höherer Widerstand übersehen wurde, bleibt ohne die gespeicherten
  Originalzonen, Zeitgrenzen und Kerzenzahlen offen. Kein unbewiesenes ATH-Urteil.

Eine zusätzliche unabhängige Quellprüfung fand keinen nachweisbaren
DAC-Richtungs-/Zeitrahmenfehler. Der Strukturpfad bindet D/W/4H an Richtung,
Signal-Session und `analysis_as_of`; LONG nutzt Gegenbarrieren über/am Kurs.
Die Referenzprüfung entfernt reine PDC/PWC-Referenzen, nicht pauschal
PDH/PDL/PWH/PWL-Strukturen (`modules/level_zones.py`,
`test_level_zone_normalization_reuse.py`). Das ist eine begrenzte negative
Quellprüfung, keine Nachrechnung der realen DAC-Zonen.

Für die nächste konkrete Prüfung erforderlich: dieselbe Original-Scannerzeile
mit sämtlichen `level_structure.zones/evidence`, `as_of`,
`completed_bar_counts`, `quality_flags`, `trade_setup.pre_vrvp_structure_decision`,
Zielprovenienz und `vrvp_levels.resistances`. Nur so lässt sich eine tatsächlich
fehlende Barriere von einer übersehenen oder zeitlich unbestätigten unterscheiden.

### Neuer BI-Long-Lauf

Der bereits automatisch laufende BI-Long-Scan wurde bis zum Abschluss beobachtet;
kein zweiter Scan gestartet und die Seite nicht neu geladen. Ergebniszeit
`2026-10-05 14:01:29.190775` (Serverzeit): 5.319/5.319 geprüft, 4.186 analysiert,
120 ungültige Kursdatensätze für diesen Lauf ausgeschlossen, 0 gültige BI-Signale.
Weitere protokollierte Ausschlüsse: Liquidität 729, SPAC/NAV 109;
Diagnosepunkte können überlappen. Der Lauf ist mit Datenausschlüssen abgeschlossen,
nicht wegen einer einzelnen Aktie abgebrochen. Kein Fehlerdatensatz dauerhaft gesperrt.
Die App hat Fortschritt und neuen Ergebnisstand automatisch angezeigt.

## Zusätzlich reproduzierter und lokal korrigierter Diagnosefehler

Acht konkrete finale Swing-Ablehnungsgründe fehlten im zulässigen Telemetrieregister
und in den Admin-Labels, unter anderem `swing_delayed_price_or_path_unconfirmed`.
Dadurch wurden echte Daten-/Plan-/Kurswegblockaden als `unclassified_code_reason`
angezeigt beziehungsweise gezählt. Die generische Anzeige garantiert außerdem
fälschlich, dass der Originalgrund im Betreiberprotokoll steht; der normale
Swing-Ablehnungspfad druckt ihn nicht zwangsläufig.

Korrektur ausschließlich an Diagnose/Telemetrie:

- Acht echte Swing-IDs bleiben in den vorhandenen anonymen Aggregatzählern erhalten.
- Admin erkennt zusätzlich die leere final geprüfte Kandidatenliste und vier feste
  Mail-Sitzungszweige. Deren Aufnahme im Register ist keine Behauptung neuer
  branch-spezifischer Producerzähler; Sitzungszweige zählen bisher weiterhin
  `stock_session_not_executable`.
- Gemischte Groß-/Kleinschreibung im privaten Sitzungsdetail verliert nicht den
  festen Zweig. Das Detail wird nicht in den anonymen Aggregatzählern gespeichert
  und nicht in der redigierten Admin-Maildiagnose ausgegeben. Der vorhandene
  RAM-Ereignispuffer und die separate Admin-Route `/api/email-alert-status`
  können weiterhin rohe Gründe enthalten; dieser Patch ändert diese Route nicht.
- Eine nicht verfügbare Sitzungsprüfung wird nicht als bestätigter Börsenschluss
  bezeichnet. Unbekannte Gründe bleiben unbekannt, ohne Protokollversprechen.
- Keine Freigaberegel, Preisprüfung, Reservierung, Zustellung oder Order geändert.

Die heutigen generischen Ereignisse lassen sich nachträglich nicht eindeutig den
Swing-Pfadfehlern zuordnen: Auch Sitzungs-Auslassungen hatten denselben allgemeinen
Text. Die Reparatur ist kein Beweis eines heutigen Provider-/SMTP-Ausfalls.
Der fehlerhafte `quote_capability`-Kontrolltest ist diagnostisch, kein globales
Swing-Mailgate; Kandidaten werden separat geprüft.

## Verifikation und verbleibende Arbeit

- TDD: zunächst 31 der 34 neuen Fälle wegen verlorener Codes fehlgeschlagen;
  3 Privacy-Gegenproben bestanden. Nach unabhängiger Prüfung weitere 12 Randfälle
  zunächst reproduziert, danach korrigiert.
- Finale gezielte Prüfung: 184 bestanden, eine bekannte anyio-Importwarnung.
  Artefakt: `output/mail-fix-qa-fe2e4c36ba514cc88f3e6ac6f5fcaae5/results.xml`.
- Unabhängige Nachprüfung des Diagnosepatches ohne verbleibende Befunde.
- Ein vorläufiger Gesamtlauf während der Bearbeitung ergab 11.556 bestanden,
  7 fehlgeschlagen und 5 übersprungen. Zwei Fehler zeigten das noch nicht
  synchronisierte Register im eigenständigen Exportcollector; dieses wurde
  anschließend korrigiert. Fünf Herkunfts-/Fingerprintprüfungen erkannten die
  während des Laufs geänderten Quelldateien. Ihre Schutzprüfung bleibt unverändert.
- Danach 407 gezielte Collector-, Registry-, Mail- und Historyprüfungen bestanden:
  `output/mail-fix-qa-15856d65a0624da998a878702095158c/results.xml`.
- Finaler isolierter Gesamtlauf mit unveränderten Quellen: **11.575 bestanden,
  0 fehlgeschlagen, 0 Fehler, 5 übersprungen**, eine bekannte anyio-Importwarnung.
  Dauer 691,50 s; XML umfasst 11.580 Fälle. Nicht mit den älteren 11.274 Tests
  gleichsetzen oder überlappende gezielte Prüfungen addieren.
  Artefakt: `output/mail-fix-qa-8a1c3d5cdb8a406b8c31dcd5c9846c8f/results.xml`.
- Ausnahmen: `test_deploy_auto_update.py:405` (Windows-Symlinkrecht),
  `test_deploy_migration.py:896`, `:1019`, `:1194` (Linux-O_NOFOLLOW/
  O_DIRECTORY/FIFO-/Atomic-Rename-Verträge), `test_gap_scan_schedule.py:329`
  (POSIX-Dateirechte unter Windows). Diese Linux-Grenzen bleiben ungeprüft,
  keine Produktfehler werden dadurch als bestanden ausgegeben.
- SHA256 vor/nach dem finalen Gesamtlauf unverändert:
  `api.py` `08e1982a5b495992aa377ae4687b3819fb5f512ed8114a9629a29ed0e4d2de70`,
  `modules/suppression_telemetry.py`
  `5b410e69c4824b1dce0ce189cf4eecb24afd8a64745df2c79d0556fdb72484e0`,
  `scripts/collect_server_evidence.py`
  `f566c61f954314bfd9816fb26843f055cfd582049f71032aa659151aa9e02b9c`,
  `test_swing_mail_rejection_diagnostics.py`
  `f3e6dbf16afc3f7f4cc8ebf7223b96c6e047580562270c2a543fc0d9e6fb1c7f`.
  Die fünf Fingerprintfehler des vorläufigen Laufs treten im eingefrorenen
  Wiederholungslauf nicht mehr auf. `git diff --check` ohne Formatfehler;
  vorhandene LF/CRLF-Hinweise sind keine Testfehler.
- Diagnosekorrektur nach ausdrücklicher Freigabe veröffentlicht:
  Codecommit `d566c5d973bd645270ae306cdbf7f6f77f0bba9b`, `origin/main` bestätigt.
  Nicht als auf Hetzner installiert bestätigt. Die ursprüngliche Reparatur
  ist dagegen live bestätigt. Keine neue Mailfreigabe durch diesen Bericht.
- Offen: konkrete Originalzonen für DAC nachprüfen, neuen historischen BI-Lauf
  auswerten und eine tatsächlich zulässige Signal-Mail bis zum Postfach nachweisen.
  Keine künstlichen Zielwerte freigeben und keine Kriterien pauschal lockern.

Private Exporte und QA-Artefakte nicht auf GitHub veröffentlichen.

## Veröffentlichungsprüfung nach ausdrücklicher Freigabe

- Ausgangsrevision auf `origin/main` nochmals bestätigt:
  `43180ffea286efcd408e7805a8880d67fb67652a`.
- Saubere Releasekopie aus dieser Revision statt Veröffentlichung des gesamten
  schmutzigen Arbeitsverzeichnisses. Unabhängiger Bytevergleich aller 622
  Ausgangsdateien: nur API, Telemetrieregister und eigenständiger Collector
  verändert; zusätzlich nur neuer Regressionstest und dieser Bericht.
  Für die TODO wird nur der aktuelle Diagnoseabschnitt übernommen.
- Der erste Releasekopie-Lauf ergab 11.572 bestanden, 1 fehlgeschlagen,
  5 übersprungen. Der Fehler `test_auto_update_is_versioned_executable`
  kam aus fehlenden Git-Metadaten der ZIP-Kopie (`git ls-files` ohne Ergebnis),
  nicht aus dem Mailpatch. Git-Metadaten der Ausgangsrevision wurden ausschließlich
  in der isolierten Testkopie ergänzt. Der unveränderte Test bestand danach:
  `output/mail-diagnostics-publication-20261005-candidate/qa-b40718d38b7c/results.xml`.
  Produkt-, Deploy- und Testcode wurden dafür nicht geändert; kein Test umgangen.
- Vollständiger Releasekopie-Lauf nach der Umgebungskorrektur:
  **11.573 bestanden, 0 fehlgeschlagen, 0 Fehler, 5 Plattformausnahmen**,
  ein bekannter anyio-Importhinweis, 859,59 s.
  `output/mail-diagnostics-publication-20261005-candidate/qa-d434dfc378a4/results.xml`.
  Der isolierte Ausgangsbaum enthält kein fremdes Deploy-/Test-WIP; deshalb
  wird diese Zahl nicht mit den 11.575 Fällen des gesamten Arbeitsbaums vermischt.
  Die fünf Ausnahmen entsprechen den oben dokumentierten Windows-/Linux-Grenzen.
  Die vier Produkt-/Testquellen blieben vor/nach diesem Lauf unverändert.
  Staginginhalt entspricht der Testkopie; beim Collector nur normale
  Git-Zeilenendennormalisierung. Keine Serveränderung.
- Codecommit `d566c5d973bd645270ae306cdbf7f6f77f0bba9b` gepusht und direkt
  durch `git ls-remote origin refs/heads/main` bestätigt. Nur sechs geprüfte
  Dateien veröffentlicht; kein privater Export und kein fremdes WIP übernommen.
  Die anschließende Statusdokumentation ändert keinen Produktcode.
