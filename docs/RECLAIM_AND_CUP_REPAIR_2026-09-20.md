# Fortsetzung: Reclaim-Zeitlinie und Cup-Laufzeit

## Ausgangslage

Gepruefter Ausgangsstand: `c650288`, am 20.09. auf Hetzner weiterhin aktiv.
Der private Export vom 19.09. belegt einen Cup-Timeout und keine neue
protokollierte Trade-Mail. Er belegt weder einen allgemeinen SMTP-Ausfall
noch die Haeufigkeit einzelner Logikfehler in realen Signalen.

## Begrenzter Reparaturauftrag

1. Zusammengefuegte Level-Evidenz darf einen bereits kausal bestaetigten
   Rollenwechsel nicht vergessen, wenn seine damalige Geometrie weiterhin gilt.
   Neue/erweiterte Grenzen, spaete Rollen und echte Fehl-Ausbrueche duerfen
   alte Ausbruchs-/Retestkerzen nicht nachtraeglich als Beleg benutzen.
2. Nachweismetadaten und finale Mailvalidierung muessen dieselbe Zeitlinie
   strikt pruefen; bestehende Nachweise bleiben rueckwaertskompatibel.
3. Cup-Aufwand offline messen und nachgewiesene unnoetige Arbeit reduzieren,
   ohne Universum, Rangfolge, Signalkriterien oder Arbeitslimits zu lockern.
4. Gezielte Regressionen, unabhaengiger Review und komplette lokale Testsuite.
   Erst danach gezielter Commit/Push; private Exporte bleiben unversioniert.

## Unveraenderte Verträge und Abnahmegrenzen

BI 17/20, Mindest-R:R, echte Strukturbarrieren, Datenfrische, Dedupe und
Mail-/Risikoschutz bleiben erhalten. Ein BI-Lauf mit isoliertem Datenfehler
wird nach dem bisherigen Vertrag weiter ehrlich als unvollstaendig markiert;
dieser Vertrag wird nicht als vermeintlicher Bugfix aufgeweicht.

Keine echten Mails, Handelsorders oder Provideraufrufe in lokalen Tests.
Vollstaendige Serverlaeufe und echte SMTP-Annahme sind separate Nachweise nach
Deployment. Keine Garantie fuer Gewinne, Trefferzahlen oder Inbox-Zustellung.

## Umgesetzt

### Zeitlinie der Zonen und finale Signalpruefung

`confirmed_at` bleibt die letzte Bestaetigung aller aktuellen Zonenmitglieder.
Ein separater, richtungsbezogener Anker darf aelter sein, wenn schon damals
die gesamte heutige Geometrie durch eine zusammenhaengende Vereinigung echter
struktureller Mitglieder gedeckt war und die relevante Rolle bestand.
Referenzen und Projektionen koennen weder fehlende Ausdehnung noch eine
Verbindung zwischen getrennten Teilzonen erzeugen. Spaet erweiterte Grenzen,
Verbindungen oder Rollen duerfen keine alten Kerzen ausleihen.

Die neue Nachweisversion `break_reclaim_close_hold_v2` bindet Zonen-ID, beide
Grenzen, Richtungsanker und aktuelle Mitgliedschaftszeit. Der letzte gepruefte
Schlusskurs muss mindestens bis zu dieser Mitgliedschaftszeit reichen. Eine
alte 4H-Serie darf deshalb keine neuere taegliche Fehl-Ausbruchskerze verdecken.
Spaetere fehlgeschlagene Schlusskurse setzen einen zuvor gelungenen Rollenwechsel
weiter zurueck. Die bisherige V1-Serialisierung und ihre Pruefung bleiben erhalten.

API und native Barrieren behalten diese Nachweise und pruefen die Bindung.
Fehlende, widerspruechliche oder zukuenftige Metadaten werden abgewiesen.
Ein gueltiger Nachweis beseitigt nur die betreffende Strukturblockade: Er setzt
weder `execution_trigger_ok` noch die uebrigen Risiko-/Mailfreigaben auf wahr.

Zusaetzliche End-to-End-Pruefung fand einen Praezisionsverlust: Die nativen
Barrierenmetadaten rundeten z.B. `100.12345670000002` auf `100.12`. Kanonische
Unter-/Obergrenzen und Reclaim-Grenzen bleiben nun exakt erhalten, auch bei V1.
Angezeigte Preise, Entry/Stop/Targets und Legacy-Fallback bleiben unveraendert.
Echte native Plaene mit ungeraden Dezimalwerten und spaeteren, vom Level-Modul
erzeugten V1-/V2-Nachweisen werden in beiden Richtungen bis zur finalen Pruefung
getestet; die Vergleichstoleranz wurde nicht erweitert.

Grenze: Die Geometrie verwendet die Rauschpolsterung des aktuellen kausalen
Snapshots. Es wird keine Rekonstruktion der historischen ATR behauptet.
Wie viele echte bisherige Ablehnungen dieser Fehler verursachte, ist unbekannt.

### Cache-Schreiblast und Cup-Messung

