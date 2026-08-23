# Design: Auditfeste Signalwahrheit und Risikokontrollen

Datum: 2026-08-21
Ausgangsrevision: `24adab5019dfe5a7433fa96709b2939018eef2d1`
Arbeitszweig: `codex/trading-audit-remediation`

## 1. Ziel und Entscheidungsgrenzen

Das Mailarchiv zeigt reale Nutzerprobleme: Updates lassen sich nicht immer eindeutig
einem Ursprungssignal zuordnen, `+1R gelaufen` kann wie realisierter Gewinn wirken,
Level-R und empfohlenes 50/50-Management werden leicht verwechselt, und mehrere
gleichgerichtete Signale erzeugen Klumpenrisiko. Der aktuelle Code hat einen grossen
Teil der historischen Ursachen bereits behoben. Diese Änderung schliesst nur die am
aktuellen HEAD reproduzierbaren Lücken.

Verbindliche Grenzen:

- Historische Mails werden nicht rückwirkend zu einem Backtest umgedeutet. Es fehlen
  stabile Ursprungsreferenzen, vollständige Fills und durchgehende Kurswege.
- Aus 24 abgeschlossenen Mailbeobachtungen werden keine neuen Entry-, Stop-, Ziel-
  oder Grade-Schwellen abgeleitet.
- Bestehende harte Gates für Geometrie, Stop-Rauschen, Session, Quote-Frische,
  Fill-Kausalität und Regime bleiben erhalten.
- Live-Trading bleibt blockiert; neue Ausführungsregeln gelten nur für den bereits
  Paper-only ausgelegten IBKR-AutoTrader.
- Kein Versand, Push oder Server-Rollout ist Bestandteil dieser Umsetzung.

## 2. Paket A: öffentliche Signalreferenz und Mailwahrheit

### 2.1 Öffentliche Referenz

Neue Entry-Intents erhalten eine unveränderliche `public_signal_ref` im Format
`AS1-XXXXXXXXXXXXXXXXXXXX`. Sie wird deterministisch aus Delivery-Intent und der
kanonischen Identität der einzelnen Row erzeugt, bevor SMTP versucht wird. Die
Eingabereihenfolge ist ausdrücklich kein Identitätsbestandteil: Ein Retry mit
denselben Rows in anderer Reihenfolge erzeugt dieselbe Row-zu-Referenz-Zuordnung.
Interne SQLite-IDs und `setup_key` bleiben für Kompatibilität erhalten, sind aber
nicht länger die einzige kunden sichtbare Identität.

Eine partielle UNIQUE-Constraint gilt für alle nichtleeren Referenzen. Eine
deterministische Kollision oder eine mehrdeutige Row-Zuordnung blockiert den
gesamten Delivery-Intent vor SMTP; eine bereits ausgegebene Referenz wird niemals
durch einen alternativen Suffix ersetzt.

Die additive SQLite-Migration setzt bei Altzeilen keinen erfundenen Wert. Ein
zusätzliches `origin_evidence` trennt `smtp_acceptance`, aktuelle ausdrücklich
nach erfolgreichem Versand erfasste `direct_post_send`-Zeilen,
`shadow_counterfactual` und `legacy_origin_unknown`. Legacy-Unbekannt bleibt für
Anzeige/manuelle Reparatur sichtbar, darf aber keine neue Kalibrierungs- oder
Breaker-Freigabe begründen. Transitionen, ausstehende Terminal-Updates,
BE-Aktivierungen, Performance-Recent-Payloads und Reparaturprüfungen reichen die
Referenz nur durch; sie dürfen sie nie nachträglich verändern.

### 2.2 Ursprung und Zustände

Jede Folge-Mail zeigt pro Zeile:

- `Signal-Ref`;
- Zeitpunkt der akzeptierten Ursprungsmail (`delivery_accepted_at`), sonst klar
  `Ursprung historisch nicht belegt`;
- kanonischen Zustand (`TP1 erreicht, Position offen`, `TP2`, `Stop`, `Expired`);
- MFE-R getrennt von abschliessendem Level-R;
- Fill, Entry-Slippage, Stop-Exit-Fill und Stop-Gap-Slippage als getrennte Felder.

`TP1_HIT_OPEN` bekommt niemals ein abschliessendes realisiertes R. Die Mail sagt
ausdrücklich, dass TP1 nur eine erreichte Zone ist und ein Teilverkauf eine
Management-Anweisung, keine vom System bestätigte Ausführung.

