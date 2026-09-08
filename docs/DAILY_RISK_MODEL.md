# Tagesrisiko: reines Offline-Modell

Stand: 08.09.2026. Kapital vom Nutzer bestaetigt: **5.000 USD**. Genannte
Tagesverlusttoleranz: **300 USD**. Das ist keine Freigabe, bestehende engere
Limits anzuheben. 150 USD entsprechen 3 % des Kapitals; ein taeglicher Gewinn
dieser Hoehe ist kein zugesicherter oder aus den bisherigen Daten belegter Ertrag.

## Was umgesetzt ist – und was nicht

Die neue reine Funktion `assess_daily_risk` in
[daily_risk_assessment.py](C:/Projekt/TradingBot/modules/daily_risk_assessment.py)
prueft einen vollstaendig uebergebenen, eingefrorenen Offline-Snapshot.
Sie liest und schreibt keine Datei, Datenbank oder Konfiguration, importiert
keine Broker-/Paper-Ausfuehrung, ruft kein Netzwerk auf und verwendet keine
versteckte Systemzeit. Es gibt keinen zweiten persistenten Risiko-Store.

Die Ausgabe enthaelt **immer**:

- `mode=OFFLINE_ONLY`, `execution_authorized=false`, `paper_authorized=false`;
- `atomic_reservation_performed=false`, `frozen_snapshot_only=true`;
- `broker_evidence_independently_verified=false`, `loss_ceiling_guaranteed=false`.

`within_model_limits=true` bedeutet ausschliesslich: Diese vollstaendigen,
widerspruchsfreien **Eingaben** passen rechnerisch zu den uebergebenen Limits.
Es bedeutet weder, dass der Broker sie bestaetigt hat, noch dass der Snapshot
jetzt noch frisch ist oder ein konkurrierender Prozess keine Order aufgegeben hat.
Das Ergebnis ist keine Kauf-, Verkaufs- oder Paper-Order-Freigabe.

Die Umsetzung erweitert **nicht** `paper_autotrader.py`, `trading_risk.py` oder
`trading_risk_store.py`. Deren Ausfuehrungs-, Kill-Switch-, Konto-, Quote-,
Cash-/Settlement-, Exposure-, Gruppen-, Richtungs-, Short-Borrow-, Stop- und
Parallelitaetspruefungen bleiben unveraendert. Ein an Snapshot und Kandidat
gebundenes Ergebnis dieser bestehenden Gates muss separat uebergeben werden;
`blocked` oder `unresolved` wird nie durch positive Tagesarithmetik ueberstimmt.

## 1. Feste Basis und konservative Mathematik

Alle Betraege sind USD. Fuer Prozentlimits gilt:

```
Basis = min(festes Session-Startkapital, ausdruecklich bestaetigtes Kapital)
Tageslimit = min(Nutzertoleranz, bestehender engerer USD-Cap,
                Basis * bestehender Tagesprozentsatz / 100)
Einzellimit = min(bestehender engerer Einzel-USD-Cap,
                 Basis * bestehender Einzelprozentsatz / 100)
Gesamtlimit = min(bestehender engerer Gesamt-USD-Cap,
                 Basis * bestehender Gesamtprozentsatz / 100)
```

Die bestehenden Prozentwerte und gegebenenfalls noch engeren aktuellen
Dollar-Caps sind **Pflichteingaben**, keine von diesem Modul neu gespeicherten
Defaults. Das bisherige Paper-Quellcode-Default ist 1 % Tagesverlust, 0,25 %
Einzelrisiko und 0,75 % Gesamtrisiko; das beweist keine aktive Serverkonfiguration.
Bei genau diesen hypothetischen Inputs entstehen aus 5.000 USD:

| Obergrenze | Rechnung | Offline-Grenze |
| --- | --- | ---: |
| Tagesverlustbudget | min(300, 50, 5.000 * 1 %) | 50 USD |
| Einzelrisiko | min(12,50, 5.000 * 0,25 %) | 12,50 USD |
| Gesamtrisiko | min(37,50, 5.000 * 0,75 %) | 37,50 USD |

Das Modell belastet auch diese Einzel-/Gesamt-Caps mit Kosten und Gap-Stress.
Es ist insoweit strenger als eine alleinige Stop-Abstandsrechnung. Eine hoehere
neue Session-Balance allein erhoeht die bestaetigte 5.000-USD-Basis nicht; eine
niedrigere Startbalance verringert die Prozentbasis. Engere bestehende aktuelle
Caps muessen weiterhin uebergeben werden und gewinnen immer.

### Genau eine PnL-Basis

