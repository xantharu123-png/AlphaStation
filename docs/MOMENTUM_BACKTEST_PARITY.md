# Momentum: Tagesmodell und Offline-Versionsvergleich

## Was jetzt identisch ist

Die Live-Kandidatensuche und der Momentum-Tagesbacktest verwenden
`modules/stock_momentum_contract.py`: denselben versionierten 10T-/20T-
Ausbruchsentscheid sowie denselben oeffentlichen Preis-, Tagesbewegungs-,
Volumen-, Schlusskurspositions- und Dollarvolumen-Grundfilter. Der Backtest
liefert dafuer ausschliesslich den historischen abgeschlossenen Tagespraefix.
Ein Regressionstest vergleicht EMA, RSI, Hochanker, 5-Tages-Aenderung und RVOL
mit dem Live-Adapter auf **exakt demselben** abgeschlossenen Eingabepraefix.

Dies ist Auswahlregel-Paritaet, keine vollstaendige Daten- oder Handelsparitaet.
Andere geladene Historienlaengen koennen insbesondere den EMA-Startwert aendern.
Die Live-Suche verwendet ihre begrenzte Provider-Historie; ein langer lokaler
Backtest kann einen anderen Praefix enthalten. Beide Datenfenster muessen fuer
einen numerisch identischen Replay gleich exportiert werden.

Die zusaetzliche Provider-Pruefung auf vollstaendige OHLCV-Werte und bereits
geschlossene US-Tagessitzungen ist fuer den Single-Ticker-API-Pfad getestet.
Der aeltere Universums-Fetcher (`run_full_backtest` / `fetch_backtest_daily_data`)
liefert weiterhin seinen bisherigen datumsbasierten Adapter; dessen implizite
Defaults sind kein nachgewiesener Abschluss- oder Provider-Vollstaendigkeitsbeleg.
Diese Grenze wird durch die gemeinsame Auswahlregel nicht aufgehoben.

## Was ausdruecklich ein Modell bleibt

Die bestehende Tages-Exitregel bleibt unveraendert: naechster Tagesopen,
5% Anfangsstop, TP1=1,5R / TP2=2,5R, 50/50-Teilverkaeufe und maximal drei
Haltekerzen. Nach TP1 gilt der modellierte Breakeven-Stop fuer den Rest.
Angenommene Kosten: Entry-Slippage 5 Basispunkte, Exit-Slippage 5 Basispunkte,
Roundtrip-Gebuehr 20 Basispunkte. Stop-/Zielreihenfolgen werden mit den bestehenden
konservativen/guenstigen OHLC-Grenzen gezeigt. Offene, unvollstaendige oder
fehlende Haltekerzen erzeugen keinen erfundenen abgeschlossenen Trade.

Das ist **nicht** der strukturabhaengige Live-Entry-/Stop-/Zielplan. Historische
5m-Trigger, 4h-Freigaben, Quotes/Spreads, Kontext und Broker-Fills fehlen im
Tagesmodell. Ergebnisse tragen deshalb `live_equivalent=false`,
`live_validation_eligible=false` und `paper_autotrade_release_eligible=false`.
Ein positives Diagnoseurteil ist keine Freigabe. Nicht beobachtete Performance
wird als `null` / `performance_available=false` ausgewiesen. Alte Caches erhalten
keine nachtraegliche aktuelle Modellversion.

## Reproduzierbarer Vergleich zweier lokaler Exporte

```powershell
.\.codex_pytest_env\Scripts\python.exe scripts\compare_scanner_cohorts.py paired-export.json
```

Der Befehl liest nur die lokale JSON-Datei und schreibt das Ergebnis auf stdout.
Er ruft weder Provider, Server noch Tracker-Datenbanken auf. Er bewertet
exportierte Entscheidungen/Outcomes; er behauptet nicht, alte Scanner-Commits
automatisch neu ausgefuehrt oder Broker-Fills verifiziert zu haben.

Schema-Version 1 verlangt folgende gemeinsamen Manifestfelder:

- `schema_version: 1`, `scanner`, `market`, `direction` (`LONG`/`SHORT`),
  `horizon`, `timeframe`, `regime` (explizit `unknown` erlaubt).
- `input_kind`: `historical_export` oder `synthetic_fixture`.
- `window.start` / `window.end`: identische zeitzonenbehaftete ISO-Zeitgrenzen
  fuer beide Versionen; Beobachtungen liegen in [Start, Ende), Exits bis Ende.
- `versions.old` / `versions.new`: unterschiedliche exakte Commit-/Modell-IDs.
- `cost_policy`: explizite gemeinsame Kostenbeschreibung; keine stillen Defaults.
- `opportunities`: jede Gelegenheit genau einmal, einschliesslich Ablehnungen.

Jede Gelegenheit enthaelt `opportunity_id`, `ticker`, `observed_at`, einen
gemeinsamen nichtleeren `input`-Snapshot und `old`/`new`-Entscheidungen. Beide
Entscheidungen muessen dessen `input_sha256` nennen. Dieser SHA-256 wird mit
`input_fingerprint(snapshot)` aus sortiertem kanonischem JSON berechnet.
Beide benoetigen `selected` (Boolean) und `state`: `DECIDED`, `NO_FILL`,
`UNRESOLVED`, `MISSING` oder `NOT_SELECTED`.

`DECIDED` verlangt `entry_filled=true`, `exit_at`, `gross_r` und
`roundtrip_cost_r`; Netto-R = Brutto-R minus komplette Roundtrip-Kosten in
Einheiten des damaligen Anfangsrisikos. `NO_FILL` verlangt `entry_filled=false`.
Fehlende Outcomes/Kosten bleiben nicht verfuegbar, nicht Nullrendite oder Gewinn.
Synthetische Tests tragen niemals empirische Performance als verfuegbar aus.
Vollstaendige synthetische Schema-Beispiele stehen in
`test_scanner_cohort_comparison.py`.

Der Bericht zeigt Auswahlunterschiede, Outcome-Abdeckung, Netto-R, Gewinnquote,
binomiales Wilson-95%-Intervall, Profit-Faktor, Verlustserien und Drawdown in
gleichen R-Einheiten. Gleichzeitige Exits werden gemeinsam verbucht; deren
interne Trade-Reihenfolge bleibt unbekannt. Dies ist kein Portfolio-Drawdown.
Das Wilson-Intervall ist nicht um abhaengige/ueberlappende Trades bereinigt.

## Erforderliche Daten fuer einen exakten Live-Replay

Zusaetzlich zu den Manifestfeldern sind pro Gelegenheit notwendig: unveraenderte
damals verfuegbare OHLCV-Praefixe mit Provider-/Zeitstempel-/Abschlussnachweis,
identische Lookbacks und Corporate-Action-Behandlung, das damalige gesamte
Aktienuniversum einschliesslich spaeter delisteter Titel, 5m-/4h-Pfade,
point-in-time Quotes/Spread/Liquiditaet, Marktregime und Unternehmens-/Newsdaten,
alle Freigabe-/Ablehnungsgruende, der eingefrorene strukturbezogene Handelsplan,
Orders/Teilausfuehrungen/Exits und tatsaechliche Kosten. Nachtraeglich gewonnene
Information darf nicht in den Entscheidungssnapshot gelangen. Ohne diese Daten
bleibt exakte Live-Paritaet **nicht nachgewiesen**; der Tagesproxy ist kein Ersatz.
