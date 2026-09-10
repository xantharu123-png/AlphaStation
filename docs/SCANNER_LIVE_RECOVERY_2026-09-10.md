# Scanner-Live-Reparatur vom 10.09.2026

## Freigabe und Abschlussgrenze

Auftrag: bekannte Scanner-/Mailprobleme reparieren und erst nach realer Prüfung
als behoben melden. Lokale Arbeit auf `codex/scanner-live-recovery`; Ausgangspunkt
`b2f69eb5d39a`. Keine neue Handels-, Risiko- oder Mailabonnement-Freigabe.

Der letzte private Export belegt eine aktive aktuelle API, einen vollständigen
BI-Long-Lauf unter 17/20, einen unvollständigen BI-Short-Lauf und vier automatische
Aktienstrategien ohne verwendbaren aktuellen Preis. Diese drei Fälle dürfen
weder zu einem allgemeinen Nullscan noch zu einem SMTP-Ausfall zusammengefasst
werden. Private Exporte und individuelle Signalzeilen bleiben außerhalb von Git.

## Lokal implementierte technische Korrektur

Erforderliche BI-GETs verwenden eine kleine laufbezogene Transportkomponente:
kurze vorübergehende Abruffehler werden begrenzt wiederholt; permanente Fehler
und unvollständige Daten bleiben blockierend. Sichere feste Fehlerklassen und
Zähler machen den nächsten Serverfehler unterscheidbar. Weder Providertexte
noch Schlüssel oder Request-URLs gehören in Diagnoseausgaben.

Pro Anfrage sind höchstens drei Versuche erlaubt, pro BI-Lauf insgesamt
höchstens 20 zusätzliche Versuche. Alle laufen durch das vorhandene Rate-Limit.
Wiederholt werden nur Timeouts, Verbindungsfehler ohne TLS-Fehler und die festen
HTTP-Statuscodes 408/500/502/503/504. Authentifizierungsfehler, 429, TLS-Fehler und
ungültiges JSON bleiben blockierend. Ein manueller Stop während der
Universe-Abfrage wird nicht mehr durch das spätere Löschen des Stop-Flags
verschluckt; ein Stop vor der Veröffentlichung verhindert einen neuen Finalcache.
Ein bereits laufender blockierender Netzwerkaufruf ist dadurch nicht sofort
unterbrechbar. Die genaue Ursache des historischen Short-Abbruchs bleibt ohne
dessen Transportdiagnose unbekannt.

Die separate lesende Providerdiagnose prüft Full-Snapshot und einen festen
Kontrolltitel. Sie meldet nur Feldzustände und sichere Statuswerte. Sie ersetzt
keine fehlende Trade-Berechtigung und führt keine Scans, Orders oder Mails aus.
Ein aus aktuellen Dateien rekonstruierter Schlüssel ist kein Beweis für den
bereits im API-Prozess gespeicherten Schlüssel; diese Bindung bleibt explizit
unsicher. Serverzugriff erfordert eine authentifizierte SSH-Sitzung.

