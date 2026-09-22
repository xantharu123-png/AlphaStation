# Aktienrunde: begrenzte Timeout-Wiederholung und Cup-Diagnostik

## Ausgangspunkt

Ein Strategiefehler ist kein erfolgreicher Nulltreffer-Lauf. Der bestehende
Aktien-Sweep verteilt sein Arbeitsbudget von 30 Minuten fair auf die noch
ausstehenden Strategien. Damit bekommt die erste Strategie bei vier Strategien
zunaechst 450 Sekunden. Bisher blieb sie nach einem Timeout fehlgeschlagen, selbst
wenn die nachfolgenden Strategien deutlich unter ihrem Anteil blieben.

Unabhaengig davon fehlte bei Cup der letzte Ablehnungsgrund: Die vorhandenen
`plan_build_counts` beschreiben den generischen Strukturplan vor der speziellen
Cup-Pruefung. Sie duerfen nicht als Ursache einer Cup-Ablehnung ausgelegt werden.

## Korrektur

Nach allen Erstversuchen darf genau eine tatsaechlich durch `ScanWorkTimeout`
abgebrochene Strategie nochmals laufen. Die Reihenfolge der Erstversuche bleibt
Momentum, Gap Long, Gap Short, Cup. Die Wiederholung:

- verwendet nur das Restbudget derselben Runde, keinen neuen 30-Minuten-Zeitraum;
- braucht mindestens 60 Sekunden Arbeitszeit nach Abzug von 15 Sekunden Reserve;
- bleibt zusaetzlich unter dem unveraenderten 20-Minuten-Einzellimit;
- laeuft im selben Thread und verwendet den bereits begrenzten, lauflokalen Cache;
- startet mit neuem Strategie-Zustand, ohne Teilresultate des abgebrochenen Versuchs;
- wiederholt weder Datenfehler noch Authentifizierungsfehler oder SMTP-Versuche;
- respektiert Pause und erzwungenen Neustart; `ScanRestartRequired` wird nicht abgefangen.

Die Reserve begrenzt die kooperative Analyse, ist aber keine Garantie fuer die
Dauer eines bereits laufenden Betriebssystemaufrufs, Cache-Schreibens oder SMTP.
Vorhandene Netzwerk-Timeouts und Watchdog bleiben deshalb erforderlich.

Erfolgreiche Geschwisterstrategien bleiben unveraendert. Nach allen Versuchen
wird hoechstens einmal die gemeinsame Mailpruefung aufgerufen. Die Grenzen
25 Kandidaten je Strategie, 75 je gemeinsamer Pruefung und 100 im Gesamtergebnis
sowie die interne Mailauswahl bleiben unveraendert. `guarded` ist weiterhin kein
Beleg fuer SMTP-Annahme. Ein weiterhin unvollstaendiger Sweep ersetzt keinen
frueheren vollstaendigen Gesamtcache.

## Nachvollziehbare Diagnostik

Die Ergebnis-API konnte ausserdem einen alten manuellen Fehler im RAM behalten,
obwohl eine spaetere automatische Strategie bereits erfolgreich abgeschlossen
war. Die Antwortprojektion entfernt diesen alten Fehler nur mit beiden Belegen:
einem strikt spaeter gestarteten, vollstaendig erfolgreichen Strategie-Versuch
und dessen zeitlich passendem, strategieeigenem, aktuellem Komplettcache.
Laufende oder neuere manuelle Versuche, fehlende/unklare Zeitangaben, Teilcaches,
falsche Cache-Versionen und fremde Ergebnisidentitaeten bleiben geschuetzt.
Scheduler-RAM, Lauf-ID und Abschlusszeit werden dabei nicht umgeschrieben.

Der Sweep meldet `timeout_retries_attempted` und `timeout_retries_recovered`.
Nur die wiederholte Strategie erhaelt `timeout_retry_count: 1` und
`initial_error_code: scan_timeout`; ihr normaler Status beschreibt den letzten
Versuch. `strategies_attempted` zaehlt weiterhin verschiedene Strategien, nicht
die Anzahl der Versuche.

`cup_terminal_counts` zaehlt genau einen Endpunkt pro fertig geprueftem
Cup-Kandidaten. Ein unterbrochener Kandidat bleibt ungeprueft. Bei mehreren
getesteten Tassenfenstern gilt die tiefste erreichte Pruefstufe des entscheidenden
Detektorversuchs, nicht jede einzelne Fensterablehnung. Erfasst werden unter
anderem Historie, Liquiditaet, Form, Schlusskursbestaetigung, Volumen, Einstieg,
Handelsplan und Score. `special_filter_accepted` bezeichnet nur die bestandene
Spezialpruefung, nicht eine Mailfreigabe oder Ausfuehrung.

API und Export lassen ausschliesslich feste Grundcodes und begrenzte Ganzzahlen
durch. Keine Ticker, Kurszeilen, Providertexte oder Zugangsdaten gelangen durch
diese neuen Diagnosefelder nach aussen. Signalbedingungen und Cup-Berechnungen
werden dadurch nicht veraendert.

## Pruefung und Betriebsgrenze

Abschliessender Offline-Gesamtlauf: **6312 bestanden, 4 uebersprungen**, ohne
externe Netzwerkverbindungen oder echten SMTP-Versand. Zusaetzlich unabhaengig
geprueft: Wiederholungsgrenzen, Fehlerisolation, Cup-Akzeptanzparitaet, Export-
Projektion, Status-Chronologie sowie unveraenderte Cup-Watch-Deduplizierung.

Die Offline-Tests decken Cache-Wiederverwendung, Zeitgrenzen, Pausen, Kontroll-
Ausnahmen, Fehlerisolation, Rangfolge, Einmaligkeit der Mailpruefung und strikte
Diagnose-Projektion ab. Ein synthetischer Lauf mit 1136 Sekunden Erstversuchen
verwendet hoechstens 649 weitere Arbeitssekunden und behaelt 15 Sekunden Reserve.

Diese Aenderung fuegt keine automatische Wyckoff-Strategie hinzu und lockert weder
BI 17/20 noch Struktur-, R:R-, Kursdaten- oder Mailpruefungen. Nach dem Serverupdate
sind ein vollstaendiger neuer Lauf und dessen aktueller Zustellnachweis gesondert
zu pruefen. Ein Commit oder lokaler Test aktualisiert Hetzner nicht.
