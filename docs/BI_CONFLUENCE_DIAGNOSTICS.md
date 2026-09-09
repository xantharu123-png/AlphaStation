# BI-Konfluenzdiagnose: Schema 1 und Betrieb

Die Diagnose erklärt **BI Long und BI Short bei Aktien**, ohne Handelslogik zu
ändern. Sie erzeugt weder Signale noch Watchlists, Tracking-Einträge oder Mails.
Die bestehende Regel bleibt: alle 20 Faktoren auswertbar, mindestens 17 grün,
zusätzlich unveränderte harte und nachgelagerte Scannerprüfungen.

Die reine Aggregation liegt in [bi_diagnostics.py](../modules/bi_diagnostics.py).
Die Identität der 20 Faktoren kommt beim Aufruf aus `BI_STOCK_INDICATORS` in
[patterns.py](../modules/patterns.py), nicht aus einer zweiten Strategie-Registry.
Es werden keine Ticker, Preise, Rohkerzen, privaten Begründungstexte oder
Einzelergebnisse in die Konfluenzdiagnose übernommen.

## Schema und Bedeutung

Der Scanner speichert das Objekt unter `diagnostics.confluence`.

| Feld | Bedeutung |
| --- | --- |
| `schema_version`, `contract_version`, `required_green` | Formatversion `1`, aktuell `stock-bi-20-v3` und `17`. Der Collector kann historische v2-Läufe getrennt lesen. Formatversion ist keine Code-Revision. |
| `scanner`, `direction` | `bi_long`/`long` oder `bi_short`/`short`. |
| `run_id`, `started_at`, `code_revision` | Eigene UUID je Diagnose-Lauf, explizit zeitzonenbezogener Startzeitpunkt und unveränderlicher Revisionsstempel des Prozesses. |
| `evaluated`, `schema_invalid` | Beobachtete Analyse-Rückgaben und davon formal inkonsistente Ergebnisse, beispielsweise falsche IDs, Typen, Versionen oder Zählersummen. |
| `green_count_histogram`, `available_count_histogram` | Jeweils alle festen Klassen `"0"` bis `"20"`, einschließlich Nullzählern; nur formal gültige Beobachtungen. |
| `bar_count_histogram` | Klassen `"36"` bis `"50"` sowie `"other"`; umfasst alle Beobachtungen. |
| `factor_counts` | Alle 20 Registry-Schlüssel, jeweils mit `evaluated`, `green`, `red`, `unavailable`. |
| `below_required`, `incomplete` | Weniger als 17 grüne Faktoren beziehungsweise weniger als 20 auswertbare Faktoren. Beide Zähler können dieselbe Analyse enthalten. |
| `pre_hard_gate_qualified` | Vollständig auswertbar und mindestens 17 grün, noch ohne Aussage über harte oder spätere Scannerprüfungen. |
| `first_hard_gate_counts` | Erster gemeldeter harter Blockierungsgrund, ausschließlich nach Konfluenzqualifikation. |
| `core_valid_count`, `payload_accepted_count` | Gültiges Rohanalyse-Ergebnis beziehungsweise nichtleeres Ergebnis des Konfluenz-Payloadhelpers; ausdrücklich getrennt. |
| `observation_errors` | Fehler der Diagnosebeobachtung; dürfen die bestehende Signalentscheidung nicht beeinflussen. |
| `failed_pair_counts` | 190 feste Faktorpaare `01:02` bis `19:20`: beide Faktoren bekannt und rot. Unbekannt zählt nicht rot. Keine unabhängigen Wahrscheinlichkeiten und keine Einzelticker. |
| `consolidation_days_histogram` | Analysemetadatum `consolidation_days`, Klassen 0 bis 50 und `other`. Kein zusätzliches Signal-Gate. |

`unavailable` bedeutet **nicht auswertbar**, nicht rot. Bei fehlerfreier
Diagnosebeobachtung (`observation_errors = 0`) gilt pro Faktor:

