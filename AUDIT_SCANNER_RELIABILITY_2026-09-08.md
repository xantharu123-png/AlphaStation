# Scanner-Zuverlaessigkeit und Profitabilitaet: Fortsetzung 08.09.2026

## Ergebnis und Abgrenzung

Momentum-/BI-Datenfehler und widerspruechliche UI-Zustaende wurden reproduziert
und korrigiert. Das ist ein Nachweis besserer Fehlerbehandlung, **kein** Nachweis
einer hoeheren Trefferquote oder eines positiven Nettoerwartungswerts.

Nutzerkapital: 5.000 USD bestaetigt. Drei Trades pro Tag sind eine Obergrenze,
keine Pflicht; 150 USD sind ein Wunsch, kein zugesicherter Ertrag. Die genannte
300-USD-Toleranz hebt keine strengeren bestehenden Risikogates an.

Keine 17/20-Absenkung, Watchlist, neue Exitregel, Broker-/Paper-Aktivierung,
produktive Migration, Mail, Order, Server-Neustart oder Cron-Aenderung.

## Reproduzierte Fehler und Korrekturen

| Fall | Bisheriges Verhalten | Gepruefter neuer Vertrag |
| --- | --- | --- |
| Aktien-Snapshotfeed verweigert Zugriff oder liefert kein Universum | Leerer oder Movers-only-Scan konnte als fertiger Nulltreffer-Stand publiziert werden | Datenfehler; vorheriger finaler Cache bleibt erhalten |
| BI-Breituniversum leer, Movers vorhanden | Unvollstaendige Abdeckung erschien vollstaendig | Abbruch vor Movers-Ergaenzung, kein neuer Ergebnisstand |
| BI-Historienabrufe scheitern | `no_data` vermischte Fehler mit legitimen Filtern; frischer leerer Cache moeglich | Abruf-/Analysefehler und echte Filterzaehler getrennt; unvollstaendige Laeufe ersetzen keinen finalen Cache |
| Manueller Start trifft laufenden Worker | API meldete trotzdem einen neuen Start | `accepted`/`already_running` und Laufidentitaet getrennt |
| Startantwort HTTP401/403/429/5xx | Spinner konnte ohne brauchbare Erklaerung verschwinden | Sichere Fehlermeldung; Cooldown als Wartezeit, nicht als Nulltreffer |
| BI-Scheduler beendet im Hintergrund | Kopfzeile neu, Ergebnistabelle alt | Neuer Abschluss laedt den passenden Richtungsstand automatisch |
| Gesunder BI-Lauf dauert ueber 30 Minuten | UI-Polling gab vor dem normalen Produktionsabschluss auf | Solange Backend explizit laeuft, kein Pollzaehler-Fehlabbruch; unbestaetigte Starts bleiben begrenzt |
| Long-/Short-Wechsel | Erster Render konnte alte Richtungszeilen zeigen | Scope-gebundene Daten/Flags schon vor dem Reset-Effekt |
| Noch kein finaler Cache | Zusaetzlicher Header zeigte dennoch `0 BI-Signale` | Anzahl unbestaetigt; erst ein gueltiger finaler Stand bestaetigt die Zahl |
| Spaeterer Hintergrundscan hat andere Trefferzahl | Alte numerische Abschluss-Notice blieb stehen | Keine dauerhaft duplizierte Trefferzahl neben dem aktuellen Ergebnisstatus |

Betroffen sind die generische Aktien-Strategieausfuehrung (darunter Momentum),
BI Long/Short, deren Start-/Ergebnis-API und die beiden UI-Komponenten.
Andere Scannerfamilien sind dadurch nicht neu vollstaendig auditiert.
Fehlende Daten werden nicht durch weniger strenge Signalbedingungen ersetzt.
Ein einziger relevanter Abruffehler kann einen Lauf nun bewusst als unvollstaendig
kennzeichnen. Das kann mehr sichtbare Betriebsfehler erzeugen, statt sie als
erfolgreiche Nulltreffer zu verdecken. Anbieterberechtigungen werden nicht umgangen.

