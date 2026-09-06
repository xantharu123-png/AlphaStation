# Scan-Waechter: wiederholte BI-Entwarnungen

Ausgangsstand: `8a9ada37a17e511c113ab13b64843e315c42d5fb`, Branch `main`.
Anlass: mehrfach taegliche Betreiber-Mail, Beispiel `bi_short laeuft wieder`,
06.09.2026 23:06 UTC, angezeigte Episode 599 Minuten.

## Was die Mail belegt und was nicht

Das ist eine Betriebsmeldung, kein Trading-Signal. Der API-Waechter prueft
aktive Worker: bei BI 45 Minuten Warnbudget, 135 Minuten Hartlimit. Er prueft
nicht einfach das Alter des letzten Ergebnisses waehrend einer geplanten Pause.

Die alte Entwarnung berechnete jedoch die Zeit vom Beginn einer offenen
Warnperiode bis zum gerade erfolgreichen Lauf oder Versandversuch. Darin
konnten weitere Fehlversuche, geplante Wartezeiten und SMTP-Wiederholungen
enthalten sein. **599 Minuten beweisen deshalb keinen 599 Minuten durchgehend
haengenden Scan.** Umgekehrt beweist eine Entwarnung keine gesunde Gesamtlage.

Die genaue Ursache langer realer BI-Laeufe ist ohne Serverlogs nicht bestimmt.
Das BI-Universum wird seriell durchlaufen und teilt sich das Datenanbieterbudget;
Laufzeit allein belegt weder einen blockierten Netzaufruf noch eine falsche
17/20-Regel. Keine Grenzwerte oder Handelsfilter wurden deshalb abgesenkt.

## Reproduzierte Fehler und Korrektur

1. Unterdrueckt, schon versandt und SMTP fehlgeschlagen waren alles `False`.
   Der Worker oeffnete dadurch auch erledigte Warnperioden wieder. Nach Ablauf
   der Sechs-Stunden-Drossel konnte eine vorher unterdrueckte Meldung doch
   erscheinen; sogar nach Ablauf der Sieben-Tage-Dedupe war Wiederbelebung
   moeglich. Jetzt gibt es explizite, getrennte Zustellausgaenge.
2. Entwarnung ohne zugehoerige versandte Warnung war nach Drosselablauf erlaubt.
   Jetzt braucht sie die bestaetigte weiche **oder harte** Warnung genau dieser
   Episode. Eine fremde Drossel blockiert keine passende Hartlimit-Entwarnung.
3. Ein gescheiterter Versand hielt den Scanner kuenstlich in derselben Episode
   und vergroesserte deren Dauer bis zum naechsten Versand. Jetzt beendet der
   erste erfolgreiche Abschluss die aktive Episode. Ausstehende Zustellung
   hat einen eigenen Zustand mit unveraendertem Abschlusszeitpunkt und Laufzeit.
4. Nach einem fehlgeschlagenen Scannerlauf verwendete die naechste Warnung eine
   andere Kennung als die spaetere Entwarnung. Die Kennung bleibt jetzt bis zum
   ersten echten Erfolg dieselbe; ein danach neuer Haenger ist eine neue Episode.
5. Unabhaengiger Review: Abschluss waehrend laufender Warn-SMTP-Zustellung konnte
   die Entwarnung verlieren. Zustellabsicht und Episode werden jetzt unter
   demselben Lock registriert; die Entwarnung wartet auf das bekannte Ergebnis.
6. Unabhaengiger Review: abgelaufene Dedupe loeschte bekannte Warnzustellung bei
   echten SMTP-Retries. Bestaetigte Zustellhistorie bleibt im Pending-Zustand
   erhalten, auch bei einer Exception oder einem Wiederholungsversuch nach
   acht Tagen.

Die Mail nennt nun den datierten erfolgreichen Abschluss, den Zeitraum der
Warnperiode und separat die Laufzeit des erfolgreichen Durchlaufs. Sie behauptet
nicht mehr, alle Scanner seien normal. Kein paralleler Ersatz-Worker, keine
Erhoehung des Laufzeitbudgets, keine Unterdrueckung echter Hartlimit-Alarme.
Scanner-/BI-/Trading-Gates, Cron und Datenbankschema bleiben unveraendert.

## Pruefung

Alle Tests laufen lokal mit isolierten Datenpfaden, simulierter Uhr und
SMTP-Ersatz; es wurden keine echten Mails, Orders oder Marktscans ausgeloest.

