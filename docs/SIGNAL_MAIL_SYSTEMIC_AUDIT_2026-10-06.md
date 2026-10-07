# Systemischer Signal-Mail-Audit – 06.10.2026

## Ergebnis

Die Ablehnung eines einzelnen ECHO-Kandidaten erklärt den langen Zeitraum ohne
Signal-Mails nicht. Die vorhandenen Belege zeigen mehrere aufeinanderfolgende
Probleme: frühe Daten-/Scanabbrüche, danach vollständig abgeschlossene Scans,
deren Kandidaten überwiegend an der gemeinsamen Struktur-/Handelsplanprüfung
scheiterten. Ein neuer unbeabsichtigter, globaler SMTP- oder Berechnungsfehler
wurde in dieser Nachprüfung nicht nachgewiesen. Es wurde nichts gelockert.

Das ist keine Entwarnung für den Auswahlvertrag: Eine technisch korrekt
implementierte konservative Regel kann zusammen mit anderen Regeln die
praktische Auswahl stark einschränken. Bestehende Regressionstests belegen den
implementierten Vertrag, nicht dessen Eignung oder die Häufigkeit gültiger
Signale. Die produktweite App-/Mailfreigabe ist weiterhin nicht einheitlich.

## Historische Serverbelege

22 vorhandene private Exporte zwischen 08.09. und 26.09. wurden ausgewertet.
Sie sind Momentaufnahmen, kein lückenloses Versandprotokoll für mehrere Wochen.
Die Aktienstrategien und Revisionen änderten sich im Zeitraum.

| Stand | Ergebnis | Nachgewiesener Engpass |
|---|---|---|
| 10.–15.09. | Vier Strategie-Läufe jeweils nicht abgeschlossen | Daten nicht verfügbar beziehungsweise erforderliche Preisfelder fehlen |
| 18.–22.09. | Nur Teile der Strategie-Runde abgeschlossen | Insbesondere Momentum/Cup-Zeitüberschreitungen |
| 25.09., Export 13:02 UTC | Vier Strategie-Läufe abgeschlossen, 48 Kandidaten | 31 erste Gegenbarriere/R:R, 15 unbestätigte überschrittene Zonen, nur 2 native Strukturpläne |
| 26.09., Export 05:30 UTC | Vier Strategie-Läufe abgeschlossen, 30 Kandidaten | 25 erste Gegenbarriere/R:R, 5 unbestätigte überschrittene Zonen, kein nativer Strukturplan |

In den letzten beiden erfassten Läufen wurden 48 beziehungsweise 30 Kandidaten
vor dem Transport ausgelassen; für diese Runde sind keine Transportereignisse
belegt. Die Strukturgründe sind nicht alle automatisch falsche Ablehnungen.
Die historische falsche Signalkerzen-PDH/PDL-Barriere wurde bereits in
`0957cba` korrigiert. Nicht jeder verbliebene Barrieregrund ist derselbe Fehler.

Private Originale: `output/profitability/hetzner-evidence-20260925T130230Z.json`
und `output/profitability/hetzner-evidence-20260926T053049Z.json`. Nicht auf GitHub
hochladen. Verwendet wurden anonyme Zähler, Status, Planursachen und
Transportaggregate, nicht persönliche Empfängerinhalte.

## Aktuelle angemeldete UI-Kontrolle

- Momentum: 12.607/12.607 geprüft, 46 Ergebnisse, Referenzschluss 05.10.2026,
  neuer Ergebniszeitpunkt `2026-10-06 16:46:17.170500` (angezeigte Serverzeit).
  Der frühere Nachtabbruch ist nicht der aktuelle Ergebnisstand.
- Von 46 angezeigten Trade-Scores: 2 bei 83, 4 bei 69, 40 bei 45. ECHO wird
  als im Scan freigegeben bezeichnet, hat aber Tagesqualität 70/96 und wird
  von der zusätzlichen Momentum-Mailprüfung mit Minimum 78/96 ausgeschlossen.