Ein sauber abgeschlossener BI-Scan kann weiterhin null gueltige Signale liefern:
17/20 plus Richtungs-, Struktur- und Risikovertrag sind Mindestbedingungen,
keine Garantie, dass zu jedem Zeitpunkt eine Aktie sie erfuellt.

## Evidenzgrenze zum konkreten Hetzner-Vorfall

Die obigen Fehler wurden mit kontrollierten Inputs im aktuellen lokalen Code
nachgewiesen. Ohne den aktuellen Serverexport ist nicht bewiesen, welcher davon
den gemeldeten Ein-Sekunden-Abbruch bzw. die leere BI-Tabelle ausgeloest hat.

Oeffentlicher Health-Lesetest am 08.09.2026, 14:16 UTC: Server healthy,
Revision `fa9fba7fdddc`, Bundle `938257a0d05f`. Ein HTTP401 am geschuetzten
Ergebnisendpunkt beweist fehlende API-Anmeldung, **nicht** einen Providerfehler.
SSH ohne Interaktion wurde mit `publickey,password` abgewiesen. Keine
Anmeldeschranke wurde umgangen, kein Passwort gespeichert.

Die lokale Trackerdatei enthaelt weiterhin 22 alte OPEN-Zeilen, juengster Start
05.08.2026. Sie erlaubt kein Ranking aktueller Scanner. Ihr unveraenderter SHA256:
`924b94bd1f01ad6d5becfa91698bb0c9f37f3204fe6df72607eb9cbc1fc595bc`.

## Sicherer privater Export und Auswertung

`scripts/collect_hetzner_evidence.ps1` fuehrt den lokal geprueften
Standalone-Collector via SSH-stdin mit `/usr/bin/python3 -I -` aus. Keine
Server-Appimporte, Hooks, Installation, Dateiuploads oder Dienste-Aenderung.
Root wird nur fuer feste Dienst-/Prozessmetadaten verwendet. Vor SQLite-/Cache-
Zugriff werden alle Zusatzgruppen entfernt und reale, effektive und gespeicherte
UID/GID dauerhaft auf die verifizierte Service-Identitaet reduziert.

SQLite liest `mode=ro`, `query_only`, eine Transaktion einschliesslich WAL.
Normale WAL-/SHM-Koordination als Servicebenutzer ist moeglich; daher weder
`immutable=1` noch die Behauptung, jegliche Dateimetadaten blieben unveraendert.
API-/BG-Identitaet und Trackerpfade werden vor/nach der Sammlung verglichen.
Health nutzt nur festen Loopback ohne Proxy/Redirect. Cache-Metadaten werden
streng projiziert; unbekannte Kategorien erscheinen allenfalls als Anzahl.
Cache/Health/DB bilden keine atomare gemeinsame Momentaufnahme.

Der private Export ist auf benoetigte Trade-Arithmetikfelder begrenzt; keine
Empfaenger, Mailtexte, Kontoblobs oder API-Schluessel. Er enthaelt trotzdem
private Signalzeilen und bleibt unter `output/profitability/`, ausserhalb Git.
Shadow ist nur ein Inventarzaehler; die gesamte App-Signalpopulation und echte
Brokerkosten/-fills sind damit nicht nachgewiesen. Besondere OS-Temp-Overrides
ohne `ALPHA_RUNTIME_TMP_DIR` koennen die optionale Fortschrittsdateisuche von
`/tmp` abweichen lassen; der Trackerpfad wird separat am Writer verifiziert.

`signal_performance_breakdown.py --snapshot-json` prueft Schema, explizite
Zeitzone, Inventarsummen, erwartete Spalten, eindeutige IDs und endliche skalare
Werte. Doppelte JSON-Schluessel werden abgelehnt. Es gibt keinen automatischen
Fallback auf die veraltete lokale DB und keinen kryptographischen Herkunftsbeweis.
Brutto-R, unbekannte Versionen, offene Faelle und fehlende Nettokosten bleiben
sichtbar. Kein historischer As-of-Replay wird behauptet.

