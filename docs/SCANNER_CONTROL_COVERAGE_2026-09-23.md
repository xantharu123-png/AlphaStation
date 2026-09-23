# Einheitliche Scanner-Bedienung: Aussehen und Faehigkeiten

## Auftrag und Betriebsstand

Der aktuelle Auftrag vereinheitlicht die sichtbare Bedienung aller Scanner.
Er erweitert **nicht** pauschal deren Backend um Pause/Fortsetzung.

Die frische oeffentliche Health-Antwort vom `2026-09-23T07:36:12.553192` UTC
meldete `healthy`, Revision `3c667b6f165d` und Frontend `5be25ee8c547`.
Auch die gelesene HTML-Antwort auf Port 3000 enthielt weder `Stop / Pause` noch
`scannerControlAction` oder die neuen CSS-Regeln. Der lokale Git-Stand
`da57a8970bd5` war damit zu diesem Pruefzeitpunkt noch nicht ausgerollt.
Die neue lokale Oberflaeche ist kein Nachweis ihrer Verfuegbarkeit auf dem Server.

## Einheitliches lokales Erscheinungsbild

`ScanControl` zeigt den Button `Stop / Pause` durchgehend an, auch ohne aktiven
oder steuerbaren Lauf. Aktiv wird er nur mit bestaetigtem, berechtigtem
Steuerungsvertrag. Ohne laufenden Scan bleibt er deaktiviert; bei einem laufenden
Scanner ohne sichere Pausenunterstuetzung nennt die Oberflaeche diesen Grund.
Ein bestaetigt pausierter Lauf bietet `Fortsetzen`. ORB verwendet nun ebenfalls
dieselbe Komponente statt einer eigenen Scan-Aktionsleiste.

Gleiche Darstellung bedeutet nicht gleiche Backend-Faehigkeit. Ein deaktivierter
Button sendet keinen vermeintlichen Stop-Befehl. Fehlende Admin-Rechte, unbekannte
Laufidentitaet und noch unbestaetigte Steuerung werden nicht umgangen.

## Tatsaechliche Backend-Abdeckung

Die unveraenderten Allowlists stehen in `api.py::_scan_control_supported` und
`modules/scan_control.py::_supported`.

| Scanner / Owner | Sichere Pause/Fortsetzung heute |
| --- | --- |
| BI Long und BI Short (`bi_long`, `bi_short`) | Ja: sichere Universums-/Kandidaten-Grenzen und Abschlussversiegelung. |
| Generische Aktienstrategien (`strat_*`), darunter Momentum, Gap, Cup und Wyckoff | Ja: gemeinsame Scan-Implementierung mit sicheren Pruefgrenzen. |
| Automatische Aktienrunde (`strategy_scan`) | Ja, fuer die gesamte Runde; nicht nur fuer die gerade geoeffnete Strategie. |
| Biotech, Bear, Turtle, ORB, Penny-Discovery, Volume Spikes, Money Flow | Nein: dedizierte Wrapper sind nicht freigeschaltet. Vorhandener Fortschritt oder Teilcache ist kein Pausenvertrag. |
| Generische Crypto-Strategien, Early Movers, BTC Divergence, Crypto Explosion, New Listing, kombinierte Crypto-Signale | Nein: nicht freigeschaltet; teilweise zusaetzliche Parallelitaets-/Pflegegrenzen. |
| Crash Monitor, Market Context, Penny Positions, Cup Watch, Quote Capability | Keine gemeinsame Scanner-Pause; Risiko-, Positions- und Kontrollaufgaben bleiben getrennt. |

Momentum ist somit bereits backendseitig kontrollierbar. Bei einem manuellen
Lauf ist dessen `strat_*`-Owner massgeblich; waehrend der automatischen Runde
meldet die Ergebnis-API den aktiven `strategy_scan`-Owner. Ein fehlender Button
allein beweist keine fehlende Momentum-Backend-Faehigkeit.

## Grenzen einer spaeteren Faehigkeitserweiterung

Eine reine Erweiterung der Allowlist waere unzureichend. Dedizierte Scanner
brauchen sichere Pausenpunkte ausserhalb von IO-/Datei-/Mail-Locks, eine
Abschlussversiegelung vor finalen Seiteneffekten, passende Daten-Epochen und
zuverlaessige Lauf-ID-/Start-/Ergebnisantworten. Live-/Intraday-Daten duerfen
nach einer Pause nicht ungeprueft weiterverwendet werden. Der bisherige
Stock-Tagesdaten-Token und die Stock-Wochenendplanung passen nicht pauschal
zu Crypto oder Nachrichten-/Katalysator-Scans.

Besonders zu schuetzen sind:

- **Crypto Explosion:** Bis zu vier Venue-Worker laufen parallel. Der Controller
  ist threadlokal; ein einzelner geparkter Worker belegt keinen pausierten
  Gesamtlauf. Erst koordinierter Stillstand und Join erlauben diesen Status.
- **New Listing:** Dieselbe Pipeline pflegt auch Stops und Ablauf offener
  Signale. Discovery muss vor einer Pause von dieser laufenden Pflege getrennt
  werden. Kombinierte Crypto-Scans rufen ausserdem Quell-Wrapper direkt auf;
  gemeinsame Eigentuemerschaft und Abschlussgrenzen sind separat zu klaeren.
- **Positions-/Schutzaufgaben:** Penny-Positionspflege, BG-Signal-Tracker,
  Outbox, Cup-Watch- und Risikokontrollen duerfen nicht durch eine globale
  Scanner-Pause oder ausgeweitete Heavy-Scan-Sperre angehalten werden.

Diese Punkte sind zukuenftige Backend-Arbeit, nicht Bestandteil des aktuellen
Darstellungs-Patches. Signalregeln, Schwellen, Mail-/Tracking-Gates und die
bisherigen kooperativen Pausenrechte bleiben unveraendert.

## Pruefgate und offene Nachweise

Der genaue Backend-Control-Regressionsgate ist:

```powershell
& .\.venv\Scripts\python.exe -B output\run_offline_repair_tests.py test_scan_control.py test_scan_control_api.py -q
```

Er prueft unter anderem Admin-/Run-ID-Schutz, reale Worker-Pause/Fortsetzung,
Epochenwechsel, Abschlussgrenzen, ausgeschlossene Pausenzeit und dass geparkte
Aktien-Discovery Crypto-/Penny-Positionsjobs nicht blockiert. Bestehende Tests
fuer nicht unterstuetzte Scanner muessen weiterhin deren Ablehnung belegen.

## Abgeschlossene lokale Pruefung

- **568 Tests bestanden:** saemtliche `test_frontend*`, `test_orb*`,
  `test_live_scan_progress*`, `test_scan_control*` und `test_bi_scan_pause*`.
  Darin enthalten sind die 78 Backend-Control-/BI-Pausenregressionen.
  Dies ist ein gezielter Gate, kein neuer Gesamtlauf aller Projekttests.
- Frontend neu gebaut und auf Uebereinstimmung mit dem Quelltext geprueft:
  Bundle-Quellhash `b293299dc48f`.
- Playwright-Skill mit isolierter lokaler Testoberflaeche: Momentum bei
  7306/12590 (58 Prozent), fehlender Trefferzahl und gespeichertem Altstand;
  ORB ohne Backend-Pausenvertrag. Desktop 1280x960 und Mobilformat 375x960
  tatsaechlich gerendert und visuell geprueft. Keine horizontalen Ueberlaeufe
  im Mobilformat; Buttonbeschriftung horizontal und vertikal zentriert.
- Pause und Fortsetzen im lokalen Momentum-Test durchgeklickt. ORB zeigt den
  gleichen, aber deaktivierten Button mit sichtbarer Erklaerung. Keine
  Anwendungsfehler im Fixture-Fehlerprotokoll.

Private Nachweise liegen unter `output/playwright/uniform-momentum-desktop.png`,
`uniform-momentum-mobile.png`, `uniform-orb-desktop.png` sowie
`output/scanner-uniform-final-20260923.xml`; sie werden nicht mit veroeffentlicht.

## Fortschrittsvertrag und verbleibender Servernachweis

Beide Strategie-Fortschrittsanzeigen verwenden denselben ausgewaehlten Stand.
Fehlende Trefferzahlen werden weder als `undefined` noch als bestaetigte Null
ausgegeben. Werte anderer Laeufe, BI-Richtungen oder Strategien einer gemeinsamen
Runde werden nicht ausgeliehen. Alte Ergebniszahlen tragen ausdruecklich die
Kennzeichnung `Gespeicherter Altstand`. Ein unbekannter Fortschritt erscheint
als begrenztes bewegtes Segment statt als scheinbar vollstaendiger Balken;
die Einstellung fuer reduzierte Bewegung wird beachtet.

Es wurden keine Produktionsscans gestartet, pausiert oder neu gestartet und
keine Serverdateien veraendert. Der interne Browserzugriff scheiterte am lokalen
Windows-Hilfsprozess; die Produktionsrevision wurde separat lesend per HTTP
geprueft. Ausrollen, anschliessend ausgelieferte Revision/Bundle und reale
Steuerung unter passender Admin-/Laufidentitaet pruefen, bleibt ein eigener
Nachweis. Ein Dienst-Neustart beendet dabei bereits laufende Scans.
