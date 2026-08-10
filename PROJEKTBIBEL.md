# Alpha Station - Projektbibel

**Gueltig ab:** 11. August 2026

**Code-Baseline vor dem Dokumenten-Commit:** `1bd9a48` (`main`)

**Zweck:** Verbindliche fachliche, mathematische, technische und betriebliche Regeln

Diese Projektbibel ist die normative Referenz fuer Alpha Station. Sie beschreibt
nicht, was irgendwann einmal beabsichtigt war, sondern welche Regeln bei jeder
Aenderung erhalten bleiben muessen. Historie und aktueller Arbeitsstand stehen im
`PROJEKTHANDBUCH.md`; die Einrichtung eines neuen Rechners steht in
`HANDOFF_PC_WECHSEL_2026-08-11.md`.

Wenn Dokumente einander widersprechen, gilt folgende Reihenfolge:

1. reproduzierbarer Test- oder Produktionsnachweis,
2. diese Projektbibel,
3. aktueller getesteter Code,
4. aktuelles Projekthandbuch,
5. alte Audit-, Chat- und Handoff-Dokumente.

Ein Widerspruch zwischen Bibel und Code ist ein Fehler. Er darf nicht durch eine
stille Umdeutung der Bibel geloest werden, sondern muss fachlich entschieden,
getestet und dokumentiert werden.

---

## 1. Produktauftrag

Alpha Station ist eine Trading-Intelligence-Plattform fuer Aktien und Krypto. Sie
soll ein grosses Marktuniversum pruefen, wenige belastbare Handelsideen erzeugen,
deren Ausfuehrbarkeit getrennt bewerten und den weiteren Verlauf ehrlich messen.

Die App ist **kein Gewinnversprechen**, kein autonomer Vermoegensverwalter und kein
Ersatz fuer Broker-, Markt- oder Rechtspruefung. Ein Score von 90 bedeutet nicht
90 Prozent Trefferwahrscheinlichkeit. Kein Scanner darf mit Formulierungen wie
"garantiert", "sicher" oder "fast immer richtig" beworben werden.

Verbindliche Produktprinzipien:

- aktive Handelsideen statt endloser Watchlisten,
- Setup-Qualitaet, Timing und Risiko sind getrennte Dimensionen,
- Stop und Ziele entstehen aus Marktstruktur, nicht aus einem gewuenschten R:R,
- fehlende oder veraltete Daten fuehren zu Warnung, Warten oder Blockade,
- die App darf Widersprueche nicht durch mehrere parallele Wahrheitssysteme erzeugen,
- Forward-Ergebnisse haben Vorrang vor optisch guten Einzelbeispielen,
- Aktien-, Penny-, ORB- und Krypto-Logik werden nicht blind gleichgesetzt.

---

## 2. Eine Wahrheit pro Signal

### 2.1 Kanonische Zustaende

Jedes Signal hat genau einen kanonischen Ausfuehrungszustand:

| Zustand | Bedeutung |
|---|---|
| `JETZT_TRADEN` | Alle fuer diesen Scanner und Horizont erforderlichen Entry-Gates sind aktuell bestaetigt. |
| `TRIGGER_ABWARTEN` | Setup ist interessant, aber der definierte Trigger fehlt. Kein Market-Buy. |
| `RETEST_ABWARTEN` | Bewegung/Breakout ist erfolgt; Einstieg erst nach Halt/Reclaim der Retest-Zone. |
| `KEIN_EINSTIEG` | Kein aktueller Entry. Das ist weder ein Kaufsignal noch ein Reminder-faehiger Triggerzustand. |
| `AKTIV_HALTEN` | Bereits markierte/gefuellte Position bleibt nach aktuellem Managementplan aktiv. |
| `JETZT_VERKAUFEN` | Exit- oder Invalidation-Bedingung einer markierten/gefuellten Position ist bestaetigt. |

"Watch", "armed", "candidate", "tradeable", "ready", "no trade" und aehnliche
Legacy-Begriffe duerfen intern als Diagnose vorkommen, aber nicht als konkurrierende
Kundenwahrheit. Tabelle, Sidebar, Mail, Reminder und Broker-Intent muessen denselben
kanonischen Zustand zeigen.