## Tagesrisiko: umgesetzt nur als Offline-Pruefung

Siehe `docs/DAILY_RISK_MODEL.md`: reine Decimal-Funktion auf vollstaendigem,
eingefrorenem Caller-Snapshot. Feste Sessionbasis, engster bestehender Cap,
realisierte Nettobasis ohne MTM-Doppelabzug, offene/reservierte Risiken,
zukuenftige Kosten, Gap-Stress, drei Entry-Slots, Teilfuellungen, bestaetigte
ungefuellte Stornos und idempotente Wiederholungen sind modelliert.

Mit den Quellcode-Prozentdefaults ergeben 5.000 USD 12,50 USD Einzelrisiko,
37,50 USD Gesamtrisiko und 50 USD Tagescap. Die 300-USD-Toleranz gewinnt nicht
gegen engere bestehende Grenzen. Diese Rechnung verifiziert keine produktive
Kontokonfiguration. 50 USD Bruttogewinn bei 12,50 USD Preisrisiko waeren 4R.

Immer `OFFLINE_ONLY`, `execution_authorized=false`, `paper_authorized=false`,
`atomic_reservation_performed=false`. Keine persistierte Tagesbudgetintegration
in den vorhandenen Risiko-Store, kein Brokerbeweis und keine Verlustgarantie.
Die vorhandene Ausfuehrung bleibt unveraendert.

## Abnahme

Die Umsetzung nutzte den Metrics-Review-Ansatz fuer getrennte Grundgesamtheiten,
Kennzahlen und Evidenzluecken sowie Playwright fuer gerenderte Desktop-/Mobil-
Gegenproben. Ausschliesslich lokale synthetische Antworten, echte Frontendbytes.

- Backend: 231 gezielte Tests bestanden, inklusive leerem BI-Breituniversum.
- Collector/Import: 143 Tests bestanden; Offline-Risiko: 131 bestanden.
- UI-Lebenszyklus: 73 fokussierte Tests bestanden; Gegenproben fuer Fehler, Busy, alte Zeitstempel, Richtungswechsel,
  Hintergrundabschluss, fehlenden Stand und ueber 900 gesunde Polls.
- Unabhaengige Gegenpruefung Backend/UI/Risikomathematik; gefundene Blocker behoben.
- Browser: automatischer BI-Abschluss ohne Reload, ehrlicher fehlender/Nullstand,
  HTTP503 bei letztem guten Stand, Momentum-Cooldown, Long/Short und 390px-Mobil.
  Simulierte HTTP-Fehler sind erwartet; keine unbehandelten JavaScript-Ausnahmen.
- Ein erster Gesamt-Testlauf wurde nach einem weiteren visuellen Statusbefund
  gezielt abgebrochen. Er wird nicht als bestanden gezaehlt.
- Der anschliessende volle Lauf auf eingefrorenen Anwendungsbytes ergab
  **3905 bestanden, vier Plattform-Skips und einen fehlgeschlagenen Test** in
  1121,35 s (`tmp/reliability-final-20260908.xml`). Einzige Abweichung:
  `test_live_scan_progress` verlangte die alte interne Polling-Funktionsschreibweise,
  die durch den getesteten gemeinsamen Lifecycle ersetzt wurde. Der Strukturtest
  wird auf diesen Vertrag aktualisiert; keine Produktionsdatei wurde deswegen
  geaendert. Alle 161 ausfuehrbaren Deployment-Tests dieses Laufs bestanden.
  Die vier Skips betreffen Windows-Symlink-Rechte sowie Linux-spezifisches
  O_NOFOLLOW/FIFO/atomare-Rename-Verhalten; kein Linux-Live-Nachweis.
