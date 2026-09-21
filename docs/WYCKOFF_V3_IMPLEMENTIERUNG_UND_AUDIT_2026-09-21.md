# Wyckoff v3: lokale Umsetzung und unabhaengige Nachpruefung

Stand: 21.09.2026. Ausgangspunkt `bd67d83`, Modell `causal_wyckoff_v3`,
Regelversion `wyckoff_v3_rules_1`. Lokale technische Umsetzung und Audit
abgeschlossen; dieser Bericht ist kein empirischer oder Live-Server-Nachweis.
Keine Serveraenderung, Live-Scans, Providerabrufe, Signalmails oder Trades.
Private Exporte und Browser-Artefakte bleiben lokal unter `output/`.

## Umgesetzt

- Bestaetigte grosse und interne Auf-/Abschwuenge innerhalb derselben
  Zeiteinheit; Zeit-IDs statt positionsabhaengiger IDs. Der offene letzte
  Abschnitt bleibt gestrichelt und unbestaetigt. Strecke, TR-Skalierung,
  Kerzenzahl, echtes Gesamtvolumen und Volumen je Kerze sind nachpruefbar.
- Wiederholte ST/LPS/LPSY nur mit getrennten Gegenbewegungen; Plateau-Tiefs
  erst nach tatsaechlicher Bestaetigung. Vorlaeufige PS/PSY, Range-Seitentests,
  Spring/UTAD-Tests und Backup werden nur bei eigener Evidenz markiert.
- C ohne Spring/UTAD ueber einen hoeheren Tiefpunkt/tieferen Hochpunkt mit
  anschliessender Richtungsbestaetigung. In-Range-D ist Chartkontext, keine
  Umgehung des Ausbruch-/Ruecktestvertrags.
- Phase E braucht neue bestaetigte Fortsetzung nach dem Ruecktest, nicht
  bloss Zeitablauf oder einen einzelnen hohen/tiefen Schlusskurs.
- Akkumulation, Distribution, Reakkumulation und Redistribution;
  nichtklimaktische Urspruenge brauchen eigenen Vortrend-/Range-Nachweis.
  Kleinere Fortsetzungsranges koennen neben einer vorher belegten E-Struktur
  bestehen. Die kleinere Range ersetzt nicht die Historie der groesseren.
- Getrennte Struktur- und Einstiegszustaende. Abgelaufene, ausgestoppte,
  zielseitig verbrauchte oder intrabar uneindeutige Plaene erzeugen keinen
  neuen Einstieg aus dem alten Trigger. Bei Stop und Ziel in derselben Kerze
  wird keine Ausfuehrungsreihenfolge und kein Gewinn erfunden.
- API, Scannerfilter und Replay verwenden denselben expliziten Triggervertrag.
  Pflichtanker werden ueber IDs aufgeloest, nicht ueber den ersten gleichen
  Ereignisnamen. Altmodelle v1/v2 koennen keine v3-Freigabe wiederverwenden.
- Tracker speichert Struktur-/Trigger-/Ankeridentitaet. Mailidentitaet eines
  gueltigen Wyckoff-Triggers bleibt bei neuer Kurs-/Plangeometrie stabil.
  Bestehende Zustellungs-, Frische-, Native-Plan-, Risiko- und Kursgates bleiben
  zusaetzlich erforderlich. BI 17/20 und andere Scannervertraege unveraendert.
- Beide Charts zeigen Range, datierte Ereignisse, separat bestaetigte Phasen
  und optional interne Swings. Auf kleinen Displays verweisen Ereignisnummern
  auf die vollstaendige Tabelle. 1D-Scanner und andere Chart-Zeiteinheiten
  werden ausdruecklich getrennt, nicht als gegenseitige Bestaetigung verkauft.
- Replay bewertet getrennte Struktur-/Ereignislabels, Phasenuebereinstimmung
  und Verzoegerung. Unbeschriftete/unklare Faelle sind keine negativen Labels.
  Positive-only-Labels erzeugen keine erfundene Precision. Kosten/Ergebnisse
  fehlen explizit, statt mit null oder fiktiven Trades aufgefuellt zu werden.

## Nach der Implementierung gefundene und korrigierte Auditfehler

