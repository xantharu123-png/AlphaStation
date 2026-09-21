# Wyckoff: Unterbewegungen, Phase E und Fortsetzungsstrukturen

Stand: 21.09.2026. Analyse auf Commit `bd67d83` / `causal_wyckoff_v2`.
**Historischer Analysestand vor der Umsetzung.** Der aktuelle lokale
Implementierungs-/Auditstatus steht in
[WYCKOFF_V3_IMPLEMENTIERUNG_UND_AUDIT_2026-09-21.md](WYCKOFF_V3_IMPLEMENTIERUNG_UND_AUDIT_2026-09-21.md).
Dieser Vertrag allein ist keine Implementierungs- oder Produktionsfreigabe.
Kein Serverupdate, kein neuer Scan, keine Signalmails, keine Trades. Dieser
Pruefvertrag ist kein Nachweis besserer Marktergebnisse.

## Problem und Ziel

Der Nutzer handelt Aktien-Swings und muss im Chart nachvollziehen koennen,
welche Auf- und Abschwuenge eine Wyckoff-Interpretation tragen. Das aktuelle
Modell erkennt eine enge Umkehrkette, jedoch nicht die vollstaendige innere
Struktur, Fortsetzungsphasen oder Reakkumulation. Eine korrekte Sperre eines
alten Einstiegs wird zudem teilweise als Scheitern aller historischen Phasen
dargestellt. Das kann selbst nach einem starken Kursanstieg passieren.

Die vorige Aussage zu fehlenden festen A1/A2/A3-Regeln darf nicht bedeuten,
dass es keine Zwischenbewegungen oder nummerierten Ereignisse gibt.
Der Nutzer hat die Analyse vor der Erweiterung und keine Teil-Deployments
verlangt. Keine weitere Pull-Empfehlung vor Abschluss der untenstehenden Gates.

## Fachliche Einordnung