Zugelassen ist ausschliesslich `realized_net_plus_entry_to_stop`:

```
realisiertes Netto-PnL = realisierte Preis-PnL - bereits verbuchte Kosten
verbrauchtes Tagesbudget = max(0, -realisiertes Netto-PnL)
Risiko je verbleibendem Intent = Entry-bis-Stop-Verlust
                              + noch nicht verbuchte Kosten
                              + ausdruecklicher Gap-/Ausfuehrungsstress
Restbudget = max(0, Tageslimit - verbrauchtes Budget - alle aktuellen Risiken)
prospektiver Verlust = verbrauchtes Budget + aktuelle Risiken + neues Risiko
```

Unrealisiertes PnL darf **nicht** zusaetzlich abgezogen werden. Ein Verlust von
10 USD zwischen Entry und aktuellem Kurs, der bereits im Entry-bis-Stop-Risiko
steckt, ist sonst doppelt belastet. Alternativ waere eine separat definierte
Mark-to-Market-plus-Mark-bis-Stop-Methode moeglich; sie wird hier ausdruecklich
abgelehnt statt still mit der ersten Methode gemischt.

Positive realisierte Netto-PnL erhoeht das Tageslimit nicht. Dieses Modell
begrenzt den Nettoverlust gegenueber der definierten Realisierungsbasis: Gewinne
und Verluste innerhalb der Session koennen sich bis zum urspruenglichen Cap
ausgleichen. Es ist **kein** Peak-to-Trough-/Trailing-Drawdown-Gate und zaehlt
nicht einfach alle verlierenden Trades ohne Gewinne zusammen.

Beispiel: -28 USD realisierte Preis-PnL, 2 USD verbuchte Gebuehren, 12 USD
offenes/reserviertes Stressrisiko und ein weiterer 12-USD-Kandidat ergeben
`30 + 12 + 12 = 54 USD`. Bei 50 USD Tagescap lautet das Ergebnis **blockiert**,
obwohl der bisher realisierte Nettoverlust erst 30 USD betraegt.

### Kosten und Cash ohne Doppelabzug

`booked_costs_usd` muss `commissions`, `financing`, `borrow` und `other`
vollstaendig ausweisen. Dazu gehoeren bereits angefallene Entry-Kosten noch
offener Positionen. Null ist nur eine ausdruecklich belegte Null, kein Ersatz
fuer eine fehlende Kommissionsmeldung. Vorzeichenbehaftete Rebates/Erstattungen
sind in diesem ersten Vertrag nicht unterstuetzt: Sie muessen erst sachlich
abgeglichen werden; negative Kosten werden nicht still auf null gekappt.

Realisierte Preis-PnL verwendet tatsaechliche bzw. im hypothetischen Szenario
ausdruecklich modellierte Ausfuehrungspreise. Darin bereits enthaltenen Spread
oder Slippage nicht nochmals als Gebuehr abziehen. `future_costs_usd` umfasst
nur noch **nicht** verbuchte Cash-Kosten, zum Beispiel ausstehende Entry-/Exit-
Kommissionen und fuer den festgelegten Haltehorizont angenommene Finanzierung.
Zusaetzliches Preisrisiko gegenueber dem verwendeten Entry-/Stop-Basisszenario
wird separat in `gap_stress_usd` erfasst. Dieselbe Abweichung nicht in beiden
Feldern buchen. Ein unbekannter Finanzierungshorizont ist keine Nullkostenaussage.

Cash-Basis ist explizit der abgeglichene verfuegbare Betrag **nach offenen
Positionen und bereits gebuchten Kosten, aber vor den uebergebenen Pending-
Reservierungen**. `pending_cash_usd` ist deren Kapitalbindung ohne die separat
ausgewiesenen Zukunftskosten. Nach allen aktuellen und neuen Reservierungen
und **allen** Zukunftskosten muss die bestehende Cash-Mindestreserve verbleiben.
Gutschriften aus Short-/Margin-Orders werden nicht erfunden; der erste Vertrag
fordert positive konservative Cash-Reservierung fuer jede Pending-Entry-Order.
Ein Brokerwert, der Pending bereits abzieht, darf nicht ohne belegte Normalisierung
in dieses Feld kopiert werden. Sonst entstuende ein doppelter Abzug.

## 2. Drei Entry-Slots, nicht drei Positionen oder Gewinner

Das reine Ereignis-Ledger akzeptiert reservierte Intents, Entry-Fills,
Stornoanforderungen, bestaetigte Stornos/Ablehnungen und vollstaendige Schliessungen.
Es muss die ganze fuer aktuelle Risiken und die Session benoetigte Historie
enthalten. Zeitpunkte brauchen explizite Zeitzonen; Sessiongrenzen sind
halb-offen (`Start <= Zeitpunkt < Ende`), nicht pauschal der UTC-Kalendertag.

