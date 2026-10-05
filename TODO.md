# Aktuelle Aufgaben / Übergabe

## 05.10.2026 – Aktuelle Signal-Mailkontrolle nach Betreiberpull

Dieser Abschnitt aktualisiert die Betriebskontrolle nach dem bisherigen Reparaturpaket.

- [x] Live-Health 13:37 UTC: `healthy`, Revision `43180ffea286`,
  Frontend-Bundle `67924e9f894a`. Das Paket mit `f46413c` läuft jetzt auf Hetzner.
  Kein weiterer Pull allein wegen des bisherigen Pakets erforderlich.
- [x] Angemeldete App/Admin-Mailversand direkt geprüft: 1 Swing-/1 Kryptoempfänger,
  0 SMTP-Annahmen, 50 ausgelassen, 0 Versandfehler, 0 wartend im sichtbaren Fenster.
  Maximal 50 RAM-Events seit API-Start, höchstens 24h – kein vollständiger Tagesnachweis.
  Aktuelle Vorprüfung: 37 Aktienstrategie-, 13 Gap-Long-, 3 Gap-Short-Kandidaten,
  jeweils 0 mailfähig. Die Vorprüfung ersetzt keinen Abschluss/Versandnachweis.
- [x] DAC konkret nachgerechnet: Score 88/S und Tagesqualität 87/96 reichen aus,
  aber TP1 170,10 und TP2 174,23 sind Projektionen ab Entry 165,15
  (3 % / 5,5 %), keine bestätigten Strukturziele. `trade_target_not_structural`
  blockiert vor SMTP; der offene Rücktest ist hier nicht die Blockade.
- [x] Neuen automatischen BI-Long-Lauf bis Abschluss beobachtet, ohne Scanstart
  oder Neuladen: 05.10., 14:01:29 Serverzeit, 5.319/5.319 geprüft,
  4.186 analysiert, 120 Kursdatenfehler einzeln ausgeschlossen, 0 gültige Signale.
  Kein globaler Abbruch; fehlerhafte Aktien werden im nächsten Lauf erneut geprüft.
  Ergebnis/Fortschritt haben sich in der App automatisch aktualisiert.
- [x] Diagnosefehler lokal repariert: acht finale Swing-Ablehnungen bleiben mit
  konkretem anonymem Code in Admin/Telemetrie erhalten; leere Endauswahl und
  Mail-Sitzungswächter sind erklärbar. Unbekannte Gründe bleiben unbekannt;
  keine unbestätigte Aussage „Börse geschlossen“ oder garantiertes Serverprotokoll.
  Keine Änderung von Freigaberegeln, Sendern, Dedupe oder Daten-/Preisprüfungen.
- [x] TDD und unabhängige Nachprüfung: 184 gezielte Tests bestanden;
  ein bekannter anyio-Importhinweis. Neue Randfälle zunächst rot nachgewiesen.
  [Prüfbericht](docs/MAIL_LIVE_GATE_AUDIT_2026-10-05.md).
- [x] Eigenständiges anonymes Exportregister mit dem korrigierten App-Register
  synchronisiert. Danach 407 gezielte Mail-/Collector-/Historytests bestanden.
  Vorläufige Gesamtlauffehler samt Ursachen sind im Prüfbericht dokumentiert;
  Ergebnis der finalen Wiederholung mit unveränderten Quellen siehe unten.
- [x] Admin um 16:07 Zürich nachgeprüft: weiterhin keine SMTP-Annahme im
  begrenzten RAM-Fenster; neue generische Events um 16:06 nicht nachträglich
  zuordenbar. System Logs liefert 0 Zeilen und ersetzt kein systemd-Journal.
- [x] Finaler isolierter Gesamtlauf: 11.575 bestanden, 0 Fehler, 5 Windows/Linux-
  Ausnahmen, ein bekannter anyio-Importhinweis. XML und SHA256 vor/nach dem Lauf
  geprüft; Produkt-/Testquellen unverändert. Ausnahmen im Prüfbericht dokumentiert.
- [x] Zusätzlich den abgegrenzten Veröffentlichungsbaum geprüft:
  11.573 bestanden, 0 Fehler, 5 Plattformausnahmen. Kein fremdes Deploy-/Test-WIP
  in dieser Releasekopie. Git-Metadatenproblem der ersten ZIP-Prüfung im
  unveränderten Test reproduziert und durch korrekte Testumgebung behoben;
  kein Produkt-/Testfix oder verdeckter Skip. Nachweise im Prüfbericht.
- [ ] Lokal geprüften Diagnosepatch bei der nächsten Veröffentlichung gezielt
  übernehmen: `api.py`, `modules/suppression_telemetry.py`,
  `scripts/collect_server_evidence.py`, `test_swing_mail_rejection_diagnostics.py`,
  neuer Prüfbericht und aktueller TODO-Abschnitt. Noch nicht committet/gepusht
  oder auf Hetzner installiert; fremdes Deploy-/Dokumentations-WIP nicht mitnehmen,
  private `output/`-Nachweise nicht veröffentlichen.
- [ ] DAC-Originalzonen, `level_structure.as_of`, `completed_bar_counts` und
  Bestätigungszeiten prüfen: fehlt eine echte Gegenbarriere oder wurde sie übersehen?
  Aus dem angezeigten Projektionsziel allein ist das nicht entscheidbar.
  Zusätzliche unabhängige Quellprüfung ohne nachgewiesenen Richtungs-/Zeitfehler;
  Original-`zones/evidence`, Qualitätsflags und Vor-/Nach-VRVP-Zielentscheidung
  bleiben zur fachlichen Nachrechnung erforderlich. Nicht pauschal als erledigt markieren.
- [ ] Generische heutige Events nicht als Score-/Providerfehler ausgeben;
  Originalursache noch offen. SSH-Keyzugriff wird abgewiesen, geschützte
  Detail-API ohne Anmeldung liefert 401; keine Zugangsdaten aus dem Browser übernommen.
  Die vorhandene Browseranmeldung bleibt für sichere UI-Leseprüfungen nutzbar.
- [ ] Tatsächlich zulässige Signal-Mail → SMTP-Annahme → Postfach nachweisen.
  Technische Testmail kam früher an. Kein neuer Export oder Testmail verlangt/gesendet.
- [ ] Neuer historischer BI-Dreimonatslauf, Wochenkohorte und DAC/VIAV/AST-
  Originaldaten bleiben offen; durch die heutige Live-Diagnose nicht erledigt.

Heute kein Serverupdate, Neustart, manueller Scan, Settings-/Produktions-DB-Schreibzugriff oder
Mailversand. Bestehendes fremdes WIP bleibt erhalten. Nur der Diagnosepatch,
zugehörige Tests und diese Dokumentation wurden lokal bearbeitet.

## 04.10.2026 – Aktueller Abschluss: BI-Backtester und Mail-Reservierungen

Dieser Abschnitt ersetzt die älteren offenen Implementierungsstände unten.
Basis: `cabb5b17ee41dc93f40c0a43270ffe71e803b820`. Kein Serverupdate durch Codex.

- [x] BI-Auswahl auf die jüngsten 50 Sitzungen vor dem Testzeitraum korrigiert;
  gewähltes Mindestvolumen 200.000 wird nicht heimlich auf 500.000 angehoben.
  Signalgrenze, letzte abgeschlossene Signalkerze und belegte Handelsfenster korrigiert.
- [x] Abdeckung und vollständiger Trichter implementiert: ausgewählte Aktien,
  auswertbare Historien, 17/20-Kandidaten, Planablehnungen, Pläne, Fills und offene Fälle.
  Fehlende/ungültige Kurse bleiben Datenlücken; kein künstlicher Ersatz.
- [x] API und gespeicherte Berichte prüfen Rohzeilen, Zählerpartitionen und Provenienz.
  Der 150-Zeilen-Anzeigecap verfälscht nicht die vollständigen Zähler.
  Nur BI erhält eine neue Modell-/Cachekennung; ältere Dateien werden nicht gelöscht.
- [x] Kurze BI-Hauptanzeige; Diagnose und Methodik geschlossen. Offene Folgekerzen,
  fehlende Indikatoren, keine Setups und abgelehnte Pläne sind getrennt.
  Gefüllte, noch offene Einstiege sind von „Ausgewertete Trades“ getrennt.
  BI bleibt bei 17/20; Score-, Risiko- und Mailfreigaben werden nicht gelockert.
- [x] Sichere Abbrüche vor SMTP geben unversuchte Reservierungen frei.
  BG bereinigt nach 30 Minuten ausschließlich unberührte, nicht angenommene Reservierungen.
  Alter Sender kann keinen Ersatzowner löschen. ATTEMPTED, unklare DATA-Annahme und
  separat journalisierte SMTP-Annahme bleiben geschützt; Journalfehler stoppen Wiederholung.
  Ungültige Prepared-Zeitstempel werden nicht durch SQLite-Normalisierung gelöscht.
- [x] Unabhängige Cache-/Mail-Nachprüfung; 445 gezielte Mailtests, 151 Producer-Tests,
  89 Frontend-/API-Proben und 45 abschließende Cache-/API-Proben bestanden.
  Diese überlappenden Läufe werden nicht zu einer Gesamtzahl addiert.
- [x] Playwright: 18 Desktop-/Mobilfälle grün, kein externer Request, kein Schreibaufruf,
  keine Laufzeitfehler, kein horizontaler Überlauf. Bundle `67924e9f894a` verifiziert.
- [x] Angemeldete App rein lesend geprüft: gespeicherter BI-Lauf vom 03.10., 21:47:24,
  3 Monate / 200 Aktien / Preis 5 / Volumen 200.000 enthält 0 Pläne/Trades,
  aber keine gespeicherten Ablehnungsdiagnosen. Seine exakte Ursache bleibt unbekannt.
- [x] Live-Mailansicht: zuletzt 50 Entscheidungen vor SMTP ausgelassen, keine
  SMTP-Annahme und kein Versandfehler in diesem sichtbaren Fenster. Kein Nachweis eines
  allgemeinen Transportausfalls. Technische Testmail laut Nutzer angekommen.
  Sonntag: Aktien-Autoläufe pausieren; nächste erlaubte Öffnung 05.10., 04:00 UTC.
- [x] Abschließender eingefrorener Gesamtlauf: 11.274 bestanden, 0 Fehler,
  1 POSIX-Rechtetest unter Windows übersprungen; `tmp/qa-1b8536a9bad0/results.xml`.
  Erster Lauf: 11.264 bestanden, 1 alte Quelltextprüfung rot, 1 POSIX-Rechtetest unter Windows
  übersprungen. Quelltextprüfung korrigiert; danach 67 gezielte Tests grün.
  Ein Windows-Queue-Timeout im Mail-Nachlauf ist dokumentiert; ohne Änderungen
  bestanden anschließend der Atomic-Test (10/10) und der vollständige 445er-Lauf.
- [x] Reparaturpaket committet und gepusht: `f46413c2d357475968109aa79269a9c07e21ddd7`.
  Remotehash kontrolliert. Fremdes WIP und private Exporte nicht aufgenommen.
  Dieser Abschlussabschnitt wird separat als Dokumentationscommit veröffentlicht.
- [ ] Nach Betreiberpull BI-Dreimonatslauf neu berechnen und echte Diagnose auswerten.
- [ ] Neue echte Handelssignalannahme, persönlicher Empfang und Wochenkohorte nachweisen.
  Kein weiterer Export, Scanstart oder Testmail durch diese Bearbeitung.