```text
evaluated = green + red
evaluated + unavailable = Gesamt-evaluated - schema_invalid
```

Unter derselben Voraussetzung ist die Summe jedes Konfluenzhistogramms ebenfalls
`Gesamt-evaluated - schema_invalid`; die Summe des Kerzenzahlhistogramms ist
`Gesamt-evaluated`. Bereits vorher wegen fehlender Historie verworfene Aktien
sind keine beobachteten Analyse-Rückgaben. Daher ist `evaluated` nicht einfach
die Anzahl aller geprüften Universumsmitglieder.

Bei `observation_errors > 0` darf weder Vollständigkeit noch die Einhaltung
dieser Zähleridentitäten unterstellt werden: Eine unerwartete Exception könnte
nach einer teilweisen Zähleränderung eintreten. Die Diagnose ist dann gesondert
zu prüfen; die bestehende Scannerentscheidung bleibt davon unberührt.

Eine reguläre Ablehnung unter 17/20 und ein deshalb fehlender Helper-Payload sind
**keine Schemafehler**. Die festen Hard-Gate-Kategorien sind `last_bar_pump`,
`range_breakdown`, `recent_bearish_pressure`, `recent_bullish_pressure` und
`unknown`. Unbekannte Begründungen werden nur unter `unknown` gezählt, nie als
freie Texte oder zusätzliche Schlüssel gespeichert. Bei zu geringer Konfluenz
beweist eine leere Hard-Gate-Liste nicht, dass alle harten Prüfungen bestanden
wären: diese Prüfungen wurden dort nicht erreicht.

Der Payloadhelper prüft Konfluenz, aber nicht sämtliche Hard-Gates. Deshalb kann
bei einem qualifizierten, hart abgelehnten Fall ein **Payload vorhanden** sein,
obwohl der **Core ungültig** ist. Bei fehlerfreier Beobachtung sind beide Zähler höchstens
`pre_hard_gate_qualified`; zwischen ihnen besteht keine notwendige
Größenreihenfolge. Auch Core gültig plus Payload vorhanden ist noch keine finale
Scannerannahme: weitere Struktur-, ATR-, Extension- und Chance/Risiko-Prüfungen
bleiben nachgeschaltet. Ein Punktescore ist weder Grünzahl noch
Trefferwahrscheinlichkeit.

## Laufidentität und Speicherung

Der Scanner verwendet eine eigene UUID als 32 Hexzeichen. Sie ist **nicht** die
API-/Scheduler-Run-ID. Der vorhandene `_detect_code_revision()` liefert den beim
Prozessstart festgehaltenen Revisionsstempel; kein Git-Aufruf je Aktie. Die
Collector-Projektion akzeptiert zwölf Hexzeichen mit optionalem `-dirty` oder
`-tree-unknown` sowie `unknown`. Solche Unsicherheitskennzeichnungen dürfen nicht
abgeschnitten werden.

Fortschritt, gespeicherte Zwischenstände (`.partial`) und erfolgreicher
Final-Cache führen `diagnostics.confluence` mit derselben Diagnose-Lauf-ID.
Ein Zwischenstand ersetzt nicht den Final-Cache. Bei Stop oder Fehler bleibt
der Lauf unvollständig; `diagnostics.final_results` ist dann `null`, nicht ein
belegtes finales Nullergebnis. Bis zur erfolgreichen Veröffentlichung eines
neuen Final-Caches bleibt der letzte vollständige Final-Cache erhalten.

Dateien werden einzeln veröffentlicht, **nicht gemeinsam atomar**. Fortschritt,
Zwischenstand und Final-Cache können daher unterschiedliche Läufe oder
Zeitpunkte darstellen. Vor einem Vergleich immer `run_id`, `started_at`,
Revision, Status und `partial` prüfen. Selbst dieselbe Run-ID bedeutet nicht
denselben Messzeitpunkt; ein Schreib-/Abschlussfehler muss gesondert geprüft
werden.