### 2.2 Setup, Timing und Risiko

- **Setup-Score/Grade:** strukturelle Qualitaet der Idee.
- **Entry-/Timing-Score:** Qualitaet des aktuellen Einstiegszeitpunkts.
- **Execution Risk:** Slippage, Spread, Liquiditaet, Chase, Fakeout, Datenfrische,
  Event-/Regime-Kontext und Bar-Bestaetigung.
- **Trade Health:** zusammenfassender Status aus Plan, Timing und Risiko; er darf
  ein hohes Setup-Grade blockieren.

Ein S-Setup darf `TRIGGER_ABWARTEN` sein. Ein niedriger Setup-Score darf niemals
durch einen hohen Entry-Score zu `JETZT_TRADEN` werden. Kein UI-Feld darf "Risk LOW"
anzeigen, wenn ein verbindlicher Execution-Blocker gleichzeitig kritisch ist.

### 2.3 Signalidentitaet

Ein Signal wird mindestens durch folgende Felder identifiziert:

- Markt und Asset-Typ,
- Ticker/Contract und bei Aktien der verifizierte Unternehmensname,
- Scanner/Strategie und Richtung,
- Horizont/Timeframe,
- Entry, Stop, TP1, TP2,
- Signalzeit und Datenzeit,
- stabile Setup-/Signalreferenz.

Ticker-Aehnlichkeit reicht nie zur Identitaet. ETFs, ETPs, Warrants, Rechte,
Preferreds, Fonds oder synthetische Produkte duerfen nicht als Common Stock
durchrutschen. Krypto-Contract, Venue und Quote-Asset muessen zum gehandelten Markt
passen.

---

## 3. Mathematischer Vertrag

### 3.1 Geometrie

Long:

```text
risk = entry - stop > 0
R1 = (tp1 - entry) / risk
R2 = (tp2 - entry) / risk
```

Short:

```text
risk = stop - entry > 0
R1 = (entry - tp1) / risk
R2 = (entry - tp2) / risk
```

Fuer ein 50/50-Management vor Kosten:

```text
effective_R = 0.5 * R1 + 0.5 * R2
```

Verbindliche Regeln:

- `risk`, R1 und R2 muessen positiv und endlich sein.
- Long: `stop < entry < tp1 < tp2`.
- Short: `tp2 < tp1 < entry < stop`.
- `abs(entry - target)` darf kein ungueltiges Ziel in positiven Reward verwandeln.
- TP1 und TP2 duerfen nicht identisch oder nach Tickgroesse wirtschaftlich
  bedeutungslos nahe beieinander liegen.
- R:R ist ein **Filter** fuer einen Strukturplan, niemals der Generator beliebiger
  Fantasieziele.
- Live-R:R wird mit dem aktuellen ausfuehrbaren Preis neu berechnet, nicht mit
  einem historischen Wunsch-Entry.

### 3.2 Kosten und Ausfuehrung

Backtest, Tracker und Brokerplan muessen dieselbe Einheit verwenden. Spread,
Slippage und Gebuehren werden als reale Preis-/Prozentkosten behandelt, nicht als
falsch skalierte Dezimalwerte. Die dokumentierte Roundtrip-Kostenannahme muss im
Ergebnis ausgewiesen sein.

Bei einem Gap durch den Stop wird nicht der alte Stop als garantiertes Fill
angenommen. Es gilt der erste realistisch ausfuehrbare Preis inklusive Kosten.
Bei duennen Penny-/Kryptomaerkten muessen Spread, Orderbuch-/Venue-Liquiditaet und
Notional separat geprueft werden.

### 3.3 Tageskerzen und Kausalitaet

Wenn Stop und Ziel innerhalb derselben OHLC-Bar beruehrt wurden, ist die Reihenfolge
ohne Intraday-Daten unbekannt. Zulaessig sind:

- eine konservative Annahme,
- oder ein Ergebnisintervall aus konservativem und guenstigem Pfad.