Die Kernengine, Replay und Frontend wurden getrennt umgesetzt und wechselseitig
geprueft. Die Hauptintegration wurde nochmals unabhaengig gelesen und reproduziert.

1. Listen/Objekte statt Statusnamen konnten Cache-/API-Pruefungen abbrechen:
   strikte Typpruefung und Fail-Closed-Tests.
2. Ein hoch bewerteter fehlerhafter Datensatz konnte einen gueltigen Trigger
   verdecken; fremde Teilmetadaten konnten die Mailidentitaet abbrechen:
   erst Belegvertrag pruefen, dann auswaehlen/identifizieren.
3. Ein bereits gestoppter Trigger konnte mit manipuliertem Ready-Flag oder
   veraendertem Planstop passieren: Triggerstatus und eingefrorener Stop gebunden.
4. ATR-Startwerte aus weggefallener irrelevanter Historie veraenderten den
   gleichen Stop: feste lokale 14 True Ranges mit dokumentierter zeitlicher
   Unterstuetzung, Faktor 0,25 unveraendert.
5. Ein Zielkontakt mit spaeterem Ruecklauf konnte denselben Einstieg erneuern;
   ein spaeterer Stop ueberschrieb die fruehere Beendigung: erster zeitlicher
   Beendigungsgrund bleibt am Trigger gespeichert.
6. Erste und wiederholte ST-Plateaus sowie vorab berechnete C-Bestaetigungen
   konnten zu frueh datiert/verwendet werden: Aktivierung erst zur belegten
   Bestaetigung, passende Ereignispreise auf der beobachteten Kerze.
7. Bereits vor dem Ursprung gescheiterte E-Strukturen wurden noch als Parent
   verwendet: historische Fehlerzeit statt bloss aktueller Status pruefen.
8. Engine-Phasenstatus und Frontendstatus passten nicht zusammen; Achsentitel
   und lange Ereignisnamen ueberlagerten Kurse: echte Engine-zu-UI-Tests und
   anschliessende Desktop-/Mobil-Pruefung mit kompakter Darstellung.
9. Kalibrierung konnte in die Holdout-Auswertung gelangen: ausdruecklicher
   Holdout-Beginn und passende Regelversion, kein stiller gemischter Score.
10. Auswertungsstichtage konnten trotz gueltiger Datenhashes veraendert werden:
    zusaetzliche Hashbindung fuer Zeitplan/Modell/Parameter und Manifest/Protokoll.
    `--prepare-freeze` berechnet nur einen Manifestentwurf, keine Ergebnisse;
    es bescheinigt weder unabhaengige Labels noch eine schon erfolgte Fixierung.

## Technische Pruefnachweise

- Gesamtsuite aller `test_*.py` im Projektwurzelverzeichnis: **5907 bestanden,
  4 uebersprungen, 0 Fehler**, 408,42 Sekunden. Darin **494 Wyckoff-Tests**.
  Isolierte Laufzeit-/Daten-/Authpfade, externe Sockets und SMTP gesperrt.
  Privater JUnit-Nachweis: `output/wyckoff-v3-full-suite.xml`.
- Die vier Skips betreffen einen unter Windows nicht erlaubten Symlinktest
  sowie drei Linux-O_NOFOLLOW/FIFO/Atomic-Rename-Pruefungen. Kein Wyckoff-Test
  wurde uebersprungen; diese Skips sind kein Linux-/Deployment-Nachweis.
- Finales Frontend-Bundle `5be25ee8c547`: gebaut, reproduzierbare
  Quelltext-Bindung geprueft, JavaScript-Syntaxpruefung bestanden.
- Zusaetzliche unabhaengige reine Gegenpruefung: sechs gespiegelte Faelle
  fuer erste ST-Plateaus, Zielkontakt vor spaeterem Stop und gleichzeitigen
  Stop-/Zielkontakt bestanden. Kein weiterer blockierender Befund in diesem Scope.
- Lokale synthetische Laufzeitstichprobe, Seed 9021: 40 Reihen mit je 180
  Kerzen, Median 5,97 ms / Maximum 8,87 ms fuer die Engine; keine Netzaufrufe.
  Das misst weder den gesamten Scanner noch eine Produktionslatenz oder Edge.
