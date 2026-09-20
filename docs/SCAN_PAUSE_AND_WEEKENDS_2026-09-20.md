# Aktien-Scans pausieren, fortsetzen und am Wochenende aussetzen

## Bedienvertrag

- Administratoren koennen die generischen Aktienstrategien (einschliesslich Cup and Handle) sowie BI Long/Short pausieren. Gemeinsame Serverlaeufe betreffen alle Nutzer.
- Die API quittiert zuerst `pause_requested`. Erst der echte Worker bestaetigt an einer sicheren Grenze `paused`. Ein bereits laufender Netzwerkaufruf wird nicht gewaltsam beendet.
- Fortsetzen nutzt denselben Thread, dieselbe Run-ID und denselben Fortschritt. Alternativ wartet der Worker auf den naechsten regulaeren Termin. Aktienstrategierunde: derzeit 60 Minuten; BI: 180 Minuten. Kein pauschaler Stundentakt fuer alle Scanner.
- Der schwere Aktienplatz bleibt waehrend der Pause reserviert. Andere schwere Aktienlaeufe warten. Krypto, leichte Jobs und Positionspflege werden nicht global angehalten.
- Nach Beginn des atomaren Abschlusses (`finishing`, Cache/Mailphase) ist keine neue Pause mehr moeglich.
- Pause ist prozesslokal. Nach Dienst-/Serverneustart ist kein gespeicherter Ausfuehrungsstapel vorhanden. Ein neuer Lauf ist erforderlich.
- Bei geaendertem Tagesdatenstand, Handelstag, Konfiguration oder unklarer Datenbasis wird der alte Worker normal abgewickelt. Ein frischer Lauf darf erst nach dessen tatsaechlichem Threadende starten. Live-Snapshot-Scans werden nach einer Pause grundsaetzlich frisch gestartet, nicht aus veralteten Kursen fortgesetzt.

## Wochenenden

Automatische neue US-Aktien-Scans starten samstags und sonntags nicht (`America/New_York`, inklusive Sommerzeit). Montag setzt die Planung ohne Neustart fort. Die Regel gilt auch fuer automatische Wiederaufnahme einer Pause.

Manuelle Starts und manuelles Fortsetzen bleiben moeglich. Bereits laufende Scans werden am Tageswechsel nicht abgebrochen. Dies ist eine Wochenendregel, kein Feiertags- oder Boersenoeffnungsfilter. Montag 00:00 ET bedeutet nur wieder zulaessig, nicht garantiert sofortiger Start: freie Worker und jeweiliges Intervall gelten weiterhin.

Nicht angehalten werden Krypto, gemischter Markt-/Crashkontext, bestehende Positionen, Signalbewertung und Mail-Outbox. Der optionale Legacy-BG-Scannerplan beachtet dieselbe Wochenendregel. Auto-Trader und Orders wurden nicht veraendert oder aktiviert.

## Integritaet

- `/api/scan-control` verlangt Administrator-Authentifizierung, genaue Scannerkennung und aktuelle Run-ID. Browser-`AbortController` ist keine Serverpause.
- Keine ueberlappenden schweren Aktienworker oder Ersatzthreads fuer geparkte Scans.
- Pausezeit zaehlt nicht zum Arbeitsbudget, Watchdogbudget oder zur protokollierten aktiven Laufzeit. Eine angeforderte, aber noch nicht erreichte Pause unterdrueckt keine echte Zeitueberschreitung.
- Nach erfolgreicher Fortsetzung gilt wieder ein voller regulaerer Abstand. Dies gilt auch fuer den frischen Ersatzlauf bei gewechselter Datenbasis; kein unmittelbarer doppelter Nachhollauf.
- Eine Pause erzeugt keinen erfolgreichen Nulltrefferlauf, keinen neuen Ergebniszeitstempel und keine Entwarnung. Fehler und letzter vollstaendiger Datenstand bleiben getrennt.
- Bereits vollstaendig veroeffentlichte Teilstrategien einer Runde bleiben erhalten. Noch unvollstaendige Ergebnisse werden nicht als neue vollstaendige Runde ausgegeben. Die unveraenderte gemeinsame Mailpruefung folgt erst dem kontrollierten Abschluss.
- BI 17/20, Struktur-, R:R-, Datenqualitaets- und Versandpruefungen bleiben unveraendert. Diese Betriebsfunktion belegt keine bessere Trefferquote.

## Abnahmegrenzen

Offline-Regressionen pruefen echte geparkte Threads, Eigentuemerschutz, Epochwechsel, Authentifizierung, Wochenenden/DST und Frontend-Zustandswechsel. Eine ausschliesslich lokale synthetische Browserfixture dient zur Bedienkontrolle. Produktion ist damit nicht aktualisiert: Pull, kontrollierter Neustart und Produktionskontrolle sind getrennte Schritte.

Private Exporte, Laufzeitdatenbanken und QA-Artefakte gehoeren nicht auf GitHub.

## Verifiziert am 20.09.2026

- Vollstaendige isolierte Regression: 5.606 bestanden, 4 uebersprungen, keine Fehler (5610 gesammelte Tests). Externe Sockets und SMTP waren im Testprozess blockiert.
- Gebautes Frontend: `9f9d56d3ae8e`; Bundle-Konsistenz und JavaScript-Syntax geprueft.
- Echter lokaler Browser: Cup pausieren/fortsetzen, Pause nach Neuladen, Auto-Fortsetzung ausschalten, BI-Pausenanzeige, Wochenendhinweis mit erlaubtem manuellen Start, Nicht-Admin ohne Steuerknoepfe. Desktop und 390-Pixel-Ansicht kontrolliert; finale Seiten ohne JavaScript-Laufzeitfehler.
- Unabhaengige Abschlusspruefung fand und behob einen doppelten Folgelauf nach epochbedingtem Ersatzstart. Reale Schedulerregressionen pruefen Strategierunde und beide BI-Richtungen; die Vor-Fix-Negativkontrolle schlug in allen drei Faellen erwartungsgemaess fehl.
- Kein Produktionsscan, Provideraufruf, Signalversand oder Serverupdate fuer diese Abnahme ausgefuehrt.