Scheitert schon die Diagnoseinitialisierung, meldet die Integration einmalig
`available: false`, `reason: initialization_failed` und
`initialization_errors: 1`. Das ist keine leere Schema-1-Messung. Weitere
Diagnosebeobachtungen werden übersprungen; es wird nicht für jede Aktie erneut
ein Initialisierungsfehler gezählt. Die Scanner-Gates bleiben davon unberührt.

## Collector und Freigabenachweis

[collect_server_evidence.py](../scripts/collect_server_evidence.py) liest Caches
im Dateisystem-Namensraum des verifizierten API-Prozesses über
`/proc/<PID>/root/...`. Bei systemd `PrivateTmp` ist das Host-`/tmp` nicht
zwangsläufig das Dienst-`/tmp`; ein fehlender Host-Cache beweist keine Nulltreffer.
Der Collector prüft Dienstidentität und Datenpfade, liest nach dauerhaftem
Wechsel auf den Dienstbenutzer und kontrolliert anschließend, ob Prozess oder
Pfade während der Erhebung gewechselt haben. Rohumgebungen und Secrets werden
nicht exportiert.

Die Projektion übernimmt nur erlaubte Identitäten, Kategorien und nichtnegative
Ganzzahlzähler. Fehlende Daten, unbekannte Schemata oder ungültige Identitäten
bleiben **fehlend/nicht auswertbar**, beispielsweise mit `available: false` und
`schema_status`; sie werden nicht durch künstliche Nullzähler ersetzt. Der
gesamte Serverexport kann unabhängig von dieser Aggregation private Daten
enthalten und gehört nicht in Git oder öffentliche Berichte.

Für einen Produktionsnachweis braucht es die ausgelieferte Version, einen
anschließenden regulären neuen Scan und dessen konsistente Diagnose. Unit- und
Integrationstests belegen nur die geprüften lokalen Verträge; synthetische
17/20-Beispiele zeigen Erreichbarkeit, keine reale Trefferquote. Die Diagnose
lockert keine Schwelle und belegt weder Profitabilität noch Broker-Ausführungen.

## Leere Historien und genaue Datenfehler (09.09.2026)