- `git diff --check` bestanden. Der Testlauf erfolgte vor der Veroeffentlichung
  auf dem hier dokumentierten Dateistand mit Ausgangs-HEAD `bd67d83`.
  Git-Veroeffentlichung und anschliessender Server-Pull sind getrennte Schritte.

Die lokale Browserpruefung benutzt ausschliesslich synthetische Kerzen und
die produktiven Komponenten/Projektionsfunktionen: 1280x900 und 390x844,
Phase E trotz passiertem Ziel, Short, interne Swings, Parent/Child und
Scanner-1D/Chart-4H-Trennung. Kein Zugriff auf die Produktiv-App.
Private Screenshots: `output/playwright/wyckoff-v3-desktop.png` und
`output/playwright/wyckoff-v3-mobile.png`. Der nur fuer diese Pruefung gestartete
Loopback-Server und der separate QA-Browser wurden danach beendet.

Gepruefte SHA-256-Dateistaende (ohne kuenstliche Commit-/Serverbehauptung):

| Datei | SHA-256 |
| --- | --- |
| `modules/wyckoff.py` | `1b1cb945dc594a4685043b9ecb61c67d003e778883f1e4e3c63d5b807a8cae80` |
| `modules/wyckoff_structure.py` | `bcd083c3195def001b19a076ff87c9c8b30788d5813e1c2d23f6812e6de7570f` |
| `modules/wyckoff_swings.py` | `ea5255ed8a8b09fce3dadb6daf6e66beb75f6c719fdfbee984a973d1343ffa0b` |
| `modules/wyckoff_contract.py` | `0e3b60b4a91d2030986d1f5f8fbaf1d50bd203a1baf15163be11ccd60a173ec5` |
| `scripts/evaluate_wyckoff.py` | `4a92fce9b687f96dfafea5a6725fc22ac6a38eacf891d1c5d25d5047cf25466f` |
| `frontend/index.html` | `79c8926e32ecb804037e3189a12b00c7417ecce1d0ec4192ac90c79e63aabff2` |
| `frontend/app.bundle.js` | `820c57f2eedd078773a65da70036f54da449aec1a4ffc415f1ef38373c4f076e` |

## Grenzen und Freigabe

Die Regeln sind explizite unkalibrierte Heuristiken, keine vollstaendige oder
universell richtige Wyckoff-Lehre. Grosse/interne Swings sind hier kausal
ATR-skaliert, keine rekursive Weis-Wave- oder Elliott-Wave-Erkennung.
Phasengrenzen bleiben interpretierte Zeitraeume; Preise und Bestaetigungszeiten
sind die beobachtbaren Belege. Volumen ist kein Kauf-/Verkaufsdelta.

Maximal 720 Kerzen und 24 Strukturkontexte je Richtung. Der Kontextkatalog ist
deshalb nicht beliebig weit rueckwirkend vollstaendig. Abgeschnittene historische
Eltern werden nicht aus fehlenden Daten rekonstruiert. Nach gescheitertem
Ausbruch wird derselbe alte Ausbruch nicht recycelt; ein neuer separater
Strukturaufbau muss wieder den Vertrag erfuellen.

**Empirische Freigabe weiterhin offen:** Im zuvor begrenzt geprueften lokalen
Bestand lag kein eingefrorener realer OHLCV-Datensatz mit unabhaengigen
Wyckoff-Labels und dazu passenden Ausfuehrungs-/Kostenbelegen vor. Die technische
Erweiterung ersetzt diesen Nachweis nicht. Weder verbesserte Precision/Recall
noch bessere Trefferquote/Nettoerwartung werden behauptet.

Vor Freigabe: vorab festgelegte Stichprobe und kalendarisch konsistente Daten,
unabhaengige positive/negative/unklare Labels, ungeaendertes v2 gegen v3 auf
demselben Holdout und anschliessend getrennter realistischer Kostenvergleich.
Vorgehen/Dateiformate: [WYCKOFF_VALIDATION.md](WYCKOFF_VALIDATION.md).
Dieser Bericht fuehrt keinen Server-Pull aus; das Serverupdate und dessen
Health-Pruefung erfolgen separat auf ausdruecklichen Nutzerauftrag.