### 2.3 BE- und 50/50-Semantik

`+1R` wird überall als `MFE >= +1R` bezeichnet. Es ist ein historisch beobachteter
Kursfortschritt, kein verbuchter Gewinn. Eine Stop-Anweisung auf Einstand reduziert
das geplante Kursrisiko; Gap-, Slippage- und Ausführungsrisiko bleiben bestehen.
Die Formulierung `risikofrei` ist verboten.

Die drei Ergebnisgrössen bleiben sichtbar getrennt:

1. `Level-R`: Ergebnis des getrackten Planpfads;
2. `Managed-R 50/50`: Modell mit 50 Prozent an TP1;
3. `Managed-R 50/50+BE`: nur bei kausal belegter Stop-Mail und belegbarem Pfad,
   sonst `managed_be_unresolved`.

## 3. Paket B: Zielreichweite ohne Scheingenauigkeit

`modules.trade_levels` erhält eine reine Funktion, die aus validierter Geometrie,
ATR und Horizont folgende Messwerte berechnet:

- Stopdistanz in ATR;
- TP1- und TP2-Distanz in ATR;
- vorhandene beziehungsweise fehlende Datengrundlage;
- maschinenlesbare Hinweise wie `target_reachability_unavailable` oder
  `target_beyond_configured_atr_budget`.

Die Funktion erfindet keine Wahrscheinlichkeit. ATR-Budgets sind explizite,
injizierbare Policy-Parameter. Ohne konfigurierte und durch Forward-Daten
freigegebene Budgets liefert sie Telemetrie, aber kein neues Hard-Gate. Scanner,
finale Quote-Revalidierung und Mailformatierung verwenden dieselbe Funktion.

## 4. Paket C: Paper-Portfolio-Risiko und Verlustbremsen

Ein neues reines Risikomodul bewertet ausschliesslich belegte Broker-/Intent-Daten:

- offenes und ausstehendes Verlust-am-Stop-Risiko in Dollar und Prozent der
  NetLiquidation;
- Long-/Short-Richtungsrisiko;
- Sektor-/Korrelationsgruppen nur bei vorhandener, deterministischer Klassifikation;
- realisierte Tages-R aus vollständigen Broker-Fill-Paaren;
- heutige Folge negativer, vollständig geschlossener Paper-Trades.

Der Paper-AutoTrader erhält begrenzte Konfigurationsfelder. Die defensiven
Paper-Defaults leiten sich aus dem bestehenden Vertrag `3 Positionen x 0,25 %`
ab; sie sind Account-Sicherheitsgrenzen und keine Behauptung über Strategie-Edge:

- `max_total_risk_pct = 0,75`;
- `max_direction_risk_pct = 0,75` (zunächst kein zusätzlicher Richtungs-Haircut,
  aber mess- und konfigurierbar);
- `max_group_risk_pct = 0,50`, nur für verifiziert klassifizierte Gruppen;
- `max_consecutive_losses = 3` innerhalb desselben UTC-Tages.

Offene Positionen und Pending-Parent-Orders zählen vor einer neuen Order mit.
Ein append-only, nach IBKR-`exec_id` dedupliziertes Fill-Ledger überlebt Reconnect,
Restart und ein verkürztes aktuelles `ib.fills()`-Fenster. Entry-/Exit-VWAP,
Teilfills und Reststück werden je Intent abgeglichen; nur vollständig geschlossene
Stückzahlen erhalten `realized_r`, `realized_at` und
`outcome_evidence=broker_fills`. Unvollständige Evidenz bleibt unresolved.

Risikoprüfung und persistente `SUBMITTING`-Reservierung laufen unter einer
prozessübergreifenden, lease-gebundenen Order-Sperre. Zwei Worker dürfen nicht mit
demselben Vorzustand gleichzeitig das Budget passieren. Eine unmatched Position,
ein Pending-Parent ohne eindeutig zugeordneten Schutzstop oder ungültige
Schutzdaten blockieren die Paper-Ausführung fail-closed; sie werden nie als
Nullrisiko interpretiert. Fehlende Gruppenklassifikation deaktiviert nur das
Gruppenlimit für diese Position und wird sichtbar gemeldet, während Total- und
Richtungsrisiko weiter gelten. Der bestehende Notional-Exposure- und DailyPnL-Guard
bleibt zusätzlich bestehen. Ein Loss-Streak stoppt neue Orders nur für den Rest des
aktuellen UTC-Tages.

