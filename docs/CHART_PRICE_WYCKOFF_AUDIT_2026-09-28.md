# Chartkurs, Planbewertung und Wyckoff – Reparatur und Nachprüfung

Stand: 28.09.2026. Ausgangspunkt `c3594ff12d57`.

## Nachgewiesene Fehler

1. **Zwei verschiedene Kursstände in einer Aussage.** In der angemeldeten
   VIAV-Seitenleiste stand beim Audit ein 4H-Chartkurs von 39,13 USD neben
   einem gespeicherten Entry von 40,68 USD und Stop von 39,29 USD. Trotzdem
   erschien die frühere Bewertung „Jetzt traden (98/100)“, mit Distanz 0R.
   Der aktuelle Chartkurs und die Bewertung gehörten nicht zusammen.
2. **Wyckoff war an den allgemeinen Patterns-Schalter gekoppelt.** Dadurch
   wurden beim Momentum-Scanner auch Wyckoff-Linien, historische Fehlerzustände
   und lange Einzelbeschriftungen eingeblendet. Die acht sichtbaren VIAV-Einträge
   waren keine acht aktuellen Signale: sieben waren als gescheitert markiert,
   eine bestätigte historische Distribution hatte einen abgelaufenen Einstieg.
3. **Spring-/UTAD-Erholung übersprang Zwischenkerzen.** Eine zunächst erlaubte
   Grenzverletzung konnte nach späterer Erholung gültig wirken, obwohl zuvor
   ein tiefer oder volumenstarker Gegenbruch erfolgt war. Nach erfolgtem Ausbruch
   musste zudem der erste Gegenbruch die Struktur endgültig entwerten.
4. **Bestätigung zu früh datiert.** InRangeSOS/InRangeSOW konnte auf `index + 1`
   datiert sein, obwohl das Plateau erst später tatsächlich bestätigt wurde.
   Bei ausgebliebener Erholung wurde ein Fristablauf außerdem auf die erste,
   zunächst noch erlaubte Grenzverletzung zurückdatiert.

## Umsetzung

- Aktienseitenleisten vergleichen den letzten passenden, nicht als veraltet
  markierten Chartkurs mit den unveränderten Planleveln. Richtung, Stopkontakt,
  Zielkontakt und Abstand zum Entry werden aus diesem einen Kurs berechnet.
  Auch die Prozentänderung im Kopf verwendet dieselbe Chartquelle; ein fremder
  oder veralteter Chart darf keine Änderung zum Fallback-Kurs liefern.
- Ein Kurs unter dem Long-Stop beziehungsweise über dem Short-Stop ergibt
  „Stopniveau unterschritten/überschritten“, nicht eine alte grüne Freigabe.
  Ein geometrisch intakter Plan ergibt lediglich „Planlevel intakt · Einstieg
  neu prüfen“: die Oberfläche erfindet keinen neuen vollständigen Health-Score.
- Der eingefrorene Scannerplan bleibt erhalten. Alte Bewertung, Ablehnungsgründe
  und Warnungen stehen in der geschlossenen Berechnungsbasis. Doppelte Gründe
  werden zusammengeführt. Plan-R:R und Gegenlevel-Abstände sind als Planwerte
  gekennzeichnet, nicht als neue Kursmessung. Krypto-Bewertungslogik unverändert.
- Eigener **Wyckoff**-Schalter in Seitenleiste und Chartanalyse. **Patterns**
  steuert weiterhin allgemeine Muster/Harmoniken. Beim Wyckoff-Scanner wird sein
  eigener Schalter initial aktiviert; Nutzer können ihn ausschalten. Der Wechsel
  zu einem anderen Scanner lässt keine automatische Wyckoff-Einblendung zurück.
- Gescheiterte, unklare oder alte Modellstrukturen werden nicht als aktive
  Wyckoff-Overlays gezeichnet. Historische Nachweise bleiben im geschlossenen
  Wyckoff-Detailbereich abrufbar, ohne separate lange Badges unter jeder Aktie.
- Erfolgreiche Ergebnisstände zeigen nur Datenstand und geschlossene Diagnose;
  kein wiederholter gelber Ergebniszähler. Fehler, veraltete/unvollständige Daten
  und blockierte Starts bleiben sichtbar. Unklassifizierte Ergebnisse verlieren
  ihren Hinweis nicht.
- Wyckoff prüft alle abgeschlossenen Grenzverletzungen bis einschließlich der
  Erholungskerze. Eine materielle Verletzung wird am tatsächlichen Zeitpunkt
  endgültig gespeichert. Der reine Erholungs-Timeout entsteht erst mit Ende
  seines dreikerzigen Zeitfensters. In-Range-Fortsetzung wartet auf die wirkliche
  Pivot-Bestätigung. LONG und SHORT sind gespiegelt getestet.