Nicht zulaessig ist eine erfundene Intrabar-Reihenfolge. Signale duerfen nur mit
Daten entstehen, die zum Signalzeitpunkt verfuegbar waren; keine spaetere Tages-
oder Wochenkerze darf in Entry, Stop, Ziel oder Score einfliessen.

### 3.4 Statistik

- Hit-Rate nur aus gefuellten und entschiedenen Signalen.
- Offene, ungefuellte, No-Fill- und unaufgeloeste Signale werden separat gezaehlt.
- Stichprobengroesse und Wilson-95-Prozent-Konfidenzintervall gehoeren zur Hit-Rate.
- `sample_reliable` beginnt erst ab mindestens 30 entschiedenen Signalen pro
  Scanner/Regime-Zelle.
- Level-R und tatsaechlich befolgbares Managed-R (z. B. 50/50 plus Einstand) werden
  getrennt ausgewiesen.
- Ein TP1-Hit ist nicht automatisch ein voller Gewinner.
- Shadow-/blockierte Signale werden getrennt verfolgt, damit ein Gate nicht allein
  deshalb "gut" aussieht, weil es fast alles unterdrueckt.

---

## 4. Daten- und Zeitvertrag

Jedes Ergebnis braucht:

- Datenquelle/Venue,
- Datenzeit und Scanzeit,
- Cachealter und Vollstaendigkeit,
- verwendeten Horizont/Timeframe,
- Asset-Pruefstatus,
- Warnungen bei Partial Data, Rate Limit oder Fallback.

Fehlende Kerzen, unbekannte Venue-Liquiditaet, stale Markt-/News-Kontexte oder
API-Fehler duerfen nicht als LOW Risk interpretiert werden. Fail-closed bedeutet:
kein automatisches Trade-Signal, wenn ein fuer diesen Signaltyp verbindlicher
Nachweis fehlt.

Zeitframes duerfen nicht falsch beschriftet werden. Ein Daily-Fallback darf nicht
hinter einem aktiven 5m-/15m-/4H-Button erscheinen. Chartpreis, Headerpreis,
Tradeplan und Overlays muessen denselben Datenstand oder eine sichtbare Abweichung
mit Zeitstempel verwenden.

---

## 5. Markt- und Scannerlogik

### 5.1 Aktien-Swing

- Primaerer Kontext: Daily plus 4H-Struktur.
- Ein 5m-Trigger ist fuer einen mehrtaegigen Swing optional und darf das Setup nicht
  nachtraeglich in einen Intraday-Trade umdeuten.
- Momentum Breakout braucht einen aktuellen, frischen Strukturbruch oder einen
  bestaetigten Retest; ein Tage alter Spike ist kein neuer Breakout.
- Mehrere bereits gelaufene 4H-Impulse, vertikale Extension, rote Rejection,
  Verlust des Ausbruchslevels oder nahe Multi-Timeframe-Barriere blockieren den
  Soforteinstieg.
- Post-Pump-Shorts brauchen eine bestaetigte 4H-Rejection bzw. einen Strukturbruch;
  ein erster roter Balken allein reicht nicht.

### 5.2 ORB

- Opening Range: drei vollstaendige 5m-Kerzen 09:30-09:45 ET.
- Prime-Fenster: 09:45-11:00 ET. Spaetere Beobachtung muss als Late Review getrennt
  behandelt und strenger begrenzt werden.
- Aktiver Breakout liegt ausserhalb der Range; innerhalb der Range ist Retest,
  Failed Breakout oder Kandidat, nicht automatisch LONG/SHORT.
- Volumenbestaetigung muss zum aktuellen Breakout-/Retest-Zustand gehoeren, nicht zu
  irgendeinem alten Post-OR-Balken.
- Entry/Stop/TP muessen zur OR-Geometrie, ATR, Struktur und Sessionzeit passen.
  Cent-Ziele ohne wirtschaftliche Relevanz sind kein Top-Setup.

### 5.3 BI und Biotech

- BI ist Multi-Faktor-Analyse; Einzelindikatoren duerfen keine automatische
  Freigabe vortaeuschen.
- Ergebnisliste darf breit genug zur manuellen Pruefung sein; Mail-Gates bleiben
  strenger als die sichtbare Kandidatenliste.