Die reine Prüfung in `modules/bi_market_data.py` behandelt das optionale
`results`-Feld entsprechend dem [Providervertrag für Tagesaggregate](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
Fehlt es, sind ein expliziter Erfolgsstatus `OK`/`DELAYED` und ein ganzzahliges
`resultsCount: 0` erforderlich. Ein vorhandenes `queryCount` muss dann ebenfalls
ganzzahlig null sein; ein Hinweis auf weitere Seiten darf nicht vorliegen.
Das ist keine pauschale Umwandlung fehlender oder fehlerhafter Antworten in `[]`.

Eine bestätigte Leerhistorie wird als `insufficient_daily_history` gezählt und
der Scanner fährt mit der nächsten Aktie fort, sowohl Long als auch Short.
Es entstehen keine Indikatorbeobachtung, kein Signal, kein Tracking und keine
Mail für diese Aktie. Die bestehende 17/20-Regel bleibt unverändert.

Explizite Ergebnislisten bleiben auch ohne Zählermetadaten kompatibel. Sind
Zähler vorhanden, werden Typ, Nichtnegativität und Konsistenz geprüft.
`results: null`, widersprüchliche Nullantworten, falsche Ergebnistypen sowie
ungültige OHLCV-Werte bleiben Fehler. Die Seitengrenze und `queryCount`-Prüfung
gelten nur für den BI-Abruf von 1-Tages-Kerzen über höchstens 320 Tage, nicht
pauschal für andere Aggregationszeiträume. Eine unvollständige Antwort darf
keinen scheinbar vollständigen Scan erzeugen.

Bei einem Datenvalidierungsfehler steht zusätzlich ein fester, datensparsamer
Code in `diagnostics.data_error_reason`. Beispiele:
`missing_results`, `invalid_json`, `invalid_bar_geometry`,
`invalid_bar_timestamp`, `result_count_mismatch`. Der öffentliche Fehlercode
bleibt beispielsweise `scan_data_invalid`; bestehende API-/UI-Verträge ändern
sich nicht. Der genaue Grund ist Diagnoseinformation, keine neue Signalregel.
Bereits gezählte Historien-/Liquiditätsfilter bleiben auch im Fehlerfortschritt
erhalten. Ein fehlerhafter Lauf ersetzt weiterhin nicht den letzten Final-Cache.

Der Standalone-Collector übernimmt `data_error_reason` nur aus seiner festen
Allowlist. `error_code` wird nur aus einer Progress-Meldung mit `status: error`
und exakt einem der fünf öffentlichen Scannerfehler übernommen. Freitext,
Ticker, Kursantworten, URLs und Zugangsdaten sind für beide Felder ausgeschlossen.
Damit ist für neue Läufe ein weiterer Journal-Auszug meist nicht mehr nötig;
ein alter Export enthält dadurch nicht nachträglich den früheren Fehlergrund.

## Isolierte Datenfehler und ein fester Analysezeitpunkt (09.09.2026)

Eine defekte einzelne Kursserie (`invalid_bar_*` oder `invalid_data_conversion`)
wird vollständig ausgeschlossen. Die verbleibenden Aktien werden weiter geprüft,
damit ein Fehler nicht sämtliche nachfolgenden Analysen verdeckt. Die ursprüngliche
Serie wird weder geglättet noch durch Entfernen einzelner Kerzen repariert.
`quarantined_symbols`, `data_error_counts` und `data_error_fields` zählen nur
feste Fehlercodes und Feldnamen, nie Ticker, Rohwerte oder Providertexte.

Auch nach Prüfung aller Aktien bleibt ein solcher Lauf **unvollständig**:
`coverage: incomplete`, `final_results: null`, `scan_data_incomplete`.
Kein neuer Final-Cache und keine automatische BI-Mail aus diesem Lauf.
Der API-Wrapper entfernt seine Zwischenstände im Fehlerpfad. `checked == total`
beweist nur, dass alle Abrufe versucht wurden, nicht gültige Datenabdeckung.
Provider-, Berechtigungs-, Rate-Limit-, JSON-/Antwortformat- und Netzwerkfehler
brechen weiterhin sofort ab; sie sind keine isolierten Kurskerzenfehler.

`run_as_of` wird vor dem Universumsabruf einmal UTC-bezogen festgehalten und ist
identisch mit `confluence.started_at`. Abgeschlossene Tageskerzen, Analysedatum
und Planstruktur verwenden diesen Stichtag. Tagesdaten werden nach New York
datiert, unabhängig von der Serverzeitzone. Ohne speziellen Frühschlusskalender
wird eine Sitzung konservativ erst um 16:00 New Yorker Zeit zugelassen; spätere
Sitzungen bleiben ausgeschlossen. `analysis_session_dates` zählt die tatsächlich
übergebenen letzten Sitzungstage (höchstens vier konkrete Tage plus `other`).
Dies synchronisiert die abgeschlossenen Analysekerzen, **nicht** die während des
Laufs einzeln aktualisierten Preise. Eine ausführbare Quote bleibt separat zu prüfen.

Version `stock-bi-20-v3` bezeichnet die gemeinsame Range-Grenze für Plan und
Fibonacci-Faktor S18. Unverändert: maximal 50 abgeschlossene Analyse-Tageskerzen,
30 Kerzen für die bestätigte Fibonacci-Swing-Suche, 2/2-Pivots, Mindest-Swing und
Nähetoleranz sowie die 6%-Konsolidierungsdefinition. Der adaptive Range-Ausschnitt
und sein bestehender 15-Kerzen-Fallback werden nicht länger mit einer separaten
starren 15-Kerzen-Grenze in S18 vermischt. Das ist kein neuer Profitabilitätsnachweis.