Live-Health vom 04.10.2026, Serverzeit 20:58:06: healthy,
Revision `cabb5b17ee41`, Bundle `17410b75a42c`.
Server unverändert; keine Einstellungen, Zugangsdaten oder Produktionsdaten geändert.
Bestehendes Dokumentations-/Deploy-/Test-WIP bleibt erhalten.
Prüfbericht: [BI-Backtester und Mail-Recovery](docs/BI_BACKTEST_MAIL_RECOVERY_2026-10-04.md).

## 03.10.2026 – Wochenreport: Versandkohorte und kausale Bewertung

Aktueller Auftrag: die irreführende Wochenbilanz (86 reife Einträge,
45 ausgewertet / 40 ungeklärt, −47,5R) und ihre Versandbehauptungen korrigieren.
Ausgangsrevision `9267b0d87e63a30a1231d8a7694efa5ba5312074`.

- [x] Wochenjob und lokale Vorschau verwenden ausschließlich aktivierte,
  kanonisch gebundene SMTP-Annahmen mit gültigen Empfänger- und Zeitbelegen.
  Alte direkte Tracker-Einträge bleiben erhalten, gelten aber nicht mehr als
  nachgewiesene Signalzustellung. Aktivitäten der letzten 7 Tage und das
  separate 30-Tage-Reifefenster werden nicht vermischt.
- [x] Die globale SMTP-Annahme wird ausdrücklich nicht als persönlicher
  Posteingang ausgegeben. Wochenreport/Crash-Mails bleiben eigene Info-Mails;
  ihr Eingang beweist keinen erfolgreichen Handelssignal-Ablauf.
- [x] Erste Einstiegs- und Stop-Berührung innerhalb derselben Tageskerze
  ohne belegten vorherigen Einstieg erzeugt keinen erfundenen Fill/Verlust.
  Historische mehrdeutige Fälle werden beim Lesen ausgeklammert, nicht in der
  Produktionsdatenbank umgeschrieben. Bloße Snapshot-/Pfad-Tags reichen nicht.
- [x] Echte bereits eingestiegene Positionen und belegte Gap-Verluste bleiben
  erhalten. Negative R-Werte werden nicht kosmetisch entfernt; die alte
  Gesamtsumme ist ohne qualifizierte Kohorte keine belastbare Gesamtbilanz.
- [x] Vollständiger, disjunkter Bestandsabgleich: ausgewertet, Evidenz offen,
  ohne Einstieg, noch offen, nicht auswertbar. Offene Kontroll-/BE-/Pfadbelege
  verhindern eine scheinbar endgültige Schlagzeile oder Scannerfreigabe.
  Lesefehler erzeugen keine scheinbar erfolgreiche Nullbilanz und überschreiben
  keine vorhandene Vorschau.
- [x] Unabhängige neue Kausalitäts-/Provenienz-Gegenfälle sowie echte
  Prepare → Attempt → Finalize → Evaluator-Lifecycles lokal geprüft.
  Akzeptanzbindungen überstehen gültige spätere Outcome-Änderungen;
  Doppelversand bleibt gesperrt. Keine Schwellen, Einstellungen oder Orders geändert.
- [x] Playwright-Skill: lokale synthetische Wochenmail auf Desktop1440 und
  Mobil390 geprüft; kein horizontaler Überlauf, keine externen Requests.
  Die Vorschau ist Formatnachweis, keine Rekonstruktion der echten 86 Einträge.
  Private Screenshots liegen unter `output/playwright/weekly-repair-*.png`.
- [x] Drei kalendarische Testfehler gegen unveränderten HEAD reproduziert.
  Nur Test-Clocks korrigiert; Admission-/Expiry-Assertions unverändert.
  142 gezielte Scheduler-Nachbarprüfungen bestanden; kein API-Produktionsfix
  hierfür erforderlich. 37 abschließende Preview-/Kohortenprüfungen bestanden
  (`tmp/qa-f44b35f2d948/results.xml`).
- [x] Eingefrorenen Gesamtlauf abgeschlossen: 11.180 bestanden,
  1 POSIX-Prüfung auf Windows übersprungen, 0 Fehler, 301,34 s.
  `tmp/qa-2a892a8eb305/results.xml`. Alle 11 Quell-/Testdateien entsprechen
  SHA-256-genau dem geprüften Snapshot. Retirierte Deploy-Umstellungstests und
  fremde Commerce-/Kalender-/Deploy-/Handbuch-WIP nicht im Reparaturpaket.
- [x] Nur dieses geprüfte Paket committet und auf `main` gepusht:
  `98da02ef104e0145e3e14c2b53d2a1f8e1440830`, durch `git ls-remote` bestätigt.
  Private Exporte/Browserartefakte und vorhandene fremde Änderungen nicht
  übernommen. Dieser abschließende Statusnachtrag ändert nur Dokumentation.
- [ ] Nach normalem Betreiberpull die echte Wochenkohorte nachrechnen und
  ein neues gültiges Handelssignal vom Scanner bis zur persönlichen Mail prüfen.
  Kein automatischer Replay historischer Einstiegsmails und keine weitere Testmail.

Letzte reine Livekontrolle dieses Auftrags: `/api/health` am03.10.2026
06:19:21 (Serverzeitstempel) healthy / `4ef6c9b28482`, Frontend `5f2a5271c188`.
Kein Serverupdate, Scanstart, SMTP-Versuch oder Produktionsdaten-Umschreiben
durch diesen Auftrag. Die temporär geöffnete App zeigte die Anmeldeseite;
SSH im Batch-Modus wies vorhandene Schlüssel ab. Beide nur lesenden Zugänge
stehen damit ohne Betreiberanmeldung nicht zur Verfügung.
Die technischen Fehler sind lokal prüfbar; neue echte Signalzustellung bleibt
eine getrennte, noch offene Betriebsprüfung.

## 02.10.2026 – Backtest zuerst tief auditiert, dann repariert

Aktueller Auftrag: Backtest Center vor dem Reload-Fix tief prüfen.
Ausgangsrevision `4ef6c9b284825686fef60913a2e932a8f9414202`.

- [x] Neue mathematische/kausale Gegenfälle vor Reparatur reproduziert;
  unabhängiger Originalvergleich 11 rot / 8 grüne Kontrollen.
- [x] Rohpräzision, Netto-P&L/R, Fills, boolesche/offene Preiswerte,
  historische Universumswahl und Datenqualitätsfortführung korrigiert.
- [x] Holdout-Leck, PF-Rundungsgrenze, Krypto-Warmup-/Jahrescap und
  irreführende Konto-/Null-Kennzahlen korrigiert; keine Signalgrenzen gelockert.
- [x] Exakte V2-Cacheidentität, atomare Erfolgsdateien und passives Reopen;
  Fehler überschreiben keinen gespeicherten Erfolg.
- [x] UI-Deadline/SingleFlight/Generation-/Unmountschutz, pure Formular-
  präferenzen, sichere Fehlertexte, Server-/UI-Capzähler und ganzzahliges
  Volumen. Parameterlose Dateien erhalten, nicht als aktuelle Studie ausgegeben.
  Verlassen der Ansicht beendet nicht den Serverworker.
- [x] Controller 30/30, Independent 19/19, gemeinsame Kernfälle205/205 grün.
  Lokale Playwright-Desktop-/Mobilansicht: Reopen/Parameter-Miss/422/Erfolg,
  keine Live-API/Mail/externen Requests, Mobilbreite390/390.
- [x] Quellen-/API-Nachtrag eingefroren: 247/247 grün,
  `tmp/qa-7495abeac921/results.xml`; finaler Browserlauf mit Bundle
  `17410b75a42c` bestanden (Desktop/Mobil, keine externen Requests).
- [x] Eingefrorenes Gesamtpaket offline geprüft: 11.030 bestanden,
  eine POSIX-Prüfung auf Windows übersprungen, keine Fehler.
  `tmp/qa-a8c5b525dfe2/results.xml`, 430,18 s; Deploy-Umstellungstests
  ausdrücklich nicht im Umfang. Erster Vollpaketlauf fand nur fünf alte
  gerundete R-Erwartungen; unabhängig hergeleitet, übrige Assertions erhalten.
- [x] Nur geprüften Backtest-Scope committet und auf `main` gepusht:
  `7bfeb167898f0452de3bb5bfd7714c6c9da941e2`, durch `git ls-remote`
  bestätigt. Bestehende Deploy-/Handbuch- und Commerce-/Kalender-WIP,
  private Exporte und Browserartefakte nicht übernommen.
  Dieser abschließende Statusnachtrag verändert nur Dokumentation.
- [ ] Nach Betreiberpull echten Backtest-Reopen kontrollieren;
  kein Serverupdate oder echter Provider-Backtest durch Codex.

Details: [Backtest-Prüfbericht](docs/BACKTEST_DEEP_AUDIT_2026-10-02.md).
Andere Mail-/Live-/Dreimonatsnachweise bleiben separat.

## 02.10.2026 – Erstaufruf ohne manuelles Neuladen

Zusätzlicher aktueller Befund: Die anfänglich leere Seite war nicht bloß ein
laufender Scan. Ergebnis-GET/Body konnten hängen; transiente Erstlesefehler
wurden nicht automatisch wiederholt, passive Aktualisierungen brachen Reads
ab. Zwei versteckte Referenz-Providerpfade und blockierendes Auth-I/O kamen
hinzu. Die unten dokumentierten Mail-/Cup-Grenzen bleiben separat bestehen.

- [x] Gemeinsamen Scannerfeed mit 20 s Request-/Bodydeadline, SingleFlight und
  automatischem transienten Retry versehen; Retry-After wird nicht umgangen.
- [x] Finale Ergebnisse bei Ladefehlern behalten; Teilstände nicht zu finalen
  Ergebnissen/Nullscans umdeuten; Scope-/Run-/Berechtigungsgrenzen erhalten.
- [x] Ergebnisse aus gespeicherten Instrumentdaten lesen, auch ohne Namen;
  keine Providerseiten/-Einzelabrufe durch den Standard-Ergebnis-GET.
  Fehlende Identitätsbeweise bleiben 503, nicht scheinbar gültige 0 Treffer.
- [x] Cachealter/Firmennamen erhalten und überschreibenden alten Displayread
  gegenüber neuer Worker-Publikation mit Lock/CAS abgesichert.
- [x] Auth-/Kontodatenreads aus dem ASGI-Eventloop verlagert, bestehende
  Auth-/Cookie-/Plan-/read_only-/Throttle-Regeln unverändert geprüft.
- [x] Playwright-Skill: tatsächliches lokales Bundle auf Desktop/Mobil
  geprüft. Verzögerter Erstread 503 → Retry → Treffer ohne Reload; spätere 503
  behalten alte Treffer. 0 Schreibanfragen/Provideraufrufe; eigene Prozesse zu.
- [x] Gezielte Offlineprüfungen bestanden: 390 bestehende Lifecycle/Authfälle
  plus 55 abschließende Kernfälle; keine externen Provider/SMTP/Secrets.
- [x] Eingefrorenen Index-Gesamtlauf abgeschlossen: 10.887 bestanden,
  1 POSIX-Dateirechteprüfung unter Windows übersprungen, 0 Fehler.
  `tmp/qa-b3fbe27b64fa/results.xml`; Deploy-Umstellungstests nicht im Umfang.
- [x] Nur das geprüfte Erstlade-Paket committet und auf `main` gepusht:
  `0b2ae89bd6b9308e66786ce8579d590476e273a1`, durch `git ls-remote` bestätigt.
  Statusnachweis hier und im Prüfbericht gespeichert; kein Serverupdate
  durch den Push.
- [ ] Nach normalem Betreiberpull echten ersten Seitenaufruf kontrollieren.
  API-Health am 02.10.2026 17:28:20 (Serverzeitstempel): healthy / `1c3fb68f51df`
  / Frontend `806260a08809`. Die Erstlade-Korrektur ist dort noch nicht
  installiert; kein Export, Scanstart oder Serverneustart durch Codex.

