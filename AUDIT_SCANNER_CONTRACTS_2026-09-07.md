# Scanner-Nachaudit und bestaetigter Breakout - 07.09.2026

## Auftrag und Freigabegrenze

Basis: `df87bda267c558ce5e7d4366f942c69205b3969f`. Nach dem read-only
Scanner-Audit beauftragte der Nutzer mit "alles machen" die vorgeschlagenen
Korrekturen, den klaren Breakout-Vertrag und belastbare Modellvergleiche.
Keine Order, echte Mail, Serveraenderung, Cron-Aktivierung oder produktive
Datenreparatur. Private `output/`, Archive und Datenbanken bleiben lokal.

## Bestaetigte Befunde und Umsetzung

### 1. Momentum war nicht mehr der klassische Breakout

Die alten Namen Breakout Long, Early Momentum, Whale Watch und Volume Surge
zeigen auf Momentum Breakout Long. Historische Git-Aenderungen erweiterten
die Auswahl um reine Range-Staerke und EMA-Rueckeroberungen. Im Juli wurde
ausserdem die Fortsetzungsbewertung materiell umgewichtet. Das beweist eine
Strategieaenderung, nicht eine bessere/schlechtere echte Trefferquote.

Neuer expliziter Vertrag: `modules/stock_momentum_contract.py`, Version 1.
Nur vorheriges 10D-/20D-Hoch, aktueller Preis und abgeschlossener 5m-Schluss
mindestens 0,1 Prozent darueber. Die 0,1 Prozent stammen aus der bisherigen
5m-Bestaetigung, keine neue nach Performance optimierte Schwelle. Bestehendes
Tagesplus >=2 Prozent, RVOL >=1,5 und weitere Qualitaets-/Liquiditaetsgates
bleiben bestehen. Keine Range-/Reclaim-Ersatzsignale oder neue Watchlist.

Die Intraday-Bestaetigung wird vor der Veroeffentlichung benoetigt, nicht
nur vor Mailversand. Datenfehler, Verlust des Levels, veraltete Bestaetigung
und waehrend eines langen Scans abgelaufener Nachweis haben eigene
Ablehnungsgruende. Der koharente Scan-Cutoff wird nicht durch spaetere
Kerzen rueckwirkend verbessert. Abgelaufene Signale werden nicht gerettet.
Cacheversion 8 verwirft alte Aktien-Strategie-Caches; Momentum-Zeilen tragen
Vertrag 1. Die App prueft diesen Nachweis nach der normalen Dekoration
erneut. Persoenliche Positionen und ihre Ausstiegsverwaltung bleiben getrennt.

Der MDR-Bonus konnte den vorgesehenen 79-Punkte-Deckel zuvor aufheben.
Die zentrale abschliessende Deckelung verhindert das. Score ist keine
Gewinnwahrscheinlichkeit. Die separat gefundene Mail-Frischepruefung
verlangt jetzt in allen positiven Zweigen auch den letzten bestaetigenden
Schlusskurs; ein inzwischen unterschrittenes Level ist kein FRESH_CROSS.

### 2. Kerzenintegritaet bei Krypto

Der alte gemeinsame Filter entfernte nur die letzte offene/zukuenftige
Kerze. Mehrere solche Zeilen konnten die Crypto-Explosion-Auswahl
beeinflussen. Der Explosion-Adapter akzeptierte zusaetzlich OHLC-Widersprueche.

Jetzt werden alle Zeitpunkte gegen den Cutoff geprueft. Explizite
Unvollstaendigkeit, ungueltige/nicht-endliche Werte und widerspruechliche
Doppelbeobachtungen werden nicht als gueltige Kerzen gewertet. Identische
Beobachtungen werden deterministisch dedupliziert. Ein Abschlussflag kann
keine zukuenftige Kerze vorzeitig freigeben. Karge Zeit-/Schlusskurszeilen
bleiben nur fuer den zeitlichen Adapter erhalten; der Explosion-Pfad
erfindet daraus keine OHLC-Werte. Mehrere alte synthetische Testreihen
wurden mit gueltigen Zeitstempeln und physikalisch moeglichen OHLC versehen;
ihre Gate-Erwartungen wurden nicht gelockert.

