# Gap Momentum — fester Zeitplan und Mail-Audit

Stand: 30.09.2026. Auftrag: vorhandene Gap-Momentum-Scanner Long/Short
um 02:00 und 12:00 Europe/Zurich betreiben, fachlich prüfen und
Versandursachen nachvollziehbar reparieren. Kein zusätzlicher After-Hours-Scanner.

## Verbindlicher Zeitplan

- Eigene Jobs `strat_gap_momentum_long` / `strat_gap_momentum_short`.
- Montag bis Freitag, 02:00 und 12:00 **Europe/Zurich**, inklusive Zeitzonenwechsel.
  Samstag und Sonntag beginnen keine automatischen Gap-Läufe. Ein bereits
  laufender Worker wird nicht gewaltsam beendet.
- Die bisherigen stündlichen Gap-Läufe im allgemeinen Aktien-Sweep entfallen.
  Momentum Breakout und Cup-and-Handle bleiben dort erhalten.
- Beide Gap-Richtungen haben beim jeweiligen Slot Vorrang vor der nächsten
  allgemeinen schweren Aktienrunde. Ein bereits laufender Besitzer wird nicht
  überlappt oder beendet; der zweite Gap-Lauf wartet auf den freien Besitzer.
- Laufplätze werden vor Worker-Zulassung atomar im privaten
  `ALPHA_DATA_DIR/gap_scan_schedule.json` gespeichert. Kein zusätzliches
  Startup-Scanning aufgrund eines fehlenden Ergebnis-Caches. Neustarts verbrauchen
  nicht denselben Slot zweimal. Abgelehnte Starts geben die Reservierung frei.
- Ein echter fehlgeschlagener zugelassener Lauf wartet auf den nächsten Slot.
  Bei längerer Warteschlange wird nur der aktuellste fällige Werktagsslot
  bearbeitet; alte Slots erzeugen keinen Scan-Sturm.
- Pause/Fortsetzen: manuelles Fortsetzen bleibt möglich. Automatisches
  Fortsetzen respektiert den nächsten festen Slot und die eigene Kalenderregel.
- Beschädigte oder nicht schreibbare Zeitplandaten blockieren nur diese
  automatische Zulassung. Kein heimlicher stündlicher Ersatzplan.

## Datenbasis und Mathematik

Der bestehende Starter-Swing-Vertrag bleibt **abgeschlossene 1D-Börsensitzung**.
02:00 und 12:00 Schweizer Zeit können denselben US-Sitzungsstand überprüfen.
Der Mittagsscan behauptet keinen bereits bekannten US-Eröffnungsgap des noch
nicht eröffneten Handelstages. Montag 02:00 Schweizer Zeit darf den letzten
abgeschlossenen Freitag prüfen, obwohl es in New York noch Sonntag ist.