Details/Nachweise: [Erstlade-Prüfbericht](docs/INITIAL_RESULT_LOAD_REPAIR_2026-10-02.md).
Keine Änderung der Signalbedingungen, keine Behauptung neuer Mailzustellung.

## Neue Livekontrolle 02.10.2026 – nach dem Betreiberupdate

Dieser Abschnitt hat Vorrang vor dem älteren Hinweis „auf Hetzner noch nicht
aktiviert“. Öffentliches `/api/health` am02.10.10:53:47 UTC bestätigt
**healthy / `7f6981f8e4f8` / Frontend `6a488c9c8f1a`**. Das vorherige Paket
ist auf dem Server angekommen; kein erneuter Pull allein für dieses Paket.

- [x] Betreiber hat den Eingang der technischen Testmail erneut bestätigt.
  Keine weitere Testmail gesendet; Transport dieser Nachricht ist belegt,
  noch nicht die Zustellung eines Handelssignals.
- [x] Authentifizierte aktuelle UI geprüft, ohne Scans oder Einstellungen zu
  ändern: Momentum vollständig12.582/12.582,32 Kandidaten, maximal Trade-Score78
  bei Mailminimum80. Neuer Snapshot02.10.10:48:55 UTC. Nicht „Scan abgebrochen“.
- [x] Die Fehlermeldung der Aktienrunde ist dem Cup-Lauf zugeordnet:
 5.712/12.582, `scan_data_unavailable`, Phase `history`,507s. Der alte Cup-
  Ergebnisstand ist kein neuer abgeschlossener Nullscan. Innerer Abruffehler
  noch nicht vorhanden; gezielter vorhandener Probeauftrag unten.
- [x] Fehlender tatsächlicher Signal-Mailanschluss des Crypto-Long-Scanners
  im aktuellen Code bestätigt: erfolgreicher Wrapper speichert nur Ergebnisse;
  auch `_run_scan_safe` und der Combined-Merge dispatchen keine Long-Signale.
  Early-Mover-Sender hat einen anderen Producer/Owner und ersetzt diesen Pfad
  nicht. Das ist zusätzlich zu den aktuellen Ablehnungen ein echter Codefehler.
- [x] Eigenen Crypto-Long-Dispatcher anschließen: unveränderte Grade-/Score-/
  Struktur-/Funding-/Frischegrenzen, native Venue/Contract/Closed-Candle-Quelle,
  finale Quote/Pfadprüfung, korrekter Crypto-Kanal, Durable-Intent/Receipt und
  Doppelversandschutz. Kein zweiter Sender aus dem Combined-Cache.
- [x] Latenten Crypto-Strategy-Kanalfehler korrigieren und prüfen: tatsächlich
  Crypto-Kanal statt `stocks_swing`, auch bei beiden Watch-Seams. Bestehender
  manueller Crypto-Watch-only-Vertrag bleibt deaktiviert für Trade-Mails.
- [x] Rolling24h-Hoch als unbelegte Beobachtung behandeln, nicht als bestätigte
  Strukturidentität. Echter Scorer → echte VRVP/Health → finale Quote/Pfad →
  tatsächlicher Sender mit Mock-SMTP/temporärem Receipt getestet. Ohne echte
  Gegenbarriere weiterhin Watch; keine Score-/Risiko-Lockerung.
- [x] Native Long-Cachevorprüfung und Crypto-Empfängerzahl in Admin ergänzen;
  zehn Gründe konsistent in API/SQLite-Telemetrie/privatem Collector.
- [x] Pauschale Combined-Behauptung „kein Fehler, sondern Marktlage“ bei
 0Longs/Shorts entfernen; Quellenstatus/Zähler statt erfundener Erklärung.
  Tatsächliche JSX-Komponente für beide Richtungen mit Fehlerstatus geprüft;
  Crypto-Empfänger im Admin separat sichtbar. Bundle `806260a08809` gebaut und
  Sourcehash/Syntax geprüft.
- [x] Neue Korrektur unabhängig/offline geprüft: 96 unabhängige Tests grün;
  eingefrorener Index-Gesamtlauf **10.842 bestanden, 1 übersprungen, 0 Fehler**,
  479,69s; `tmp/qa-77f0423f384f/results.xml`. Externe Provider/SMTP gesperrt,
  keine lokalen Secrets. Deploy-Tests ausdrücklich ausgeschlossen.
- [x] Geprüfte Korrektur scoped committet und gepusht:
  **`d2c14b1329aeb4099fc454c4ac9d9a0b0d6c9b72`**, Remote `origin/main`
  unabhängig bestätigt. Genau12 Pfade, keine privaten Exporte/Deploy-WIP.
  Normalen Pull unten verwenden; Server nicht selbst verändert.
- [ ] Cup-Abruffehler über `python3 -I /home/tradingbot/app/scripts/probe_stock_attempt_errors.py`
  im vorhandenen Server-Terminal lesen. Kein Gesamtexport/Passwortloop nötig.
- [ ] Aktueller Crypto-Long-Wiederholungslauf und echte Handelssignalannahme/
  Posteingang bleiben offen. Letzter Admin-Stand nach Restart:0 angenommen,
  6 ausgelassen,0 Fehler,0 Queue am02.10.13:18:02MESZ; begrenztes Prozessfenster,
  keine Inboxhistorie. Automatischer Retry weiter aktiv, kein Start durch Codex.

Nach Abschluss laufender Scans und SMTP-Vorgänge im Server-Terminal:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Health: `healthy`, neueste `origin/main`-Revision einschließlich des reinen
Dokumentationsnachtrags, Frontend `806260a08809`. Kein Deploy-Skript, keine
Cache-/DB-Löschung. Danach Strg+F5. Echte neue Signalzustellung bleibt offen.

## Historischer Abschlussstand des ersten Pakets 02.10.2026 – Tiefenaudit / Dreimonatsproben

Dieser Abschnitt dokumentiert den Abschluss vor der neuen Livekontrolle oben.
Die Abschnitte ab „01.10.“ bleiben historische Nachweise, keine neuen Pull-/
Versandaufträge. Ausgangs-HEAD ist `796f8211a5692bb6cc98fb13075827926d30351d`.
**Das heutige Reparaturpaket ist committet und gepusht:
`93428a4998b0027740bea1f9c9df188f6743663f`, Remote `origin/main` separat bestätigt.
Auf Hetzner noch nicht aktiviert.** Geerbtes Deploy-/Handbuch-WIP und private
Quellen bleiben erhalten und wurden nicht veröffentlicht.
Veröffentlichung durch „alles erledigen“ freigegeben. Unabhängig geprüftes
93-Dateien-Paket im Git-Index, ohne private Exporte/Secrets/Deploy-WIP.
Exakter Index-Gesamtlauf: **10.777 bestanden, 1 übersprungen, 0 Fehler**,
425,95s; `tmp/qa-2c7fae09a9bc/results.xml`. Ausgeliefert wird auch der isolierte
Testlauncher `scripts/run_offline_tests.py`; keine neue Paket-/Reminder-Migration.

### Erledigt und am aktuellen Quellstand geprüft

- [x] Tatsächliches Inventar statt alter Chat-Auditlisten: 14 öffentliche
  Aktienstrategien, 11 manuelle Krypto-Profile, dedizierte BI-/Bear-/ORB-/Turtle-/
  Volume-/Penny-/Biotech-/Krypto-Pfade, Kontext-/Quote-/Watch-/Positionsjobs.
  Futures/Forex/International sind nicht implementiert; reine Kontextjobs
  haben keine zusätzliche Handelstrefferquote.
- [x] Aktienquellen und gemeinsame Strukturketten vertieft geprüft: abgeschlossene
  Regular-Session-4H-Slots, Adjustierung/Antwortstatus/Duplikate/Verfügbarkeit,
  Rohpräzision bis zum gerichteten Ordertick, SMC/FVG/Orderblock-/Pool-Lifecycle,
  Bear-60-Sitzungsbaseline und native erste Gegenbarriere, Harmonic-Pflicht-
  verhältnisse und bestätigter D-Pivot, inverse ETF-Quellenvertrag.
- [x] Reale MSFT-Probe bestätigt und korrigiert: eine transitive Levelkette
  hatte einen riesigen Unterstützungscluster erzeugt. Bei gleichen historischen
  Quellen Stop443,94 statt370,16; Entry451,10/TP1452,53 unverändert, weiterhin
  WAIT wegen naher Gegenbarriere. Zonenmodelle jetzt v2, keine erfundenen Ziele.
- [x] Hidden-Legacy-Pfade: Dip Buy korrekt Long; RVOL ohne Kappung/Entscheidungs-
  rundung; Volume Void/Churn eigene Cache-/Status-/Producerpfade. Manuelle
  Registrierung für Status, Cache und Datenquelle zusammen unter `_scan_lock`.
  Insider ohne Form-4-Quelle und entfernte Wick/All-Harmonic-Aufrufe ausdrücklich
  501 vor Provider/Worker; keine neuen Mails/Reminder aus alten Ersatzzeilen.
- [x] BI-RVOL/Struktur-/Liquiditätsgrenzen roh korrigiert; BI-Vertrag
  **`stock-bi-20-v8`**, alle20 Faktoren erforderlich, **17/20 unverändert**.
  Biotech Full/Quick verwenden gemeinsame Newsnegation/Ergebnis-/Risikoregeln,
  keine alten positiven Katalysatoren bei frischer Nichtverfügbarkeit.
  Penny strikt datierte echte Daily-/5m-Quellen, keine Ersatzpreise/Futurelevels.
- [x] Krypto-BTC-Kontext/-Vergleichsfenster, echte Closed-ATH-/Triggerkerzen,
  Rohschwellen, Quotes/Book/Contractgröße, bekanntes Null gegenüber unbekannt,
  Listingepisode/Retry/Annahme von Lease getrennt. Kein erfundener MarketCap/OI/
  Funding- oder Neutralwert. Profilcache3, Listingcache/episode4.
- [x] Cup-Watch echter Versandfehler korrigiert: alter30-Tage-Cooldown war als
  aktuelle Mailannahme behandelt worden. Nur frische gültige SMTP-Annahme
  beendet die Watch. Generation strikt integer; alter Claim löscht keine neue
  Generation. Ablauf nach tatsächlicher Session/Frühschluss.
- [x] Frontend-Dauerloading korrigiert: Kalenderprognose ist keine Ergebnisrevision,
  langsame Reads bewahren vorhandene32 Zeilen, angezeigter Snapshot bestimmt
  „Ergebnisstand“ statt Owner-/fremder Laufzeit. Tatsächlich gebautes Bundle im
  lokalen Browser mit gesperrten POSTs/externem I/O geprüft, keine Konsolenfehler.
  Browsertab und temporärer Testserver anschließend geschlossen.
- [x] Abschließender eingefrorener Offline-Gesamtlauf: **10.773 bestanden,
  1 übersprungen, 0 Fehler**, 415,52s; `tmp/qa-b4e0cda07cd9/results.xml`.
  Der Skip betrifft POSIX-Dateirechte auf Windows.
  `test_deploy*.py` ausdrücklich ausgeschlossen, kein Linux-Installernachweis.
  Zwei reine Deploy-Entfernungs-/Installer-Guardtests separat bestanden:
  `tmp/qa-1ed7a76b2832/results.xml`. Fokusgruppen nicht addieren.
- [x] Unabhängiger finaler Cup/Hidden/Krypto-Lifecycle188/188 grün,
  `tmp/qa-8f79860cad66/results.xml`. Alte/future/bool/NaN/Inf/abgelaufene
  Annahmestempel, gleichzeitiges Watch-Upsert und Generationenwechsel geprüft.