- Acht erste Lebenszyklus-Gegenproben scheiterten am alten Code und bestanden
  nach Korrektur.
- Vier weitere Gegenproben aus dem Review (Warnversand parallel zum Abschluss,
  erfolgreicher/gescheiterter Warnversand, SMTP-Retry nach acht Tagen bei False
  und Exception) wurden ebenfalls zuerst rot und danach gruen ausgefuehrt.
- Enger finaler Code-Review: keine offenen Critical/Important-Befunde;
  separat erneut 39 Waechter-/Lebenszyklustests bestanden.
- Python-Compile und Frontend-Verifikation bestanden. Das Frontend ist
  unveraendert, Bundle-Quelle `cc0d82106285`.
- Finale breite Abnahme: **3026 Nicht-Deployment-Tests bestanden**, keine
  Fehler/Skips, 98,92 Sekunden. XML lokal:
  `tmp/watchdog_release_final_20260907.xml`. Die drei Code-/Testdateihashes
  waren vor/nach diesem Lauf identisch.
- Der vorherige Gesamtlauf enthielt 3179 bestandene Faelle, vier Skips und
  vier fehlgeschlagene `inspect.getsource`-Pruefungen: Die waehrenddessen
  eingearbeitete Review-Korrektur hatte Zeilen verschoben, waehrend Python
  noch den vorher geladenen Code verwendete. Dieser Lauf ist ausdruecklich
  **keine** gruene Endabnahme. Alle vier betroffenen Faelle bestanden im
  frischen, eingefrorenen finalen Lauf oben; kein Test wurde entfernt.
- Die 165 Deploymentfaelle sind unveraendert und bestanden im Gesamtlauf
  mit **161 PASS und vier Windows-/Linux-spezifischen Skips**, ohne Fehler.
  Zusammen sind damit **3187 unterschiedliche bestandene Tests und vier
  Plattform-Skips** abgedeckt, nicht 3179+3026 unabhaengige Faelle und nicht
  ein einzelner vollstaendig gruener Prozesslauf. XML des ersten Laufs:
  `tmp/watchdog_full_20260907.xml`.
- `git diff --check` und Credential-Musterscan im exakten Aenderungsumfang
  bestanden. Private Archive, Datenbanken und `output/` sind ausgeschlossen.

## Live-Stand und offene Betriebspruefung

Am 07.09.2026 um ca. 01:21 MESZ wurde der direkte oeffentliche Health-Endpunkt
auf Port 8000 lesend abgerufen. Antwort: `healthy`, Revision `47eca9ac05ec`,
Bundle `b7bc31f215b9`, Antwortzeitstempel `2026-09-06T23:21:29.082039`.
Die API meldet damit noch **nicht** das vorherige Auditpaket `8a9ada3`.
Dies prueft nicht den Dateisystem-HEAD und beweist keine korrekte Scannerlaufzeit.
nginx lieferte fuer den gleichen Health-Pfad auf Port 80 HTTP 404.

SSH mit den vorhandenen Schluesseln wurde abgewiesen (`publickey,password`).
Kein Server-Pull, Neustart, Cron-Eingriff oder produktives DB-Repair durchgefuehrt.
Vor Rollout des gesamten seit `47eca9a` fehlenden Auditpakets gelten weiterhin
die DB-Backup-/Deploy-Gates aus `AUDIT_REMEDIATION_2026-09-04.md` und dem Handoff.

Kleine **rein lesende** Pruefung auf dem Server, um Dateistand und tatsaechliche
Laufzeiten zu erhalten:

```bash
sudo -u tradingbot git -C /home/tradingbot/app log -1 --oneline
journalctl -u tradingbot-api.service --since '48 hours ago' --no-pager | grep -E 'WATCHDOG: bi_|bi_(long|short) (DONE|ERROR)' | tail -n 60
```

Warn-/Entwarnungs-Dedupe ist persistent. Aktive Episoden und noch ausstehende
Entwarnungen sind wie bisher Prozesszustand; dieser Patch fuehrt keine dauerhafte
Wiederaufnahme ueber API-Neustarts ein. Echte neue Episoden koennen weiter
berechtigte Betreiber-Mails erzeugen. Der Patch ist keine Zusage von null Mails
und kein Nachweis, dass langsame Produktionsscans bereits behoben sind.