- Biotech trennt technischen Zustand, Catalyst-Qualitaet und binaere Risiken wie
  Dilution, CRL, Trial-Fail, Sell-the-News und Halt-Risiko.
- Externe Catalyst-Daten sind Zusatznachweis, keine unkritische Wahrheit. HTTP 401,
  abgelaufener Key oder unbestaetigtes Datum muessen sichtbar und defensiv sein.

### 5.4 Penny Stocks

- Eigenes Universum und eigene Logik; nicht einfach normale Aktienfilter kopieren.
- Entscheidend sind handelbares Dollarvolumen, Spread, Float/Share-Struktur,
  Catalyst, Halt-/Dilution-Risiko, frischer Volumenaufbau, Intraday-Struktur und
  Exit-Liquiditaet.
- Ein bereits vollendeter Pump mit Rejection ist kein Long-Aufbau.
- Der Scanner darf Analyse-/Aufbauzustaende intern halten; standardmaessig zeigt die
  Kundenansicht aktive `JETZT_TRADEN`, `AKTIV_HALTEN` und `JETZT_VERKAUFEN`-Ideen.
- Der komplette Penny-Universe-Scan darf nicht durch einen kleinen Top-N-Block
  ersetzt werden. Teilpruefung oder Laufzeitbudget muss als unvollstaendig markiert
  und rotierend fortgesetzt werden.

### 5.5 Krypto Early Movers / Explosion

- Universum und Trigger werden exchange-native geprueft, nicht nur aus CoinGecko-
  Gesamtvolumen abgeleitet.
- Fuer Entry zaehlen konkrete Venue, Perp-/Spot-Verfuegbarkeit, Spread, Orderbuch,
  5m-Ausfuehrungsstruktur und hoeherer Zeitrahmen.
- `EXPLOSION_ARMED`/Aufbau ist kein Market-Buy. `JETZT_TRADEN` braucht den definierten
  bestaetigten Trigger.
- BTC-Kontext ist ein Modifikator, kein alleiniger Blocker fuer idiosynkratische
  Listings; er muss aber in Risiko und Positionsgroesse eingehen.
- Vertikale Pumps, schlechte Venue-Liquiditaet, Funding-/OI-Risiko, verpasster TP1
  und Chase werden blockiert oder auf Retest gestellt.

### 5.6 New Listing / Pump-and-Dump

- Listing-Ankuendigungen werden gespeichert und mit Venue/Contract verifiziert.
- Erst Pump/Exhaustion plus bestaetigter 5m-Crack/Rejection erzeugt ein Short-Signal.
- Zu wenig Historie bleibt im Monitoring und wird spaeter erneut geprueft.
- Listing-Alter stammt aus echter Listing-/Announcement-Zeit, nicht nur aus der
  Zahl geladener Kerzen.
- Ein bereits unterschrittenes Short-Ziel liefert keinen positiven Reward.

### 5.7 Crash, Bear, Turtle, Volume und Narrative

- Crash erkennt Risiko/Flush, ist nicht automatisch ein spaeter Short-Entry.
- Bear und Crash duerfen denselben Ticker nicht ohne klare semantische Trennung und
  Deduplizierung mehrfach als identische Idee senden.
- Turtle-/Breakout-Level brauchen echte historische Struktur und frische
  Bestaetigung.
- Volume-Spike ohne Richtung, Struktur und Liquiditaet ist kein Trade-Signal.
- Narrative/Market Weather ist Kontext und Positionsgroessenhinweis, keine
  eigenstaendige Kauf-/Verkaufsfreigabe.

---

## 6. Stops, Ziele und Barrieren

Prioritaet bei der Planbildung:

1. harte Invalidation der Setup-These,
2. mehrfach bestaetigte horizontale S/R-Zone,
3. Multi-Timeframe-Barrier (4H, Daily, Weekly),
4. VRVP-POC/VAH/VAL/HVN/LVN mit korrekter Richtung,
5. relevante EMA/MA-Struktur,
6. Fibonacci/Measured Move/ATR-Erweiterung als nachrangige Projektion.