- [x] Private historische Studien mit echten Quellen abgeschlossen und gehasht:
  **02.07.–01.10.2026**, 64 US-Sessions, je3 vorab fixierte Assets pro Datenfamilie.
  18 eindeutige Quelldateien; vollständige RTH-Slots und87.264 Spot-Kryptobars.
  Je Scanner erste3 chronologische Beobachtungen, nicht Gewinnercherrypicking.
- [x] Alle fünf finalen `history-*-verified.json` enthalten dieselben88 aktuellen
  Produktions-/Research-Quellfingerprints. Baseline vor Producerimport;
  Prüfung vor/nach Replay und unabhängig gegen tatsächlichen Checkout.
  Ältere `release`-/`final`-/Probe-Dateien bleiben historische Zwischenstände.
- [x] Historische Nenner ehrlich getrennt:100 technische Aktienkandidaten ohne
  Elliott,98 native Level,0 kandidatenseitig freigegebene Modellpläne;
  BI384 Prüfungen, maximal13/20,0 bei17/20. Quote ohne gefüllte Trades ist
  **nicht berechenbar**, nicht0%. 5-Session-/24h-Richtung ist keine Netto-PnL.
  Biotech/Penny/ORB/Bear/Krypto fehlende historische Vollscannerinputs ausdrücklich
  unbekannt; keine erdachten News-/Universe-/Quote-/Funding-/Listingzustände.
- [x] Alte tatsächliche Trackerhistory292 Zeilen separat bewertet; Export endet
  26.09., nicht01.10. Fehlende Herkunft/Ticker und alter Algorithmus verhindern
  eine Gewinnquote des heutigen Reparaturpakets.

[Aktueller Reparatur-/Prüfbericht](docs/SCANNER_DEEP_AUDIT_2026-10-02.md).
Private Übersicht: `output/scanner-deep-audit-20261002/HISTORICAL_SAMPLE_REPORT.md`.
Vollständige Code-/Test-/Quellenmatrix:
`output/scanner-deep-audit-20261002/SCANNER_COVERAGE_MATRIX.md`.
Private Ausgaben sind gitignored; nicht mit Source nach GitHub laden.

### Tatsächlich noch offen / nicht als erledigt übernehmen

1. [x] Scoped Veröffentlichung des heutigen Pakets: Freigabe, unabhängige
   Prüfung,93 konkrete Stage-Pfade, Index-Gesamtlauf, Commit/Push und separater
   Remotehashvergleich abgeschlossen. Private Quellen/Exporte ausgeschlossen.
   Kein safe_deploy, Ersatzinstaller oder Eigentumsumbau.
2. [ ] Nach Betreiberupdate vollständige neue Scannerläufe unter neuen Verträgen
   prüfen: Aktiencache20, BIv8, Kryptoprofil3, Listing4, Zonenmodelle v2.
   Alte Cachezeilen nicht als neue Resultate/Reminderanker umetikettieren.
3. [ ] Konkreten inneren Grund der Serverabbrüche vom01.10. für Momentum/Cup/
   Turtle weiter einholen. Vorhandener gezielter Lesetest
   `scripts/probe_stock_attempt_errors.py` ist fertig; aktuelle Antwort fehlt.
   Kein pauschaler weiterer Gesamtexport nötig. Heute kein direkter SSH-Zugang;
   lokale Präzisions-/Quellfixes beweisen nicht den damaligen Fehleruntercode.
4. [ ] Echte aktuelle **Handelssignalzustellung** separat bis Posteingang prüfen.
   Einmalige technische Testmail am01.10. angenommen UND Betreiberempfang
   bestätigt; Autorisierung verbraucht, nicht erneut senden. Aktuelles begrenztes
   Mailfenster02.10.11:54:18 MESZ:1 Swing-Empfänger,37 ausgelassen,0 angenommen,
   0 Versandfehler,0 Queue. Ablehnungen vorSMTP, kein allgemeiner Transportausfall
   belegt. Keine Freigabe fingieren, Kanäle/17/20/Grade/Risiko lockern.
5. [ ] Vollständige historische Netto-Trefferquoten dort erst mit fehlenden
   damaligen Daten berechnen: News/Earnings/BPIQ/Float/SEC, Marktbreite/VIX/ETFs,
   CG-Universum/MarketCap/Umsatz, Listingzeiten/Contracts/Books/Funding/OI,
   Ausführungs-/Positionszustände. Kleine feste Stichproben und Spot-Candlekerne
   ersetzen diese Inputs nicht. Aboerneuerung/zusätzliche Kosten nicht autorisiert.
6. [ ] Originalkerzen für acht alteVIAV-Strukturen undAST-Zeichnungen bleiben
   unvollständig. 8H nicht als unterstützten Chartzeitrahmen behaupten;
   TradingView-Profil nicht mit unserem OHLCV-Rangeprofil gleichsetzen.

### Nächste Betreiberaktion – kein erneuter Gesamtexport

Direkter SSH-Batchzugang hier mit `Permission denied` abgewiesen. Nach Abschluss
laufender Scanner und SMTP-Vorgänge im eigenen Server-Terminal ausführen:

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Danach Healthausgabe/Revision schicken und Browser hart neu laden. Erwartetes
Bundle `6a488c9c8f1a`; Quellcommit `93428a4` plus Abschlussdokumentation. Kein
Deploy-Skript, keine Paketinstallation, Reminder-Migration, Unitkopie, chown,
daemon-reload oder Cachelöschung. Stop/Pause ist kein Restart-Checkpoint.

### Quell-/Test-/Betriebscheckpoint

- API-SHA256 `1118c313be477386fade5e10fcd1ffd0123c95e44df4cd7abea022ade7fa095f`.
- Frontend Sourcehash `6a488c9c8f1a`; Bundle-/Syntaxprüfung bestanden.
- Python-Kompilierung und repository-normalisierte CRLF-/Whitespace-Prüfung grün.
- Quellenmanifest `412439cd67cd7cd95b7e54933b05bcd6d173e4c7126bd5f0898326b479ef0775`;
  `source-coverage-oct02-final.json` mit echten Dateihashes/64Sessions/Slotprüfung.
- Produktive rein lesende Healthkontrolle02.10.: healthy, Revision
  `796f8211a569`, Bundle `41f168c9f109`; Server seit dieser Arbeit unverändert.
- Interpreter `.codex_pytest_env\Scripts\python.exe`; isolierter Launcher
  `scripts/run_offline_tests.py`, QA-Ausgabe nur in `tmp`. Der frühere private
  Launcher bleibt historisch, nicht Voraussetzung für einen frischen Checkout.
- Keine echte Order, erneute Testmail, Scanstart, Account-/Abo-/Dienständerung
  oder Entfernung privater History während dieses Auftrags. Veröffentlichung
  des geprüften Quellpakets erfolgte nach ausdrücklicher Abschlussfreigabe.

## Historischer Stand 01.10.2026 – nicht mehr maßgeblicher Einstieg

## Maßgeblicher Accountwechsel-Stand 01.10.2026

Diese Zusammenfassung ist der aktuelle Einstieg. Die älteren Abschnitte darunter
sind ein historisches Anschlussprotokoll; deren Pullbefehle, Serverstände und
offene Rolloutkästchen nicht als heutige Arbeitsanweisung übernehmen.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.

### Abgeschlossen und jetzt erneut geprüft

- [x] Level-/Trendlinien-/Volumenprofil-Reparatur committet und veröffentlicht:
  `796f8211a5692bb6cc98fb13075827926d30351d`. Lokales HEAD und GitHub `main`
  am 01.10. erneut geprüft und identisch. Vorgänger `0a3de85` und `77d0480`
  sind enthalten; keine offenen Codeänderungen dieses Reparaturpakets.
- [x] Gemeinsamer verfügbarer Chartdatenstand: offene/future Kerzen bestätigen
  keine Struktur; Starter-US-Aktien berücksichtigen 900 Sekunden Verzögerung
  vor dem Abruf. Live, Krypto und Nicht-US-Routen bleiben davon getrennt.
- [x] Kausale Trendlinien mit drei bestätigten Ankern, eingefrorener Geometrie
  und dauerhafter Entwertung nach Bruch. Horizontale Zonen und Projektionen
  bleiben getrennte Nachweise; keine erfundenen Entry-/Stop-/TP-Level.
- [x] Native und sichtbare Volumenprofile: gültige echte Volumenträger,
  Volumenerhaltung, Mikropreise und geschlossene Kerzen. Sichtbares Profil
  folgt Zoom und Chartpreisskala. Finale Stop-/Risiko-/Zielmetadaten stimmen
  mit dem ausgegebenen Plan überein; historische Nachweise bleiben separat.
- [x] Cache-Neuberechnung abgesichert: Aktienstrategien Version 18,
  BI `stock-bi-20-v6`, Krypto-Profilvertrag 1, New Listing Vertrag 3.
  17/20, Mailfreigaben und Risikogrenzen nicht gelockert.
- [x] Eingefrorener App-Gesamtlauf: **9.959 bestanden, 1 Windows-Skip,
  0 Fehler**, 514,69 Sekunden. Getesteter Code-/Test-Indexbaum
  `88806fc2c21a2bce66b4e16fe788f954eaac07ca`; Commit unterscheidet sich davon
  ausschließlich durch den neuen Prüfbericht. Bei der Übergabe erneut anhand
  von Git und JUnit geprüft, keine erneute lange Testsuite gestartet.
  `test_deploy*.py` war ausdrücklich ausgeschlossen; frühere Windows-Bash-
  Zeitüberschreitungen sind kein erfolgreich geprüfter Installerpfad.
- [x] Vier lokale Browserfälle (Sidebar/Analyse, 1440/390 Pixel) ohne Fehler;
  tatsächliches Bundle mit synthetischen Daten, externe Zugriffe gesperrt.
  Bundle-Quellhash `41f168c9f109`, Syntax-/Bundleprüfung bestanden.
- [x] **Serverupdate inzwischen bestätigt:** öffentliche API-Health-Abfrage
  am 01.10.2026 um 20:30 MESZ (Europe/Zurich) liefert `healthy`,
  Revision `796f8211a569`, Frontend `41f168c9f109`. Das Paket ist aktiv;
  hier kein Pull, Neustart, Scanstart oder Versand. Kein erneuter Pull nötig.
  Diese Health-Antwort ersetzt keine Einzelprüfung aller systemd-Dienste.

[Reparaturbericht](docs/LEVEL_TRENDLINE_VRVP_REPAIR_2026-10-01.md).
Der dortige Serverstand `0a3de85` beschreibt die frühere Abnahme vor dem Update;
maßgeblich für den jetzigen Serverstand ist die neue Health-Prüfung oben.

### Nächster Anschluss – in dieser Reihenfolge

1. [ ] Neue vollständige Strategie-/Gap-/BI-Läufe unter `796f8211a569` prüfen:
   Laufkennung, Datenzeit, korrekter neuer Cachevertrag, vollständiger Abschluss,
   konkrete Ausschlussgründe und finale Plangeometrie. Alte Caches nicht als
   neue Ergebnisse bewerten. Laufende Scanner nicht unnötig neu starten.
2. [ ] **Mailversand bleibt offen, nicht als repariert abgeschlossen melden.**
   Bei genau einem aktuellen gültigen Signal die vollständige Kette im selben
   Zeitfenster verfolgen: Scannerfreigabe, Empfänger-/Kanalzulassung,
   Unterdrückungsgrund, Outbox, SMTP-Annahme und tatsächlicher Posteingang.
   Eine Crash-/Infomail ist kein Beleg für Handelssignalzustellung; keine
   Schwellenlockerung oder fingierte Freigabe. Vorhandene private Exporte
   zuerst prüfen, keinen identischen Export ohne konkreten Bedarf verlangen.
