# Gap-/BI-Reparatur und mathematische Gegenpruefung

Stand: 30.09.2026. Basis: `f16a1cc05ef2348c65d5a63671450e59fc6b699d`.
Auftrag: alle sieben Befunde des Gap-/BI-Audits beheben, verwandte Fehler
suchen und erneut pruefen. Keine Lockerung der 17/20-Regel oder Mailfreigabe.

## Reparierte Befunde

| Befund | Korrektur und Gegenprobe |
| --- | --- |
| Erster DX fehlt beim ADX-Start | Ersten DX aus den initialen Wilder-DM/TR-Werten einbeziehen. Vergleich mit separat berechneter Referenz, fuenf Kerzen Abstand, 200 deterministischen Reihen und verschiedenen Historienlaengen. |
| Gueltiger 17/20-Kandidat verschwindet bei Planwarnung | Nur bekannte nachgelagerte Planablehnungen werden als Warnkandidaten erhalten. Ungueltige Daten und unbekannte Fehler bleiben ausgeschlossen. Mailklassifikation, abschliessende Revalidierung und Tracking blockieren `BI_PlanAccepted=False`. |
| Gap mischt widerspruechliche Tagesbeobachtungen | Vor Indikatoren und Plan den aktuellen O/H/L/C/V sowie den vorherigen Sitzungsschluss aus Einzelhistorie und Sammelfeed vergleichen. Beide echten Boersensitzungen muessen eindeutig vorhanden sein. |
| Ungepruefte BI-Mover umgehen das CS-Universum | Bonus-Gainers/Losers-Abfragen entfernt. Das vollstaendig paginierte Common-Stock-Referenzuniversum enthaelt bereits die zulaessigen Mover. |
| Anteilsklassen werden wegen Punkt/Suffix abgelehnt | Gemeinsame Symbolsyntax; Instrumenttyp entscheidet weiterhin nach Referenzdaten. Bestaetigte CS-Anteilsklassen bleiben zulaessig, unbestaetigte Symbole/ETFs nicht. |
| Veraltete BI-Historie erzeugt vermeintlich aktuelle Treffer | Neueste abgeschlossene, verfuegbare Boersensitzung vor Analyse verlangen. Andernfalls Einzelwert ausschliessen und `stale_daily_history` zaehlen. Keine dauerhafte Tickersperre. |
| Zwei-Tages-Pump addiert Tagesprozente | Gesamtrendite `(Schluss_neu / Schluss_vor_zwei_Sitzungen - 1) * 100`. Beispiel: zweimal +5,9 % ergibt +12,1481 %, nicht +11,8 %. |

Die Konsistenzpruefung gilt fuer den gemeinsamen abgeschlossenen
1D-Aktienstrategiepfad, nicht nur Gap. Einzelne fehlerhafte Aktien werden
isoliert; mehr als 20 widerspruechliche Referenzen brechen den Lauf als
Datenfehler ab und erhalten den alten Final-Cache. Ein gezielter Test prueft
zwei Symbole mit einem Fehler, ein weiterer den Fehlercluster. Die numerische
Vergleichstoleranz (`rel_tol=1e-6`, Preise `abs_tol=1e-4`, Volumen `abs_tol=1`)
deckt Darstellungsrauschen ab, nicht verschiedene Kurse oder Handelsstaende.

## Zusaetzlich gefundene und reparierte Fehlerklassen

1. **Vorzeitiges Runden:** Aktien-BI verwendet fuer ADX, RSI und Stochastic
   ungerundete Entscheidungswerte. Die bisherigen gerundeten Standard-Rueckgaben
   bleiben fuer andere bestehende Anzeigekonsumenten erhalten. Konkrete Proben:
   RSI 49,992554 bleibt unter 50; ADX 14,033525 ueber 13,966047 bleibt steigend;
   Stochastic 12,247319 ueber 12,159157 bleibt eine positive Relation. Alle drei
   Entscheidungen kippten zuvor durch Anzeige-Rundung. Die Tests pruefen nicht
   nur den Indikator, sondern die echte zugehoerige BI-Stimme.
