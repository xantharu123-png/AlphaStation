# Scanner-Reparatur und unabhängige Nachprüfung — 23.09.2026

Ausgangsrevision: `852cb3b6d38bf6f2fd062408cc404701c867b013`.
Bezugsbericht: privates Gesamtaudit `output/deep_audit_20260923/AUDIT_ALL_SCANNERS_20260923.md`.
Dieser Bericht enthält keine privaten Signalzeilen, Kontodaten oder Zugangsdaten.

## Vertrag

- BI bleibt mindestens 17/20 plus harte Gegenindikationen. Keine Ersatz-Watchlist.
- Ein abgeschlossener bestätigter Ausbruch ohne späteren Rücktest bleibt mit Warnung zulässig. Henkel, Flaggenkorrektur und MA-Bounce sind eigenständige Musterbestandteile und bleiben erforderlich.
- Aktien-Swing bleibt auf abgeschlossenen 1D-Sessions; Intraday-ORB, Penny und kurzfristige Krypto-Trigger benötigen ihre eigene Frische.
- Modellposition, Signal, Mailannahme und Broker-Ausführung bleiben getrennt.
- Keine echten Providerabfragen, SMTP-Nachrichten, Orders, Produktionsscans oder Serveränderungen während dieser Reparatur.

## Umgesetzte Befunde