- LSPD: Setup-Score 94 und Tagesqualität 96/96, trotzdem Trade-Score 45.
  Gespeicherter Plan: Entry 10,21, Stop 10,04, TP1 10,23, TP2 10,26;
  Plan-R:R 0,21. Die Gegenbarriere vereinigt bestätigte Swing-High-/Low-Quellen
  aus 1D/1W. Ihre ursprünglichen Zonenbounds und Bestätigungszeiten fehlen in
  der UI; deshalb ist ihre fachliche Korrektheit noch nicht nachgerechnet.
  Der separat geladene 4H-Chartkurs wurde nicht als Scanner-Referenzkurs benutzt.
- Performance, vorläufige 30-Tage-Ansicht: drei Trackerzeilen, alle offen.
  Ältere reife Statistikzeilen sind keine heutigen Versandbelege. Trackerzeile
  und SMTP-Annahme beweisen keinen Eingang im persönlichen Postfach.
- Die zuvor um 18:27 gelesene Maildiagnose enthielt 0 SMTP-Annahmen,
  50 Auslassungen, 0 Fehler und 0 wartend. Dieses begrenzte Fenster ist kein
  kompletter Tages- oder Wochennachweis.

## Nachgestellte gemeinsame Sperren

### Zonenverschmelzung und Bestätigung

Mit nativen synthetischen Tageskerzen in LONG und SHORT nachgestellt: Eine
neu bestätigte Unterstützung kann den Rand einer alten Widerstandszone
erweitern. Die bisherige Bestätigung gilt nicht automatisch für den erweiterten
Rand; der Bestätigungsanker wird zurückgesetzt. Der globale crossed-zone-Guard
kann dadurch den gesamten Plan ablehnen, obwohl die nächste echte Gegenbarriere
ausreichend Platz bietet.

Das ist durch `test_level_zone_reclaim_history.py` ausdrücklich geschütztes
konservatives Verhalten. Es ist nicht als neuer Implementierungsfehler behoben
worden. Die synthetische Zone lag unter dem Entry; die aktuelle LSPD-Barriere
überlappt den Entry. Eine Gleichsetzung dieser Ursachen wäre unbewiesen.

Siehe `output/level-gate-systemic-audit-20261006.md` und
`output/level-gate-systemic-probe-20261006-mixed-role.py`.

### Zusätzliche Tageshoch-/TP1-Regel

Vier kohärente synthetische Fälle mit echten ausgewählten API-Funktionen,
Wilder-ATR, nativer 1D-Zonenbildung, Setup-/Trade-Score, Qualität und Health:
Ein gültiger Plan mit Trade-Score 92, Qualität 83/96, Health 100 und TP1 bei
1,82R wird dennoch abgelehnt, wenn das Tageshoch weniger als 0,5 Prozent unter
dem TP1 liegt. Der positive Kontrollfall besteht dieselben Teilprüfungen.

Auch diese tägliche Auswahlregel ist ausdrücklich dokumentiert und getestet,
nicht versehentlich aus einem Intraday-Pfad übernommen. Der Probeablauf ist
kein vollständiger Scanner-/Provider-/VRVP-/Sender-/Revalidierungsdurchlauf
und kein Nachweis, wie oft die Regel im untersuchten Mailzeitraum wirkte.

Siehe `output/systemic-mail-audit-20261006-chronology-4c33/README.md` und
`probe.py`. Der dortige Quelltext-SHA bezieht sich auf normalisierten Text,
nicht auf die unveränderten Rohbytes der API-Datei.

## Gegenprüfungen und Grenzen

- Doppelschutz ist nach Ticker, Markt, Richtung und Mailklasse eingegrenzt.
  Die vorhandenen PLNT/ZD/TMO-Zeilen blockieren nicht pauschal andere Aktien.
- SMTP-/Cooldown-Markierungen erfolgen nach erfolgreichem Versandaufruf;
  erfolglose reservierte Claims werden im geprüften Pfad freigegeben.
