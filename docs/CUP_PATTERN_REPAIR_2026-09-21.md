# Cup-and-Handle: Form und Schlusskurs repariert

Ausgangsstand `9557cf0`. Auftrag: die zwei noch offenen Cup-Befunde beheben,
normale Zickzackbewegungen zulassen und anschliessend unabhaengig auditieren.
Keine Serveraenderung, echten Providerabrufe, Mails oder Orders in der Abnahme.

## Fehler und Korrektur

1. **Docht statt Schluss:** Der alte Detektor akzeptierte ein Hoch ueber dem
   Tassenrand bei hohem Schlusskursanteil der Tageskerze, auch wenn der Schluss
   selbst unter dem Rand lag. Nur der unveraenderte Mindestschluss
   `Widerstand * 1.002` bestaetigt jetzt den Ausbruch. Ein spaeterer Kurs ersetzt
   den gespeicherten Schluss nicht; gerechnet wird mit ungerundeten Preisen.
2. **Ausweichen auf kleinere Fenster:** Nur den Docht-Zweig zu entfernen reichte
   nicht. Ein kleineres Fenster konnte den echten linken Rand abschneiden und
   den tieferen Rand bestehen lassen. Der hoechste strukturell gueltige Rand
   aller geprueften Splits wird deshalb VOR der Schlusspruefung festgehalten.
   Vorherige Henkelhochs bleiben ebenfalls Widerstand. Nicht beliebige alte
   Hochs, sondern Cup-/Henkel-gepruefte Strukturen liefern diese Zusatzschranke.
3. **Drei tiefe Dochte sind keine Tasse:** Die neue gemeinsame Formpruefung
   verlangt einen vom Schlusskurs getragenen Boden, breitere Bodenbildung,
   allmaehliche Seitenbewegungen und chronologische Originalanker.
   V-Spitzen, rechteckige Abstuerze und eine grosse Zwischenrally zwischen
   tiefen Boeden ersetzen diese Struktur nicht. Tagesrauschen, asymmetrische
   Seiten und moderate Bodentests sind ausdruecklich erlaubt.

Die beschreibende Grundlage ist die Schalen-/Bodenbildung mit anschliessendem
kleinerem Henkel; ungleiche Randhoehen sind moeglich:
[Fidelity: Cup With Handle](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cup-with-handle).
Die folgenden Zahlen sind unsere expliziten technischen Regeln, keine dort
vorgegebene universelle Formel:

- Schlusskurstiefe mindestens 60% der gesamten High/Low-Tiefe.
- Nachlaufender Median ueber maximal drei Schlusskurse nur fuer Formpruefung;
  Originalpreise/-daten bleiben Anker und Handelslevels.
- Mindestens 8% der Rand-zu-Rand-Kerzen, mindestens drei Kerzen, in der unteren
  20%-Zone; deren Anzahl mindestens 50% der Kerzen in der unteren Haelfte.
- Auf beiden Seiten mindestens 6% der Rand-zu-Rand-Kerzen, mindestens drei,
  im mittleren Preisband zwischen 25% und 75% der Erholung.
- Zwischen tiefen Tests darf keine mindestens zwei geglaettete Kerzen lange
  Rueckkehr ueber 65% der Erholung liegen. Ein spaeterer Rueckfall nach dem
  rechten Rand in die untere Haelfte verwirft den gewaehlten Cup ebenfalls.

Eine lange Historie mit zwei Boeden kann einen eigenstaendigen spaeteren Cup
enthalten. Dieser darf nur mit seinen tatsaechlichen spaeteren Ankern bestehen;
das gesamte W wird nicht als eine Tasse umgezeichnet.

## Durchgaengiger Vertrag

`cup_bowl_close_v2` wird nur nach der Form- und Schlusspruefung erzeugt.
Ungekuerzte Bestaetigungs-/Randpreise werden durch die bestehende begrenzte
Watch-Persistenz getragen. Anzeige, Klassifizierung, regulaerer/Premarket-
Versand, Swing-Nachpruefung und Watch-Promotion weisen alte Cup-Zeilen ohne
diesen Vertrag ab. Sammelscan-Namen sind keine fremde Strategie: gueltige
Cup-Zeilen bleiben in gemischten Aktienrunden zugelassen.

Cache-Version 10 verlangt einen frischen Lauf statt Umdeutung alter Resultate.
Bestehende Watch-Mechanik wird abgesichert, keine neue Watchlist eingefuehrt.
Fehlerhafte Schluss-/Henkel-OHLC und ungueltige letzte Bestaetigungskerzen
koennen keine neue Freigabe erzeugen. Die bisherige Kennzeichnung fehlender
Zwischenbars bleibt erhalten; fehlende Chartdaten werden nicht erfunden.