| Audit | Reparatur | Dauerhafte Gegenproben |
| --- | --- | --- |
| F01 ORB | Abgeschlossene Triggerkerze maximal 15 Minuten alt, aktuelle Quote auf korrekter Ausbruchsseite; dieselben Prüfungen bei erneuter Cacheanzeige, explizites NO_TRADE bei Verletzung | `test_stock_audit_repair_contracts.py` |
| F02 Penny | Modell-TP1/EXIT sofort atomar vor Folgesymbol/Cache/SMTP speichern; Nachricht separat mit Ereignis-ID, Ablauf, Retry und Unknown-Zustand; konkurrierende Entry-Reservierungen positionsgebunden | `test_penny_model_event_persistence.py`, `test_penny_management_durability.py` |
| F03 New Listing | Beobachtungs- und Kerzenschlusszeit durch Adapter erhalten; Direct/Combined gemeinsam bei Anzeige neu bewerten | `test_crypto_group_b_repair_contract.py` |
| F04 Compression | Kausale vorherige Range, echter abgeschlossener Ausbruch und Volumenexpansion statt allein enger Range | `test_deep_audit_shared_fixes.py` |
| F05 Flag | Impuls, Tiefe, begrenzte Flaggenrange und Volumenkontraktion in beiden Richtungen prüfen; kein zusätzlicher späterer Retest-Zwang | `test_deep_audit_shared_fixes.py` |
| F06 Datenfehler | Turtle/Volume-Spikes/Pflichtquellen-Biotech/MoneyFlow sowie Bear-Komplettausfall melden Fehler statt erfolgreichen Nullscan; letzter guter Cache bleibt erhalten | `test_stock_audit_repair_contracts.py`, `test_context_scanner_repair.py` |
| F07 MA | EMA21/SMA50/SMA200 nach jeweils tatsächlich benötigter Historie prüfen, kein pauschales SMA200-Veto für ausreichendes EMA21-Profil | `test_stock_audit_repair_contracts.py` |
| F08 Renditen | Exakt fünf/zwanzig Sessions Abstand unabhängig davon, ob heutige Session bereits in Historie steckt; gleicher 5D-Bezug im chronologischen Momentum-Backtest | `test_stock_audit_repair_contracts.py`, `test_momentum_backtest_parity.py` |
| F09 Turtle | Einheitlicher abgeschlossener Swing-Referenzschluss mit zugehörigen Kennzahlen; aktuelle Snapshotdaten separat, kein erfundener Fill | `test_stock_audit_repair_contracts.py` |
| F10 Kalender | VRVP, Aktienbars sowie BI/Biotech verwenden tatsächliche Sessionenden einschließlich Feiertagen, DST und verkürzten Tagen | `test_deep_audit_shared_fixes.py`, `test_context_scanner_repair.py`, `test_stock_bar_integrity.py` |
| F11 Märkte | Futures/Forex/International ausdrücklich nicht implementiert: HTTP501 mit Capabilityvertrag, kein Aktien-Ersatzscan; Auswahl deaktiviert und Guide gekennzeichnet | `test_crypto_group_b_repair_contract.py` |
| F12 Crypto-Risiko | Crowded Funding sperrt den Explosion-Soforttrigger; Combined übernimmt HIGH bzw. ausdrücklich nicht freigegebene Rows nicht als JETZT_LONG | `test_crypto_group_b_repair_contract.py` |
| F13 Narrative | Fehlende/ungültige gewichtete Horizonte ergeben unbekannte Bewertung; tatsächliche Null bleibt Null; Teilabdeckung wird ausgewiesen | `test_context_scanner_repair.py` |
| F14 Outbox | Dedupe-Prüfung und Einfügen atomar unter SQLite-Transaktion | `test_deep_audit_shared_fixes.py` |
| F15 Outbox-Zeit | Ablauf unmittelbar vor jeder Zustellung erneut prüfen; echte Zustellzeit statt Beginn des Batches; unklarer SMTP-Ausgang wird nicht blind erneut versandt | `test_deep_audit_shared_fixes.py` |
| F16 R:R | Schwellen auf ungerundeten Distanzen mit enger numerischer Toleranz für mathematisch exakte Grenzwerte; nicht-endliche/überlaufende Ergebnisse abweisen; Anzeige erst anschließend runden | `test_deep_audit_shared_fixes.py` |
| F17 Harmonic | Kausale Zeitfelder des echten Detektors und Chartadapters stimmen überein | `test_deep_audit_shared_fixes.py` |
| F18 Wolfe-Chart | Konsistentes Indexschema bis zum tatsächlichen Chart-Endpunkt | `test_deep_audit_shared_fixes.py` |
| F19 Wolfe-Geometrie | Positive, zeitlich abnehmende Kanalbreite und zulässiger Schnittpunkt statt invertiertem Steigungsvergleich | `test_deep_audit_shared_fixes.py` |
| F20 Market Weather | Quellzeit/Quellalter durch abgeleitete Caches erhalten; alte, zukünftige oder ungültige Quelle wird nicht durch Neuberechnung frisch | `test_context_scanner_repair.py` |
| F21 Inverse ETF | Fehlende Mehrtagesrenditen/RVOL bleiben null/unbekannt; gemischte bekannte/unbekannte Werte sortierbar | `test_context_scanner_repair.py` |
| F22 BTC-Divergenz | Unvollständiges Universum und Quellenwarnung bleiben in Cache/API erhalten; weiterhin Kontext, kein neues Handelssignal | `test_crypto_group_b_repair_contract.py` |

Zusätzlich umgesetzt:

- NewListing-Short-Caches versioniert und vollständiger Vertrag statt Vertrauen auf einzelne Booleans. Ältere Caches werden nicht zu Sofortsignalen hochgestuft; der nächste erfolgreiche reguläre NewListing-Lauf erzeugt das neue Format.
- Combined-Crypto erhält getrennte vollständige Börsenalternativen. Auswahlpolitik und Risikorang, Score, R:R, Funding, Spread und Quelle sind nachvollziehbar; Preise verschiedener Börsen werden nicht kombiniert.
- Kostenmetadaten unterscheiden `net`, `after_known_costs` und `gross_no_cost_data`. Keine erfundenen Gebühren/Slippage und kein neues pauschales Kostengate (`test_cost_coverage_contract.py`).
- Fibonacci-Anzeigen nennen 23.6/38.2/61.8/78.6/127.2/161.8 Prozent korrekt. Die Berechnung blieb dieselbe.
- Veraltete „nur Retests“-Warntexte und Wyckoff-Guide-Texte wurden an den vereinbarten Warnungsvertrag angepasst.
- Fehlende UI-Zahlen werden nicht als Null, positive Null oder vorhandene Provenienz ausgegeben.