- Erfolgreiche Strategie-Teilrunden können Kandidaten trotz anderer fehlerhafter
  Teilrunden zum Sender weiterreichen. Kein gemeinsamer Alles-oder-nichts-
  Transportabbruch in diesem Pfad nachgewiesen.
- Kein allgemeiner 1D/5m-Widerspruch oder unvermeidbarer Scoredeckel durch
  fehlende Intraday-Quotes wurde gefunden; die positive native Gegenprobe
  erreicht Trade-Score 93 ohne diese Daten.
- Offene PREPARED/ATTEMPTED_UNKNOWN-Claims sind weiterhin ein tickerbezogener
  Prüfpunkt. Nicht auf andere Ticker oder einen globalen Versandausfall schließen.
- Die automatische allgemeine Aktienrunde enthält im aktuellen lokalen Code
  Momentum und Cup; Gap, BI, ORB, Turtle und weitere Scanner haben eigene Jobs.
  Die Zahl der Schedulerjobs bedeutet nicht, dass jede Dropdownstrategie
  täglich dieselbe Grundgesamtheit vollständig als Mailstrategie prüft.

Die vorhandene historische Drei-Aktien-Studie AAPL/MSFT/NVDA über 64 Sitzungen
lieferte 100 technische Nicht-Elliott-Kandidaten, 98 gültige Levelgeometrien
und keinen endgültig freigegebenen Modellplan. 92 native Planablehnungen
betrafen die erste Gegenbarriere; fünf Turtle-Pläne bestanden die native Stufe,
scheiterten aber später. Das stützt den gemeinsamen Engpass, ist jedoch keine
Statistik über 15.000 Aktien oder ein Beweis der Live-Mailursache.

## Verifikation und Zustand

Methodik: Systematic Debugging, vier unabhängige Teilprüfungen, getrennte
Beweisgrenzen; keine verdachtsbasierte Filterlockerung.

- Versand-/Doppelschutzprüfungen: 162 bestanden, zusätzlich 8 bestanden.
- Zonen-/Referenz-/Reclaim-Prüfungen einschließlich vier Probevarianten:
  136 bestanden. XML ohne Fehler/Skips vom Hauptagenten gelesen.
- Momentum-Probe: vier Assert-Szenarien vom Hauptagenten unverändert wiederholt,
  Exit 0. Die Testsammlungen können sich überschneiden; keine einzigartige
  Gesamtzahl behaupten.
- Keine Programmcodeänderung, kein Commit/Push, keine Serveränderung,
  kein Scanstart, keine Testmail und keine Einstellungsänderung in dieser
  systemischen Nachfrage. Vererbtes WIP vollständig erhalten.
- API-Rohbytes unverändert:
  `6BE9EBC5093BECD68B0A77EE11596317AE785BAE8D1259E4C4941A4B5BAB45D8`.

## Noch offen

1. Den gemeinsamen Struktur-/Freigabevertrag an vollständigen aktuellen
   Kurspräfixen prüfen: Zonenquelle, bestätigter Zeitpunkt, Bounds, Rolle,
   tatsächlicher Ausbruch und erste echte Gegenbarriere. LSPD ist noch kein
   belegter Fehlalarm, weil diese Originale in der UI fehlen.
2. App- und Mailfreigabe auf denselben vollständigen Prüfvertrag beziehen;
   eine allgemeine Scanfreigabe nicht als finale Signal-Mailfreigabe darstellen.
3. Zusätzliche konservative Auswahlsperren nur nach expliziter Vertragsänderung
   verändern, nicht aus einem erfolgreich nachgestellten bestehenden Vertrag
   einen behaupteten Rechenfehler machen.
4. Bereits lokal geprüfte BI-Start-/Datenkohortenreparaturen separat
   veröffentlichen und deren Serverstand bestätigen. Sie wurden in dieser
   Nachfrage nicht deployed und erklären nicht automatisch jede Mailauslassung.
5. Ein wirklich final freigegebenes Signal bis SMTP und anschließend Postfach
   verfolgen. Der Eingang der früher erlaubten technischen Testmail wurde vom
   Nutzer bestätigt; eine weitere wurde nicht versendet.