### 3. Backtest-Logik, Evidenz und Vergleich

Die kanonische Tagesauswahl verwendet denselben reinen Momentum-Vertrag
wie der Scanner. Das bedeutet **Auswahlregel-Teilparitaet**, kein identischer
Live-Replay: Tagesdaten koennen historische 5m-/4H-Ausfuehrung, damalige
Quotes, Kontext und reale Fills nicht nachweisen. Der explizite Tages-Proxy
behaelt seine ausgewiesenen naechster-Open-/Prozent-Stop-/50:50-R-Exits.
Ein willkuerlich erfundener historischer Strukturplan waere keine Reparatur.

Zusaetzlich gefunden und korrigiert: Der Single-Ticker-API-Pfad uebergab
Strategienamen statt Regeldictionaries an den Rechenkern. Ein Test nutzt
den echten Rechenkern. Offene historische Exit-Kerzen erzeugen keinen
abgeschlossenen EOD-Exit. Eine fehlende Eroeffnung darf nicht still durch
einen Schlusskurs ersetzt werden; die rohe OHLCV-Validierung verhindert
dies vor der Normalisierung. Unbekannte Ergebnisse sind nicht 0R.

API, Cache-Antworten und UI weisen Modellversion, Grenzen und Kosten aus.
Alte Cache-Ergebnisse werden nicht dem neuen Modell zugeschrieben.
Daily-Proxys liefern keine automatische Paper-/Live-Freigabe. Bestehende
scanner-spezifische Einschraenkungen duerfen dabei nicht verloren gehen.
Die UI zeigt fehlende Kennzahlen als unbekannt statt als 0 Prozent und
kann eine widerspruechliche positive Proxy-Freigabe nicht anzeigen.

`scripts/compare_scanner_cohorts.py` vergleicht explizit exportierte alte/
neue Ergebnisse auf derselben gehashten Eingangsstichprobe und demselben
Zeitfenster. Richtung, Markt, Horizont/Timeframe, Regime, Versionen und
Kosten gehoeren zum Vergleich. Nicht ausgewaehlte, ungefuellte, offene
und fehlende Verlaeufe sind getrennt. Das Werkzeug erzeugt keine fehlenden
historischen Fills und fuehrt keine echten Trades aus. Eingabeschema und
Replay-Grenzen stehen in der zugehoerigen Werkzeugdokumentation.

## Einordnung der anderen Scanner

| Familie | Abnahmebereich | Nicht daraus ableitbar |
|---|---|---|
| BI Long/Short | Bestehender 17/20-Vertrag und Downstream-Regressionen bleiben bestehen | Neuer empirischer Handelsvorteil |
| ORB | Vollstaendige Opening-Range-Intervalle, Richtung und Plan-/Zielpruefungen | Reale Fills/Profitabilitaet |
| Turtle | Vorheriger Donchian20-Vertrag bleibt; Beschreibung der eigenen ATR-/Fest-R-Variante korrigiert | Original-Turtle-Ausfuehrung/Erfolg |
| Biotech/Penny | Daten-/Struktur-/Event-/Lifecycle-Regressionen | Vollstaendige Events, Halts, echte Orderbuchtiefe |
| Early Movers/Explosion/New Listing | Gemeinsame Kerzenintegritaet plus bestehende Venue-/Funding-/Trigger-/Planpruefungen | Neue Netto-Forward-Performance |
| Flaggen, C&H, Compression, Reversal, MA, Wyckoff | Bestehende Strategie- und gemeinsame Strukturregressionen | Vollstaendig neue empirische Validierung jeder Variante |
| Crash/Bear/BTC/Narrative | Bestehende Missing-Data-/Kontextvertraege | Eigenstaendiger profitable Entry-Vertrag |

Kein anderer Scanner bekommt blind die Momentum- oder BI-Schwellen.
Diese Freigabe ist ein begrenzter technischer Nachaudit, kein Beweis, dass
jeder Pfad auf jedem Markt korrekt oder profitabel ist.

## Abnahme

