# Lesende Mail-Abnahme: 14.09.2026

## Ausgangslage und begrenzter Auftrag

Die öffentliche Hetzner-API meldet am 14.09.2026 um 12:04 UTC `healthy` und
Revision `45dc14bba9fe`. Damit ist die API-Auslieferung bestätigt, nicht jedoch
der Zustand aller Hintergrunddienste oder die aktuelle Scannerleistung.
Der neueste lokal vorhandene private Serverexport stammt weiterhin vom
10.09.2026 um 08:35 UTC. Eine neue Providerdiagnose liegt noch nicht vor.

Der vorhandene Collector liest die Outbox und Unterdrückungszähler. Das separate
SMTP-Annahmejournal wurde bisher ausdrücklich nicht gelesen. Eine leere Outbox
ist daher kein Nachweis fehlender oder erfolgreicher Signal-Mails.

## Umgesetzte Ergänzung

- Ausschließlich `scripts/collect_server_evidence.py` und seine Tests erweitert.
- Journalpfad wie im Tracker ableiten: explizites
  `SIGNAL_DELIVERY_JOURNAL_DB_PATH` oder Geschwisterdatei des konfigurierten
  Trackers, nicht pauschal ein Dateiname unter `data_cache`.
- Verifizierte API-/BG-Identität, Pfad- und Inode-Übereinstimmung sowie den
  irreversiblen Rechtewechsel vor SQLite-Zugriffen beibehalten.
- Nur begrenzte Status-, Zeit- und Wiederholungsmetadaten lesen. Keine
  Intent-IDs, Empfängerhashes, Inhalte, Fehlertexte oder Anwendungscode laden.
- Fehlende Datei, unbekanntes Schema, unzulässige Werte und überschrittene
  Abfragelimits als unbekannt/unverfügbar ausweisen; nicht als null Zustellungen.
- Keine Journal-Reconciliation, Migration, Mail, Scanner- oder Brokeraktion.
- Verhalten zuerst mit fehlschlagenden Tests reproduziert; gezielte Tests,
  integrierte Regression und unabhängige Prüfung folgen unten getrennt.

Die unabhängige Prüfung fand zusätzlich eine Pfadmehrdeutigkeit: Eine lexikalische
Normalisierung von `symlink/../datei.sqlite` kann eine andere Datei bezeichnen als
die tatsächliche POSIX-Auflösung der Anwendung. Konfigurierte Mail-Store-Pfade
mit `..` werden deshalb nicht normalisiert und als vermeintlich verifiziert
gelesen, sondern als `unverified_runtime_path` ausgewiesen. Dies gilt für Journal,
Outbox und Unterdrückungsdatenbank; normale verifizierte Pfade bleiben unverändert.

## Aussagegrenzen

Eine Journalzeile repräsentiert einen Zustell-Intent und kann mehrere
Empfänger/Versuche zusammenführen. Ihr gespeicherter Annahmezeitpunkt ist der
früheste, nicht die Zeit jedes einzelnen SMTP-Versuchs. Zeitfensterzähler sind
daher keine Anzahl aller heute versandten Mails. `PENDING` beschreibt einen
noch ausstehenden Tracker-Abgleich nach protokollierter SMTP-Annahme, nicht
zwangsläufig eine noch unversandte Mail. `RECONCILED` ist kein Posteingangsbeleg.
Die Projektion validiert keine Empfängerkohorte, weil sie diese nicht liest.

Zukünftige Annahmezeitpunkte werden separat gezählt und nicht in vergangene
24-Stunden-Zähler, älteste ausstehende oder neueste bereits erfolgte Annahmen
einbezogen. Unbekannte Journalzustände erscheinen nur als Zähler ohne Rohtext.
Wiederholungszähler betreffen fehlgeschlagene Tracker-Abgleiche, nicht die Anzahl
von SMTP-Versuchen. Ein älterer `reconciled_at`-Wert kann auch an einer wieder
`PENDING` markierten Zeile stehen; er wird nicht als aktuelle Freigabe gewertet.

Einzelne Datenbanken werden separat beobachtet; keine atomare Gesamtsicht über
Tracker, Outbox, Journal und Caches. Ausweichjournale und Mailarten ohne diesen
Zustellweg bleiben ausdrücklich außerhalb der Abdeckung.

## Unverändert offene Schritte

1. Aktuelle Providerfelder und Frische prüfen; keine geratenen Preis-Fallbacks.
2. Vollständige BI-Long-/Short- und Aktienstrategie-Läufe sowie Dienste prüfen.
3. Bestehende Mailpfade anhand neuer Journal-/Tracker-/Outbox-Nachweise auswerten.
4. Neue Ergebniskohorten inklusive Kosten nach dem Profitabilitätsprotokoll
   vergleichen; App-Signal, Mail und Broker-Fill nicht gleichsetzen.
5. Tagesrisikoschutz erst mit belegter Broker-Session, Netto-Kosten-/Fill-Ledger
   und atomarer Reservierungsintegration als wirksam bezeichnen. Die bestätigten
   5.000 USD aktivieren keine Trades und erhöhen keine engeren Risikogrenzen.

## Verifikation und Anwendung

Die erste Journal-Testserie reproduzierte 30 fehlende Verhaltensfälle vor der
Implementierung. Der unabhängig entdeckte Pfad-Sonderfall wurde mit sieben
zusätzlichen zunächst fehlschlagenden Fällen reproduziert und korrigiert.
Alle 273 Collector-Tests bestehen; einschließlich Providerdiagnose und bestehender
Auswertungs-Kompatibilität bestanden im eigenen finalen Teillauf 434 Tests.
Die unabhängige Prüfung der eingefrorenen Änderung hat keine offenen Befunde.

Die erste Gesamtprüfung ergab 4.683 bestandene Tests, vier übersprungene und einen
Fehler außerhalb der Änderung: Der Insider-Cluster-Test verwendete einen festen
Beispieltrade vom 30.07.2026, der am 14.09.2026 korrekt aus der 45-Tage-Aufbewahrung
fiel. Nur die Testuhr in `test_smart_money_radar.py` wurde eingefroren; die
Produktions-Aufbewahrung und Clusterregeln bleiben unverändert. Alle 35 Tests
dieser Datei bestehen, und der Test-Fix wurde separat unabhängig geprüft.
Die abschließende Gesamtprüfung des eingefrorenen Pakets ist abgeschlossen:
**4.691 bestanden, vier übersprungen** in 315,91 Sekunden. Die vier übersprungenen
Prüfungen betreffen plattformabhängige Symlink-/Linux-Dateioperationen. Dies ist
ein lokaler Windows-Testnachweis, keine Live-Linux- oder Server-Abnahme. Nach
diesem Lauf wurden nur die Dokumentation und der Arbeitsstand aktualisiert.

Für diese lokale Diagnoseergänzung ist kein Server-Pull erforderlich: Der
PowerShell-Collector überträgt das lokale Skript über SSH-Standardeingabe und
führt es einmalig aus. Ein erfolgreich gespeicherter Export ist noch keine
positive Scanner-, Zustell- oder Profitabilitätsabnahme.

In Windows-PowerShell ausführen; das Passwort bleibt im Terminal:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projekt\TradingBot\scripts\probe_hetzner_provider.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projekt\TradingBot\scripts\collect_hetzner_evidence.ps1"
```

Kein neuer Server-Pull, Neustart, Handelsauftrag oder Mailversand durch diese
Arbeit. Die zuvor auf Benutzerwunsch pausierte automatische Nachkontrolle bleibt
pausiert. Private Exporte werden nicht veröffentlicht.