Ein Stop kommt dorthin, wo die These falsch ist, plus angemessener Volatilitaets-
und Tickpuffer. Er wird nicht rueckwaerts aus einem gewuenschten 2R-Ziel erzeugt.
TP1 ist die erste realistisch handelbare Gegenbarriere; TP2 die naechste valide
Zone. Liegt eine starke Barriere vor TP1, wird TP1 angepasst oder der Trade
verworfen.

VRVP ist hilfreich, aber kein Orakel. Profilzeitraum, Timeframe und Volumenquelle
muessen passen. HVN/LVN-Bezeichnungen muessen richtungslogisch sein. Jede angezeigte
Levelquelle muss dem tatsaechlich verwendeten Preislevel entsprechen.

---

## 7. Mail-, Reminder- und Positionsvertrag

- Erstsignal-Mails enthalten nur die freigegebene Handelsklasse und alle
  entscheidungsrelevanten Felder: Name, Ticker/Contract, Richtung, Horizont, Zeit,
  Preis, Entry, Stop, TP1, TP2, R-Werte, Zustand und wichtige Blocker.
- Swing-Mails duerfen nicht "JETZT" suggerieren, wenn der Plan mehrtaegig und der
  Einstieg nicht aktuell bestaetigt ist.
- Reminder sind nur fuer `TRIGGER_ABWARTEN` oder `RETEST_ABWARTEN` zulaessig. Sie
  melden erst die tatsaechliche Zustandsaenderung, nicht nur den Ablauf einer Uhr.
- Persoenliche Positionen filtern auf Wunsch Folge- und Stop-Mails. Sie veraendern
  weder Scanneruniversum noch globalen Forward-Track-Record.
- Deduplizierung erfolgt je Empfaenger, Signalidentitaet und Mailklasse.
- Signal-Update und Stop-auf-Einstand-Mail sind verschiedene Ereignisse.
- Die persistente Outbox muss Retry, Lease, Fehler und Dead Letter sichtbar machen.

---

## 8. Forward-Tracker und Backtests

Forward-Tracking beginnt beim tatsaechlichen Versand bzw. beim dokumentierten
Broker-Fill. Entry, Stop, Ziele, Richtung, Strategie und Horizont werden danach
nicht rueckwirkend verschoenert.

Backtests muessen denselben Signalvertrag wie Produktion verwenden oder die
Abweichung deutlich ausweisen. Insbesondere:

- strategieabhaengige Haltedauer,
- Entry erst nach Signalerzeugung,
- Kosten und Slippage,
- Gap-through-Stop,
- Same-Bar-Ambiguitaet,
- Partial TP/BE-Management,
- No-Fill/offen/unaufgeloest getrennt,
- keine Survivorship-/Look-ahead-Verzerrung,
- keine Freigabeentscheidung aus einer zu kleinen Stichprobe.

Schwellen werden nicht wegen eines einzelnen Verlusts oder Gewinners geaendert.
Kalibrierung verlangt ausreichend grosse Forward-Kohorten je Scanner, Richtung,
Horizont und Marktregime.

---

## 9. Broker- und AutoTrader-Sicherheit

Der AutoTrader bleibt standardmaessig deaktiviert und auf IBKR Paper (`DU...`)
begrenzt, bis ein dokumentierter mehrtaegiger Soak-Test bestanden ist.

Nicht verhandelbar:

- Broker ist Quelle fuer Positionen, Orders, Fills, Kontowerte und PnL.
- Keine lokale Scheinposition und kein erfundener Fill.
- Stabile Order-/Setup-IDs und Restart-Reconciliation.
- Bracket/OCA-Schutz fuer ausgefuehrte Positionen.
- Partial-Fill- und Reject-Behandlung.
- Tagesverlust-, Exposure-, Positions- und Notional-Limits.
- Kill-Switch blockiert neue Entries; keine blinde Liquidation.
- Stop darf nur enger, nie weiter vom Risiko weg gesetzt werden.
- Live-Trading braucht eine neue explizite fachliche und technische Freigabe.

---

## 10. UX-Vertrag