Für reine Signal-Mails existiert keine verlässliche Kenntnis der individuellen
Positionen. Deshalb wird dort kein fiktives Kontorisiko behauptet. Der vorhandene
Clusterhinweis wird um eine deterministische Batch-Risikozusammenfassung erweitert;
eine spätere harte Mail-Unterdrückung benötigt Forward-Kalibrierung und eine echte
Portfolioquelle.

## 5. Paket D: Grade-Kalibrierung und Actionability

Der Tracker liefert eine getrennte, rein informative Grade-Kalibrierung je
`scanner x grade x direction x horizon x exogenes Marktregime`. Eine Zelle ist nur
bei mindestens 30 vollständig beobachteten Entscheidungen und null ungelösten
Managed-BE-Fällen belastbar. Ausgegeben werden n, Trefferquote mit Wilson-95%-KI,
ØR, Summe R und Profit Factor. Sie verändert weder die bestehende vierdimensionale
Breaker-Zelle noch Mailfreigaben oder Grade. `Score` beziehungsweise `Grade` wird
in Mails und Reports als Setup-Rang, niemals als Gewinnwahrscheinlichkeit
bezeichnet.

Der bereits vorhandene Session-Vertrag bleibt verbindlich:

- `JETZT` nur für frische Intraday-Trade-Mails;
- Swing-Mails ohne `JETZT`;
- Daily-Close nur als Plan für die nächste Session;
- Pre-Market als Watch-Klasse.

Die in Task 3 erfassten gebrandeten API-Mailpfade verwenden im Markenheader
denselben injizierten Renderzeitpunkt und denselben dualen UTC-/MEZ-/MESZ-
Formatter wie der Body. Der Rendering-Guard prueft diese erfassten Klassen
zentral, einschliesslich der EU-DST-Grenzen; historische unbranded Nebenpfade
sind damit nicht pauschal migriert.

## 6. Datenmigration und Kompatibilität

- Alle SQLite-Änderungen sind additiv und idempotent.
- Bestehende Zeilen bleiben unverändert und werden nicht künstlich einer neuen
  Ursprungsreferenz oder einem Broker-Outcome zugeordnet.
- Alte direkte Tracker-Aufrufe funktionieren weiter; neue Felder sind nullable.
- JSON-State des Paper-AutoTraders wird versionskompatibel normalisiert. Alte
  Intents ohne vollständige Fill-Evidenz zählen nicht als Verlust und nicht als
  Entwarnung; sie werden als `outcome_unresolved` gemeldet.
- Keine Migration greift auf `Mailarchiv/`, Produktionsdaten oder Secrets zu.

## 7. Fehlerverhalten

- Identitäts- oder Broker-Risikodaten fehlen: neue Paper-Order blockieren und
  maschinenlesbaren Grund liefern.
- Zielreichweiten-Daten fehlen: `UNAVAILABLE`, bestehende Geometriegates bleiben
  massgeblich.
- Folge-Mail-Referenz fehlt bei Legacy-Zeile: Mail darf versendet werden, muss aber
  den unbekannten historischen Ursprung sichtbar machen.
- Report-/Rendering-Zusatz schlägt fehl: keine falschen Kennzahlen erzeugen; der
  bestehende Kernpfad bleibt verfügbar und protokolliert den Ausfall.

## 8. Teststrategie und Abnahme

Jedes Paket wird testgetrieben umgesetzt: neuer Test muss zuerst aus dem fachlich
erwarteten Grund rot sein, danach minimal grün werden.

Pflichtnachweise:

- Migration, Retry-Stabilität und Entry-zu-Folge-Mail-Korrelation der öffentlichen
  Referenz, einschliesslich zwei gleichzeitiger Setups desselben Tickers;
- TP1/MFE/Level-R/Managed-R-Texte und Verbot von `risikofrei`;
- LONG/SHORT-Spiegelung und fehlende ATR-Daten bei Zielreichweite;
- offene plus Pending-Risiken, Richtungs-/Gruppenlimit, Fill-basierte Tages-R,
  Streak-Grenze und Fail-closed-Verhalten;
- Grade-Zellen unter/ab n=30, Wilson-Intervall und Legacy-Unbekannt;
- duale Zeit im gebrandeten Header sowie `JETZT`-Invarianten;
- vollständige Repository-Suite, Python-Compile, Frontend-Bundle-Prüfung,
  `git diff --check`, Secret- und Scope-Prüfung.
