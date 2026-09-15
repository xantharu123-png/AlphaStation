# Aktien-Swing mit Polygon Starter

Freigabe des Nutzers vom 15.09.2026: Starter behalten; fuer mehrtaegige
Aktien-Swings keine generelle Echtzeit-/90-Sekunden-Pflicht. Das ist eine
eigene Signalsemantik, keine Lockerung der Intraday- oder BI-Auswahl.

## Was der Modus liefert

- Standard `STOCK_SWING_DATA_MODE=starter_swing` (auch ohne ENV-Eintrag).
  `realtime` behaelt den bisherigen Snapshot-/5m-/BidAsk-Pfad.
- Aktien-Strategien verwenden die letzten zwei vollstaendig abgeschlossenen
  US-Handelstage aus dem datierten, splitbereinigten Daily-Market-Summary.
  Aktueller Tag erst ab Boersenschluss **plus 15 Minuten**. Wochenenden,
  Feiertage, Sommerzeit und bekannte vorzeitige Boersenschluesse kommen aus
  dem gemeinsamen Exchange-Kalender.
- Keine automatische Ersatz-Timestamp-Erfindung aus `updated` oder `lastTrade`.
  Bulk-Daten benoetigen weder Last-Trade- noch Echtzeit-BidAsk-Berechtigung.
- Der preisliche Bezug ist der letzte abgeschlossene Tages-Schlusskurs.
  Das ist bewusst **nicht** ein Intraday-Kurs, der immer nur 15 Minuten alt ist.
  App und Mail nennen Modus, Referenzdatum und fehlende Live-Ausfuehrung.
- Historie, RVOL, Struktur und Momentum-Bestaetigung beziehen sich auf denselben
  abgeschlossenen Tag. Momentum braucht weiterhin einen wirklichen 10D-/20D-
  Ausbruch, Volumen, Preis-, Liquiditaets- und Strukturpruefungen; im Swing-Modus
  ersetzt eine bestaetigte Tageskerze die frische 5m-Bestaetigung.
- Cup-and-Handle nutzt seinen gemessenen Daily-Ausbruch als Swing-Plan.
- BI nutzt ebenfalls abgeschlossene Tagesdaten und kennzeichnet den Plan;
  **17/20 und harte Gegenindikationen bleiben unveraendert.** Unter 17/20 wird
  keine Ersatz-Watchlist und kein handelbares Signal eingefuehrt.

## Versand und Bewertung

- Ein gueltiger datierter Swing-Plan darf auch ausserhalb der laufenden
  US-Sitzung versendet werden. Das ist keine Market-Order-Empfehlung.
- Vor Versand werden Quelle, neuester abgeschlossener Handelstag, Preisbezug
  und Plangeometrie geprueft. Keine allgemeine Abschaltung von SMTP-, Dedupe-,
  Risiko-, Regime-, Struktur- oder Empfaengerpruefungen.
- Im laufenden Handel prueft der Mailpfad die abgeschlossenen Starter-
  Minutenkerzen bis zur 15-Minuten-Datenmarke. Bereits beobachtete Stop-/TP1-
  Beruehrung, fehlende Minuten, zu spaeter Einstieg oder unzureichendes R:R
  blockieren. Die letzten 15 Minuten bleiben ausdruecklich unbekannt.
- Scanner: `price_mode=swing_reference_close`; Versand:
  `price_mode=swing_delayed_close`; beide `fill_evidence_verified=false`.
  Der historische Schlusskurs beweist **keinen ausgefuehrten Einstieg**.
  Bestehende Zustellungsintents/SMTP-Annahme bleiben der Aktivierungszeitpunkt;
  die Modellbewertung muss erst spaetere Preisintervalle verwenden.
- ORB, Penny-Intraday und Krypto werden nicht durch diesen Vertrag freigegeben.
- Keine Gewinnzusage und keine erzwungene Anzahl Signale. Eine 15-Minuten-
  Verzoegerung kann insbesondere bei Gaps oder schnellen Bewegungen relevant
  bleiben; der aktuelle Kurs muss vor einer echten Order geprueft werden.

## Betrieb

Lokale Tests, Git-Push, Server-Pull und tatsaechlich zugestellte Produktionsmail
sind getrennte Nachweise. Nach Deployment ist ein vollstaendiger neuer Scan
notwendig; alte Caches belegen den neuen Datenvertrag nicht (Strategiecache v9).
Private Exporte und Provider-Schluessel gehoeren nicht in Git.

Primaerquelle: https://massive.com/docs/rest/stocks/aggregates/daily-market-summary
(Starter: 15 Minuten Verzoegerung; datierter OHLC-Marktendpunkt).

## Nachweise vom 15.09.2026

- Gesamte lokale Root-Testsuite: **4787 passed, 4 skipped**; davon 43 neue
  Starter-Swing-Vertragstests. Kein echter SMTP-Versand und kein Handel.
- Lesender Bulk-Abruf mit dem vorhandenen lokalen Schluessel: 12566 datierte
  Tagesdatensaetze, alle im neuen Vertrag; keine Last-Trade-Felder notwendig.
- Lesender AAPL-Minutenpfad: vollstaendig bis zur Starter-Datenmarke,
  letzter beobachteter Schlusskurs 947 Sekunden alt. Das ist kein Handelsvorschlag.
- Desktop 1280x720 und Mobil 390x844 mit synthetischen API-Daten im echten
  Browser geprueft. Mobil keine horizontale Ueberbreite; neue Hinweise und
  Umlaute korrekt. Frontend-Bundle `0f97ef3e11dc`.
- Geltungsbereich dieses Changes: Aktien-Strategierunde (einschliesslich
  Momentum, Gap, Cup und menuebasierten Strategien) sowie BI-Hintergrundscan.
  Eigenstaendige Bear-/Biotech-/Turtle-Scanner behalten ihren separaten
  Datenpfad; dies ist keine pauschale Migration jedes Aktien-Endpunkts.
- Server-Deployment und ein neuer vollstaendiger Produktionsscan stehen nach
  diesem lokalen Nachweis noch aus. Keine neue Trefferquote nachgewiesen.