- Nach ausschliesslicher Aktualisierung dieses Strukturtests: **3745/3745
  Nicht-Deployment-Tests bestanden** in 160,65 s
  (`tmp/reliability-recheck-20260908.xml`). Damit disjunkte Gesamt-Abdeckung:
  **3906 bestandene Tests plus vier Plattform-Skips** aus den beiden Laeufen.
  Kein neuer vollstaendig gruener Einzel-Vollsuite-Lauf wird behauptet.
  Die 16 eingefrorenen Anwendungs-/Skript-/Testdateien blieben zwischen dem
  vorherigen Vollsuite-Lauf und dem Nachtest hashidentisch; nur der genannte
  weitere Strukturtest wurde aktualisiert. Seine 76 fokussierten Gegenproben
  bestanden ebenfalls. Bundle-Pruefung und `git diff --check` erfolgreich.

Private Screenshots/Snapshots liegen unter
`output/playwright/scanner-reliability-20260908/`; keine Produktionsdaten.

Final gerendertes Bundle: `df791ada6247`. Die Artefakte mit Suffix `-release.png`
beziehen sich auf diese Version; vorherige `-final.png`-Artefakte waren
Zwischenpruefungen vor der letzten Notice-Korrektur. Der Ablauf
`background_running -> abgeschlossen 1 -> neuer abgeschlossener Stand 0`
wurde bis zum echten UI-Abschluss abgewartet und ohne alte numerische Notice
erneut bestaetigt. 390px-Viewport und Dokumentbreite stimmen ueberein; die
breite Tabelle scrollt in ihrem Container. Browserkonsole: erwartete synthetische
HTTP429/503, vorhandene Tailwind-Runtime-Warnung und initialer fehlender Favicon;
keine unbehandelte JavaScript-Ausnahme. Der Testbrowser wurde geschlossen.

Quellbytes des finalen Freeze (SHA256; vor Git-Zeilenendennormalisierung):

| Datei | SHA256 |
| --- | --- |
| `api.py` | `32708f3d06c8dda09de099a5ea4c95ab16955be1d934a41d519bb1b21f4dcd94` |
| `modules/scanners.py` | `592a6d1fbc33ccd1a439abd5e5e6ebb7a3cb1786f538ebcc5e9c98bcc1cb451c` |
| `frontend/index.html` | `9bf51ce8e9139ea3d2ab1bc23904926238419ff2a69613fb7805530110ae5d30` |
| `frontend/app.bundle.js` | `a9bc610fb845ab6596823124596ce7b8b267de1843b287eb418f9d22846aaa19` |
| `scripts/collect_server_evidence.py` | `1eb8e7881e5c35bd4578c19d4de8d1939cfd56d333eda9daf8d8748c8627b320` |
| `scripts/signal_performance_breakdown.py` | `0ac9a1c55bf99134e9c416d82764651a913af6963bb1e876a65732bdb5db40e3` |
| `modules/daily_risk_assessment.py` | `6b264b084d1882df6c933221833dc4c88e62c705e751eeeccceb6eb10f6b9a47` |

## Noch offen

1. Aktuellen privaten Hetzner-Export in der Nutzer-PowerShell ausfuehren und
   auswerten. Danach Scanner/Version/Asset/Richtung/Zeitraum getrennt beurteilen.
2. Vollstaendige Kosten- und Fill-Evidenz; ohne sie keine Netto-Kontorendite.
3. Eine vorab definierte Strategie-/Exit-Alternative auf gleichen Chancen
   kontrolliert vergleichen, nicht nachtraeglich Gewinner heraussuchen.
4. Separat gepruefte atomare Sessionbudgetintegration und etwaige Forward-Paper-
   Freigabe. Kein Live-Handel aus einem bestandenen Softwaretest ableiten.
5. Dieses Paket erst nach Push auf Hetzner installieren und Revision/Bundle/
   Dienstgesundheit erneut nachweisen. GitHub-Push ist kein Serverupdate.