3. [x] Einmalige persönliche technische Testmail ist vom Betreiber autorisiert.
   Implementierter Admin-Button stammt aus `0a3de85` und ist jetzt im Rollout
   enthalten. Am **01.10.2026, 22:41:53 MESZ** genau einmal über den Admin-Dialog
   gesendet: Antwort **„Vom Mailserver angenommen“**, danach auch im Versandprotokoll
   eine SMTP-Annahme sichtbar. Kein zweiter Versuch. Der Betreiber hat danach
   den tatsächlichen Postfachempfang bestätigt. Damit ist der technische
   Transport bis ins Postfach nachgewiesen, nicht die Handelssignal-Freigabe.
   Privater Bildnachweis: `output/mail-transport-proof-20261001.jpg`.
4. [ ] API/BG/Frontend als einzelne systemd-Dienste rein lesend prüfen, sofern
   für den konkreten Betriebsfehler erforderlich. Health allein beweist nicht
   jeden Hintergrundlauf. Kein Deploy-Skript, keine Eigentums-/Installationsmigration.
5. [ ] BPIQ/Biotech-Abo ist laut Betreiber abgelaufen: 401 als separate
   Anbieterberechtigung behandeln. Kein globales Mailproblem daraus ableiten;
   andere Scanner unabhängig prüfen. Abos/Schlüssel nicht eigenmächtig ändern.
6. [ ] Originalkerzen für die acht alten VIAV-Strukturen und die drei manuell
   gezeichneten AST-Linien fehlen weiterhin. Keine exakte Nachberechnung behaupten.
   8H ist kein implementierter Chartzeitrahmen; unterstützt sind 5m/15m/1H/4H/1D/1W.
   TradingView-Profil und unser OHLCV-Rangeprofil haben nicht dieselbe Datengrundlage.
7. [ ] Separates geerbtes Deploy-Entfernungs-/Handbuch-WIP bleibt uncommittet.
   Nicht mit dem abgeschlossenen Levelpaket vermischen oder ohne Nachprüfung
   veröffentlichen. Private `output/`-/`tmp/`-Artefakte nicht auf GitHub laden.

### Reproduzierbare Übergabe / lokale Arbeitskopie erhalten

- Funktionierender Interpreter: `.codex_pytest_env\Scripts\python.exe`.
  Isolierter Launcher: `tmp/offline_mail_fix_tests_20260925.py`.
  App-Tests nur mit separaten kurzen QA-Datenpfaden ausführen; keine echten
  Secrets, produktiven Datenbanken oder SMTP für Tests verwenden.
- Finales privates JUnit: `tmp/qa-a7f6faae0bad/results.xml`.
  Getesteter Indexexport: `tmp/levels-publish-8a2e757587d848898ac9f920c13d4a97/source/`.
  Browsernachweis: `output/playwright/levels-20261001/result.json` und Screenshots.
- Geerbte lokale Änderungen erhalten: TODO, Handbuch-/Übergabedateien,
  `COMMERCIAL_LAUNCH_CHECKLIST.md`, `docs/SCANNER_REAUDIT_REPAIR_2026-09-30.md`,
  Deploy-Anleitungen/Updater/Installer/Migration, gelöschtes `deploy/safe_deploy.sh`,
  Deploy-Tests sowie `test_calendar_and_crypto_safety.py` und restliche
  `test_commerce_hardening.py`-Änderungen. `test_deploy_retirement.py` ist untracked.
  Nicht pauschal stagen, zurücksetzen oder löschen.
- Diese Accountwechsel-Aktualisierung ändert ausschließlich `TODO.md` lokal.
  Kein neuer Commit/Push, Codeeingriff oder Server-/Kontoeingriff dafür.

### Direkte Mailprüfung am 01.10.2026, 22:36–22:48 MESZ

- [x] Angemeldete Produktivansicht **Admin → Mailversand** direkt geprüft,
  ohne neuen Export, Scanstart, Neustart oder Einstellungsänderung.
  Vor dem technischen Test: 1 Swing-Empfänger, 50 ausgelassene Entscheidungen,
  0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange. Das ist das begrenzte
  Fenster der letzten maximal 50 Ereignisse seit API-Start, höchstens 24 Stunden,
  keine vollständige Versandhistorie.
- [x] Eigene Einstellungen rein lesend bestätigt: Signal-Mails aktiv,
  Aktien Swing und Biotech eingeschaltet, Mailmodus **Swing**. Auch die anderen
  angezeigten Kanäle sind eingeschaltet; Watchlist-Mails bleiben wie bisher aus.
  Keine Kanal-/Modusänderung und kein Eingriff in das abgelaufene BPIQ-Abo.
- [x] Crash-Mails sind `info`/`bear`, Aktienstrategie-Mails
  `swing_trade`/`stocks_swing`; Crash-Zustellung beweist daher keinen
  bestandenen Handelsplan. Die aktuellen Ablehnungen erfolgen vor SMTP.
- [x] Gegenprüfung des bestehenden Gap-Vertrags: bestätigte Long-/Short-
  Schlusskursausbrüche **ohne Rücktest** können den echten Produzenten,
  Planprüfer, finalen Revalidator und Sender bis zum Journal durchlaufen.
  **8 gezielte Tests bestanden**, externe Zugriffe und SMTP ersetzt/gesperrt.
  Privates JUnit: `output/mail-fix-qa-a5830c6ecd39480d92b06fec87319571/results.xml`.
- [x] Alle 22 vorhandenen Serverexporte sind historisch; neuester vom
  26.09.2026, Revision `75dad91de2d5`, also vor `796f821`. Kein weiterer
  identischer Export angefordert. Die aktuelle Prüfung erfolgt direkt in der App.
- [x] Reproduzierter Anzeige-/Frischefehler lokal behoben: Gap nutzte bei der
  Ergebnisdiagnose die 60-Minuten-Strategierunde und deren Zwei-Stunden-
  Altersgrenze statt seiner vereinbarten 02:00/12:00-Zeitfenster und aktuellen
  abgeschlossenen 1D-Sitzung. Neue Prüfung berücksichtigt den nachgewiesenen
  abgeschlossenen US-Handelstag einschließlich 15-Minuten-Verfügbarkeit,
  Feiertagen und Frühschluss. Auch leere Ergebnisse benötigen diesen Beleg.
  Veraltete Sitzungen bleiben gesperrt; expliziter Live-Modus bleibt getrennt.
  Keine Änderung am Zeitplan oder an tatsächlichen Mailfreigaben.
- [ ] Echte aktuelle Handelssignalzustellung weiterhin nachweisen; der
  technische Test hat den SMTP-Transport bestätigt, nicht die einzelnen
  Berechnungen hinter allen Live-Ablehnungen. Vorhandene Gap-Caches gegen
  22:40 MESZ waren ca. zehn Stunden alt und nach dem neuen US-Tagesabschluss
  fachlich nicht mehr aktuell. Nächster Gap-Termin laut Scheduler: 02:00 MESZ.

### Laufende Reparatur nach dem bestätigten Testmail-Empfang

- [x] Echter Empfang der einmaligen technischen Testmail vom Betreiber bestätigt.
  Kein zweiter Testversand. Aktuelle SMTP-/Postfachverbindung funktioniert;
  ein genereller Versanddefekt erklärt die fehlenden Handelssignale nicht.
- [x] Weitere Grenzwertfehler lokal reproduziert und korrigiert: ATR-Erweiterung,
  Wick-Anteil, Tageshoch gegenüber TP1, Tagesbewegung, ATR-Mindestbudget und
  Schlusskurslage wurden vor Entscheidungsprüfungen gerundet. Das erzeugte
  sowohl falsche Ablehnungen als auch falsche Freigaben. Entscheidungswerte
  bleiben nun ungerundet; Schwellen und Tickgeometrie der Orderlevel unverändert.
  Cacheversion lokal **19**; Produktionsrevision `796f821` verwendet noch 18.
- [x] Kombinierte Offline-Nachprüfung des eingefrorenen lokalen Gap-/Präzisions-
  Pakets: **558 bestanden, 1 Windows-Skip**, 23,30 Sekunden; private Ergebnisse
  `tmp/qa-a10af1d3653d/results.xml`. Zusätzlich unabhängige Gegenprüfung der
  75 neuen Gap-/Präzisionsfälle und drei Kalendergrenzfälle bestanden.
  Zwischenbundle `0f72f762011d` aufgebaut, Quellzuordnung und Syntax geprüft;
  letzter Stand nach der kompakten Warntextkorrektur: **`c09114e47194`**.
- [ ] **Neu belegter Produktionsabbruch** am 01.10., 20:48–20:49 UTC:
  Momentum stoppt bei 3.379/12.582, Cup bei 1.171/12.582 mit `scan_data_invalid`;
  Turtle scheitert am Vergleich von Sammelfeed und Einzelhistorie mit
  `scan_data_incomplete`. Das sind keine abgeschlossenen Nulltrefferläufe.
  Der äußere Stacktrace verdeckt den inneren Grund. Vorhandene feste Untercodes
  und Ausschlusszähler gezielt lesen; nicht aus Laufzeit oder Abrufzahl erraten.
- [x] Turtle-Feldnamen-/Sessionwechsel-Vermutung kontrolliert: kein Aliasfehler
  nachgewiesen; Adapter liefert sowohl kanonische als auch kurze OHLCV-Namen.
  Preis-/Volumenabweichungen können den Abbruch reproduzieren, sind aber noch
  nicht als tatsächlicher Produktionsgrund belegt. Keine Toleranzen gelockert.