Finale Gesamtpruefung: **3495 bestanden, vier Plattform-Skips, null Fehler**,
293,56 Sekunden. JUnit lokal: `tmp/scanner_release_final_20260907.xml`.
SHA-256-Vergleich aller beruecksichtigten Python-/JS-/HTML-Code- und
Testdateien vor/nach dem Lauf war identisch. Die vier bestehenden
plattformabhaengigen Skips sind kein Linux-Produktionsnachweis. Gezielte
Testgruppen ueberlappen und werden nicht addiert.

- Python-Compile, JavaScript-Syntax, Frontend-Bundle-Verifikation
  (`3738379e722f`), `git diff --check` und gezielter Credential-Musterscan
  des vorgemerkten Diffs bestanden. Keine Abhaengigkeits- oder DB-Migration.
- Der erste diagnostische Gesamtlauf fand drei veraltete Vertrags-Assertions
  (alte Inline-Backtestregel, UI-Ueberschrift, verschobene Reason-Quelle).
  Nach Anpassung sowie der expliziten NO_TRADE-Registrierung ungueltiger
  Momentum-Eingaben bestand die gesamte finale Suite ohne Ausnahmen.

- Browserabnahme mit Playwright auf real gebautem Frontend und rein lokaler
  synthetischer API: 1440x1100 und 390x844. Dokumentbreite jeweils exakt
  Viewportbreite, kein horizontaler Seitenueberlauf. Kein JS-Runtimefehler
  im Fixture-Fehlerjournal; bekannte Tailwind-Production-Warnung bleibt.
- Absichtlich widerspruechliche Antwort (Proxy + positive Freigabe) zeigt
  trotzdem "MODELLTEST - KEINE LIVE-FREIGABE" und keine Paper-Auto-Freigabe.
  Fehlende Best-/Worst-/Durchschnittsmetriken erscheinen als unbekannt.
  Screenshots: `output/playwright/scanner-contracts-desktop.png` und
  `output/playwright/scanner-contracts-mobile.png`, lokal und nicht im Commit.
- Unabhaengige Gegenpruefungen fuer Momentum/Kerzen und Backtest/Ergebnis-
  arithmetik schlossen die reproduzierten Findings. Insbesondere wurde eine
  zwischenzeitlich falsche Guard-Platzierung vor der finalen Abnahme
  korrigiert; spaetere APIs nutzen keine ungebundenen lokalen Variablen.
- Der Reason-Registry-Test erfasst den extrahierten Momentum-Regelkern
  weiter. Ungueltige Eingaben sind NO_TRADE, kein WATCH-Ersatz. Alte UI-
  und Inline-Regel-Assertions wurden dem beauftragten Vertrag angepasst,
  nicht durch Ausnahmen deaktiviert.

## Offene Daten- und Rollout-Grenzen

- Noch keine neue produktive Trefferquote oder positive Netto-Erwartung
  nachgewiesen. Die lokale Datenbank enthaelt 22 Zeilen; dies ist kein
  aktueller Produktions-/Vorher-Nachher-Nachweis. Keine Serverdaten abgerufen.
- Vollstaendiger Live-Replay bleibt bis zum Vorliegen der ausgewiesenen
  historischen Intraday-/Quote-/Kontext-/Fill-Evidenz offen. Ein Daily-Proxy
  und ein Exportvergleichswerkzeug schliessen diese Luecke nicht.
- Daten-/Kalender-/Weekly-/4H-Grenzen aus dem Fibonacci-Audit bleiben
  bestehen; hier keine neue Multi-Timeframe-Strategie oder Gewinnoptimierung.
- Zweistellige Aktien-Anzeigerundung kann einen Rohpreis unmittelbar an
  der Bestaetigungsgrenze konservativ ausschliessen. Kein Schwellen-Lockern.
- Weniger Signale nach dem strengeren Breakout-Vertrag koennen korrekt
  sein. Nicht fuer mehr Ergebnisse wieder die Regeln abschwaechen.
- Letzter vom Nutzer belegter Serverstand war `4dc49eb`. Push, Server-Pull,
  neue API-Revision, Bundle und Health sind gesondert nachzuweisen.
- Keine DB-Schemaaenderung in diesem Paket. Vor einem Server-Rollout bleibt
  die bestehende Daten-/Backup- und saubere-Checkout-Pruefung erforderlich.