Die Diagnose verwendet genau zwei feste Provider-Endpunkte, solange die
Dienstidentität stabil und eine Konfiguration verfügbar ist. Datei- und
Netzwerkzugriffe erfolgen nach dem Wechsel auf den Dienstbenutzer. Weiterleitungen,
Proxy-Umgebungsvariablen und fremde Hosts werden nicht verwendet. Die Abfrage ist
zeit- und größenbegrenzt. Positive Zeitstempelfelder sind noch kein Nachweis ihrer
Aktualität; Quelle, Zeiteinheit und Frische müssen vor einer Preisänderung separat
geprüft werden. Lokaler Aufruf in Windows-PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projekt\TradingBot\scripts\probe_hetzner_provider.ps1"
```

Das SSH-Passwort gehört nur in dieses Terminal. Das Ergebnis wird lokal als
`output/profitability/provider-probe-*.json` gespeichert und nicht veröffentlicht.
Für diese Diagnose sind weder Pull noch Neustart erforderlich.

## BI-Konfluenz bleibt unverändert

Erneute unabhängige Funktionsprüfung zeigt echte 17/20-Konstellationen für Long
und Short mit unveränderten synthetischen OHLCV-Eingaben. Die Kriterien S5/S6/S10/
S18 können gleichzeitig grün sein. Die geringe Zahl produktiver Treffer ist
damit kein Beweis eines mathematisch unmöglichen Vertrags. Keine Schwelle wird
auf gewünschte Trefferzahlen optimiert.

Das betrifft Roh-Konfluenz, nicht automatisch alle vorgelagerten Filter,
Handelspläne, ausführbare Quotes oder Mailfreigaben. Fibonacci-Swings verwenden
30 abgeschlossene Tageskerzen; Plan und S18 teilen die Range-Grenze. Die
15-Kerzen-Ausweichrange ersetzt nicht die separate Swing-Suche.

## Offene Produktionsabnahme

Vor der Aussage „funktioniert wieder“ sind nachzuweisen:

1. Tatsächlich gelieferte Preisfelder und ihr Zeitbezug mit der relevanten
   Konfiguration, nicht nur eine gesetzte API-Key-Variable.
2. Richtige Serverrevision, aktive Dienste und gesunde API nach einem etwaigen
   Update; keine Wiederherstellung durch Überschreiben unbekannter Serverdateien.
3. Vollständige aktuelle BI-Long-/Short-Läufe sowie vier vollständig ausgewertete
   Aktienstrategien in einer vom Datenfeed unterstützten Handelssitzung.
4. Für tatsächlich qualifizierte Ergebnisse die bestehende Mailprüfung und
   gegebenenfalls SMTP-Annahme. Kein künstliches Handelssignal als Mailtest.

Ein gültiger vollständiger Lauf darf null Treffer liefern. Eine gesunde
Scannerpipeline ist weder eine Gewinn- noch eine feste Trefferanzahlgarantie.
Die aktuelle Reparatur ändert keine historischen Trackerergebnisse und liefert
noch keinen neuen Netto-Profitabilitätsnachweis.

## Verifikation und Veröffentlichung

Aktueller lokaler Nachweis für die Quelländerungen bis `7b462f6`:

- 524 gezielte Transport-/BI-/Collector-Tests bestanden; unabhängige Prüfung der
  Transportkorrektur ohne Befund.
- Vollständige zu Beginn des Laufs Git-verfolgte Root-Tests: **4.595 bestanden,
  4 übersprungen**, 278,53 Sekunden. Isolierte Daten-, Runtime- und Temp-Pfade;
  keine private Exportdatei als Test eingesammelt.
- Die erst während dieses Laufs hinzugefügte Providerdiagnose wurde separat
  erneut geprüft: **54 bestanden**, 4,81 Sekunden. Echte Windows-PowerShell-
  Wrapperausführung mit simuliertem SSH; Linux-Identitätsgrenzen kontrolliert
  simuliert, nicht auf dem Produktionsserver abgenommen.
- Unveränderte reale Analysefunktionen und gemeinsame Range-Kontexte zusätzlich
  geprüft: 73 Tests bestanden. Synthetische 17/20-Fälle sind keine Live-Trades.

Unabhängige Aufgaben- und Gesamtprüfung der Quelländerungen ohne Befund
abgeschlossen; Veröffentlichung und Produktionsabnahme bleiben getrennte Schritte.
Noch keine Serveränderung, kein neuer vollständiger
Live-Scan und keine neue SMTP-Annahme durch diese Reparatur nachgewiesen.

Nach der Fast-forward-Integration in `main` (`2f757e6`) wurde die jetzt vollständige
Suite einschließlich der Providerdiagnose gemeinsam erneut ausgeführt:
**4.649 bestanden, 4 übersprungen**, 298,94 Sekunden. Keine weiteren
Quellcodeänderungen nach diesem Lauf. Der öffentliche Health-Abruf um 10:05 UTC
meldete noch `b2f69eb5d39a`; geschützte Scannerstatusdaten erfordern eine Anmeldung.
Das ist keine Produktionsabnahme der Reparatur. Die lesende Providerdiagnose
wartet weiterhin auf die SSH-Anmeldung des Benutzers.