2. **Null ist nicht unbekannt:** Ein vorheriger ADX von 0 ist verfuegbar.
   Steigender ADX aus einer flachen Phase erhaelt konsistente Stimme und Punkte.
3. **Live-/Daily-Adapter-Paritaet:** Der gemeinsame Aktien-Metrikpfad reicht RSI
   ebenfalls ungerundet weiter. Der Daily-Momentum-Vergleichsadapter verwendet
   dieselbe RSI-Praezision und keine RVOL-Rundung vor seiner 1,5-Grenze.
4. **Verdraengung im BI-Pool:** Kein Abschneiden der Rohkandidaten auf die ersten
   50 vor der Mailselektion. 50 hoch bewertete Warnkandidaten koennen daher einen
   spaeteren gueltigen Plan nicht verdecken. Die begrenzte, separate
   Versandselektion und ihr Duplikatschutz bleiben bestehen.
5. **Diagnoseanschluesse:** `bi_plan_not_released` ist als Nicht-Freigabe und
   erlaubter fester Telemetriegrund registriert; neue Datenkategorien bleiben
   auch im datensparsamen Export erhalten. BI-Laufzeittexte nennen die gemischte
   Ergebnisliste jetzt Kandidaten, nicht pauschal Signale.

## Unveraenderte Regeln und Datenbasis

- BI: genau 20 explizite Aktienchecks, mindestens 17 bestaetigt, alle erforderlichen
  Daten verfuegbar; harte Gegenanzeigen bleiben zusaetzlich wirksam. Keine
  Mindestzahl erzwungener Ergebnisse. Gewichteter Score ersetzt diese Regel nicht.
- Gap: echtes regulaeres Open gegen vorherigen Sitzungsschluss; Long/Short
  getrennt, unveraenderte Grenzen. Kein Nachboersenpreis als Tages-Open.
- Gap-Zeitplan des Vorgaengerpakets: Montag bis Freitag, 02:00 und 12:00
  Europe/Zurich. Beide Slots beziehen sich im 1D-Modus normalerweise auf dieselbe
  letzte abgeschlossene US-Sitzung. Keine Behauptung neuer Vormittags-Gaps.
- Anzeige, Handelsplan, Mailfreigabe, SMTP-Annahme und Trade-Tracking bleiben
  getrennt. Sichtbare Warnkandidaten werden nicht als versandte Trades verbucht.

