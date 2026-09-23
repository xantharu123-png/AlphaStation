# Scan-Steuerung und validierte Live-Ergebnisse

## Stand und Geltungsbereich

Diese Notiz beschreibt die lokalen Aenderungen vom 23.09.2026 an der
Scanner-Bedienung, der Cup-Zwischenveroeffentlichung und der Ergebnisanzeige.
Sie sind vom bereits ausgerollten Basisstand zu trennen.

Die oeffentliche Produktions-Health-Pruefung meldete in diesem Arbeitsgang
`healthy`, Revision `3c667b6f165d` und Frontend-Hash `5be25ee8c547`, mit
Zeitstempel `2026-09-23T07:05:28.569207`. Das bestaetigt den erreichbaren
Basisstand, nicht einen neuen vollstaendigen Aktien-Scan oder die Bereitstellung
der hier beschriebenen lokalen Aenderungen. Der vollstaendige lokale Basis-Commit
ist `3c667b6f165d1a593a193a744f4892149701df33`.

Die vorherige begrenzte Sweep-Wiederholung und Status-Chronologie sind getrennt
in [STOCK_SWEEP_RECOVERY_2026-09-23.md](STOCK_SWEEP_RECOVERY_2026-09-23.md)
dokumentiert. Sie werden hier nicht als neue Aenderung gezaehlt.

## Auffindbare Steuerung: Stop / Pause

Die laufende Scan-Aktion zeigt jetzt ausdruecklich `Stop / Pause`; ein bestaetigt
pausierter Lauf bietet `Fortsetzen`. Angeforderte, aber noch nicht bestaetigte
Aktionen heissen `Pause angefordert...` beziehungsweise `Fortsetzung angefordert...`.
Die Beschriftung sitzt in einer eigenen mittleren Grid-Spalte mit gleich breiten
Seitenspalten und bleibt unabhaengig von Symbol und Statusanzeige zentriert.

Stop bedeutet weiterhin **kooperative Pause am naechsten sicheren Punkt**,
keinen erzwungenen Thread-Abbruch. Ein bereits laufender Provideraufruf kann
zunaechst weiterlaufen. Ein Pausenwunsch ist noch keine bestaetigte Pause; waehrend
der Abschlussphase wird keine neue Pause zugesagt.

Die Backend-Grenzen bleiben bestehen:

- `/api/scan-control` verlangt einen authentifizierten Administrator und erlaubt
  nur `pause` oder `resume`.
- Scanner-Schluessel, aktuelle Lauf-ID, laufender Status und lebender Worker
  muessen zusammenpassen. Ein fremder, alter oder unbelegter Lauf wird nicht
  gesteuert.
- Fehlende Admin-Rechte oder noch unbestaetigte Steuerungsdaten bleiben in der
  Oberflaeche erkennbar; sie werden nicht durch eine scheinbar aktive Aktion
  uebergangen.
- Bei geaenderter Daten-/Konfigurationsepoche ist eine Fortsetzung mit alten
  Daten unzulaessig; erforderlich ist ein neuer Scan. Die bestehende Option zur
  automatischen Fortsetzung und die Scan-Eigentuemerschaft bleiben unveraendert.

## Cup: nur abgeschlossene Spezialpruefungen als Zwischenstand

Cup hatte waehrend der allgemeinen Universum-Vorauswahl bewusst keine
Ergebniszeilen veroeffentlicht: Ein generischer Kandidat ist noch kein bestaetigtes
Cup-Muster. Diese Grenze bleibt bestehen.

`_apply_special_strategy_post_filter` meldet nun ueber einen optionalen internen
Callback den Fortschritt der Cup-Spezialpruefung. Erst nach einer vollstaendig
abgeschlossenen Kandidatenpruefung koennen bereits akzeptierte Zeilen in einen
Teilcache gelangen. Ein unterbrochener Kandidat gilt nicht als geprueft. Der
Publisher prueft zusaetzlich den bestehenden Zeilenvertrag; die Ergebnis-API
wendet weiterhin ihre Anzeige- und Signalregeln an. WATCH-/BEOBACHTEN-Zeilen und
generische Vor-Kandidaten werden dadurch nicht zu sichtbaren Signalen.