Die F12-Korrektur betrifft den reproduzierten Risiko-/Funding-Widerspruch und die Combined-Normalisierung. Sie setzt Scanner-Muster und Mailfreigabe nicht pauschal gleich: Der Explosion-Producer hat weiterhin eine gesonderte höhere R:R-Bedingung für `alertable_crypto`. Aus einem Scanner-Trigger wird deshalb nicht automatisch eine Mailfreigabe.

## Zusätzliche Befunde der Nachprüfung

Nicht nur die ursprünglichen Fehlerfälle wurden umbenannt: unabhängige Gegenproben fanden weitere Randfälle, die in den Reparaturzyklus zurückgingen:

1. Penny-Management konnte vor finaler Batch-Speicherung durch einen Fehler beim nächsten Symbol verloren gehen. Jetzt sofortige Persistenz.
2. Verspätete Discovery-/Mailupdates konnten eine inzwischen aktive andere Modellposition ersetzen. Jetzt positionsgebundene Reservierung/Abschluss und Schutz vor alten Bestätigungen.
3. Unbekannte Inverse-ETF-Renditen konnten die Sortierung abbrechen. Bekannte und unbekannte Werte werden getrennt sortiert.
4. Zukunfts-/Offset-Zeitstempel wurden fälschlich als frisch bzw. ungültig behandelt. Quellzeitprüfung verwendet echte Zeitdifferenzen und akzeptiert keine Zukunft als Alter null.
5. Narrative unterschied boolesche Werte im Score, nicht aber im Abdeckungsstatus. Beide Verträge sind jetzt konsistent.
6. Der gemeinsame Kalender enthielt einen falschen frühen Handelsschluss am 2. Juli 2026. Laut [NYSE-Handelskalender](https://www.nyse.com/trade/hours-calendars) ist der 3. Juli 2026 geschlossen; die frühen Schlusstage 2026 sind der 27. November und der 24. Dezember. Die betroffenen Kalender und Gegenproben wurden korrigiert.
7. Crypto-Risikosortierung stufte unbekanntes Risiko fälschlich wie hohes Risiko ein; ein ausdrücklich unsicherer Short konnte trotz LOW-Label gewinnen. Alte reale Kerzenschlusszeiten konnten zusätzlich durch fehlende Cachezeit zu jung erscheinen. Alle drei Fälle wurden korrigiert und in der unabhängigen Annahmeprüfung mit echten Direct-/Combined-Adaptern erneut geprüft: 55 Tests bestanden, einschließlich 600-/601-Sekunden-Grenze und vollständiger Börsenalternativen.
8. Der Momentum-Backtest verwendete nach der Live-Korrektur noch den alten 5D-Bezug. Beide Pfade verwenden jetzt denselben zeitlichen Abstand; zusätzliche Prefix- und Unveränderlichkeitsprüfungen sichern ihn ab.
9. Eine mathematisch exakte 1.5R-Geometrie konnte durch binäre Gleitkommaabweichungen als 1.499999999999997R abgewiesen werden. Eine enge Toleranz korrigiert ausschließlich diese numerische Grenze; echte 1.495R-/1.4999R-Pläne bleiben unter der Schwelle.
10. Ein alter Penny-HOLD-Snapshot konnte nach EXIT und Neueinstieg den neuen aktiven Eigentümer ersetzen. Die positionsgebundene Zusammenführung schützt jetzt auch diese Reihenfolge; alte gesendete bzw. ausstehende Mailbestätigungen verändern die neue Position nicht.

## Verifikation

Gezielte und unabhängige Offline-Prüfungen sind Teilmengen des Gesamtbestands und werden nicht zu dessen Gesamtzahl addiert. Der Launcher isoliert Datenbanken/Laufzeitpfade und sperrt externe Sockets sowie SMTP. Windows-/Linux-spezifische Grenzen werden nicht als erfolgreicher Serverlauf ausgegeben.

Vollständiger Reparaturlauf vor der anschließenden Erweiterung der Scanner-Steuerung: **7.041 bestanden, 0 Fehler, 4 übersprungen**, 687.31 Sekunden. Befehl:

```powershell
.\.venv\Scripts\python.exe output/run_offline_repair_tests.py -q --ignore=output --ignore=archive --ignore=tmp --junitxml=output/deep_audit_20260923/repair_final_full.xml
```

Die vier übersprungenen Fälle sind drei Linux-spezifische Dateisystem-/Deployverträge und ein unter diesem Windows-Konto nicht verfügbarer Verzeichnissymlink-Test. Sie wurden nicht als bestanden gezählt. Nachweis: `output/deep_audit_20260923/repair_final_full.xml`.

Die unabhängige Krypto-Annahmeprüfung besteht zusätzlich mit 55 Fällen (teilweise Überschneidung mit dem Gesamtbestand); die separat erneut geprüfte Penny-/Aktien-/Exportgruppe mit 403 Fällen ebenfalls. Diese Zahlen werden ausdrücklich nicht zum Gesamtbestand addiert. Die unabhängige Berichtsnachprüfung bestätigt die Zuordnung F01–F22; die Aussage zu F12 wurde auf ihren tatsächlich geprüften Risiko-/Funding- und Combined-Vertrag präzisiert.

Der frühere Integrationslauf `repair_full.xml` hatte zehn Fehlschläge, darunter echte Backtest-/R:R-Grenzfehler, anzupassende Vertragsfixtures und durch gleichzeitige Quelltextänderungen verfälschte Source-Inspection-Tests. Er bleibt als Historie erhalten und ist nicht der Abschlussnachweis. Nach Korrekturen erfolgte der obige neue Gesamtlauf ohne weitere Code-/Teständerung.

Gerenderte Nachprüfung mit dem Playwright-Skill an den tatsächlichen Frontend-Dateien und einer isolierten lokalen API-Fixture:

- Desktop 1440×1000 und Mobil 390×844: Narrative zeigt fehlende Horizonte/Flowdaten als unbekannt statt Null/BULLISCH; bekannte Werte bleiben sichtbar.
- Combined-Crypto zeigt die vollständigen getrennten Venue-Pläne auch im aufgeklappten Mobilzustand. Unbekannte Zahlen/Quellen werden nicht erfunden. Breite Tabellen scrollen innerhalb ihrer Karten; kein horizontaler Überlauf der gesamten Seite.
- Kein JavaScript-Laufzeitfehler im geprüften Ablauf. Der vorhandene Tailwind-CDN-Produktionshinweis bleibt eine davon unabhängige technische Altlast.
- Private Bildnachweise: `output/playwright/repair-narrative-desktop.png`, `repair-narrative-mobile.png`, `repair-crypto-venues-desktop.png`, `repair-crypto-venues-mobile.png`.
- Frontend-Bundle mit `scripts/verify_frontend_bundle.py` gegen den Quelltext geprüft: `004570c462be`.

Die Browserprüfung hat ausdrücklich reale gerenderte Zustände geprüft; sie behauptet weder einen Produktionsscan noch die Anmeldung/Installation auf Hetzner. Der lokale Fixture-Server und der eigene Testbrowser wurden anschließend geschlossen.

## Folgeauftrag: Scanner-Steuerung und Veröffentlichungsprüfung

Die vorherige UI-Vereinheitlichung besaß nicht für jeden Discovery-Scanner einen echten Pausenvertrag. Dieser wurde ergänzt; die genaue Abdeckung steht in `docs/SCANNER_CONTROL_COVERAGE_2026-09-23.md`.

- BI und generische Aktienstrategien behalten ihren vorhandenen Fortsetzungsvertrag für denselben gültigen Datenstand.
- Biotech, Bear, Turtle, ORB, Penny-Discovery, Volume Spikes, Money Flow, Early Movers, BTC-Divergenz, Crypto Explosion und generische Kryptostrategien unterstützen kooperative Pause. Beim Fortsetzen starten sie mit frischen Daten; es gibt keinen parallelen Ersatzworker.
- Crypto-Explosion-Kindjobs werden vor bestätigter Pause vollständig beendet. Finale Cache-/Mail-Publikation ist gegen zu spät eintreffende Pausenbefehle versiegelt.
- Schutzmonitore sowie New Listing/Combined mit integrierter Positions-/Stop-Pflege sind ausdrücklich ausgenommen. Die Oberfläche zeigt die Begründung und bietet keine ausführbare Pause für diese Jobs an.
- Dedizierte Scanner lesen echte Owner-/Lauf-IDs. Ein POST-Erfolg ist noch keine bestätigte Worker-Pause. Veraltete, fehlende oder fehlgeschlagene Statusantworten führen nicht zu erfundenem Stillstand.
- Generische Strategieauswahl und tatsächlicher dedizierter Worker verwenden denselben Steuerungs-Owner. Adminverlust, Ansichtswechsel und alte GET-/POST-Antworten können keinen fremden Lauf steuern.
- Gefundene UI-Randfehler wurden korrigiert: fehlender Parent-Status, veraltetes `isScanning`, nicht bestätigter Worker und liegen gebliebene Fehlermeldung nach verspäteter Pausenbestätigung.

Gezielte Backend-Prüfung: 698 bestanden. Unabhängige Backend-Gegenprüfung: 267 bestanden. Dedizierte Frontend-Gegenproben: 35 bestanden, gemeinsam mit den Scan-Aktionstests 69. Diese überlappenden Teilmengen werden nicht zum Gesamtbestand addiert.

Erneute gerenderte Browserprüfung mit Playwright an lokaler Fixture: Pause angefordert, vom Worker bestätigt, Desktop 1440×1000 und Mobil 390×844 geprüft, anschließend frischer Lauf durch Fortsetzen. Combined-Crypto zeigt den geschützten Status. Kein Seitenüberlauf und kein JavaScript-Laufzeitfehler; vorhandener Tailwind-CDN-Hinweis bleibt bestehen. Nachweise: `output/playwright/pause-biotech-desktop.png`, `pause-biotech-mobile.png`, `pause-protected-crypto-mobile.png`. Nur lokale synthetische Zustände, kein Produktionsnachweis. Browser und Fixture wurden geschlossen.

Der erste neue Gesamtlauf (`release_final_full.xml`) ergab 7.260 bestanden, 4 übersprungen und einen fehlgeschlagenen alten Negativtest: `crypto_strat_*` wurde dort noch als unbekannter Steuerungs-Owner behandelt. Die unabhängige Prüfung der Producer bestätigte dagegen einen echten, run-gebundenen Live-Partial-Vertrag für generische Kryptostrategien. Dieser bleibt erhalten. Zugleich wurde die Timeout-Ausnahme ausdrücklich auf BI und generische Aktien-/Kryptostrategie-Producer begrenzt, damit die neue Pausefähigkeit dedizierter Scanner nicht unbeabsichtigt eine Teilresultat-Freigabe bedeutet. Positive Krypto- und negative Dedicated-Gegenproben sichern beide Seiten ab.

Aktuelles Bundle nach dieser Präzisierung: `540078fa6755`, gegen den Quelltext geprüft. Der vollständige Testbestand wurde erneut in vier disjunkten Gruppen ausgeführt: Kernbestand ohne zwei Deploy-Dateien, gerade/ungerade Testindizes der 71 Auto-Update-Fälle sowie die 51 Security-Reaudit-Fälle. Jede Gruppe verwendete eigene isolierte Laufzeit-/Datenbank-/Tempverzeichnisse; keine Quell- oder Teständerungen während ihrer jeweiligen Abschlussprüfung. Die Deploy-Quellen blieben auch während der vorausgehenden Frontend-Präzisierung unverändert.

### Abschließender vollständiger Nachweis

**7.272 bestanden, 0 Fehler, 4 übersprungen; 7.276 eindeutige Testfälle.**

| Gruppe | Bestanden | Übersprungen | Dauer | Privates XML unter `output/deep_audit_20260923/` |
| --- | ---: | ---: | ---: | --- |
| Kernbestand | 7.151 | 3 | 324,36 s | `release_final_core.xml` |
| Auto-Update, gerade Indizes | 35 | 1 | 440,57 s | `release_final_deploy_a.xml` |
| Auto-Update, ungerade Indizes | 35 | 0 | 546,72 s | `release_final_deploy_b.xml` |
| Deploy-Sicherheitsnachprüfung | 51 | 0 | 213,77 s | `release_final_deploy_security.xml` |

Frische Pytest-Collection und XML-Vereinigung stimmen überein: null fehlende, null zusätzliche und null doppelte Testfälle. Nur der bekannte dynamische Parameter eines einzelnen Zeitstempeltests wurde für den Identitätsvergleich kanonisiert; `None`, ungültiger Text und echter Offset-Zeitstempel bleiben drei getrennte Fälle. Der Test selbst wurde nicht verändert oder übersprungen. Die vier Plattform-Skips bleiben die oben dokumentierten drei Linux-Verträge und ein Windows-Verzeichnissymlink-Fall.

Unveränderter Manifest-Hash der 343 Quell-/Testdateien vor und nach dem abschließenden Kernlauf: `08219AD7ECB59105078E568B316C28F098336236944A70D59F9CB2060F1237B9` (SHA-256 über sortierte Pfade und Dateihashes). Die endgültige Bundle-Prüfung und `git diff --check` sind erfolgreich. Unabhängige Veröffentlichungsprüfung fand keine privaten Exporte, erkannten Zugangsdaten oder sachfremden Aktivierungen im vorgesehenen Diff; Broker-/Live-Trading-Aktivierungsdateien sind unverändert.

Bei der abschließenden Indexprüfung wurden ausschließlich drei überzählige Leerzeilen am Ende von `test_penny_model_event_persistence.py` entfernt. Beide Tests dieser Datei wurden danach erneut erfolgreich ausgeführt; keine inhaltliche Test- oder Produktionscodeänderung. Diese Wiederholung wird nicht zusätzlich zu den 7.272 eindeutigen bestandenen Fällen gezählt.

## Nicht als erledigt ausgegeben

- Hetzner ist durch diese lokale Reparatur nicht aktualisiert oder live verifiziert.
- Eine echte neue Mailzustellung und vollständige Produktionsläufe sind separate Betriebskontrollen nach einer freigegebenen Installation.
- Futures/Forex/International wurden nicht neu gebaut, sondern ihr nicht implementierter Zustand korrekt offengelegt.
- Der persönliche Tagesrisiko-Baustein bleibt OFFLINE_ONLY. Ohne verlässlichen Kontostand, offene reale Positionen und Ausführungsereignisse wird kein vermeintlicher scannerübergreifender Kontoschutz aktiviert.
- Pause/Fortsetzen ist für die oben genannten Discovery-Worker umgesetzt. New Listing/Combined mit integrierter Positionspflege sowie Schutzmonitore bleiben ausdrücklich ausgenommen; ihre sichere Trennung ist kein Bestandteil dieser Freigabe.

Keine privaten Exporte, QA-Datenbanken oder lokalen Screenshots gehören in einen öffentlichen Commit.

Der Nutzer hat Commit und Push sowie die Ausgabe des Server-Pull-Befehls beauftragt. Vor Veröffentlichung werden Gesamttest, Diff, Bundle und ausschließlich explizit ausgewählte Quell-/Test-/Dokumentdateien geprüft. Keine Serverinstallation durch diese lokale Reparatur. Persönlicher Kontoschutz bleibt ohne Broker-/Konto-Zuordnung inaktiv; die vorhandenen Schutzpipelines werden nicht abgeschaltet.