Die allgemeine Chart-Mustersuche benutzt dieselbe Formpruefung. Ihre Anzeige
bleibt ausdruecklich **Chartstruktur, kein Scannersignal**; sie ersetzt keine
separate Scanner-, Volumen-, Handelsplan- oder Versandpruefung.
Die sieben datierten Originalanker bleiben unveraendert nachvollziehbar.
BI 17/20, Wyckoff, RVOL-, R:R-, Stop-, Risiko- und Versandanforderungen werden
nicht gelockert. Top-180-Vorauswahl und Arbeitszeitbudgets bleiben bestehen.

## Unabhaengige Nachpruefung

Die ersten vier Regressionen reproduzierten den Dochtfehler im Ausgangscode.
Formpruefung und Zustellungsvertrag wurden getrennt umgesetzt; eine weitere
Pruefung testete den integrierten Detektor und die allgemeine Chart-Erkennung.
Dabei zusaetzlich gefunden und behoben: kleineres Ausweichfenster, fehlerhafte
Henkel-/Ausbruchskerzen, widersprechende Chartmuster und falsche Ablehnung
gueltiger Cup-Zeilen unter dem Sammelscan-Namen.

Getestet werden insbesondere verrauschte/asymmetrische gueltige Boeden,
Negativmuster, Originalanker, Preis-Skalierung, exakte Schwellen, falsche
Datentypen, alte Cache-/Watch-Zeilen und getrennte Versandwege.
Private Testprotokolle bleiben unter `output/` und werden nicht gepusht.

295 neue isolierte Cup-Tests bestanden vor dem finalen Gesamtlauf. Die
unabhaengigen Versandtests umfassen auch gemischte Sammelscans und die Bindung
persistierter Watch-Zeilen an ihren urspruenglichen Ticker und Pivot.
Vier bestehende Test-Fixtures wurden um den neuen erfolgreichen Detektorbeleg
ergaenzt; ihre bisherigen Ranking-, Dedupe- und Unterdrueckungsassertionen
bleiben bestehen. Der alte Test fuer verdrehte Rand-/Bodenchronologie verlangt
jetzt Ablehnung durch den Detektor, prueft aber weiterhin die ehrliche
Darstellung alter Geometrie direkt am Evidenzbaustein.

Lokale synthetische Zeitmessung: 40 komplette Detektoraufrufe mit je 100
Kerzen, Median 8,16 ms / Maximum 10,68 ms; keine Netzaufrufe. Die neue
Formpruefung fuehrt insbesondere keine zusaetzlichen Providerabrufe ein.
Das bestehende Frontend-Bundle `5be25ee8c547` wurde unveraendert verifiziert.

Eingefrorene Produktionsdateien fuer den abschliessenden Testlauf:

| Datei | SHA-256 |
| --- | --- |
| `api.py` | `28467e92b0663d06526cc35d9e9675d2a5ac8dcfdbf35573554d37059103b73d` |
| `modules/patterns.py` | `73d420d16fa41d22f8afe6fa0508aeba0dcdc580b15a1732333b9b2adc595dc8` |
| `modules/cup_shape.py` | `3e45534f1940899ab550074445bdab2753af71bc0d75d21767ad0bac09d03f6d` |
| `modules/cup_signal_contract.py` | `5e61169dab031335e3536daaad622c97eb1e5618261358a2ca1020dbea0a6ae1` |

## Abschluss

Finaler Gesamtlauf auf eingefrorenem Produktionscode: **6.202 bestanden,
4 plattformabhaengige Skips, 0 Fehler**, 544,08 Sekunden. Die Skips betreffen
Windows-Symlinkrechte und Linux-Dateisystemvertraege, keine Cup-Pruefung.
Nachweis: `output/cup-repair-final-suite.xml`, ausschliesslich lokal.
`git diff --cached --check` und unveraenderte Produktionshashes geprueft.
Die letzte unabhaengige Inhaltskontrolle fand keine weiteren blockierenden
Befunde im beschriebenen Umfang und keine privaten Exporte im Commit.

Commit/Push enthalten nur diese Korrektur und ihre Tests/Dokumentation.
Der Server wird hier nicht veraendert: Pull und Neustart erfolgen separat.
Danach ist ein neuer Cup-Lauf erforderlich; alte Cache-Versionen werden
bewusst nicht zu Ergebnissen des korrigierten Detektors umetikettiert.
