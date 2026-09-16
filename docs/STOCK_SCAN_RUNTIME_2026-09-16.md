# Strategierunde: Laufzeit, Datenwiederverwendung und Zustellung

## Anlass und Beleggrenze

Produktion `bc132ca` meldete am 16.09.2026 um 07:03 UTC das 105-Minuten-
Hartlimit. Der vorherige Logauszug belegte 33 Momentum-Long-, 3 Gap-Long-
und 8 Gap-Short-Treffer, aber keinen Cup-/Sweep-Abschluss. Ein separater
API-Neustart am Vorabend war geordnet; sein Ausloeser ist nicht belegt.
Gesamte Prozess-CPU-Zeit ist nicht die Laufzeit eines einzelnen Scans.

Der genaue Produktions-Wartepunkt ist nicht durch einen Thread-Stack oder
ein Provider-Timing belegt. Offline-Profiling zeigte fuer eine synthetische
780-Tages-Historie etwa 0,035 s fuer die Metriken und 0,005 s fuer den
180-Tages-Cup-Detektor auf diesem PC. Das ist kein Hetzner-Benchmark und
keine Garantie ueber andere Kursverlaeufe.

## Aenderungen

- Die automatische Aktien-Strategierunde ist ein schwerer API-Schedulerjob.
  Sie laeuft dort nicht mehr parallel zu BI/Biotech. Das ist **keine**
  prozessuebergreifende Scheduler-Sperre: ein separat konfigurierter
  Backgrounddienst bleibt durch das bestehende gemeinsame Providerbudget
  begrenzt.
- Manuelle und automatische Aktien-Strategiejobs erhalten gegenseitigen
  Besitzschutz. Unterschiedliche Jobnamen erlauben keine ueberlappenden
  Schreiber derselben Strategie-Caches.
- Starter-Swing behaelt den 60-Minuten-Takt auch im Opening-Fenster;
  unveraenderte abgeschlossene Tagesdaten werden nicht alle zehn Minuten
  erneut voll analysiert. Der explizite Realtime-Modus behaelt seinen Takt.
- Ein threadlokaler Cache teilt identische datierte Bulk-Feeds und komplette
  Tageshistorien innerhalb **einer** Runde. Datum, Symbol und strikte
  Datenpruefung bleiben getrennt. Eine zu kurze Historie ersetzt keine
  laengere. Keine dauerhafte Wiederverwendung alter Scans, kein fremder
  Thread-Zugriff; Dekodierung liefert unabhaengige Datenbaeume.
- Der Cache haelt maximal 32 MiB komprimierte Nutzdaten (LRU). Das begrenzt
  nicht den gesamten Prozessspeicher oder temporaere Dekodierungsobjekte.
  Es werden keine Kerzen abgeschnitten und keine technischen Regeln geaendert.
- Kooperative Arbeitsbudgets: 20 Minuten je Teilstrategie und insgesamt
  30 Minuten Analysearbeit je automatischer Runde. Kontrollpunkte liegen
  zwischen Aktien, Phasen, Provider-Requests und Rate-Limit-Wartezeiten.
  Ein verspaetetes HTTP-Ergebnis wird nicht weiterverarbeitet. Bestehende
  Netzwerk-Timeouts werden nicht verlaengert.
- Ein Budgetabbruch ist `scan_timeout`, **kein** erfolgreicher Nulltreffer.
  Der letzte vollstaendige Cache bleibt bestehen und die neue Runde sichtbar
  unvollstaendig. Erfolgreiche andere Strategien erreichen weiterhin genau
  die bisherige gemeinsame Mailpruefung: max. 25 Beitraege je Strategie,
  globale Sortierung und bestehende 50-Zeilen-Pruefung. Kein zweiter Batch,
  kein unverifiziertes Teilergebnis, keine erneute Zustellung alter Caches.
- Die Arbeitsfrist wird nicht in SMTP-/Dedupe-Eigentumsuebergaenge hinein
  unterbrochen. Mailpruefung bleibt getrennt; `guarded` ist **kein** Beleg
  fuer SMTP-Annahme oder Inbox-Zustellung.
- Sofort geflushte Phasen-/Fortschrittslogs zeigen Provider-Aufrufe,
  Cachetreffer und Rate-Limit-Wartezeiten ohne Schluessel, Kurse oder
  Empfaenger. Scanstatus und Betreiberwarnungen enthalten Fortschritt;
  begrenzte Zaehler/Phasen werden auch im lesenden Export erhalten.

## Bewusst unveraendert / Grenzen

BI 17/20, Signalfilter, Einstiegs-/Stop-/Zielberechnung, R:R, Regime- und
Risikogates, Dedupe, Broker-/Orderverhalten bleiben unveraendert. Das
Warnlimit 35 Minuten und das Hartlimit 105 Minuten werden nicht erhoeht.

Kooperative Fristen koennen einen bereits blockierten OS-Aufruf oder
Python-Thread nicht zwangsweise beenden. Der exklusive Worker und der
Hartlimit-Waechter bleiben dafuer bestehen. SMTP liegt ausserhalb des
Analysebudgets. Ein weiterhin regelmaessig abgebrochener Cup-Scan waere
**nicht** als vollstaendig reparierter Scanner zu werten: Fortschrittsdaten
muessen dann die naechste gezielte Korrektur begruenden.

## Abnahme

Lokale Tests verwenden synthetische Daten, Fake-Uhren und isolierte
Transportfunktionen. Sie pruefen Budgetabbruch, Rate-Limit-Warten,
Cache-/Datums-/Striktheitsgrenzen, keine Doppelworker, Erhalt alter Caches
und den unveraenderten gemeinsamen Mail- und Rangfolgevertrag.

Finaler lokaler Gesamtlauf: **4806 passed, 4 skipped** (317,53 Sekunden),
darunter 19 neue Runtime-Regressionstests. Zusaetzlich wurden die bestehenden
Mail-/Dedupe-, Starter-Swing-, Watchdog- und Shared-Budget-Tests gezielt geprueft.
Ein bisher tagesabhaengiger Starter-Mailtest friert jetzt beide beteiligten
Uhren ein; seine fachlichen Erwartungen wurden nicht gelockert.

Nach Pull und Neustart auf Hetzner sind ein neuer **vollstaendiger**
Strategielauf, dessen Laufzeit und tatsaechliche Zustellnachweise noch zu
pruefen. Keine Behauptung einer besseren Trefferquote oder eines Gewinns.