Gap-Prozent = `(reguläre Eröffnung / regulärer Vortagesschluss - 1) * 100`.
RVOL = Referenzsitzungsvolumen / durchschnittliches Volumen vorheriger Sitzungen;
die Signalkerze gehört nicht in ihren eigenen Nenner. Nachbörsenkurse und
zukünftige Kerzen überschreiben die abgeschlossene Referenz nicht.
Die Trennung der Snapshot-/Historienfelder entspricht der
[Provider-Dokumentation](https://massive.com/docs/rest/stocks/overview);
[Aggregate](https://www.massive.com/docs/rest/stocks/aggregates/custom-bars)
und der Börsenkalender bestimmen den tatsächlichen Sitzungsstand.

### Reparierte tatsächliche Defekte

1. **Fremder 79-Punkte-Deckel:** Gap-Setups mussten versehentlich zusätzliche
   Momentum-10/20-Tageshoch-Ausbrüche erfüllen. Ein gültiger Gap-Score 90 wurde
   auf 79 gedeckelt und verfehlte damit die Mailgrenze 80. Die Gap-Typen heißen
   jetzt `GAP_UP_MOMENTUM` / `GAP_DOWN_MOMENTUM`; fremde Momentum-Deckel greifen
   nur im Momentum-Vertrag. Scoregrenze und primäre Gap-Bedingungen unverändert.
2. **Vorzeitige RVOL-Rundung:** 1,496 wurde auf 1,50 gerundet und passierte
   eine 1,5-Grenze. Auswahl und Punkteberechnung verwenden nun den tatsächlichen
   Quotienten; Rundung ist allein Darstellung.
3. **Schuldpapier im Aktienuniversum:** Explizite Senior-Notes-/Debt-Bezeichnungen
   werden auch bei falsch gelieferten `CS`-Typen ausgeschlossen. Ein alter
   Common-Stock-Cache darf diesen Namen nicht überschreiben. Kontrollnamen
   „Bond Street“, „Notes Live“ und „Senior Living“ bleiben erlaubt.
4. Strategie-Cacheversion **15**: Alte Berechnungen gelten nicht als neu geprüft.

Geprüft: Gap-Richtung und Nenner, feste abgeschlossene Tagesbasis, RVOL-Unter-/
Gleich-/Überschreitung, Long und Short, kausale strukturelle Invalidierung und
erste Gegenbarriere. Keine künstlichen Entry-/Stop-/TP-Werte, keine Lockerung
von BI 17/20, Struktur-, R:R-, Daten- oder finalen Mailprüfungen.

## Mailbetrieb und Diagnose

Live vor diesem neuen Paket in der angemeldeten App beobachtet:

- Signal-Mails aktiv, Aktien-Swing-Kanal AN, Swing-Modus ausgewählt.
- Seit aktuellem API-Start zunächst 0 gesendet / 11 ausgelassen / 0 Fehler,
  später 0 / 18 / 0. Das zeigt protokollierte Vorversandentscheidungen,
  nicht einen belegten SMTP-Ausfall. Diese Daten sind kein Nachweis der neuen
  lokalen Korrektur auf dem Server.
- Keine Mail-/Konto-Einstellung bei dieser Prüfung verändert.

Neuer Bereich **Admin → Mailversand**: berechtigte Swing-Empfänger,
tatsächliche SMTP-Annahmen einschließlich Teilannahmen, ausgelassene Entscheidungen,
Fehler und verfügbare Outboxdaten. Gründe bleiben im Betreiberbereich;
keine neuen Textblöcke pro Aktie. Fehlende Daten sind `—`, nicht erfundene Nullen.
Prozessereignisse sind auf maximal 50 seit API-Start und 24 Stunden begrenzt.
SMTP-Annahme ist kein Postfach-/Lesenachweis. Die Scanner-Vorprüfung bleibt
geschlossen und heißt ausdrücklich `PRECHECK_PASSED`, nicht „Mail wird gesendet“.

Der Admin-Audit sendet keine Mail und verändert weder Ergebnis-Caches noch
Versandzustände. Empfänger-, Transport- und Tracker-Belege werden getrennt geprüft.
Die vollständige fachliche Freigabe erfolgt weiterhin erst im tatsächlichen
Sendepfad; Mail-Dedupe und Delivery-Intent schützen identische Zustellungen
und offene äquivalente Pläne gegen Duplikate.
Ein bereits offener oder noch zur Zustellung vorgemerkter äquivalenter Gap-Plan
wird auch nach Ablauf des 8-Stunden-Cooldowns nicht allein durch den zweiten
Tageslauf erneut gesendet. Das ist keine unbegrenzte Sperre nach Abschluss
eines früheren Trades: ein später neu validiertes Setup bleibt separat prüfbar.

Die unabhängige Nachprüfung fand zusätzlich einen echten Diagnosefehler:
Die alte Outbox-Statistik führte Wartung aus und konnte ausgerechnet durch das
Lesen einer Statusseite überfällige Zustellungen als abgelaufen markieren.
Der neue Diagnosepfad benutzt ausschließlich lesende Zugriffe auf Auth,
Outbox, Tracker und Dedupe. Auch das Auth-Middleware vor diesem GET wurde
mitgeprüft; alle regulären Schreib-/Worker-/Sendepfade behalten ihre bisherigen
Defaults. Temporär abgelaufene Konten werden für die Empfängerprüfung nur
in einer Kopie bewertet, nicht durch diesen GET umgeschrieben.

Primärdateien bleiben im vollständigen Audit-GET byte- und zeitstempelgleich
(isolierter DELETE-Journal-Test). Ein separater aktiver WAL-Test liest bereits
gespeicherte, noch im WAL liegende Zustellungen korrekt und beweist unveränderte Queuezeilen,
keine Schemaänderung und keine Retry-/Expire-/Recovery-Aktion. SQLite kann für
WAL-Leser Koordinationsdateien `-wal`/`-shm` anlegen; das ist ausdrücklich kein
Versandzustandswechsel. `immutable=1` wird nicht verwendet, weil es aktuelle
WAL-Zustellungen übersehen könnte. Fehlende Datenbanken bleiben unverfügbar.
Bei fehlender Journalprüfung bleiben ausstehende Aktivierungen unbekannt
(`pending_count=null`), nicht scheinbar null. Eine bekannte Mindestmenge wird
separat ausgewiesen; nachweislich leere, verfügbare Journale bleiben echte Nullen.

## Nachweise / Freigabe

- Schedule/Store: 58 bestanden, ein Windows-spezifischer POSIX-Modus-Skip.
- Unabhängige API-Admission-Nachprüfung: 10 bestanden. Reproduzierte und
  korrigierte Fälle: verlorene nächste Zeit, Freigabe einer bloßen Reservierung
  fälschlich als Cachefehler, Reihenfolge Gap Short gegenüber Stundenrunde.
- Gap/Stock/Level/Mail-Regressionen: 321 bestanden.
- Scheduler/Control/Runtime/Mail-Vorprüfung: 424 bestanden, ein Plattform-Skip.
- Frontend-Zähler: 10 bestanden; Desktop-/Mobil- und Fehlerfälle lokal im
  Playwright-Browser geprüft, ausschließlich synthetische Daten.
- Unabhängige Read-only-/Auth-/Worker-Nachprüfung: 45 + 80 bestanden.
- Abschließende Admin-/Gap-/Readiness-Regressionsprüfung: 66 bestanden;
  zusätzlich breite Outbox-/Native-/Auth-/Mailprüfung mit 376 bestandenen Tests.
  Diese gezielten Läufe überschneiden sich; ihre Zahlen werden nicht addiert.
- Frontend-Bundle `4379c5dca540`, JavaScript-Syntax und Python-Syntax von
  20 betroffenen Dateien geprüft. Lokale Handyansicht 390×844 ohne
  Seitenüberlauf, SMTP-Teilannahme und unbekannte/fehlgeschlagene Abfrage geprüft.
- Unbekannter vs. leerer Tracker-Backlog: sechs neue Regressionen,
  mit 54 Mail-/Journaltests zusammen geprüft.
- Zwei vollständige native Gap-Produzentenfälle Long/Short nach zehn Stunden
  prüfen denselben abgeschlossenen Tagesplan erneut: Cooldown abgelaufen,
  äquivalenter Trade noch offen, keine zweite SMTP-Nachricht. Mit den übrigen
  nativen Planfällen 14 Tests bestanden; SMTP ausschließlich simuliert.
- Zwei Integrationsnachkorrekturen: eigene Gap-Quellenmetadaten und die
  angepasste Stundenrunden-Testannahme (Momentum/Cup, nicht mehr Gap).
  Frischer gezielter Nachlauf einschließlich aller betroffenen Quellcode-Prüfungen:
  82 bestanden. Danach wurden Code und Tests für die Gesamtsuite eingefroren.

Finaler eingefrorener Gesamtlauf: **9.304 bestanden, 5 Plattform-Skips**,
eine Pytest-Importwarnung (`anyio`), keine Fehler, 900,97 Sekunden.
Ausgeführt mit dem isolierten Offline-Launcher:

```powershell
& '.\.codex_pytest_env\Scripts\python.exe' -B tmp/offline_mail_fix_tests_20260925.py -q --tb=short
```

Der Launcher sperrt Provider-/SMTP-Verbindungen und isoliert Datenbanken,
Dateien und Konfiguration. Privater JUnit-Nachweis:
`output/mail-fix-qa-b012da0204e04a78b392646988098431/results.xml`.
Git-Diff, Bundlebindung und Syntax wurden zusätzlich geprüft.
Das Paket ist damit lokal zur Veröffentlichung freigegeben.

## Rollout / verbleibender Betriebsnachweis

Der in dieser Prüfung zuletzt bestätigte Serverstand ist weiterhin `b742bbe`;
das neue Paket wurde hier nicht auf Hetzner ausgeführt. Es benötigt keine
Schema-/Datenmigration, neuen Abhängigkeiten oder geänderten systemd-Units.
Nach Veröffentlichung: laufende Scans beenden lassen, als `tradingbot`
Fast-Forward-Pull und kontrollierten API-/BG-Neustart durchführen. Health muss
die neue Git-Revision und Bundle `4379c5dca540` liefern. Danach braucht es neue
Cacheversion-15-Ergebnisse; alte Pläne werden nicht als neu geprüft übernommen.

Die neue Admin-Mailansicht erlaubt die weitere Betriebsprüfung ohne erneuten
privaten Export. Ein neuer erfolgreicher Produktionslauf, dessen SMTP-Annahme
und tatsächlicher Empfang sind getrennt und noch offen. Bei dieser Arbeit
wurde keine echte Testmail oder Handelsorder ausgelöst. Private Dateien unter
`output/` oder `tmp/` werden nicht veröffentlicht.
