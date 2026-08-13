# Signal-Pipeline-Audit 13.08.2026

Stand: lokale Implementierung und unabhaengiger Folgeaudit. Dieses Dokument ist
kein Produktions- oder Profitabilitaetsnachweis. Ein Server-Rollout dieses
Arbeitsstands ist erst belegt, wenn Server-HEAD, API-Revision, Bundle-Hash,
Services und Health denselben Commit melden.

## 1. Anlass und belastbarer Mailbefund

Die Gmail-Suche fuer 06.-12.08.2026 ergab 16 Signal-Update-Digests mit 45
Ereigniszeilen. Nach Plan-Geometrie-Dedupe blieben 41 Plaene, davon 33 terminal
und 8 nur mit TP1-offen. Die 33 terminalen Update-Ereignisse enthielten:

- 12 positive und 21 negative Ausgaenge,
- +21,70R positive und -23,81R negative Summe,
- netto -2,11R,
- 36,36% positive terminale Ausgaenge,
- Wilson-95%-Intervall 22,19-53,38%,
- Profit Factor 0,911.

Diese Zahlen sind ein Update-Ereignisstrom, keine vollstaendige Kohorte der in
diesem Zeitraum neu versendeten Einstiegssignale. Update-Mails enthielten keine
stabile Signal-ID und keine urspruengliche Signalzeit. Aeltere Signale koennen
enthalten sein; NO_FILL, UNTRACKED, offene Signale ohne TP1 und ausgefallene
Zustellungen fehlen. Aus 18 Stop- und 17 TP-Betreffereignissen darf deshalb
keine Tradebilanz abgeleitet werden.

## 2. Forensisch korrigierte Extremfaelle

| Fall | Roh | Auditkorrektur | Ursache |
|---|---:|---:|---|
| ONON | -4,65R | -3,94R | Produktionsstand verwendete laufenden Daily-Close als fehlendes Open; echtes Gap-Open 31,59 |
| ECO | -1,27R | -1,00R | echtes Open lag ueber Stop, spaetere normale Stopberuehrung statt Close-als-Open |
| CBLL | -1,57R | NO_FILL | Planpreis 20,41 war vor Mail bereits veraltet; Markt lag bei Mail unter Stop |
| AURA | -1,38R | konservativ unveraendert; wahrscheinlich -1,00R | gleiche Missing-Open-Signatur, aber Erstmailzeit nicht abschliessend belegt |

Konservative Korrektur der vier Faelle: -6,32R statt -8,87R, drei Verluste und
ein No-Fill statt vier Verluste. Gegenueber dem Rohereignisstrom verbessert das
die Summe um +2,55R und reduziert entschieden/verloren jeweils um eins. Mit der
wahrscheinlichen AURA-Korrektur waere die Verbesserung +2,93R. AURA bleibt bis
zum Erstmail-/Produktions-DB-Nachweis ausdruecklich unbestaetigt.

## 3. Bestaetigte technische Ursachen

1. Der Produktionsstand `de4e7cf` lieferte im Aktien-Daily-Fetcher kein echtes
   Open. Der Normalizer ersetzte fehlendes Open durch Close. Gap-Ausfuehrungen
   wurden dadurch mit einem spaeteren laufenden Kurs statt dem ersten
   ausfuehrbaren Tages-Open bewertet.
2. Ein `price_at_alert`-Skalar erzeugte ohne Zeit-, Quellen- oder Marktpfadbeleg
   einen sofortigen Fill. CBLL wurde so nach bereits gerissenem Stop als
   gefuellt verbucht.
3. Entry-Slippage und Stop-Gap-Slippage waren in Update-Mails vermischt bzw. die
   Stop-Gap-Kosten unsichtbar.
4. Richtung, Horizont, Regime und Scanner wurden in Teilen unvollstaendig
   persistiert oder zu grob aggregiert. Dadurch waren Freigabe- und
   Performancezellen nicht belastbar.
5. SMTP, Tracker und Folgeupdate waren nicht durchgaengig transaktional:
   unbekannter DATA-Ausgang, Teilannahme, Crash nach Versand und alte Outbox-
   Eintraege konnten Doppelmail, Mail ohne Tracker oder falsche Empfaenger-
   Follow-ups erzeugen.
6. BI Long, BI Short und Biotech waren als BG-owned konfiguriert, waehrend der
   unsichere BG-Entry-Mailpfad bereits fail-closed war. Dadurch entstanden
   frische Caches ohne automatische Entry-Mail. Diese Scanner sind nun
   API-owned; nur der API-Prozess darf nach finaler Revalidierung und
   Delivery-Intent senden. Der BG-Dienst bleibt Owner fuer Tracker-Evaluation,
   Folgeupdates und Outbox.

## 4. Neuer verbindlicher Forward-Vertrag

- Tracking beginnt fruehestens bei nachgewiesener SMTP-DATA-Akzeptanz bzw.
  Brokerfill, nie bei Scanstart oder einem vor Zustellung beobachteten Kurs.
