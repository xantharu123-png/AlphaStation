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
| `schema_version`, `contract_version`, `required_green` | Formatversion `1`, aktuell `stock-bi-20-v2` und `17`. Formatversion ist keine Code-Revision. |
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