- [x] Separat reproduzierter Turtle-Quellenvertragsfehler lokal korrigiert: ausdrücklich nicht
  splitbereinigte Einzelhistorie wurde für einen splitbereinigten Plan akzeptiert.
  Anfrage nun ausdrücklich splitbereinigt, widersprüchliche vorhandene
  Antwortkennzeichnung vor jeder Berechnung abgewiesen. Fehlende alte Kennzeichnung
  bleibt bei ausdrücklicher Anfrage kompatibel. **38 gezielte Tests bestanden**;
  diese erklären den
  heutigen Datenabbruch noch nicht. [Anbieter-Datenvertrag](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
- [x] Gezielter Lesetest `scripts/probe_stock_attempt_errors.py` und 45 Regressionen
  fertig. Er liest nur zwei feste Attempt-Dateien im `/tmp`-Namespace des API-
  Prozesses; prüft PID/Startzeit, Dateityp, Größe, unveränderten Inhalt und JSON-
  Vertrag. Keine App-Imports, Umgebungs-/Zugangsdaten oder Kurs-/Empfängerzeilen.
  Turtle wird ehrlich als nicht dauerhaft gespeicherte Diagnose gemeldet.
  Unabhängige erste Abnahme: 54/54 Probe-/Turtlefälle bestanden.
- [x] Abschließender kombinierter Offline-Lauf nach Turtle-, Probe- und UI-
  Nachkorrekturen: **905 bestanden, 1 Windows-Skip**, 32,49 Sekunden,
  0 Fehler; bestehende anyio-Importwarnung. Privates JUnit:
  `tmp/qa-f18387473152/results.xml`. Erstlauf enthielt einen Windows-
  Metadatenrennen-Test und eine alte UI-Text-Erwartung; Parserprüfung sauber
  vom Dateirennen getrennt und kompakte Warnung im tatsächlichen UI-Ablauf geprüft.
  Keine Linux-Dateiprüfung oder Handelsgrenze gelockert.
- [ ] Antwort auf den gezielten aktuellen Untercode-Check auswerten:
  `Get-Content -Raw "C:\Projekt\TradingBot\scripts\probe_stock_attempt_errors.py" | ssh -T -o StrictHostKeyChecking=yes root@178.104.69.209 "/usr/bin/python3 -I -"`.
  Konkrete Karte entscheidet zwischen Historienformatfehler, Referenzmismatch
  und Ausschlusslimit. Direkter SSH-Zugang abgewiesen; direkte API-Navigation
  im Browser blockiert. Keine Zugangsdaten aus der Sitzung extrahiert.
  Nicht blind Ausschlusslimits, Kohärenzprüfungen oder Freigaben entfernen.
- [ ] Aktuelles Paket noch **nicht committet/gepusht oder auf Hetzner aktiviert**.
  Geerbtes Deploy-/Handbuch-WIP und private Exporte/Bildbeweise bleiben getrennt.
  Kein Deploy-Skript, keine Installationsmigration, kein Serverneustart in dieser Prüfung.

## Historisches Anschlussprotokoll – frühere Stände, kein neuer Pullauftrag

## Anschluss 30.09., technische Testmail und native Plan-Nachprüfung

- [x] Drei Planfehler aus neuen Long-/Short-Kerzenfolgen nachgestellt:
  TP2 verliert unabhängige Zone/Quellfamilie; TP1 verliert Validierung;
  VRVP zieht Stop in die ursprüngliche Invalidierungszone. Lokal korrigiert.
- [x] 16 neue kausale Plan-/Cachetests bestanden. Engere Gegenbarrieren,
  Projektionsstatus, Zukunftskerzen und neu berechnetes Risiko/R:R geprüft.
  Aktienstrategie-Cachevertrag jetzt 17; Version 16 verlangt einen neuen Lauf.
- [x] Sendergegenprüfung: echte Gap-Pläne Long/Short erreichen mit simuliertem
  SMTP den Tracker; BPIQ-401 und Watch-AUS sind keine globale Swing-Mail-Sperre.
  Im isolierten Transportpfad kein neuer Mailablehnungsfehler nachgewiesen.
- [x] Technische Testmail vom Betreiber ausdrücklich freigegeben. Admin-Button
  mit Inline-Bestätigung, eigene Adminadresse statt Verteiler, Klasse `info`,
  kein Handelssignal/keine Order. Keine automatische Wiederholung oder spätere
  Warteschlangen-Zustellung dieser persönlichen technischen Prüfung.
- [x] 11 neue UI-Handler-/Cookie-/Doppelklick-/Abbruchtests bestanden;
  Bundle `baffb67797ba`. Neue Testmail löst keinen Scanner aus.
- [x] 21 neue Backend-/Transporttests und eingefrorener Gesamtlauf bestanden:
  9.809 bestanden, 5 Plattform-Skips, keine Fehler in 1.191,42 Sekunden.
  SHA256 der acht Code-/Testdateien unverändert. Bestehende anyio-Importwarnung.
- [x] Scoped Commit/Push der neun zugehörigen Dateien:
  `0a3de85639039c739baf014a6d002301bb9d3744` auf `main`, HEAD und `origin/main`
  identisch. Bestehende Deploy-Änderungen und private QA-/Exportdateien nicht
  eingeschlossen. Der Commit enthält auch den zuvor veröffentlichten BI-
  Fortschrittsfix `77d0480` als Vorgänger.
- [ ] Betreiber-Pull und neuer vollständiger Strategielauf. Kein Serverupdate
  durch diesen Anschluss, keine Installationsumstellung/Deploy-Skript.
  Erwartet: Revision `0a3de8563903`, Frontend `baffb67797ba`. Nach Abschluss
  laufender Scans normal pullen, API/BG neu starten, Health prüfen, Strg+F5.
- [ ] Genau eine freigegebene technische Testmail auf Hetzner senden und
  SMTP-Ergebnis sowie Empfang prüfen. Sie wurde hier noch NICHT gesendet.
  Direkte SSH-Authentifizierung fehlt; vorhandener Browser besitzt den neuen
  Button erst nach Pull. Kein weiterer privater Export angefordert.
- [ ] Gültige reale Signal-Mail danach getrennt kontrollieren. Live-Ansicht
  19:44 UTC: 31 ausgelassen, 0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange
  im begrenzten Fenster seit API-Start. Die neuen Planfälle beweisen nicht die
  alleinige Ursache sämtlicher ausbleibender Mails. Keine Grenzen gelockert.

[Aktueller Bericht](docs/NATIVE_PLAN_MAIL_REAUDIT_2026-09-30.md).

## Anschluss 30.09. nach Accountwechsel

- [x] `d06b8af58f35` am 30.09. um 17:49 UTC live auf Hetzner bestätigt:
  gesund, Bundle `2ebd16934df3`; lokales HEAD und `origin/main` identisch.
  Die beiden vorigen Pakete sind bereits eingespielt; dafür kein weiterer Pull.
- [x] BI-Fortschritt reproduziert und lokal an tatsächliche Lauf-/Worker-ID
  gebunden, auch ohne ersten Treffer. API und UI prüfen dieselbe Zuordnung;
  Altläufe, Richtungsverwechslungen und ungültige Zähler bleiben ausgeschlossen.
  Biotech nutzt denselben Fortschrittsvertrag und atomare Veröffentlichung.
- [x] Widersprüchlichen zweiten BI-Text „Scan läuft“ bei Pause entfernt.
  Desktop, pausierter Lauf, fremder Short-Lauf und Mobilansicht geprüft;
  205 gezielte Tests bestanden. Eingefrorener Gesamtlauf: 9.761 bestanden,
  5 Plattform-Skips, keine Fehler in 1.439,70 Sekunden. SHA256 aller sieben
  geänderten Code-/Testdateien unverändert; Bundle `15fd2ea4752d`.
- [x] Fortschrittskorrektur als `77d048077321` committet und auf `origin/main`
  bestätigt. Ausschließlich neun zugehörige Code-/Test-/Berichtsdateien;
  keine privaten Exporte oder bestehenden Deploy-Änderungen enthalten.
- [ ] Betreiber-Pull von `77d0480` nach laufenden Scans; diese Revision ist
  noch nicht auf Hetzner bestätigt. Kein Deploy-Skript, keine Umstellung.
- [ ] Neue vollständige Biotech-Auswertung und tatsächliche Signalzustellung
  bleiben offen. Mailansicht 18:14 UTC: 8 ausgelassen, 0 SMTP-Annahmen,
  0 Versandfehler, Warteschlange leer im begrenzten Laufzeitfenster.
  Biotech-Vorprüfung noch 12 Altkandidaten mit altem News-Vertrag.
  Scheduler 18:21 UTC: Strategierunde abgeschlossen, BI Long läuft,
  leichte Überwachungsprüfungen laufen parallel; Biotech weiterhin Altdaten.
- [x] Ursache des BPIQ-401 am 30.09. vom Betreiber erklärt: Biotech-Abo beim
  Anbieter abgelaufen. Kein globaler Mail-Schalter; andere Scanner und
  Alpha-Station-Empfängerberechtigungen bleiben davon unabhängig. Keine
  Zugangsdaten, Abos oder Mailpräferenzen geändert.
- [x] Reale Mail-/Kanalprüfung 30.09.: Signal-Mails AKTIV, alle sieben Kanäle
  AN, Watchlist-Mails AUS. Schlusskontrolle 18:46 UTC: 16 ausgelassen,
  0 SMTP-Annahmen, 0 Versandfehler, leere Warteschlange; begrenztes Fenster
  seit API-Start, kein historischer Gesamt- oder Postfachnachweis.
  Konkrete Momentum-Beispiele: ABCL Score 87, Tagesqualität 73/96 statt 78
  und kein bestätigtes Strukturziel; FPI Tagesqualität 89/96, aber fehlende
  Planwerte und Score 45; IDT Score 69, Tagesqualität 66/96. BI-Caches leer.
  Somit Ablehnung vor SMTP, nicht durch das Biotech-Abo.
- [ ] Echten Transport/Empfang gesondert prüfen, sobald ein gültiges Signal
  entsteht oder der Betreiber eine technische Testmail ausdrücklich anfordert.
  Kein erzwungenes Handelssignal, keine Schwellenlockerung, keine Testmail
  oder Signalwiederholung in dieser Prüfung. 175 gezielte Offline-Tests
  bestanden (110 Momentum, 65 Provider/Scheduler/Empfängerrouting).
- [ ] Bestehende lokale Entfernung des Deploy-Skripts samt abhängigen
  Schutzprüfungen bleibt als getrennte, uncommittete Arbeit erhalten.

[Prüfbericht](docs/BI_PROGRESS_RUN_BINDING_2026-09-30.md).

Normaler Betreiberbefehl (erst nach Abschluss laufender Scans):

```bash
sudo -u tradingbot git -C /home/tradingbot/app pull --ff-only origin main &&
sudo systemctl restart tradingbot-api.service tradingbot-bg.service &&
curl -fsS --retry 30 --retry-connrefused --retry-delay 2 --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health
```

Erwartet: `revision` **`77d048077321`**, `frontend_bundle` **`15fd2ea4752d`**.
Danach App mit Strg+F5 laden. Kein Neustart oder Scanstart durch diesen Anschluss.

## Anschluss 30.09., Abend: reale Mailprüfung und präzise Ablehnungsgründe

- [x] Hetzner live gesund auf `716f2ebd1e0c` bestätigt (16:28 UTC).
  Vorherige Biotech-/Schedulerkorrektur ist damit vom Betreiber eingespielt.
- [x] Aktien-Strategierunde abgeschlossen, BI Long danach laufend zusammen
  mit leichten Prüfungen beobachtet; kein eigener Scanstart/Neustart.
- [x] Langsame Admin-Maildiagnose auf anfragegebundene Referenzwiederverwendung
  umgestellt; keine zusätzliche Cache-Wiederverwendung im Sender.
- [x] Crash-Hinweise und Handelssignale in der Vorprüfung getrennt, bekannte
  SMTP-Fehlercodes verständlich zugeordnet; UTC und unbekannter nächster Lauf
  eindeutig beschriftet.
- [x] Falsches „R:R unter Mindestwert“ bei bloßen Projektionszielen reproduziert
  und behoben. Struktur, Ausbruchsbestätigung, Zielaufteilung und numerisches
  R:R getrennt; Freigaberegeln unverändert. Telemetrie/Export mitgezogen.
- [x] 400 fokussierte Gegenproben, danach 370 Diagnose-/Exportprüfungen grün;
  Desktop/Mobil geprüft. Eingefrorener Gesamtlauf: 9.704 bestanden, 5 Skips
  in 880,59 Sekunden. SHA256 der 13 geänderten Code-/Testdateien unverändert.
  Zusätzlich 2.880 Zulassungsentscheidungen gegen die bisherige Version
  verglichen: identisch. Bundle `2ebd16934df3`.
- [x] Diagnose-Patch nach Gesamtabnahme als `d06b8af` committet und nach
  `origin/main` gepusht. Nur 15 zugehörige Code-/Test-/Berichtsdateien;
  keine privaten Exporte oder bestehenden Installationsänderungen enthalten.
- [x] Hetzner auf `d06b8af` aktualisiert: beim Accountwechsel am 30.09.
  um 17:49 UTC live gesund bestätigt. Der Betreiber hat das Update eingespielt;
  kein Serverneustart durch diesen Anschluss. Der lokale TODO samt früherer
  Handbuch-/Deploy-WIP bleibt ausdrücklich uncommittet.
- [ ] Neue vollständige Biotech-Auswertung sowie SMTP-Annahme eines tatsächlich
  freigegebenen Signals und Empfang prüfen. Schlusskontrolle 17:25 UTC:
  14 übersprungene
  Versandentscheidungen, 0 SMTP-Annahmen, 0 Versandfehler, 0 Warteschlange im
  begrenzten Laufzeitfenster. Kein Beweis über sämtliche historischen Mails.
- [ ] BPIQ-Zugriff bleibt ein separates Anbieter-Autorisierungsproblem (401),
  kein SMTP-Problem; keine Zugangsdaten geändert.
- [x] Separate BI-Fortschrittslücke lokal behoben, Serverabnahme noch offen.
  Ursprünglicher Befund: Live läuft BI Long, Anzeige bleibt
  „Fortschritt noch nicht bestaetigt“. Im Code sind BI-Zähler vorhanden,
  aber `get_scan_status` gibt deren Zeit-/Laufbindung nicht weiter;
  `scannerSelectedProgress` ignoriert solche ungebundenen Zähler korrekt.
  Ein partieller Ergebnis-Cache entsteht erst beim ersten Treffer. Dadurch
  blieb ein arbeitender Nulltrefferlauf unsichtbar. Im nächsten Anschluss
  als `77d0480` repariert: Fortschrittsdaten bereits beim Schreiben sicher
  an Lauf/Owner binden und bis zur UI durchreichen; keine alten Zähler übernehmen.
  Dieser Fund wurde nicht in den eingefrorenen Mail-Abnahmelauf hineineditiert.

[Details](docs/MAIL_DIAGNOSTIC_CAUSES_2026-09-30.md).

Stand: **30.09.2026**, Reparatur der neun erneuten Scannerbefunde.
Workspace: `C:\Projekt\TradingBot`, Branch `main`.
Aktueller Anschlussauftrag: R1–R9 aus der Nachprüfung von `b6be1f2` beheben.
Implementiert: ORB-Finalquote/Datenausfall, Biotech-Negation/Publikationszeit,
Turtle-Sitzungskohärenz, abgeschlossene Chartkerzen, kausale FVG-/OB-Schwellen,
FVG-Entwertung und Penny-OHLCV. Zusätzlich ORB-Zielalias am Tracker repariert.
Lokale Gesamtabnahme abgeschlossen: **9.611 bestanden, 0 Fehler,
5 Plattform-Skips**, darunter 224 neue gezielte Gegenproben. SHA256 aller
392 Python-Dateien während des Schlusslaufs unverändert. Paket für
Commit/Push abgenommen; Veröffentlichung am Git-Verlauf prüfen.
Server unverändert; sicherer Rollout und reale Zustellung bleiben offen.
[Aktueller Reparaturbericht](docs/SCANNER_REAUDIT_REPAIR_2026-09-30.md).

### Aktuelle Abnahme / Rolloutgrenze

- [x] Alle neun dokumentierten Ursachen implementiert und Gegenproben in
  reguläre Tests übernommen; 17/20 unverändert, BI-Vertrag jetzt v5.
- [x] Zusätzlichen ORB-Fehler `target1/target2` am Zustellungsintent behoben;
  Long/Short bis zum Tracker mit simuliertem SMTP geprüft.
- [x] Biotech-Altdaten getrennt von `biotech-news-v2`; keine erneute Freigabe
  nur durch Cache-Schreibzeit. Hintergrund-Entry-Sender unverändert gesperrt.
- [x] Abschließende Gesamtsuite und Diff-/Syntax-/Bundleabnahme dokumentiert:
  9.611 bestanden, 5 Plattform-Skips; Bundle `4379c5dca540` unverändert.
- [x] Reparaturpaket für Commit/Push abgenommen; keine privaten `output/`-
  Dateien. Veröffentlichte Revision anhand Git/Remote prüfen.
- [x] Server-Rechteinventur am 30.09. vom Nutzer erhalten: Home root:root
  0755; App und `.git` tradingbot:tradingbot 0755; `venv` 0775;
  `data_cache` 0750. API/BG aktiv als tradingbot, weiterhin BindPaths
  `data_cache/runtime:/tmp:rbind`, kein StateDirectory; Frontend aktiv als
  root. API hat nur `legacy-direct-frontend.conf`, BG/Frontend keine Drop-ins.
- [x] Betreiberentscheidung: `safe_deploy.sh` lokal entfernt, keinen Ersatz-
  Installer und keine Installationsumstellung einrichten. Abhängige Auto-Update-
  und Migrationsaufrufe stoppen ohne das Skript vor Änderungen. Anleitungen und
  Tests sind angepasst; bestehende Scanner-/Mailprüfungen bleiben erhalten.
- [x] Gezielte Abhängigkeitsprüfung: zunächst 255 bestanden, 4 Plattform-Skips,
  2 Fehler durch fehlende temporäre Lock-Pfade in umgezogenen Tests. Beide
  Test-Fixtures korrigiert; vollständiger Nachlauf der 12 Entfernungs- und
  umgezogenen Trust-Tests bestanden. Alle vier abhängigen Shell-Dateien
  mit `bash -n` und den Diff mit `git diff --check` geprüft. Kein neuer
  Gesamtlauf der Scanner-Suite und kein Server-/SMTP-Eingriff.
- [ ] Entfernung ist noch nicht veröffentlicht oder auf Hetzner angewendet.
  Der letzte Deploy-Versuch brach vor Pull/Neustart ab; API/BG/Frontend waren
  laut Nutzerinventur aktiv. Manuellen Rollout passend zum vorhandenen Aufbau
  gesondert prüfen; Produktionsdaten, Reminder und Secrets erhalten.
- [ ] Erst nach sicherem Rollout neue vollständige Scans und reale
  Mailzustellung prüfen. Lokale Transporttests sind keine Posteingangsbelege.

## Vorherige Gap-/BI-Reparatur (Basis `b6be1f2`)

Vorheriger Anschlussauftrag: alle sieben Gap-/BI-Auditbefunde und verwandte
Rechenfehler beheben. Lokale Abnahme abgeschlossen: **9.385 Tests bestanden,
5 Plattform-Skips**, davon 80 neue Regressionstests. Desktop/Mobil, Syntax und
Bundle geprueft. [Reparaturbericht](docs/GAP_BI_CALCULATION_REPAIR_2026-09-30.md).
BI-Regelversion **v4**, Aktienstrategie-Cache **16**; 17/20 unveraendert.

## Aktuelle Gap-/BI-Reparatur

- [x] ADX-Initialisierung, ungerundete ADX-/RSI-/Stochastic-Entscheidung,
  Null-ADX und korrekt zusammengesetzte Zwei-Tages-Rendite.
- [x] Gleiche Sitzung/OHLCV-Basis fuer Gap und gemeinsamen 1D-Strategiepfad;
  Einzelausschluss, Fehlercluster und Erhaltung alter Final-Caches getestet.
- [x] BI nur aus referenzgeprueftem CS-Universum; bestaetigte Anteilsklassen
  nicht per Punkt/Suffix ausschliessen. Veraltete Historien getrennt zaehlen.
- [x] Bekannte Planwarnungen nach 17/20 sichtbar erhalten; unabhaengige
  Mail-/Tracking-Sperre und kein Verdraengen gueltiger Plaene durch Warnkandidaten.
- [x] Gegenproben in regulaere Tests uebernommen; feste Diagnosegruende
  und Export angepasst. Daten-/unbekannte Fehler bleiben ausgeschlossen.
- [x] Abschliessenden eingefrorenen Gesamtlauf dokumentiert: 9.385 bestanden,
  0 Fehler, 5 Plattform-Skips. SHA256 aller 22 Python-Dateien unveraendert.
- [x] Reparaturpaket fuer Commit/Push abgenommen; keine privaten Exporte
  oder Browserartefakte Bestandteil des Pakets. Veroeffentlichung siehe Git-Verlauf.
- [ ] Rollout, neue vollstaendige Gap-/BI-Laeufe und reale Mailzustellung pruefen.

## Vorheriges Gap-/Mail-Paket

Vorgaengerauftrag: Gap Momentum Long/Short **02:00 und 12:00
Europe/Zurich, Montag–Freitag**, mathematischer Audit und Abschluss der
Maildiagnose. [Neuer Prüfbericht](docs/GAP_MOMENTUM_SCHEDULE_AUDIT_2026-09-30.md).
Dieses Vorgaengerpaket (`f16a1cc`) ist lokal umgesetzt und unabhängig nachgeprüft:
**9.304 Tests bestanden, 5 Plattform-Skips**. Zur Veröffentlichung freigegeben;
der Produktions-Rollout ist noch nicht erledigt.
Die folgende Live-Revision ist der vorherige Serverstand, nicht der neue Zeitplan.
Live bestätigte Code-Revision: **`b742bbeb3a40`**,
Frontend **`00c5288ac5f0`**. Öffentliche Health-Antwort vom **29.09.2026,
21:52:35** (Serverzeit): `healthy`, Revision und Bundle stimmen überein.
Damit ist auch die Scan-/Chart-Textkorrektur auf Hetzner aktiv; Details im
[Nachprüfbericht](docs/SCAN_PRICE_BASIS_FOLLOWUP_2026-09-29.md).
Für das neue Gap-/Mail-Paket ist nach erfolgreicher Veröffentlichung ein Pull
mit API-/BG-Neustart erforderlich; laufende Scans vorher beenden lassen.
Der alte Health-Nachweis belegt nicht den Zustand aller Dienste, neue vollständige
Scans oder Mailzustellung.

## Neuer Gap-/Mail-Auftrag vom 30.09.2026

- [x] Gap Long/Short auf feste Werktagsslots 02:00/12:00 Zürich umgestellt;
  dauerhafte Zulassung, Sommer-/Winterzeit, Wochenenden, Pause/Fortsetzen und
  konkurrierende Aktienworker geprüft. Keine zusätzlichen Startup-/Stundenläufe.
- [x] Gap-Mathematik und fachliche Auswahl korrigiert: fremder 79-Punkte-Deckel,
  ungerundete RVOL-Grenze, explizite Schuldpapiere. Cacheversion 15.
- [x] Maildiagnose einschließlich Auth-Middleware, Outbox, Tracker und Dedupe
  rein lesend. Unbekannte Zustellungsdaten bleiben unbekannt, nicht scheinbar null.
- [x] Kompakter Bereich Admin → Mailversand lokal am Desktop und Mobilgerät
  geprüft; keine neuen Diagnose-Textblöcke unter jeder Aktie.
- [x] Erneute Mailprüfung identischer offener Gap-Pläne nach Ablauf des
  8-Stunden-Cooldowns mit echtem Produzenten und simuliertem SMTP geprüft.
- [x] Finalen eingefrorenen Gesamttest abgeschlossen: 9.304 bestanden,
  5 Plattform-Skips. Bundle `4379c5dca540`, Syntax von 20 Python-Dateien und
  Diff geprüft; Code, Tests und Dokumentation zur Veröffentlichung freigegeben.
- [ ] Neue Revision auf Hetzner ausrollen und neue Gap-Läufe prüfen.
- [ ] Echte Signalzustellung nach Rollout bestätigen; keine Testmail oder
  fingierte Signal-Freigabe als Ersatz für einen gültigen Produktionslauf.

## Erledigt

- [x] VIAV-Widerspruch live in der angemeldeten App bestätigt: Chartkurs
  39,13 USD unter Plan-Stop 39,29 USD, trotzdem alte Bewertung „Jetzt traden
  (98/100)“. Ursache: vermischte Beobachtungsstände.
- [x] Aktien-Seitenleiste: Kurs, Prozentänderung, Stop-/Zielstatus und Abstand
  verwenden denselben passenden Chartstand; gespeicherte Planlevel bleiben
  unverändert. Historische Bewertung und Ablehnungsgründe sind geschlossen
  unter „Berechnungsbasis“ abrufbar, keine erfundene neue Health-Freigabe.
- [x] Eigener Wyckoff-Schalter in Seitenleiste und Chartanalyse, unabhängig
  von Patterns. Gescheiterte historische Strukturen nur in Details.
- [x] Wyckoff-Zwischenbrüche bei mehrkerziger Spring-/UTAD-Erholung,
  Plateau-Bestätigungszeitpunkte und Rückdatierung von Erholungsfristen korrigiert.
- [x] Regelversion `wyckoff_v3_rules_3`, Strategie-Cacheversion 14.
- [x] Redundante Ergebniszähler entfernt; Datenstand und geschlossene Diagnose
  bleiben. Echte Fehler, unvollständige Daten und blockierte Starts bleiben sichtbar.
- [x] Unabhängiges Audit einschließlich Nachprüfung abgeschlossen.
- [x] Desktop-/Mobilprüfung: 1440×1000 und 390×844, keine Konsolenfehler/Überläufe.
- [x] Finale Gesamtsuite: **9.105 bestanden, 4 Windows-/Linux-Plattformskips**;
  Bundlebindung, JavaScript-Syntax und Git-Diff geprüft.
- [x] Commit und GitHub-Push bestätigt. Private `output/`-Dateien nicht hochgeladen.
- [x] Vorheriges Paket `c3594ff`: Reminder-Laufzeiten 1/3/7/14/30 Tage,
  getrennte Mail-/App-Auswahl, Löschung aktiver Reminder, einmalige Auslösung
  ohne erneute Anzeige sowie Chart zuerst. Dieses Paket ist im aktuellen HEAD enthalten.
- [x] Rollout von `5acd7cf` am 29.09. per Health-Revision und Frontend-Fingerprint
  bestätigt. Kein Pull oder Neustart durch diese Nachprüfung.
- [x] Verbliebene VIAV-Formulierung korrigiert: Tabelle **„Im Scan freigegeben“**,
  Seitenleiste **„Chartkurs unter/über Entry“**. Kein Schluss aus einem einzelnen
  Chartkurs darauf, ob der Einstieg früher erreicht oder eine Order ausgeführt wurde.
- [x] Gleichartige Stop-/TP1-Texte einschließlich exakter Preisgleichheit korrigiert.
  Gespeicherte Level, Scannerregeln und serverseitige Mailfreigabe unverändert.
- [x] Nachkorrektur: **1.452 Tests bestanden** (Frontend, Wyckoff, Reminder),
  vier lokale Browserfälle (Long/Short, 1440/390 px) ohne Konsolenfehler oder
  Seitenleistenüberlauf; Bundlebindung, JavaScript-Syntax und Git-Diff geprüft.
- [x] Neue Code-Revision `b742bbe` und Frontend-Bundle `00c5288ac5f0`
  auf Hetzner per Health bestätigt. Keine Dienste durch diese Nachprüfung verändert.
- [x] Tatsächlich öffentlich ausgelieferte `index.html` und `app.bundle.js`
  auf Port 3000 mit dem Checkout verglichen: beide HTTP 200, beide Inhalts-Hashes
  identisch nach alleiniger CRLF/LF-Normalisierung. Bundle-Quellfingerprint
  `00c5288ac5f0550904f6f8650dea1b35928224dfe9473cf87104eda3f243e81f`.
- [x] Erneute isolierte Versand-/Scanner-Regressionsprüfung: **132 bestanden**,
  eine harmlose Pytest-Importwarnung. Geprüft: native Aktienpläne/Mailintegration,
  Swing-Isolation, SMTP-Ablehnungsbehandlung, Outbox, abschließende
  Strategierevalidierung, Pre-Market und Mail-Kandidatenpool. Keine echte Mail
  versandt und keine Produktionsdaten verändert.

## Als Nächstes – offen, nicht als erledigt melden

1. [ ] **Alle Dienste einzeln prüfen.** Revision,
   Bundle und ausgelieferte Frontend-Dateien sind bereits nachgewiesen.
   SSH ohne Passworteingabe wurde abgewiesen. Die angemeldete Browseransicht
   ist am 30.09. geprüft: Scheduler aktiv, Mailkonfiguration vorhanden,
   Signal-Mails aktiv, Aktien Swing AN, Mailmodus Swing. Keine Zugangsdaten
   gespeichert und keine Konto-Einstellungen geändert.
2. [ ] Einen neuen vollständigen Strategie-/Wyckoff-Lauf prüfen; alte
   Cacheversionen dürfen nicht als neu berechnete Ergebnisse gelten.
   Browser-Anmeldung ist vorhanden; neuer Zeitplan/Rollout bleibt separat.
3. [ ] **Exakte acht historische VIAV-Strukturen:** ohne deren Original-OHLCV
   noch nicht einzeln nachgerechnet. Live waren sieben als gescheitert und eine
   Distribution mit abgelaufenem Einstieg markiert – keine acht aktuellen Signale.
   Falls Originalkerzen verfügbar werden, eingefrorenen Datenstand mit dem
   vorhandenen Replay prüfen; keine bloße Sichtprüfung als Vollnachweis ausgeben.
   Die erneute Suche in den benannten privaten Audit-/Browserartefakten ergab
   Screenshots und synthetische UI-Fälle, keinen belegten Original-Kerzensatz.
4. [ ] **Echte Signal-Mailzustellung** anhand eines neuen gültigen Signals,
   Zustellungsjournal und tatsächlichem Empfang bestätigen. Das letzte Paket
   ändert keinen SMTP-Transport; UI-Kandidaten sind keine automatisch versandten Mails.
   Nutzer meldet eine empfangene Mail am **29.09.**; Betreff/Signalreferenz fehlen
   noch. Ohne Zuordnung weder eine Handelssignalzustellung noch einen allgemeinen
   SMTP-Ausfall behaupten. Anschließend Freigaben, Unterdrückungsgründe, Outbox und
   SMTP-Akzeptanzen im selben Zeitfenster vergleichen.

## Nachbörsen-/Nächster-Handelstag-Idee – untersucht, noch kein neuer Scanner

- Vorhanden: **Gap Momentum Long/Short**; die alten **Earnings Mover**-Namen
  sind Aliasse dieser Scanner, keine zusätzlichen unabhängigen Läufe
  (`modules/strategies.py`, `api.py`: `STOCK_STRATEGY_ALIASES`).
- Nutzer hat bestehende Gap-Scanner gewählt: feste Läufe 02:00/12:00 Zürich
  statt eines neuen Nachbörsen-Scanners. Mathematik korrigiert: fremden
  79-Punkte-Deckel entfernt, RVOL nicht vor Auswahl runden, Schuldpapiere
  ausschließen; Cacheversion 15. Dauerhafter Zeitplan und unabhängige
  Admission-Prüfung umgesetzt. Abschlussprüfungen grün; Rollout noch offen.
- Der Standard-Swingmodus verwendet abgeschlossene **1D-Börsensitzungen**.
  In diesem Modus schaltet `_strategy_scan_wrapper` die Beimischung von
  Nachbörsenpreisen bewusst ab. Der Datensatz bleibt ein Tagesplan, kein
  Nachbörsen- oder Live-Einstieg (`modules/stock_swing_contract.py`).
- Eine eigene zusammengefasste **Vorbereitung für den nächsten Handelstag**
  mit Abendbericht und erneuter Morgenprüfung ist noch nicht implementiert.
  Die Nutzerfrage nach ihrer Existenz aktiviert noch keinen neuen Versandplan.
- Vorschlag zur anschließenden Umsetzung: Long/Short getrennt; Tagesstruktur,
  Trend, relative Stärke, Liquidität, ATR und echte Unterstützungs-/Widerstandszonen.
  Nachbörsenbewegung separat mit Kurszeit, Volumen und Datenverzögerung anzeigen.
  Relatives Nachbörsenvolumen nur gegen vergleichbare Nachbörsen-Zeitfenster messen.
  Kompakte Ausgabe: Aktie, Richtung, Begründung, bestätigbarer Trigger,
  strukturelle Invalidierung, nächste Zielzone und Ereigniswarnung. Keine erfundenen
  Level, keine Pflichtzahl an Treffern und keine automatische Handelsfreigabe.
- Nächste Sitzung über den Börsenkalender ermitteln (Feiertage, verkürzte Tage,
  Zeitumstellung); vor Handelsbeginn Gap/News/Datenstand erneut prüfen. Eine
  Short-Auswahl beweist keine beim Broker verfügbare Aktienleihe.
- Datenquellen geprüft: [Massive-Nachbörsendaten](https://massive.com/knowledge-base/article/does-massive-offer-pre-market-and-after-hours-data),
  [Aggregate](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
  [FINRA](https://www.finra.org/investors/insights/extended-hours-trading)
  erläutert unter anderem, warum Nachbörsenpreise den nächsten Eröffnungskurs
  nicht festlegen. Deshalb Vorbereitung und aktuelle Handelsfreigabe getrennt halten.

## Nachweis der erneuten lokalen Prüfung

```powershell
& '.\.codex_pytest_env\Scripts\python.exe' -B tmp/offline_mail_fix_tests_20260925.py -q --tb=short test_stock_native_plan_mail_integration.py test_stock_swing_mail_isolation.py test_api_smtp_rejection_recovery.py test_mail_outbox.py test_stock_strategy_final_revalidation.py test_premarket_radar.py test_stock_mail_candidate_pool.py
```

Ergebnis: `132 passed, 1 warning in 40.29s`.
Privates JUnit-Artefakt:
`output/mail-fix-qa-c948d45cbf4a4bc3b81779c866256261/results.xml`.
Das ist eine gezielte Nachprüfung, kein neuer Gesamtsuiten- oder Zustellnachweis.

## Wiederaufnahme ohne erneute Untersuchung erledigter Arbeit

- Zuerst Git-Stand und [Prüfbericht](docs/CHART_PRICE_WYCKOFF_AUDIT_2026-09-28.md)
  sowie [Nachprüfung](docs/SCAN_PRICE_BASIS_FOLLOWUP_2026-09-29.md)
  lesen; dort stehen Testbefehle, Artefakte und genaue Grenzen.
- Funktionierender lokaler Testinterpreter: `.codex_pytest_env\Scripts\python.exe`.
  Die alte `.venv` kann auf eine fehlende Python-Installation zeigen.
- API-Tests nur mit dem isolierenden lokalen Launcher
  `tmp/offline_mail_fix_tests_20260925.py`: keine produktiven DBs, Secrets oder SMTP.
- Vorhandene private Exporte zuerst auswerten; keinen identischen Export ohne
  konkreten neuen Bedarf verlangen. `output/`, Secrets und Browser-Anmeldung privat halten.
- Keine Lockerung von BI 17/20, Datenprüfungen oder Mailfreigaben als Ersatz für Fehlerbehebung.
- Die Übernahmen vom 28./29.09. bleiben erhalten. Bei der Nachprüfung vom
  29./30.09. wurden nur diese Aufgabenliste und der Handbuch-Einstieg aktualisiert;
  keine Scanner-, Mail-, Konto-, Datenbank- oder Serverkonfiguration geändert.
  Der anschließende neue Gap-/Mail-Auftrag verändert jetzt Code gemäß obigem
  Prüfbericht; diese frühere reine Nachprüfung ist kein Rolloutnachweis dafür.
- Ältere übergreifende Aufgaben bleiben in
  [Profitabilitäts-Prüfplan](docs/PROFITABILITY_PROTOCOL_2026-09-08.md)
  und [Projekthandbuch](PROJEKTHANDBUCH.md); deren historische Serverstände nicht
  mit einem heute geprüften Stand verwechseln.