Die naechste 1,5-Sekunden-Frist fuer einen Zwischenstand startet jetzt nach dem
abgeschlossenen Schreiben. Vorher konnte ein langsamer Schreibvorgang seine
eigene Frist verbrauchen und sofort beim naechsten verworfenen Symbol erneut
ausgeloest werden. Ein Fake-Clock-Test reproduzierte 83 statt 2 Schreibvorgaenge;
der Fix bewahrt initiale Ausgabe und erzwungene erste Trefferveroeffentlichung.

Der Cache wird weiterhin atomar im Zielverzeichnis ersetzt. Statt sehr vieler
Python-Einzelschreibvorgaenge wird der unveraenderte kompakte JSON-Inhalt einmal
kodiert und geschrieben. Bei Serialisierungsfehlern bleiben Altdatei und
Temp-Dateibereinigung erhalten. Preis: der kodierte Text liegt voruebergehend
zusaetzlich im Arbeitsspeicher.

Ein rein synthetischer Vergleich mit 50 gleichen Zeilen und 8.043.897 Bytes
ergab 1.187.659 Schreibaufrufe / 0,410 s gegen 1 Schreibaufruf / 0,067 s bei
identischen Bytes. Das ist ein lokaler Encodervergleich, kein Nachweis fuer
die Dauer eines vollstaendigen Hetzner-Scans.

Sechs feste Zeitzaehler zeigen abgeschlossene Bloecke je Einzelstrategie:
`history`, `structure`, `execution_history`, `plan`, `cache_publish` und
`special_filter`. Sie werden auch bei Exceptions aktualisiert; aktive Bloecke
erscheinen erst beim Verlassen. Zeiten koennen einander einschliessen und sind
ausdruecklich nicht additiv. Die finale Attempt-Datei enthaelt auch vorangegangene
Cache-Schreibzeit, nicht jedoch ihre eigene abschliessende Speicherung.
Der nur lesende Export uebernimmt nur begrenzte Zaehler aus dieser festen Liste.
Alte Ergebnisse ohne Messung bleiben ohne Messung, nicht vermeintlich bei null.

Die vorhandene Produktionsmeldung entstand in `analyzing`, vor dem speziellen
Cup-Detektor. Eine pauschale Schuldzuweisung an diesen Detektor waere unbelegt.
Doppelte Struktur-Aliase bleiben kompatibel erhalten und koennen grosse Caches
erzeugen. Der Export behaelt seine 8-MiB-Lesegrenze und kann solche Caches weiterhin
ehrlich als `too_large` melden; die kleineren Attempt-Dateien liefern die neuen
Zeitzaehler separat. Historienabruf, Universum, Rangfolge und Laufzeitlimits
wurden durch diese Korrektur nicht gelockert oder verkuerzt.

## Noch benoetigte Produktionsnachweise

Nach Commit/Push und separatem Deployment:

1. Health meldet die neue Revision; API und Hintergrunddienst sind aktiv.
2. Eine vollstaendige Aktienrunde wird je Strategie als abgeschlossen oder
   unvollstaendig ausgewertet; alte Caches gelten nicht als aktueller Erfolg.
3. Falls Cup erneut ins Limit laeuft, Zeitzaehler und einzelne Abrufe gezielt
   untersuchen. Der aktuelle Fix beweist noch nicht, dass der Timeout weg ist.
4. Ein wirklich gueltiges Signal durch getrennte Scanner-, Risiko-/Mailpruefung
   und Zustellungsjournal verfolgen. Kein Testsignal oder SMTP-Versand erzwungen.
5. Ergebnisse nach Kosten benoetigen weiter eine getrennte belastbare Auswertung.

## Lokale Abnahme am 20.09.2026

Finaler Gesamtlauf nach Schreibstopp aller Quellcode-/Testdateien:
**5.059 bestanden, 4 uebersprungen**, 310,72 Sekunden. Einschliesslich 131 neuer
Regressionsfaelle fuer historische Zonen, strenge Weitergabe, Dezimalpraezision,
Cache-Schreiblast und gespeicherte Laufzeitdiagnose. Der alte atomare
Schreibfehlertest injiziert seinen Fehler jetzt bei `json.dumps`; seine
Datenerhalt-/Temp-Bereinigungspruefungen wurden nicht entfernt.

Die vier Skips betreffen drei Linux-Dateisystemvertraege
(`O_DIRECTORY`/`O_NOFOLLOW`, atomare Umbenennung und FIFO/Hardlink) sowie einen
Directory-Symlink-Test ohne erforderliche Windows-Berechtigung. Diese Faelle
sind nicht als auf diesem Host ausgefuehrt zu werten.

Mehrere unabhaengige Reviews und fokussierte Tests bestaetigten die zeitliche
Zonenlogik, die nachgelagerte Validierung und den atomaren Cachepfad. Der finale
Gesamtlauf war offline mit isolierten Datenpfaden und gesperrtem externem
Netzwerk/SMTP. Seine XML-Ausgabe und alle privaten Serverexporte bleiben lokal.

Zuletzt vor Commit live geprueft: Hetzner-Health gesund auf `c65028801657`.
Passwortloser SSH-Zugriff wurde abgewiesen; kein Server-Pull, Neustart oder
Cron-Eingriff in diesem Reparaturdurchgang. Lokale Abnahme ersetzt nicht die
oben aufgefuehrten Produktionsnachweise.
