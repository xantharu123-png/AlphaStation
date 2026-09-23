# Bestaetigter Ausbruch, optionaler Ruecktest

## Verbindliche Scanner-Regel

Ein durch abgeschlossene Kerzen bestaetigter Ausbruch/Breakdown darf nicht
allein wegen eines fehlenden spaeteren Ruecktests blockiert werden. Ohne
bestaetigten Ruecktest lautet die Warnung:

> Ausbruch bestaetigt; Ruecktest noch nicht bestaetigt.

Die Warnung ist keine Freigabe. Datenqualitaet, Muster, Richtung, BI 17/20,
Liquiditaet, Entry-Abstand, Stop, Ziele, R:R, Session- und Versandpruefungen
bleiben eigenstaendige Bedingungen. Eine noch ungebrochene Barriere ist kein
Ausbruch. Ein Docht oder ein neuer Livepreis ersetzt keinen Schlusskurs.

## Ursache und gemeinsame Korrektur

Die kausale Strukturplanung verlangte fuer bereits gekreuzte historische
Zonen bisher einen spaeteren Hold und Ruecktest. Das blockierte auch echte
frische Schlusskurs-Ausbrueche. Ausserdem konnte ein kuerzeres 4H-Fenster
einen bereits auf 1D belegten Ruecktest verdraengen.

Die Zonen unterscheiden jetzt:

- `BREAK_CONFIRMED` / `break_confirmed_optional_retest_v1`: echter
  abgeschlossener Ausbruch; kein Ruecktest behauptet.
- `RECLAIMED`: tatsaechlich bestaetigter Hold/Ruecktest; bisheriger strenger
  Nachweis bleibt bestehen.
- Ohne belegten Ausbruch: bisherige Strukturblockade bleibt bestehen.

Jeder Nachweis ist an Zone, Richtung, Geometrie, Bestaetigungszeit, verwendete
abgeschlossene Kerzen und Beobachtungszeit gebunden. Spaetere ungueltige
Schlusskurse oder widerspruechliche Daten entwerten alte Nachweise. Ein neuer
Ausbruch nach einer solchen Entwertung benoetigt einen eigenen Nachweis.
Zeitrahmen werden nicht zu einer erfundenen Hold-/Ruecktestfolge vermischt.

Eine bereits erteilte Freigabe wird bei erneuter Verwendung nochmals
geprueft. `action=None` nach einem Ausbruch ist keine dauerhafte Freikarte.
Unabhaengige Strukturablehnungen werden beim Entfernen der alten Barriere-
Blockade nicht ueberschrieben.

## Scanner-Abdeckung

| Pfad | Verhalten |
| --- | --- |
| Gemeinsamer Strukturplan, Chart, Mail | Bestaetigter Ausbruch ohne Ruecktest zugelassen; neue Warnmetadaten. Gilt fuer alle Verbraucher dieses Plans. |
| Momentum Long, Gap Long/Short, weitere Aktienstrategien | Kein Ruecktest-only-Veto im gemeinsamen Strukturplan; Momentum uebernimmt seinen eigenen abgeschlossenen 1D-/5m-Nachweis. |
| BI Long/Short | 17/20 unveraendert. Vor einem tatsaechlichen Ausbruch wird ein geplanter Stop-Entry nicht als bereits bestaetigter Ausbruch bezeichnet. |
| Wyckoff inkl. Reakkumulation/Redistribution | Neuer `confirmed_breakout` mit Origin/Reaction/Test/SOS bzw. SOW. Kein erfundenes LPS/LPSY. `confirmed_retest` behaelt fuenf Ereignisrollen. |
| Cup-and-Handle | Bestaetigter Tagesschluss-Ausbruch bleibt notwendig. Henkel ist kein Ruecktest nach dem Ausbruch. Spaeterer bestaetigter 5m-Ruecktest entfernt die Warnung. |
| Turtle, beide Aufrufpfade | Abgeschlossener Donchian-Ausbruch erhaelt Warnung; Launch-Kerzen-Docht wird nicht als spaeterer Ruecktest ausgegeben. |
| ORB | Geschlossener, volumenbestaetigter Ausbruch bleibt erforderlich. Nur eine spaetere abgeschlossene Kerze kann einen Ruecktest belegen. |
| Crypto Early Movers | Bestaetigter Execution-Ausbruch ist Alternative zum Ruecktest. Der bisherige Schutz gegen zu grosse Entfernung vom geplanten Einstieg bleibt erhalten. |
| Crypto Explosion | Freigegebener, abgeschlossener 5m-Ausbruch erhaelt die Warnung. Bei abgelaufenem Trigger wird sie zusammen mit der alten Ausbruchsfreigabe entfernt. |
| Pennystocks | Bereits vorhandenes Breakout-ODER-Ruecktest-Prinzip bleibt; frischer bestaetigter Breakout bekommt die Warnung. |
| New Listings | Warnung nur fuer wirklich bestaetigten frischen 5m-Supportbruch und gueltigen Trade; Lower-High allein ist kein Breakdown. |
| Sonstige Krypto-/Spezialscanner | Gemeinsame Barriere- und Mailpruefungen verwenden denselben Nachweis. Nicht-Ausbruchsstrategien bekommen keine erfundene Ausbruchsmarkierung. |