| Zustand | Slotbehandlung |
| --- | --- |
| Neue Pending-Order | Ein Slot reserviert |
| Storno nur angefordert | Slot und Risiko bleiben reserviert |
| Terminal, bestaetigt vollstaendig ungefuellt | Reservierung darf frei werden |
| Irgendeine Entry-Teilfuellung in der Session | Slot bleibt bis Sessionende verbraucht |
| Teilfuellung, anschliessend Restorder storniert | Kein Slot-Refund; offene Restposition bleibt im Risiko |
| Gefuellter Trade heute bereits geschlossen | Slot bleibt verbraucht |
| Pending-Order aus Vortag | Reserviert auch in heutiger Session einen Slot |
| Teilfuellung gestern, Entry-Restorder heute noch offen | Reserviert heute bereits vor der naechsten Fuellung |
| Voll gefuellte Altposition ohne neue Entry-Restorder | Kein neuer Tages-Entry, aber volles verbleibendes Risiko und Position-Slot |

Pro Session sind hoechstens drei Entry-Slots zulaessig; ein uebergebenes Limit
von eins oder zwei bleibt enger. Mehrere Teilfuellungen desselben Intents
zaehlen nicht als mehrere Trades. Ein exakt wiederholtes Ereignis wird
idempotent gelesen. Wiederholte Bewertung eines identischen, bereits reservierten
Kandidaten belastet Risiko, Cash und Slot nicht doppelt. Veraenderte Parameter
unter derselben Intent-Identitaet, fehlende Sequenzen, doppelte Exposure-Zeilen,
Ereignisse nach dem Snapshot oder widerspruechliche Terminal-/Fill-Folgen
blockieren die Bewertung. Ein bereits gefuellter oder beendeter Intent darf
nicht als vermeintlich neuer Entry erneut verwendet werden.

Das ist **keine persistente Ausfuehrungssperre**. Ein Caller darf nicht bei jedem
Neustart mit einem leeren Ledger beginnen und einfach `ledger_complete=true`
behaupten. Vollstaendigkeit und historische Broker-Identitaeten sind externe
Nachweise. Ein spaet gemeldeter Fill nach einem angeblichen ungefuellten Storno
ist zuerst abzugleichen; das Modell nimmt nicht an, er koenne ignoriert werden.

## 3. Eingabevertrag und Fehlerverhalten

Alle Mapping-Schemata sind exakt; fehlende **und unbekannte** Felder blockieren.
Damit wird zum Beispiel ein versehentlich uebergebenes `unrealized_pnl_usd`
nicht unbeachtet verworfen. Zahlen muessen endlich sein, hoechstens acht
Nachkommastellen haben und im Betrag hoechstens 10^12 betragen. Boolesche Werte
sind keine Zahlen. Geldarithmetik verwendet Dezimalzahlen mit 50 Stellen
Arbeitsgenauigkeit und gibt exakte Dezimalstrings zurueck. Reale Ueberschreitungen
werden nicht durch Float-Toleranzen oder Abrunden von Risikocents versteckt.

| Argument | Pflichtinhalt |
| --- | --- |
| `session` | Identitaet, USD-Konto, expliziter Anfang/Ende, feste Startbasis, bestaetigtes Kapital |
| `expected_session` | Identischer, extern dauerhaft erhaltener Session-Anker; kein aus aktuellen Inputs neu erzeugter Ersatz |
| `policy` | Ausdrueckliche bestehende engere Dollar-/Prozentlimits, Tages- und Positionsslots, Exposure- und Cash-Caps |
| `snapshot` | Bindung an Konto/Session, As-of-Zeit, Herkunft, vollstaendiges Realisierungs-/Kosten-/Order-/Positions-Ledger, Cash-Basis |
| `ledger_events` | Stabile Ereignis-/Intent-IDs, lueckenlose Sequenz je Intent, abgeglichener Zustand und Fuellungen |
| `exposures` | Genau eine nicht doppelt gezaehlte Zeile fuer jeden aktiven Intent; Schutz, Pending-Zustand, Rest-Risiko, Kosten, Stress, Cash und Exposure |
| `candidate` | Ein klar identifizierter neuer oder unveraendert bereits reservierter Pending-Intent mit geprueftem Schutzplan |
| `existing_gates` | An denselben Snapshot und Kandidat gebundene vorangehende Gates; keine Uebersteuerung von `blocked`/`unresolved` |