- Gleiche Begriffe, Farben und Spaltenreihenfolge in allen Scannerfamilien, soweit
  fachlich vergleichbar.
- Aktive Auswahl einer Zeile bleibt sichtbar markiert.
- Technische Diagnosen sind klein/aufklappbar; die Hauptansicht zeigt Entscheidung,
  Plan und Risiko.
- Kein Widerspruch zwischen Tabelle, Sidebar, Chart und Mail.
- Kein Button fuer einen unzulaessigen Reminder oder Trade.
- Timeframe, Datenquelle und Datenalter sind sichtbar, aber nicht dominanter als
  die Handelsentscheidung.
- Mobile Nutzung bleibt ein One-Hand-Dashboard: klare Prioritaet, keine doppelten
  Textbloecke und keine kryptischen internen Reason-Codes.

---

## 11. Deployment-, Security- und Commercial-Vertrag

- Entwicklung und Commit erfolgen lokal; auf Produktion wird nicht entwickelt.
- Produktion zieht ausschliesslich `origin/main`.
- Deployment nur ueber `deploy/safe_deploy.sh` oder den revisionsgebundenen
  `deploy/auto_update.sh`.
- Ein Rollout ist erst erfolgreich, wenn Git-Revision, API-Revision,
  Frontend-Bundle-Hash, ausgelieferte Dateien, Services und Health uebereinstimmen.
- Secrets gehoeren nie in Git, Screenshots, Mails oder Projektdokumente.
- `data_cache/`, Tracker-DB, Outbox und Brokerzustand sind Produktionsdaten und
  werden nicht aus einem lokalen Checkout ueberschrieben.
- Kommerzieller Betrieb verlangt Auth-Enforcement, zufaelliges JWT-Secret,
  deaktivierten Legacy-Master-Key, HTTPS/CORS, Live-Stripe, verifizierten Webhook,
  Backup/Restore, Datenschutz/AGB/Risikohinweis und geklaerte Datenlizenzen.
- Ein gruener lokaler Testlauf beweist weder Server-Rollout noch profitable
  Handelsperformance.

---

## 12. Definition of Done

Eine Aenderung ist erst fertig, wenn:

1. fachliche Annahme und betroffene Scanner/Horizonte benannt sind,
2. Mathematik und Einheiten geprueft sind,
3. es nur eine kanonische Zustandsentscheidung gibt,
4. Datenfrische, Fallback und Fehlerpfad geprueft sind,
5. Regressionstests fuer den Fehlerfall existieren,
6. volle lokale Suite und Frontend-Bundle-Pruefung gruen sind,
7. Git-Diff keine fremden oder geheimen Dateien enthaelt,
8. Commit auf `main` gepusht ist,
9. bei Produktionsaenderung der exakte Server-Commit und Health nachgewiesen sind,
10. bei Tradinglogik die Forward-Messung geplant bzw. fortgefuehrt wird.

"Tests gruen" allein bedeutet nur technische Abnahme. "Health OK" bedeutet nur
Betriebszustand. "Score hoch" bedeutet nur Modellbewertung. Erst die Kombination
aus korrektem Code, konsistenter Mathematik, sinnvoller Traderlogik, sauberer UX,
einem Produktionsnachweis und belastbarer Forward-Performance rechtfertigt Vertrauen.

---

## 13. Aktuell offene harte Nachweise

- Exakter Produktionsstand nach jedem neuen Push muss separat verifiziert werden.
- IBKR-DU-Paper-Soak ueber Disconnect, Restart, Partial Fill, Reject, Gap und OCA
  ist noch nicht abgeschlossen; Live bleibt blockiert.
- Profitabilitaet ist nicht bewiesen. Pro Scanner/Regime werden mindestens 30
  vollstaendig beobachtete Entscheidungen benoetigt.
- Mail-Outbox, persoenliche Positionsfilter und Stop-Updates muessen produktiv mit
  realen Empfaengern weiter beobachtet werden.
- Commercial Launch benoetigt weiterhin menschliche Rechts-, Steuer- und
  Datenlizenzpruefung sowie Live-Stripe/TLS-Nachweis.