Mathematische Referenz fuer Wilder-Initialisierung:
[ADX-Berechnung](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx).
Providervertrag:
[adjustierte Tagesaggregate](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
50 BI-Analysekerzen werden nicht mit einer beliebig langen ADX-Vorgeschichte
gleichgesetzt; dieselbe Historie ist Voraussetzung fuer einen Zahlenvergleich.

## Teststrategie und Nachweise

Die zuvor privaten roten Gegenbeispiele wurden in
`test_gap_bi_repair_20260930.py` uebernommen und erweitert. Reine Plan-/Pipeline-
Tests injizieren bewusst einen qualifizierten Indikatorausgang, um die getrennten
Sicherungen zu pruefen. Die mathematischen Tests berechnen dagegen echte Werte
und echte BI-Stimmen gegen unabhaengige Formeln. Ein injiziertes 17/20-Fixture
ist kein Nachweis, dass flache synthetische Kerzen natuerlich 17/20 ergeben.

Bestehende Tests wurden dort angepasst, wo sie den alten, inzwischen geaenderten
Anzeigevertrag erwarteten, unbrauchbare Mover-Antworten einschoben oder
widerspruechliche/veraltete Kerzen als gute Eingangsdaten verwendeten.
Es wurden keine Fehler als erwartete Fehler markiert und keine fachlichen
Schwellen zur Erzeugung gruener Tests abgesenkt.

Zwischenpruefungen:

- Neue Gegenproben plus BI-Indikator-/Mathematiktests: 114 bestanden.
- Ergaenzte Gegenproben plus Daily-Paritaet und Mathematik: 121 bestanden.
- Nachpruefung aller im ersten Gesamtlauf betroffenen Bereiche: 376 bestanden.
- Diese Laeufe ueberschneiden sich; ihre Zahlen werden nicht addiert.
- Erster, noch nicht eingefrorener Gesamtlauf: 9.309 bestanden, 15 Fehler,
  5 Plattform-Skips. Darunter noch fehlende neue Diagnosezuordnungen,
  inkonsistente Alt-Fixtures und Quelltextpruefungen mit waehrend des Laufs
  verschobenen Zeilennummern. Dieser Lauf ist ausdruecklich keine Freigabe.

Abschliessender unveraenderter Gesamtlauf: **9.385 bestanden, 0 Fehler,
5 Plattform-Skips**, 1.193,85 Sekunden. Darin sind **80 Regressionstests** in
`test_gap_bi_repair_20260930.py` enthalten. Die SHA256-Werte aller 22 geaenderten
Python-Dateien waren vor und nach dem Lauf identisch. Ausfuehrung:

```powershell
& '.\.codex_pytest_env\Scripts\python.exe' -B tmp/offline_mail_fix_tests_20260925.py -q -x --tb=short --durations=15
```

Der Launcher isoliert Dateien, Datenbanken und Konfiguration und sperrt
externen Provider-/SMTP-Zugriff. Keine echte Testmail und keine Brokeraktion.
Private QA-Ausgaben unter `output/` werden nicht auf GitHub gestellt.

Privater JUnit-Nachweis:
`output/mail-fix-qa-1a425c184e9142849c067360ebb8994f/results.xml`.
Die fuenf unveraenderten Plattform-Skips betreffen Windows-Symlinkrechte sowie
Linux-O_NOFOLLOW/FIFO/atomare-Rename- und POSIX-Dateirechtepruefungen. Eine
harmlose AnyIO-Pytest-Importwarnung bleibt; keine Tests wurden neu deaktiviert.
Die vorher fehlende kopierte Diagnose-Allowlist im Export wurde vor diesem
Gesamtlauf durch 439 gezielte Tests nachgeprueft.

Weitere Abnahme:

- Syntax aller 22 Python-Dateien, Frontend-Bundlebindung und Diff geprueft.
- Lokaler Browsercheck bei 1440 x 1000 und 390 x 844: ein synthetischer
  17/20-Kandidat ohne freigegebenen Plan durchlaeuft die echte API-Klassifikation
  und Sichtbarkeitspolitik. Er bleibt sichtbar mit Nicht-Freigabe, statt als
  freigegebener Trade dargestellt zu werden. Keine JavaScript-Fehler oder
  horizontaler Seitenueberlauf; die breite Tabelle scrollt innerhalb ihrer Karte.
- Details bleiben geschlossen; kein neuer ausfuehrlicher Warnblock im Frontend.
- Nur lokale synthetische Daten; Browser und Testserver anschliessend beendet.

## Versionswechsel und Produktion

- BI-Regelversion: **`stock-bi-20-v4`**. Alte ADX-/Auswahlberechnungen duerfen
  nicht unter neuer Version weiterverwendet werden.
- Aktienstrategie-Cacheversion: **16**. Konsistenz und Praezision erfordern
  neu berechnete Ergebnisse.
- Frontend unveraendert: Bundle **`4379c5dca540`**, Bindungspruefung bestanden.
- Keine Schema-/Datenmigration, keine neuen Abhaengigkeiten oder systemd-Aenderung.
- Dieses Reparaturpaket ist noch nicht auf Hetzner installiert. Vollstaendige
  neue Produktionslaeufe und reale Mailzustellung bleiben separate Betriebsnachweise.
- Nach Rollout neue Gap-/BI-Ergebnisse mit aktueller Regel-/Cacheversion,
  Daten-Ausschluessen, Freigaben und Versandjournal im selben Zeitfenster
  vergleichen. Alte Ergebnisse nicht nachtraeglich als neue v4-Ergebnisse zaehlen.
