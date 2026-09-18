# Aktien-Signalkette: Reparatur vom 18.09.2026

## Anlass und belegte Fehler

Der private Serverexport vom 18.09. belegte unter `b82b876` nur zwei von vier
vollstaendig ausgewerteten Aktienstrategien. Momentum erreichte sein Arbeitslimit;
Cup erhielt nach den vorherigen Strategien nur den verbliebenen Rest des
gemeinsamen Budgets. `healthy` war kein Nachweis einer vollstaendigen Runde.

Die lokale Untersuchung zeigte wiederholte Normalisierung derselben Kerzen pro
Level-Zone sowie umfangreiche formatierte Zwischenstandscaches. Ausserdem wurden
Strukturzonen bereits fuer Kandidaten berechnet, die danach am Volumenfilter
scheiterten. Fehlende native Handelsplaene wurden nur als geschaetzter Plan
sichtbar, ohne den konkreten Grund aus dem Plan-Builder zu erhalten.

Ein weiterer reproduzierter Fehler: Ein reiner Schlusskurs-Referenzwert (PDC)
konnte am identischen Swing-Einstieg als eigene Gegenbarriere mit null Abstand
gelten. Referenzinformation ist keine unabhaengige Bestaetigung von Angebot oder
Nachfrage. Gemischte Zonen mit echten Hochs/Tiefs oder Swing-Evidenz sind davon
ausdruecklich zu unterscheiden und muessen weiterhin als Struktur gelten.

## Reparaturumfang

- Reine PDC-/PWC-Referenzzonen bleiben sichtbar, gelten alleine aber weder als
  Gegenbarriere noch als Stop-Invalidierung. Gemischte Zonen mit PDH/PDL,
  Swing-Pivots oder einer echten Strukturrolle bleiben erhalten. Widersprechen
  sich Referenzhinweis und Strukturrolle, bleibt die Zone konservativ wirksam.
- Einmalige kanonische Kerzenaufbereitung innerhalb eines Struktur-Snapshots;
  oeffentliche Einstiege pruefen weiter rohe, ungeordnete, widerspruechliche
  oder zukuenftige Eingaben. Kein globaler Daten-Cache, keine kuerzere Historie.
- Teure D/W-Struktur erst nach den bestehenden RVOL-/Momentum-Pruefungen.
  Ueberlebende Kandidaten erhalten weiterhin die vollstaendigen Metriken.
- Kompakte JSON-Speicherung bei unveraendertem Cache-Schema und atomarem Austausch.
- Faire Arbeitsanteile fuer die noch ausstehenden Strategien. Ungenutzte Zeit
  schneller Strategien bleibt fuer die folgenden verfuegbar. Maximal 20 Minuten
  pro Teilstrategie und 30 Minuten Gesamtanalyse bleiben unveraendert; SMTP wird
  nicht mitten in einem Eigentums-/Zustelluebergang abgebrochen. Vollstaendige
  Abdeckung ist weiterhin ein zu pruefendes Ergebnis, keine Zeitbudget-Garantie.
- `native_plan_status`/`native_plan_reason` auf Analysezeilen und begrenzte
  `plan_build_counts` erklaeren den Stand im Struktur-Builder. Sie sind **kein**
  Beleg fuer bestandene spaetere VRVP-, Qualitaets-, Ausfuehrungs- oder Mailgates.
- `leaf_elapsed_seconds` trennt die Teilstrategiezeit von der kumulativen
  Rundenlaufzeit.
- Der private Nur-Lese-Export nennt sichere Cache-Fehlerkategorien und zeigt
  bekannte Krypto-Zustaende/Scanstatistiken. Gespeicherte Zeilenzustaende sind
  weder eine aktuelle Triggerbestaetigung noch ein Zustellnachweis.

## Unveraenderte Grenzen

BI 17/20, Mindestqualitaet, R:R, echte erste Gegenbarriere, Break/Reclaim,
Kursfrische, Liquiditaet, Dedupe und Handelsrisikogrenzen bleiben bestehen.
Ungereclaimte echte Struktur, zu wenig Raum bis zur echten Gegenbarriere und
geschaetzte statt nativer Handelsplaene werden nicht nachtraeglich freigegeben.
Ein Teilscan ersetzt keinen vollstaendigen Cache und erzeugt keine Signale aus
unverifizierten Teilergebnissen. Keine Watch-/ARMED-Mails werden aktiviert.

Crash-Risiko ist ein gesonderter Info-Mailpfad. Er belegt nicht, dass jeder
Handelssignalpfad funktioniert. Crypto Explosion und die kombinierte
Kryptoansicht besassen bei der Diagnose keine eigene automatische Entry-Mail-
Anbindung; die Aktivierung neuer Mailtypen ist eine getrennte Entscheidung.

## Abnahmegrenzen

Die Regressionstests verwenden feste OHLCV-Daten, echte Struktur-/Planberechnung,
simulierte Uhren und abgeschirmte Transportgrenzen. Positive LONG-/SHORT-Faelle
durchlaufen Scanner, Score, Qualitaetsgates, finale Mailvalidierung, Sender und
Zustellungsjournal. Nur der SMTP-Transport ist simuliert: genau eine Nachricht
und ein protokollierter `accepted`-Status pro Fall; keine behauptete Orderfuellung.
Negative Faelle mit echter naher Gegenbarriere oder fehlendem Reclaim bleiben
abgelehnt. Solche Tests beweisen keine Trefferquote und keine reale
SMTP-/Inbox-Zustellung.

Der isolierte Vorher-/Nachher-Vergleich nutzt dieselben synthetischen Kursdaten
und tauscht ausschliesslich die Referenzzonen-Klassifizierung aus: Die alten
LONG-/SHORT-Plaene wurden auf 0,22R und Score 45 begrenzt; nach der Korrektur
erreichen sie 1,74R bis TP1 und bestehen die echten Mailgates (Score 92/93).
Das belegt den Selbstblocker, nicht seine Haeufigkeit in Produktionsdaten.

Eine feste Acht-Kandidaten-Messung mit jeweils 753 Tageskerzen reduzierte die
Normalisierungsaufrufe von 432 auf 88 und die Zwischenstandscaches von 1.880.358
auf 1.061.692 Bytes. Das ist lokale Rechen-/Speicherevidenz, keine zugesicherte
Hetzner-Laufzeit. Die Kerzen-Wiederverwendung wurde vor der absichtlichen
Referenzzonen-Korrektur mit 960 exakten Strukturvergleichen gegen den alten
Implementierungsstand geprueft.

Lokale Abschlusspruefung: **4.928 bestanden, 4 uebersprungen** in der gesamten
aktiven Python-Testsuite (offline, isolierte Datenpfade). Die vier ausgelassenen
Pruefungen benoetigen Linux-Dateisystemfunktionen oder Windows-Symlinkrechte.
Nach der letzten Testverstaerkung bestanden zusaetzlich **74 gezielte Tests**
fuer Referenzzonen und die native Mailkette. Dabei wird die tatsaechlich
gespeicherte Klasse `trade / email / stocks_swing / ACTIVE` mit
`fill_evidence_verified=0` geprueft; eine WATCH-Mail kann den positiven Nachweis
nicht erfuellen. Die Quellcode-Dateien blieben waehrend des finalen Gesamtlaufs
unveraendert.

Vor der Betriebsfreigabe sind auf Hetzner die neue Revision, ein vollstaendiger
Scan und die aktuellen Plan-/Versanddiagnosen zu pruefen. Ein erneuter Restart
oder ein gruenes Health allein reicht nicht. Private Exporte bleiben lokal und
werden nicht mitcommittet.