- Eine Vorversandquote darf den Mailpreis validieren, ist aber kein Fill.
- Aktien-Jetzt-Mails benoetigen ausfuehrbaren Bid/Ask, Quellen- und UTC-Zeit,
  richtige Session, frische Quote und lueckenlosen Marktpfad bis zur Quote.
- Stop oder TP1 bereits seit Scan beruehrt: keine Einstiegsmail.
- Ohne vollstaendigen Post-Alert-Pfad bleibt ein Signal OPEN/UNTRACKED; es wird
  kein guenstiger oder unguenstiger Ablauf erfunden.
- Daily-Bars ohne echtes Open und laufende, noch nicht abgeschlossene Bars
  duerfen keine Gap-/Expiry-Entscheidung erzeugen.
- First Executable Price gilt symmetrisch fuer Long und Short sowie fuer Stop-
  und Break-even-Gaps.
- NO_FILL, OPEN, UNTRACKED, EXPIRED, STOP, TP1 und TP2 bleiben getrennt.
- Level-R, 50/50-Managed-R und 50/50+BE-R sind getrennte Metriken. Unbewiesene
  BE-Zustellung ist `managed_be_unresolved`, nicht 0R und kein herausgefilterter
  Verlust.
- Freigaben erfolgen nur in der gemeinsamen Zelle Scanner x Richtung x Horizont
  x exogenes Marktregime, mit mindestens 30 vollstaendig beobachteten
  Entscheidungen und null unresolved Control-Ergebnissen.

## 5. Zustell- und Empfaengervertrag

- Vor SMTP wird ein stabiler Signal-/Delivery-Intent persistiert.
- Nur der atomare PREPARED->ATTEMPTED-Owner darf DATA senden.
- Akzeptierte Empfaenger werden pseudonymisiert und mit Akzeptanzzeit dauerhaft
  journalisiert; erst danach wird das Signal ACTIVE.
- Ein unbekannter DATA-Ausgang wird quarantainiert und nie automatisch erneut
  gesendet.
- Teilannahme bildet nur aus den in demselben Versuch akzeptierten Empfaengern
  eine kausale Kohorte; spaetere Empfaenger werden nicht in einen frueheren
  Signalstart gemischt.
- Terminale Updates und BE-Updates werden persistent pending gehalten und erst
  nach Zustell-Acknowledge abgeschlossen.
- Folgeupdates gehen nur an die nachgewiesene Ursprungskohorte geschnitten mit
  aktuellem Opt-in. Neue Abonnenten erhalten kein Exit ohne Entry.
- Alte offene Signale ohne Empfaengerledger werden als
  `legacy_open_cohort_unknown` gezaehlt; Empfaenger werden niemals geraten.

## 6. Sicherheit und Datenschutz

- SMTP verwendet einen verifizierenden Standard-TLS-Kontext.
- Mehrere Empfaenger werden nicht im sichtbaren To-Header offengelegt.
- Provider-Ausnahmen werden vor Logs redigiert; API-Antworten verwenden stabile
  Fehlercodes statt URL, Token, Query-Key, Dateipfad oder Empfaengeradresse.
- Public Health zeigt nur Counts, Alter, Status und Booleans. Raw Exceptions,
  Journalpfade und Empfaenger bleiben intern.
- Trade-/Swing-Outbox-Eintraege werden nicht zeitversetzt mit alter Quote
  zugestellt; alte zeitkritische Eintraege gehen in manuelle Quarantaene.

## 7. Reparatur historischer Trackerzeilen

`scripts/signal_tracker_repair.py` und `deploy/SIGNAL_TRACKER_REPAIR.md` liefern
einen Dry-run-first Reparaturpfad mit Vorzustands-Fingerprint, SQLite-Backup,
Audit-JSONL, Status-/Geometrie-/Zeit-/R-/Gap-/BE-Invarianten und Recheck in einer
gesperrten Transaktion. Ein Manifest darf keine Werte raten. Fuer CBLL ist
STOP_HIT->NO_FILL nur mit belegter ID und vollstaendigem Vorzustand zulaessig;
Exit-, Gap-, BE- und Trajektorienfelder muessen dabei konsistent geleert werden.

## 8. Rollout-Gates

Vor Produktion zwingend:

1. volle lokale Pytest-Suite gruen,
2. Frontend-Bundle neu gebaut und `scripts/verify_frontend_bundle.py` gruen,
3. `py_compile`, `git diff --check` und Secret-Diff-Scan gruen,
4. Commit auf `origin/main`,
5. produktives Backup von Tracker, Zustelljournal und Outbox,
6. Repair nur nach Dry-run und Vier-Augen-Pruefung,
7. Server-HEAD/API-Revision/Bundle/Services/Health auf denselben Commit,
8. reale Realtime-Quote-Berechtigung und <=90s Recency nachgewiesen,
9. offene Legacy-Kohorten und unklare SMTP-Zustellungen gleich null oder
   dokumentiert manuell behandelt,