- Regelversion `wyckoff_v3_rules_3`, Aktienstrategie-Cacheversion **14**. Alte
  Strategieergebnisse müssen neu berechnet werden, nicht als korrigierte
  Ergebnisse neu etikettiert werden. Keine Löschung von Tracker oder Remindern.

## Prüfung

- Neue ausführbare Regressionsfälle verwenden die produktiven JavaScript-
  Hilfsfunktionen, keine nachgebaute Preislogik. VIAV-Fall mit 39,20 USD und
  Stop 39,29 USD: kein neuer Einstieg, Abstand −1,06R; ausgewählte Planlevel
  bleiben unverändert. Gegenfälle: SHORT, Stop-/Zielgleichheit, falscher Ticker,
  andere Zeiteinheit, veralteter Chart, fehlende oder ungültige Zahlen.
- Zwölf neue Wyckoff-Regressionsfälle prüfen Zwischenbrüche, Erholung,
  Fristablauf und Plateau-Bestätigung einschließlich historischer Präfixe.
  Die gezielte Wyckoff-Prüfung bestand mit **439 Tests**.
- Unabhängige Codeprüfung mit zusätzlichen gespiegelten Erholungs- und
  Preisgrenzfällen. Dabei gefundener Verlust historischer Ablehnungsgründe
  wurde korrigiert und durch einen neuen Test sowie Browserprüfung abgesichert.
- Browserprüfung der tatsächlich erzeugten Anwendung: 1440×1000 und 390×844,
  Seitenleiste und Chartanalyse. Chart zuerst, richtiger Stopstatus, historische
  Gründe geschlossen, unabhängige Schalter, keine alten Wyckoff-Badges,
  kompakte Ergebnisanzeige, kein horizontaler Überlauf, keine Konsolenfehler.
  Browserdaten sind synthetische lokale Fixtures; sämtliche Netzaufrufe sind
  abgefangen. Keine Providerabfrage, Testmail oder Order aus dem Test.
- Finale Gesamtsuite in zwei disjunkten Läufen: **9.105 bestanden, 4
  übersprungen, 0 Fehler**. Hauptlauf: 8.995 bestanden (365,47 Sekunden);
  separat ausgeführte Deployment-/Migrationstests: 110 bestanden,
  4 plattformbedingte Skips (328,19 Sekunden). Die Skips betreffen unter
  Windows nicht verfügbare Symlink-/Linux-Dateisicherheitsprüfungen.
  Bekannte harmlose Warnung: bereits importiertes `anyio` nicht umgeschrieben.
- Reproduktion mit isolierten Datenpfaden, gesperrten externen Sockets und SMTP:

  ```powershell
  .\.codex_pytest_env\Scripts\python.exe -B tmp/offline_mail_fix_tests_20260925.py -q --tb=short --durations=5 --ignore=test_deploy_auto_update.py --ignore=test_deploy_migration.py
  .\.codex_pytest_env\Scripts\python.exe -B tmp/offline_mail_fix_tests_20260925.py -x -vv --tb=short test_deploy_auto_update.py test_deploy_migration.py
  ```

  Privater JUnit-Nachweis jeweils `results.xml` unter
  `output/mail-fix-qa-c2b5856d8be54c6486f7f4f2d23dd004/` und
  `output/mail-fix-qa-e793d015854d412ebbc2b4a539bf666a/`.
  Der lokale QA-Launcher ist absichtlich nicht Teil des Produktions-Deployments.
- Bundle gebaut und mit `scripts/verify_frontend_bundle.py` geprüft:
  Quellfingerprint **`22c113cb3556`**. `node --check` und `git diff --check`
  bestanden. Unabhängige Nachprüfung nach der letzten Korrektur ohne offene
  Befunde. Keine produktiven Quelländerungen während dieser finalen Testläufe.

## Grenzen der konkreten Prüfung

Die Live-Seite wurde angemeldet gelesen, aber nicht aktualisiert oder neu
gescannt. Ihre acht exakten historischen VIAV-Zählungen sind ohne die zugehörigen
Original-OHLCV nicht einzeln nachgerechnet; aus den sichtbaren Labels wird kein
solcher Nachweis abgeleitet. Der kausale Regelcode und seine nachgewiesenen
Fehler wurden unabhängig mit kontrollierten Kursverläufen geprüft.

Fachlicher Bezug: [Wyckoff Analytics – The Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/).
Die numerischen Toleranzen bleiben ausdrücklich Modellregeln dieser Anwendung.
Keine Änderung am Mailtransport, an BI 17/20 oder an anderen Scanner-Mindestregeln.
Server-Pull, Neustart und danach neue vollständige Läufe sind getrennte Schritte.
