# Cup-Laufzeit und ehrliche Fehlerdiagnose

## Nachweis und begrenzter Auftrag

Der private Hetzner-Export vom 20.09.2026, 10:30 UTC, stammt von
`d9cfb3154977`. Der manuelle Cup-Lauf endete nach 1.200 Sekunden mit
`scan_timeout` und unvollstaendiger Abdeckung: letzter Zwischenstand
4.984 von 12.588 Snapshoteintraegen. Er kam noch nicht bis zur speziellen
Cup-Musterpruefung. Die angezeigten alten Ergebniszeilen sind kein Ergebnis
dieses fehlgeschlagenen Laufs.

1.282 Provider-Aufrufe und 15 Sekunden Rate-Limit-Wartezeit sind protokolliert.
Die inklusiven Zeitbloecke betragen unter anderem 458,6 Sekunden Historie,
307,1 Sekunden Ausfuehrungshistorie, 212,2 Sekunden Struktur und 140,8 Sekunden
Zwischenstandspeicherung. Sie koennen einander einschliessen: nicht addieren
und daraus keine Laufzeitanteile berechnen. Aus den Daten folgt insbesondere
kein allgemeiner SMTP-Fehler und kein Beweis fehlender handelbarer Aktien.

## Korrektur ohne weichere Signalkriterien

- Die native 4H-/Struktur-/VRVP-Handelsplananreicherung erfolgt fuer Cup erst
  nach der bisherigen stabilen Vorauswahl nach Score und Kursveraenderung.
  Dieselben ersten 180 Kandidaten bleiben zugelassen; keine Nachruecker.
- Nur diese 180 Kandidaten behalten den benoetigten internen Rechenkontext.
  Ausgeschiedene Kandidaten loesen diese teure Zusatzarbeit nicht mehr aus.
- Die native Berechnung bleibt VOR der Cup-Pruefung der ausgewaehlten Zeile.
  Der ausgelagerte Berechnungsblock ist AST-identisch zum bisherigen Block;
  volle Historie, ungerundete Eingaben und fester Analysezeitpunkt bleiben
  erhalten. Der generische Rang wird durch diese Anreicherung nicht geaendert.
- Datenfehler bei erforderlicher Arbeit und Zeitueberschreitungen bleiben
  Fehler; unvollstaendige Arbeit ersetzt keinen finalen Ergebnis-Cache.
  Nicht mehr benoetigte Zusatzabrufe ausserhalb der Vorauswahl entfallen.
- `plan_build_counts` zaehlt dadurch nur noch die wirklich benoetigten nativen
  Pruefungen der Vorauswahl. Kleinere absolute Ablehnungszahlen gegenueber
  frueher sind deshalb kein Beweis besserer Signalqualitaet.
- Cup-Zwischenstaende veroeffentlichen nur Fortschritt, keine vor der
  eigentlichen Musterpruefung entstandenen Kandidaten oder internen Kontexte.
- BI 17/20, Stop-/Zielgeometrie, Strukturbarrieren, R:R-, Mail- und Risikoschutz,
  Scanneruniversum, Analysehistorie und Arbeitszeitbudgets bleiben unveraendert.

## Transparente Abdeckung

Die vier festen Zaehler `special_filter_input_count`,
`special_filter_checked_count`, `special_filter_unexamined_count` und
`special_filter_limit` zeigen die Vorauswahl vor dem Spezialfilter, dessen
abgearbeitete und unbearbeitete Kandidaten sowie das bereits bestehende Limit.
Abgearbeitet umfasst auch ausdrueckliche Ablehnungen wegen fehlender Historie,
nicht aber abgebrochene Einzelpruefungen. Nicht untersucht ist nicht abgelehnt.
Eine abgearbeitete Vorauswahl beweist keine Cup-Pruefung aller Universumstitel.
Historische Ergebnisse ohne diese Zaehler bleiben unbekannt statt null.

Der private Export uebernimmt ausschliesslich begrenzte ganzzahlige Zaehler.
Neue Fehlerdetails verwenden feste Fehlercodes, Phasen und Zeitfelder;
keine rohen Providerantworten, privaten Zeilen oder Fehlermeldungen gelangen
dadurch in die Anzeige. Persistierte Messwerte werden nur einem passenden
fehlgeschlagenen Versuch zugeordnet, niemals dem erhaltenen alten Ergebnis.
Inklusive Zeitbloecke sind ausdruecklich als nicht additiv beschriftet.

## Lokale Abnahme und verbleibende Produktionspruefung

Abnahmekriterien: exakte Rangfolge/Top-180-Paritaet einschliesslich Gleichstand,
konstante Praezision und Zeitgrenze, kein interner Kontext in Cache/API/Mail,
keine unbestaetigten Cup-Zwischenzeilen, korrekte Fehlercodes, unveraenderte
Alt-Caches bei Fehlern und ehrliche Teilabdeckung. Tests nutzen isolierte
Dateien und Provider-/Mail-Stubs; keine echten Handelsorders oder Mails.

Eingefrorene lokale Vollabnahme: **5.331 bestanden, 4 plattformbedingte Skips**
in 352,93 Sekunden. Frontend-Bundle und JavaScript-Syntax sind geprueft;
Bundle-Fingerprint `52eb7aca00d6`. Der unabhaengige Review umfasst den exakt
gleichen nativen Berechnungsblock, Praezision, Rangfolge und Diagnoseherkunft.
Ein isolierter Vorher-/Nachher-Vergleich mit 225 festen Eingangskandidaten
liefert in zwei Rankingvarianten dieselben 50 finalen Zeilen und dieselbe
Top-180-Reihenfolge, bei unveraendert 405 Historienabrufen (225 allgemeine und
180 Spezialhistorienabrufe) und nur noch 180 statt 225 nativen
Pruefungen. Das ist Pipeline-Paritaet mit gestubbtem Musterfilter, kein
Produktionsbenchmark; der echte Cup-Detektor hat separate Regressionstests.

Ein Deployment und anschliessend ein vollstaendiger Cup-Kontrolllauf bleiben
separate Nachweise: neue Health-Revision, `status=complete`, neue finale
Cache-Zeit, Abdeckungszaehler und Laufzeit. Ein gruener Testlauf garantiert
weder die Unterschreitung von 1.200 Sekunden auf Hetzner noch neue Signale,
Zustellung oder Profitabilitaet. Der verbleibende volle Historienaufwand
wurde nicht durch kuerzere Serien oder gelockerte Kriterien versteckt.