`protection_verified` ist bei existierenden Positionen der vom Caller
nachzuweisende aktive Schutz; beim hypothetischen Pending-Kandidaten ist es nur
der fuer die Modellrechnung validierte Schutz-/Reservierungsplan, keine schon
platzierte Broker-Stoporder. Eine nicht geschuetzte bzw. unbekannte Position
blockiert. Ein profitabler Stop darf als nichtnegatives Null-Preisrisiko
erscheinen, nicht als negatives Risiko, mit dem andere Positionen vergroessert
werden. Kosten und Stress bleiben trotzdem zu beruecksichtigen.

`evidence_kind` ist entweder `hypothetical` oder
`caller_reconciled_broker_snapshot`; die Herkunft wird nicht umetikettiert.
Letzteres bleibt eine **Caller-Aussage**, kein durch diese reine Funktion
erlangter Brokerbeweis. Bei ungueltigen Eingaben ist `metrics=null`, nicht ein
scheinbar risikoloser Nullwert. Eine vollstaendige Testeingabe steht in
[test_daily_risk_assessment.py](C:/Projekt/TradingBot/test_daily_risk_assessment.py).

## 4. Noch erforderliche Integration – ausdruecklich NICHT erledigt

1. Festlegen und belegen, welche reale Broker-Session gilt (Kalender, Zeitzone,
   Sommerzeit, Ereigniszuordnung), und den unveraenderlichen Startanker dauerhaft
   speichern. Offene Altpositionen und Restorders sind vor dem Sessionwechsel
   abzugleichen, nicht durch Mitternacht wegzusetzen.
2. Tatsachengetreues Netto-Ledger aus stabilen Execution-IDs, Teilfuellungen,
   Kommissionsberichten, Finanzierung/Leihe und gegebenenfalls Waehrungsnachweisen
   ergaenzen. Eine bestehende Signal-Tracker-OPEN-Zeile ist keine Brokerposition.
3. Die bestehende reine `aggregate_stop_risk`-Pruefung fuer Broker-/Stop-/Intent-
   Identitaeten nutzen; keine zweite widersprechende Stopzuordnung bauen. Danach
   verbleibende Mengen, Kosten, Stress und Cash auf denselben Snapshot normalisieren.
4. Tagesbudget, Entry-Slots und bestehende Portfolio-/Cash-Reservierung unter
   **derselben** SQLite-Transaktion, Lease/Fence-/Execution-Generation und
   anschliessender Broker-Reconciliation in `TradingRiskStore.reserve_if_allowed`
   integrieren. Zwei parallel positive Offline-Ergebnisse sind keine zwei
   erworbenen Slots. Dieses Modul setzt keinen Lock und erwirbt keinen Slot.
5. Crash-/Restart-, spaete Fill-/Kommissions-, Cancel-/Fill-Race-, Carryover-,
   Sommerzeit- und Parallelitaetstests auf der tatsaechlichen Integration
   ausfuehren. Ein offline gruener Test ersetzt diese Abnahme nicht.
6. Erst anschliessend eine getrennte Paper-Freigabe mit realen Konto-/Daten-
   Nachweisen erwägen. Hier wurden weder Paper noch Live aktiviert.

Der vorhandene Risk-Store wird bewusst nicht voreilig um ein vermeintlich
fertiges Tagesgate erweitert: Ohne die noch fehlende Session-/Kostenprovenienz
koennte ein technisch atomarer Zugriff trotzdem die falsche Tages-PnL oder
doppelt gezaehlte Reservierungen verwenden. Die bestehende Ausfuehrung bleibt
deshalb unangetastet, bis diese Eingabevertraege real erfuellt und getestet sind.

## Grenzen

Ein Gap, eine Handelsunterbrechung, Slippage oder ein Ausfall kann die angenommene
Stressreserve ueberschreiten. Selbst ein korrekt integriertes Gate kann 50 oder
300 USD nicht als harte Verlustgarantie versprechen. Ein Exit-Ziel von 50 USD
pro Trade wird hier weder optimiert noch implementiert. Weder Trefferquote noch
Profitabilitaet wird durch diese Risikomathematik bewiesen.

Die lokalen Vertragsfaelle pruefen 5.000/300/1 %, engere Caps, Kosten, Gleichheit
und kleinste reale Ueberschreitungen, offene/Pending-Risiken, Teilfuellungen,
Refund-Regeln, idempotente Retries, Carryover, explizite Sommerzeitgrenzen,
unbekannte/ungueltige Daten, Cash und bestehende Gates. Die verbindliche aktuelle
Testzahl wird im Handoff mit dem finalen Testlauf dokumentiert.