Die vorhandene Cache-Drosselung begrenzt die Schreibfrequenz auf den bestehenden
1,5-Sekunden-Abstand. Phasenbeginn, erster akzeptierter Treffer und Phasenende
werden gezielt veroeffentlicht. Kopien der begrenzten Vorschau entstehen beim
tatsaechlichen Schreiben, nicht bei jedem Kandidaten. Rangfolge, Speziallimit
und Endergebnislimit bleiben gleich; der Callback veraendert keine Ergebniszeile.

Der Zwischenstand ist ausdruecklich `partial`, `scan_in_progress` und in seiner
Abdeckung unvollstaendig. Spaetere Risiko-/Qualitaetsanreicherung und die finale
kausale beziehungsweise Momentum-Vertragspruefung bleiben erforderlich. Ein
Teilcache ersetzt keinen finalen Cache und loest weder Mailversand noch Tracking
aus. Schreibfehler werden nicht in einen erfolgreichen Nulltreffer-Lauf verwandelt.

## Wahrheitsgemaesser Fortschritt in zwei Phasen

Die Universum-Vorauswahl und die nachgelagerte Musterpruefung haben verschiedene
Nenner. Die API bewahrt deshalb die urspruenglichen Universumszahlen und ergaenzt
die Spezialphase separat mit `runtime_phase: special_filter` sowie
`special_filter_input_count`, `special_filter_checked_count`,
`special_filter_unexamined_count` und `special_filter_limit`.

Die Oberflaeche zeigt waehrend dieser Phase `Musterpruefung: geprueft/ausgewaehlt`
und sagt ausdruecklich, dass die Universum-Vorauswahl abgeschlossen ist. Der
Spezial-Nenner ist das kleinere von Eingangsmenge und Speziallimit. Beispielsweise
sind bei 185 vorausgewaehlten Kandidaten und Limit 180 hoechstens 180
Musterpruefungen belegt; die weiteren 5 werden nicht als muster-geprueft dargestellt.
Unplausible, widerspruechliche oder nicht zum aktiven Teilstand gehoerende
Fortschrittszahlen werden nicht als Spezialfortschritt angezeigt.

## Lebender Timeout-Zwischenstand ist kein abgeschlossener Fehlerlauf

Ein Watchdog-Hinweis `scan_timeout` beendet die Anzeige nur dann nicht, wenn
saemtliche Belege fuer denselben weiterlaufenden Scan vorliegen: `partial: true`,
`scan_running: true`, unterstuetzte Steuerung, lebender Worker im Zustand
`running` sowie identische Ergebnis-, Steuerungs- und gegebenenfalls erwartete
Lauf-ID. Die Oberflaeche pollt dann weiter und kennzeichnet die Treffer als
vorlaeufigen Zwischenstand mit Zeitbudget-Warnung, nicht als erfolgreichen Scan.

Diese enge Ausnahme gilt nicht fuer Daten-/Providerfehler, fehlende oder fremde
Laufidentitaet, einen beendeten Worker oder andere unbestaetigte Zustaende.
Bei Fehler, unvollstaendigem Abschluss, erforderlichem Neustart oder Laufwechsel
wird nur ein zuvor bestaetigter finaler Ergebnisstand wiederhergestellt. Existiert
noch keiner, werden die vorlaeufigen Zeilen, deren Zeitstempel und Fortschritt
entfernt. Auch Netzwerk-/HTTP-Fehler duerfen solche Zeilen nicht spaeter wieder
als Endergebnis hervorholen. Aktuelle Steuerungsinformationen koennen getrennt
erhalten bleiben. Weder ein leerer Teilstand noch ein Worker-Ende ohne neuen
vollstaendigen Cache ist ein bestaetigter erfolgreicher Nulltreffer-Scan.

## Cup und Wyckoff: Funktionsumfang und Beweisgrenzen

Cup erhaelt mit dieser Aenderung fruehere, bereits vertragsgepruefte Vorschauen.
Wyckoff besitzt bereits einen manuellen Scanpfad mit Muster- und
Eintrittsvertragspruefung sowie validierter Teilveroeffentlichung. Wyckoff ist
weiterhin **nicht Teil der automatischen Aktienrunde**; diese Aenderung fuegt
keine automatische Wyckoff-Planung hinzu.