A--E bezeichnen Prozessphasen, nicht jeweils eine einzelne Kurswelle. Innerhalb
von A beschreibt ein Lehrbeispiel vier Schritte: PS, SC, AR, ST. Deren Nummern
sind von den Phasenbuchstaben zu unterscheiden; Auspraegung und Anzahl weiterer
Bewegungen sind variabel. [Wyckoff Analytics: Accumulation, The Bigger Picture](https://www.wyckoffanalytics.com/accumulation-the-bigger-picture/)

Phase B kann mehrere Auf- und Abschwuenge, STs und Tests der anderen Range-Seite
enthalten. Eine Range nach einem Aufwaertstrend ist nicht automatisch Distribution;
auch Reakkumulation ist moeglich. [Bruce Fraser: Context is King](https://articles.stockcharts.com/article/articles-wyckoff-2015-09-context-is-king/)

Phase C kann einen entscheidenden Test innerhalb der Range ohne Spring/UTAD
enthalten. D beschreibt gerichtete Fortschritte mit Ruecklaeufen; E den Trend
ausserhalb der Range. Neue Fortsetzungsranges koennen darin entstehen. Die
Ausbildung von Reakkumulation muss keinen Verkaufsclimax wie eine primaere
Bodenbildung enthalten. [Wyckoff Analytics: Methode und Phasen](https://www.wyckoffanalytics.com/wyckoff-method/)

Volumen und Kursfortschritt ueber ganze Swings zu vergleichen ist eine eigene
Analyseebene; sie ist nicht mit einer festen Elliott-Unterwellenzaehlung
gleichzusetzen. Es wird kein proprietaerer Weis-Wave-Indikator behauptet oder
nachgebaut. [Wyckoff Analytics: Wave- und Volumenanalyse](https://www.wyckoffanalytics.com/best-of-wyckoff-2017-online-conference/)

## Nachgewiesener Ist-Zustand

| Befund | Quelle im aktuellen Code | Konsequenz |
| --- | --- | --- |
| Nur erster ST pro Range | `modules/wyckoff.py:202-219,254` | Keine vollstaendige Testfolge in B |
| Climax und vorheriger Gegenmove zwingend | `modules/wyckoff.py:188-195` | Kein eigenstaendiges Modell fuer Fortsetzungsranges oder nicht-klimaktische Varianten |
| C nur Spring/UTAD | `modules/wyckoff.py:258-277` | Test innerhalb der Range ohne Grenzueberschreitung wird nicht als C modelliert |
| SOS/SOW erfordert Schluss ausserhalb der Range | `modules/wyckoff.py:279-292` | Enges Ausbruchsmodell statt allgemeiner Phase-D-Erkennung |
| Erster LPS und ein alter Stop | `modules/wyckoff.py:298-324` | Spaetere eigenstaendige Ruecktestgelegenheiten fehlen |
| Eine Ungueltigkeitsfunktion fuer Struktur und Einstiegsplan | `modules/wyckoff.py:141-145,241-243,318-332` | Historische Phasen werden auch bei abgelaufenem Einstieg als ungueltig markiert |
| E explizit nicht modelliert | `modules/wyckoff.py:158`, `frontend/index.html:600,2987` | Weder Zustand noch korrekte E-Anzeige vorhanden |
| API erlaubt exakt D und verwendet erste Ereignisse | `api.py:16197-16235` | E/mehrere Einstiege brauchen einen neuen konsistenten Vertrag |
| Replay verlangt exakt einen ST und LPS | `scripts/evaluate_wyckoff.py:117-133` | Ein blosses Anhaengen von ST #2 oder LPS #2 kann die Auswertung abbrechen |
| Nur ein Kandidat pro Richtung bleibt | `modules/wyckoff.py:344` | Aeltere Einstiegskette kann den Kontext einer neuen kleineren Range verdraengen |

### Konkrete synthetische Reproduktion

Aus `test_wyckoff_engine.textbook_bars('LONG')`: Modell-Einstieg 108,50,
erstes Projektionsziel 114,25. Wird die letzte abgeschlossene Kerze auf
Schlusskurs 130 gesetzt, liefert v2 `projected_target_not_beyond_entry` und
markiert A/B/C/D als `invalidated`. Die Ablehnung eines neuen Einstiegs bei
bereits ueberschrittenem Ziel ist richtig; daraus folgt aber nicht, dass die
historische Struktur gescheitert ist. Dies ist ein kontrollierter Softwarefall,
kein realer Trade oder Profitnachweis.

### Verfuegbare Datengrundlage

Eine begrenzte, rein lesende Pruefung von `output/` direkt,
`output/profitability/`, `data_cache/` direkt und `tmp/` direkt fand keinen
eingefrorenen Real-OHLCV-Datensatz mit unabhaengigen Wyckoff-Labels. Das ist
keine Behauptung ueber die gesamte Festplatte oder externe Speicher.

Private Betriebs- und Exportdaten bleiben vom Veroeffentlichungsumfang
ausgeschlossen. Sie ersetzen weder einen eingefrorenen OHLCV-/Labeldatensatz
noch eine aktuelle Live-Pruefung des Servers.
Die geprueften Listing-Caches enthalten Symbolinventare statt Kerzen.
Kurs-/Requestcaches sind laut `modules/data_fetchers.py:26` und
`modules/stock_scan_runtime.py:198-199,244-275` fluechtig. Existierende
Provider-Backtests belegen daher keinen konservierten Referenzdatensatz.

Der heutige Replay bewertet Signal-Boolean pro Zeitpunkt/Richtung. Fuer die
Erweiterung fehlen zusaetzlich struktur- und ereignisbezogene Referenzlabels.
`modules/scanner_cohort_comparison.py:104` bietet bereits einen Kostenvergleich
gepaarter Chancen und soll geprueft/wiederverwendet werden statt unnoetig einen
zweiten Gewinnrechner zu bauen. Fehlende Kosten bleiben fehlend, nicht null.

## Ziele und Nutzerfaelle

1. Als Swing-Nutzer kann ich jeden bestaetigten Swing und Test auf die
   zugrunde liegenden Kerzen, Preise und Bestaetigungszeiten zurueckfuehren.
2. Ich sehe den Unterschied zwischen einer intakten Struktur und einem
   unattraktiven, abgelaufenen oder ausgestoppten Einstiegsplan.
3. Fortsetzungsranges werden nach vorherigem Trend und eigener Ereigniskette
   beurteilt, nicht nur wegen einer Seitwaertsbewegung passend benannt.
4. Wiederholte Tests erzeugen weder doppelte Trades noch wiederholte Mails;
   ein neuer Einstieg benoetigt einen neuen bestaetigten Trigger und alle Gates.
5. Aussagen ueber Erkennungsqualitaet und Nettoergebnisse enthalten echte
   Stichproben, Unsicherheit und einen unveraenderten Vergleichsstand.

## P0: fuer den angefragten Umfang erforderlich

### 1. Kausale innere Swings

- Bestaetigte Pivotfolge und daraus Auf-/Abschwuenge, mit separatem noch
  unbestaetigtem letzten Abschnitt. Keine spaeteren Kerzen am frueheren Stichtag.
- Pro Swing: Start/Ende, Bestaetigung, Richtung, Strecke, Dauer in Kerzen,
  Preisfortschritt relativ zur damals bekannten Volatilitaet, kumuliertes
  Volumen und Volumen je Kerze. Laengere Swings nicht allein wegen groesserer
  Volumensummen als staerker einstufen; kein erfundenes Kauf-/Verkaufsdelta.
- `modules.level_zones.confirmed_pivot_evidence` ist als vorhandenes kausales
  Primitive zu pruefen. Gleichhohe Extrempunkte und eine Kerze, die gleichzeitig
  Hoch und Tief markiert, brauchen eindeutige Regeln; OHLC verraten nicht deren
  Intrabar-Reihenfolge. Ungeklaerte Reihenfolge darf keinen Trigger bestaetigen.
- Ein wiederholter Test erfordert einen getrennten Ruecklauf nach einer echten
  Gegenbewegung. Drei Nachbarkerzen am selben Tief sind nicht drei STs.
- Schwellen fuer Pivotgroesse, Abstand, Toleranzen und Volumenaenderungen sind
  versionierte Modellannahmen, vor Holdout-Auswertung eingefroren. Keine
  Optimierung auf eine gewuenschte Trefferanzahl.

### 2. Phasen, Ereignisse und Hierarchie

- PS/PSY, initiale Tests, wiederholte STs, Tests der oberen/unteren Range-Seite,
  Spring/UTAD samt Tests, SOS/SOW, LPS/LPSY und Backup werden getrennt abgebildet,
  soweit tatsaechlich belegt. Fehlende Evidenz bleibt unklar, nicht erfunden.
- C ohne Spring: eigener hoeherer Tief-/tieferer Hoch-Test mit anschliessender
  Richtungsbestaetigung. Ein beliebiger Pivot darf nicht automatisch C werden.
- D innerhalb der Range: gerichtete Fortschritte und Ruecklaeufe nach dem
  entscheidenden Test koennen bereits D-Evidenz bilden. Eine gewoehnliche
  B-Rallye ohne diese Abfolge darf nicht hochgestuft werden. Diese fachliche
  Phasenzuordnung allein ersetzt keinen bestaetigten Einstiegs-Trigger und
  umgeht nicht das bisherige Ausbruchs-/Ruecktest-Gate fuer Handelssignale.
- E: nach belegtem Ausbruch und Fortsetzung ausserhalb der Range; nicht nur
  nach Zeitablauf, einem hohen Score oder Zielerreichung. Ruecklaeufe und neuer
  Trendfortschritt werden mit expliziter Ereigniskette beschrieben.
- Ein E-Trend kann eine neue kleinere Range enthalten. Eine solche Range
  erhaelt eigene Identitaet und darf die alte Phase nicht rueckwirkend ersetzen.
- Darstellung etwa `Phase B / Aufschwung 1 / Ruecklauf 2 / ST #2`, explizit als
  lokale Zaehlung. Keine Pflichtzahl von Unterwellen und keine simulierten
  Ereignisse zum Fuellen eines idealen Schemas.

### 3. Umkehr und Fortsetzung unterscheiden

- Akkumulation nach Abwaertsbewegung, Distribution nach Aufwaertsbewegung;
  Reakkumulation im Aufwaertskontext und Redistribution im Abwaertskontext.
- Vortrend muss vor Beginn der Range beobachtbar sein, nicht aus deren
  spaeterem erfolgreichen Ausbruch abgeleitet werden.
- Eine Range im Aufwaertstrend kann trotzdem Distribution werden. Ohne
  ausreichende gerichtete Aufloesung bleibt ihre Interpretation offen.
- Klimaktische und nicht-klimaktische Varianten erhalten eigene Evidenzregeln;
  es wird nicht bloss die bisherige Climax-Schwelle abgesenkt.
- Historienlaenge und bereits angeschnittene Ranges sichtbar machen. 180
  angefragte Tageskerzen belegen nicht automatisch den gesamten Vortrend.

### 4. Struktur, Handelsplan und Zustellung trennen

Vorgeschlagene getrennte Dimensionen:

- `structure_state`: im Aufbau, bestaetigt, Fortsetzung, gescheitert, unklar.
- `phase` und versionsgebundene `phase_evidence`: historischer Erkenntnisstand.
- `entry_state`: kein Trigger, bereit, abgelaufen, Ziel bereits passiert,
  ungueltige Geometrie, Daten fehlen, Stop dieses Einstiegs verletzt.
- `structure_id`, `event_id`, `trigger_id` und referenzierte Anker. Mehrere
  Tests aendern nicht automatisch die Identitaet eines bereits gemeldeten Trades.
- Identitaeten verwenden Marktzeitpunkte, Ereignistyp und Parent-Bezug, keine
  lokalen Array-Indizes. Wenn beim rollierenden Fenster irrelevante alte Kerzen
  herausfallen, bleiben IDs unveraendert, sofern die benoetigten Anker noch
  belegt sind. Fehlen solche Anker, lautet der Zustand unvollstaendige Historie;
  keine erfundene neue Range oder stillschweigende Neuzuordnung.

Ein Stopbruch invalidiert den betreffenden Plan. Nur ein gesondert belegter
Strukturbruch invalidiert die Struktur. Das erlaubt niemals die Wiederverwendung
eines ausgestoppten Signals. Ein neuer Plan braucht einen spaeter bestaetigten
Trigger und unveraenderte Risiko-/Struktur-/Kosten-/Mailpruefungen.

API, Cache, Replay, Chart, Tracking und Mail muessen denselben Vertrag verstehen.
Ein blosses `phase in ('D','E')` ist keine ausreichende Korrektur. Alte v1/v2-Caches
bleiben als Signale gesperrt; die Migration muss bei allen Konsumenten greifen.

### 5. Zeitrahmen und UX

- Aktien-Signalentscheidung weiter auf abgeschlossenen 1D-Sessions. 1W kann
  uebergeordneten Kontext liefern; 1H bleibt eine getrennte Detailansicht.
  Das ist eine Produktentscheidung fuer Swings, kein universelles Optimum.
- Grosse und kleine Swings auf derselben 1D-Historie nicht mit einem Wechsel
  auf 1H verwechseln. Zeitrahmen und Strukturgrad werden separat beschriftet.
- Chart mit Range-Zonen, Phasenabschnitten, kleinen/grossen Swings und datierten
  Ereignissen. Dichte Detailansicht einklappbar, kein unlesbares Liniengeflecht.
- Abgeleitete Phasen duerfen spaeter beurteilt werden; ihre Bestaetigung darf
  nicht auf das fruehere Extrem zurueckdatiert werden. Abfragen desselben
  historischen Stichtags muessen immer denselben Erkenntnisstand liefern.
- Kein neues Kandidaten-/Watchlistprodukt. Unbestaetigte Chartkontexte sind
  keine Scanner-Treffer, Tracking-Einstiege oder Signal-Mails.

## Pruefung und messbare Freigabekriterien

### Technische Gates

- Gespiegelte LONG/SHORT-Faelle fuer alle vier Strukturtypen und Varianten;
  mehrere getrennte ST/LPS sowie benachbarte Kerzen ohne Mehrfachzaehlung.
- Negative Faelle: Zufalls-/Seitwaertsbewegung, gewoehnlicher Pullback, echter
  Breakdown, fehlendes Volumen, offene Kerzen und unvollstaendige Historie.
- Positiver in-Range-D-Fall gegen negative gewoehnliche B-Rallye; beide bleiben
  ohne separat belegten Einstiegstrigger ausserhalb der Scanner-Signalliste.
- Rollierende 180-Bar-Fenster: gleichbleibende IDs bei entfallenden irrelevanten
  Kerzen; explizit unvollstaendiger Kontext bei abgeschnittenen Pflichtankern.
- 100% Stichtagskonsistenz der Fixtures; kein bestaetigtes Ereignis ohne
  Kerzen- und Zeitnachweis. Zusaetzliche spaetere Kerzen veraendern keine alte
  Stichtagsabfrage und verdoppeln keine unveraenderten Signal-IDs.
- Ziel ueberschritten, alter Stop verletzt und spaeter neuer Trigger haben
  getrennte erwartete Zustaende. Keine Lockerung von Risikogrenzen.
- Cache-/API-/Mail-/Tracker-/Replay-Paritaet; Modellwechsel und historischer
  Altcache koennen keine Ereignispruefung umgehen.
- Vollsuite, reproduzierbarer Frontend-Build und Desktop-/Mobil-Chartpruefung.
  Laufzeitmessung auf eingefrorenen Datensaetzen; keine unbeschraenkte Suche,
  kein paralleler Ersatzscan, keine unkontrollierten zusaetzlichen Kursabfragen.

### Erkennungsqualitaet

Vor Auswertung: Datenmanifest, Symbol-/Zeitraumauswahl, Adjustierung, Session-
kalender, Parameterversion und zeitliche Kalibrierungs-/Holdout-Trennung fixieren.
Nicht nur heutige Gewinner oder nur vom Modell erkannte Beispiele auswaehlen.
Manuelle Labels werden unabhaengig vom Modelloutput und ohne zukuenftige Kerzen
erstellt. Unklare Beispiele bleiben unklar; fehlende Labels sind keine Negativen.

Messung pro Strukturtyp und Richtung: Precision, Recall, Fehlalarme, verpasste
Strukturen, Ereignis-/Phasen-Uebereinstimmung, Erkennungslatenz und Fallzahlen.
Benachbarte Stichtage derselben Struktur sind keine unabhaengigen Stichproben.
Vergleich gegen unveraendertes v2 auf demselben gehaltenen Datensatz. Keine
willkuerliche Qualitaetszahl ohne beschriftete Referenz; Detailmetriken duerfen
sich nicht hinter einer Gesamtquote verbergen.
Neben gemeinsamen Chancen auch hinzugekommene und entfallene Chancen auf der
gesamten vorher festgelegten Auswahl berichten. Ein Vergleich nur der
Schnittmenge kann die durch neue Regeln veraenderte Auswahl verdecken.

### Handelsergebnisse: separater Nachweis

Vorab definierter Trigger, fruehester nach Bestaetigung moeglicher Einstieg,
Stop/Ziele, Haltedauer, Gebuehren, Spread, Slippage und Short-Verfuegbarkeit.
OHLC-Tage mit Stop und Ziel zugleich erlauben keine erfundene Reihenfolge;
konservative/mehrere Szenarien oder tatsaechlich passende Intradaydaten nutzen.
Split-/Dividendenadjustierung und verwendete Handelspreise konsistent halten.

Auswerten: Netto-Erwartungswert in R, Trefferquote mit Gewinn-/Verlustgroesse,
Drawdown, Verlustserien, Ausfuehrungsabdeckung und Unsicherheitsintervalle.
Portfolio-Ueberschneidungen und korrelierte Signale nicht als unabhaengige
Gewinnchancen behandeln. Hoehere Trefferquote allein ist kein Profitnachweis.
Positive Nettoergebnisse duerfen nur behauptet werden, wenn der eingefrorene
Test sie traegt; eine Softwareimplementierung kann dieses Ergebnis nicht
garantieren. Ist die Evidenz negativ/unklar, bleibt genau das das Ergebnis.

## Nichtziele und P1/P2

- Keine Live-Trades, kein Hebel-/Risikoumbau, keine garantierten Tagesgewinne.
- Keine Aenderung von BI 17/20 oder anderen Scanner-Schwellen.
- Kein Ersatz fehlender Volumendaten durch Kerzenspannen, keine Behauptung
  tatsaechlicher institutioneller Kaeufe aus OHLCV allein.
- Point-and-Figure-Zielzaehlung ist eine eigene Methodik. Bestehende
  Range-Projektionen werden nicht als bereits implementierte P&F-Ziele verkauft.
- P1: Komfortfilter/Export der belegten Swings und Vergleich verschiedener
  Chartdetails. P2: weitere Marktplaetze/Intraday-Mikrostruktur nur mit passenden
  Daten und eigenem Testvertrag. Die oben angefragten E-/Fortsetzungs-/Test-
  Funktionen werden nicht als P1 verschoben und dennoch als fertig bezeichnet.

## Arbeitsreihenfolge und offene Abhaengigkeiten

1. Analyse abschliessen und reproduzierte Statusprobleme als Regressionen fassen.
2. Gemeinsame Struktur-/Ereignis-/Trigger-Identitaeten und kausale Swings bauen.
3. Wiederholte Tests, C-Varianten, E und Fortsetzungsranges samt Gegencases bauen.
4. Alle Schnittstellen und Chartdarstellungen gemeinsam migrieren und pruefen.
5. Unabhaengig bewertete Marktdaten und chronologischen Kostenvergleich pruefen.
6. Erst nach dokumentiertem Gesamtstatus Commit-/Push-/Serverfreigabe trennen.
   Zwischenstaende bleiben lokal; kein Zwischen-Pull erforderlich.

Kein fester Zeit-/Gewinntermin: Datenqualitaet und unabhängige Labels sind echte
Abhaengigkeiten. Engineering verantwortet kausale Regeln/Integration, Daten-QA
das eingefrorene Manifest und Session-/Adjustierungsnachweise, unabhängige
Chartpruefer die Labels. Die konkrete Stichprobengroesse und wirtschaftliche
Mindestwirkung muessen vor Holdout-Auswertung feststehen, nicht danach.

Die technische Erweiterung kann nach dieser Analyse lokal beginnen. Die Aussage
"funktioniert besser und profitabel" bleibt blockiert, solange die Daten- und
Bewertungsgates nicht erfuellt sind; das ist keine Ausrede, sie auszulassen.