10. danach neue Forward-Kohorte sammeln; historische Rohdaten nicht still
    umdeuten.

Bis diese Gates produktiv nachgewiesen sind, gilt der lokale Fixstand nicht als
live und die Profitabilitaet bleibt unbewiesen.

## 9. Lokale Endabnahme am 13.08.2026

Der nach dem Folgeaudit erneut eingefrorene Arbeitsbaum wurde vollstaendig in
einer isolierten Testumgebung geprueft. Eigene temporaere Tracker-, Auth-,
Dedupe- und Outbox-Dateien, Dummy-Providerwerte und ein nicht erreichbarer
lokaler SMTP-Port stellten sicher, dass weder Produktionsdaten noch reale
Empfaenger beruehrt wurden.

- volle Pytest-Suite: **1768/1768 bestanden** in 38,90 Sekunden,
- alle 47 geaenderten oder neuen Python-Dateien: `py_compile` gruen,
- Frontend-Bundle neu gebaut und verifiziert: **`a6c74874a925`**,
- `node --check` fuer Bundle und Smart-Money-Skript: gruen,
- `git diff --check`: gruen; nur plattformbedingte CRLF-Hinweise,
- Secret-Musterscan ueber 57 geaenderte/neue Dateien: keine Private-Key-,
  Live-Provider-, GitHub-, Slack-, Stripe- oder Google-Key-Signatur,
- lokale reale Mail-Outbox: 19 abgelaufene, **0 aktive**
  (`pending/sending/delivering/uncertain`) Eintraege,
- unabhaengiger finaler Read-only-Audit: **P0 0, P1 0, P2 0**.

Die Browserabnahme erfolgte gegen das gebaute lokale Frontend mit gemockten
API-Antworten. Bei 1440 px und 390 px waren `scrollWidth` und `clientWidth`
identisch; die Konsole enthielt keine Fehler. Die Landingpage zeigt keine
erfundenen Testimonials oder unbelegten Profitabilitaets-/Beliebtheitsclaims.
Der Paper-Bereich ist als illustrative Demo ohne echte Orders oder Ergebnisse
markiert und zeigt keine Live-P&L. Das Smart-Money-Radar renderte einen
absichtlich HTML-haltigen Ticker ausschliesslich als Text (`0` injizierte
Bilder, `window.__xss == false`) und ohne Konsolenfehler. Nicht-blockierender
P3-Hinweis: das lokal vendorte Tailwind-Runtime-Skript meldet weiterhin seine
allgemeine Production-Build-Warnung; funktionale oder sicherheitsrelevante
Browserfehler wurden daraus nicht beobachtet.

Aktien-Reminder bezeichnen einen Triggerkurs nun ausdruecklich als letzten
abgeschlossenen 5-Minuten-Schluss, nennen den ISO-UTC-Kerzenschluss und weisen
darauf hin, dass dies kein Live-Bid/Ask ist. Alte, fehlende, zukuenftige oder
ausserhalb der ausfuehrbaren Session liegende Kerzen bleiben fail-closed und
der Reminder wird spaeter erneut geprueft.

Diese Endabnahme ist ein lokaler technischer Nachweis. Sie ersetzt weder den
noch ausstehenden Server-Rollout-/Health-Nachweis noch Realtime-Quote-
Berechtigung, Forward-Performance, Broker-Paper-Soak oder Store-Freigabe.

## 10. Git- und Produktionsvergleich nach der Endabnahme

Der Implementierungsstand wurde lokal als Commit **`e9cba06`** erstellt. Der
GitHub-Push ist auf diesem PC nicht erfolgt: der HTTPS-Remote besitzt kein
hinterlegtes Schreib-Credential, kein GitHub-CLI-Login und keinen SSH-Schluessel.
Der Remote-Branch blieb beim letzten read-only Abgleich auf **`9987c7f`**.

Die oeffentliche Produktions-API antwortete am 13.08.2026 um ca. 21:24 MESZ mit
HTTP 200, meldete jedoch weiterhin Revision **`de4e7cfac0ec`** und
Frontend-Bundle **`c0b3b13a6c86`**. Das oeffentliche Frontend auf Port 3000
enthielt die alte Landing-Copy und nicht den lokal verifizierten Bundle-Stand.
Der read-only SSH-Versuch scheiterte mit `Permission denied
(publickey,password)`, weil auf dem neuen PC kein autorisierter privater
Server-Schluessel vorhanden ist.

Damit ist positiv belegt, dass dieser Fixstand **nicht produktiv** ist. Es wurde
kein Deployment, kein Produktions-Repair und kein Service-Neustart versucht.
Das Health-Feld `market_data: true` beweist nur vorhandene Konfiguration und
nicht die fuer den neuen Versandvertrag erforderliche Realtime-Quote-
Berechtigung oder <=90-Sekunden-Recency.