Lokale Fixtures pruefen diese Codepfade, beweisen aber keinen frischen
Produktionslauf mit aktuellen Marktdaten. Fehlende Treffer oder ein fehlender
aktueller Cache allein belegen weder einen Defekt noch einen vollstaendigen
Nulltreffer-Lauf. Die Live-Funktionspruefung von Cup und Wyckoff bleibt von
Health-Status, synthetischer Vorschau und Offline-Tests getrennt.

## Unveraenderte Schutzgrenzen

Keine Signal-Schwelle, BI-17/20-Regel, Cup-/Wyckoff-Geometrie, Schlusskurs- oder
Volumenbestaetigung, Struktur-, Kausalitaets-, R:R- oder Kursdatenpruefung wurde
gelockert. Keine neue Watchlist wurde eingefuehrt. Mail-Gates, Empfaenger,
Deduplizierung und Tracking wurden fuer diese Bedienungs-/Vorschauaenderung
nicht geaendert. Vorlaeufige Treffer sind keine Mailfreigabe oder Ausfuehrung.

## Lokale Nachweise und ausstehender Rollout

Die abschliessende vollstaendige Offline-Suite bestand mit **6.405 Tests**, bei
**4 uebersprungenen Tests**, in 520,83 Sekunden. Ergebnisdatei (nur lokal):
`output/scan-ui-full-suite-20260923.xml`. Das neu gebaute Frontend wurde mit
`scripts/verify_frontend_bundle.py` geprueft: Quellhash `46ba474ad543`.

Die unabhaengige Nachpruefung bestand mit **210 gezielten Tests** in 19,34 Sekunden.
Sie fand nach der Korrektur des Erstlauf-/Teilansichtsfehlers keine blockierenden
Befunde mehr; insbesondere blieben Berechtigungen, Laufidentitaet und der Schutz
des finalen Caches erhalten. Ergebnisdatei (nur lokal):
`output/independent-final-partial-review-sep23.xml`.

Der gezielte Backend-Offlinelauf ueber 17 Testdateien bestand mit **609 Tests**
in 9,91 Sekunden. Er deckt unter anderem Cup-Vorschau, Drosselung,
Endergebnis-Paritaet, Vertrags- und Watch-Ausschluss, Universum/Spezial-Nenner,
Fehlerbereinigung sowie bestehende Cup-, Wyckoff- und Scan-Control-Vertraege ab.
Der Offline-Harness isoliert Laufzeitdaten und sperrt externe Netzwerkverbindungen
und echten SMTP-Versand. Diese Zahl ist ein begrenzter Teillauf, kein neuer
Gesamtteststand.

Die lokale Browser-QA mit synthetischen Daten pruefte Desktop-Breite 1280 und
mobile Breite 375. In beiden Ansichten betrug der gemessene Versatz der
Aktionsbeschriftung zur Button-Mitte 0 Pixel; Pause und Fortsetzung funktionierten
im Fixture. Die Screenshots liegen lokal unter
`output/playwright/scan-controls-desktop.png` und
`output/playwright/scan-controls-mobile.png`. Das ist keine reale
Geraetepruefung und kein Pausen-/Fortsetzungsnachweis eines Produktionsscans.

Noch offen beziehungsweise separat nachzutragen:

- Bereitstellung dieses lokalen Aenderungsstands und erneute Health-/Revision-
  Kontrolle danach.
- Ein frischer vollstaendiger Cup-Lauf und ein ausdruecklich gestarteter
  Wyckoff-Lauf mit aktueller Laufidentitaet, vollstaendiger Abdeckung und finalem
  Cache; reale Pause/Fortsetzung ist bei Bedarf getrennt zu pruefen.
- Ein Mail-Zustellnachweis nur dann, wenn ein vollstaendiger Lauf tatsaechlich
  die unveraenderten Versandbedingungen erfuellt. Lokale Tests und sichtbare
  Teiltreffer sind dafuer kein Ersatz.