`WAIT_FOR_RETEST` ist an einigen alten Stellen zugleich eine Bezeichnung
fuer zu weit gelaufene Einstiege oder unzureichende Trade-Health. Diese
eigenstaendigen Risiken werden nicht durch pauschales Loeschen des Wortes
"Retest" abgeschaltet. Explizite Pullback-Strategien behalten ihre eigene
Musterdefinition; sie blockieren dadurch keine anderen Ausbruchsstrategien.

## Darstellung, Persistenz und Migration

- Eine gemeinsame Warnkomponente versorgt Aktienlisten, Krypto-Listen,
  mobile Karten, Detailfenster und Chartanalyse.
- Darstellung setzt vollstaendige, producer-validierte Metadaten voraus:
  `breakout_confirmation`, `retest_status`, `warning_codes`, `retest_warning`.
- Mailvorlagen und ein tickergebundener Fallback zeigen dieselbe Warnung.
  Tracking speichert nur die freigegebene feste Warnvokabel und den Trigger-Modus.
- Veraltete Warnungen werden bei einem neuen Ruecktest bzw. fehlendem aktuellen
  Nachweis entfernt, ohne andere Warnungen zu verlieren.
- Aktienstrategie-Cacheversion: 11. Alte Ergebnisse werden nicht nachtraeglich
  durch ein neues Label zu gueltigen Ausbruechen gemacht; neuer Scan erforderlich.
- Wyckoff-Replayparameter: `wyckoff_v3_rules_2`, Entry-Policy
  `confirmed_breakout_optional_retest`. Alte eingefrorene Replay-Protokolle
  duerfen nicht stillschweigend mit der neuen Entry-Regel ausgewertet werden.

## Pruefungen und Betrieb

Neue Regressionen decken Long/Short, offene/future Kerzen, reine Dochte,
spaetere Entwertung, Zeitrahmenkonflikte, Geometrie-Bindung, erneute Nutzung
einer alten Freigabe, unveraenderte Risikoablehnungen, Warn-Lebenszyklus,
Scanner-zu-Mail-Weitergabe und Wyckoff-Ereignisrollen ab.

Abschliessender Gesamtlauf auf Windows: **6.770 bestanden, 4 uebersprungen**,
0 Fehler (534,92 Sekunden). Die vier Ausnahmen betreffen Linux-Dateisystem-
Vertraege bzw. auf diesem Windows-Host nicht erlaubte Verzeichnis-Symlinks.
Zusaetzliche unabhaengige Codepruefungen deckten die gemeinsame Freigabe,
Warn-Lebenszyklen, Maildarstellung und den erhaltenen Entry-Abstandsschutz ab.
Frontend-Bundle gegen Quellcode verifiziert: 9c9fd749c8c6.

Browserpruefung mit lokaler synthetischer Fixture auf Desktop und 390x844:
Warnung in Ergebniszeile/Karte und Detail sichtbar; fehlende Chartdaten
verursachen keinen Render-Absturz. Private Screenshots verbleiben in `output/`.

Keine Kursanbieter-Aufrufe, echten E-Mails oder Trades waehrend der Tests.
Private Serverexporte werden nicht committet. Ein lokaler Test/Push aktualisiert
Hetzner nicht. Nach Pull und Neustart muss ein neuer vollstaendiger Scan den
aktuellen Produktionsdurchlauf belegen.
