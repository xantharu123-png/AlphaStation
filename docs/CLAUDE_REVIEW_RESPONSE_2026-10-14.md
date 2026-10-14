# Gegenprüfung des Claude-Audits und LEAPS-Entwurfs

Stand: 14.10.2026, `d88584a` plus erhaltenes WIP. Basis ist
`AUDIT_CLAUDE_2026-10-14.md` und der vom Nutzer eingefügte Folgekommentar.
**Ausgewählte statische Gegenprüfung, kein neues vollständiges Scanner-Audit.**
Kein Provider-/SMTP-/Brokerabruf, keine Live-Anmeldung/Serveränderung in dieser
Prüfung. Die Security-Triage ist getrennt im verwalteten Security-Artefakt
abgelegt; konkrete Live-Konfiguration bleibt uninspektiert.

## 1. Urteil zum alten Anschlussentwurf

`d88584a` so **nicht umsetzen**. Die Kritik am ausgelagerten Pflichtumfang
trifft: Ohne reale Adapter, temporäre Wiederaufnahme und skalierbaren Speicher
wird aus einem korrekten Rechenkern kein brauchbarer Scan. Der revidierte
[schriftliche Entwurf](superpowers/specs/2026-10-14-leaps-live-composition-design.md)
nimmt die notwendigen Reparaturen in ein zusammenhängendes Baupaket auf.
Die Nutzerwahl „nur echte Bid/Ask-Daten“ ist übernommen, kein Richtpreis-Modus.

Nicht übernehmen: Nachtzeit als Beweis fehlender Konkurrenz, Datenalter nur
als unverbindliche Warnung, fehlende Einheiten/Modellzeiten erfinden oder
veralteten Ask mit neuem Aktienpreis ranken. Der Nachtstand ist ausdrücklich
ein datierter Vergleich, nicht ein jetzt ausführbarer Kurs.

## 2. LEAPS: belegte Fehler und notwendige Einschränkungen

| Punkt | Gegenprüfung am tatsächlichen Producer/Consumer |
| --- | --- |
| LP1 | `provider.py:371–398` speichert Greek/IV-Rohwerte, erzeugt aber keine normalisierten Delta-/IV-Felder. Default-Deltafilter kann deshalb nicht bestehen. Nicht „alle Daten verworfen“; Rohwerte sind erhalten. Normalisierte Werte **und** Einheiten zuordnen, nicht nur `units_evidence` setzen. |
| LP2 | `provider.py:235,264–266`/`runtime.py` halten Rate-Limit als Sperre fest. Reconciliation/Resume existiert, daher nicht irreparabel „für immer“. Automatische 429-Erholung fehlt. SEC-Sperre ist bereits lokal vom Marktbereich getrennt. |
| LP3 | Admission kennt Wartegrund/-zeit, Provider verdichtet ihn zu `admission_denied`, Service/Runner persistieren Pause. Kein automatisches Wake-up. Budget ist `min(20, allocation//10)`, nicht immer20; Cooldown läuft ab der letzten Adminaktion, nicht erst ab Ablehnung. |
| LP4 | Kein produktiver Terms-Produzent. Einfacher realer Referenzadapter ist sinnvoll; Zusatzliefergegenstände und tatsächliche Symbol-/Währungsidentität trotzdem prüfen. CFI allein genügt nicht. |
| LP5 | `runtime.py:534–547` verlangt heutiges ET-Datum beim Resume; nach Mitternacht kann der Cursor nicht fortgesetzt werden. Bereits zugelassene letzte Einheit kann trotzdem nach Mitternacht enden. Datum nicht einfach durch heutiges Datum ersetzen. |
| LP6 | Produktions-Factories fehlen. Die Gate-/Speicher-/Terms-Arbeit muss Teil desselben Anschlusses sein, nicht Aktivierung durch Fakebelege oder spätere undefinierte Entwürfe. |
| LP7 | `service.py:79–99,372` berechnet/sortiert relative BE-Werte auch für alte isolierte Ask-Basis. Quote-Assessment verweigert aktuellen Vergleich bereits, aber Rangbildung ignoriert das. Numerisches Beispiel +5,56%/Rang1 ist ohne konkrete Quotes/Konkurrenten nicht separat belegt. |
| LP8 | Kein Nachweis, dass aktueller Vergleich stets0 sein muss. Snapshot ist paginiert, nicht garantiert ein Request. SEC verbraucht lokales Admissionbudget, keine Massive-Anbietercalls. Fehlende Optionsvolumennormalisierung und UTC/ET-Probedatum bleiben konkrete Anschlussarbeiten. Dot-Symbole sind nicht pauschal verboten: belegte BRKB/BRK.B-Zuordnung existiert. |

Bestehende `evaluate_expiry`-Algebra ist korrekt für das geprüfte Standard-
Long-Call-Modell: Prämie100×n×Ask, BE=Strike+Ask, terminaler P&L aus innerem Wert
minus Prämie. Sie ist **kein** Vor-Verfallswert oder Ersatz für Aktien-Exposure.
Neue jährliche Extrinsik/BE-Kennzahlen, HV, Delta-Hebel und Dividendenvergleich
sind im Entwurf definiert, noch nicht als fertig implementiert behauptet.

## 3. Zustands-/Funktionskritik: enger bestätigen als Claude formuliert

### R1 — App/Mail-Ausführungsstand

Für normale Strategy-Ausgaben ist die Lücke real: Cache wird vor Mail-Revalidation
gespeichert (`api.py:25495–25505`), Dekoration arbeitet auf Kopie (`18050–18054`),
NOW setzt neue Quote-/Pfadbelege voraus (`17672–17676`). Mail-Revalidator liefert
diese Belege in einer separaten Kopie; Caller ersetzt lokale Mailquelle, nicht
den normalen Cache. Das erklärt fehlende Verbindung, beweist aber keinen Mail-
Gate-Bypass und keine tatsächliche Häufigkeit „JETZT praktisch nie“.

Gegenbeispiel: Penny revalidiert im Scanabschluss, ersetzt dieselben Row-Objekte
(`42537–42571`) und speichert sie danach. „Belegflag nur in Mailkopien, nie im
Scan-Cache“ ist zu breit. Historische Swing-Pläne sollen außerdem ausdrücklich
nicht allein durch eine Quote zu NOW werden.

### L1 — schlechtere Health darf keinen besseren Zustand erzeugen

Resolver erhält explizites WAIT, übergeht dagegen WATCH_ONLY bei vorhandener
Execution-Flag-/Action-Kombination (`17593–17657`). Diese Nichtmonotonie ist real.
Nachfolgender Adapter verlangt jedoch aktuelle Quote-/Pfadbelege; die unabhängige
Mail-Health-Prüfung bleibt aktiv. Keine finale App-/Mailfreigabe allein aus
diesem Resolverdefekt oder eine reale Schadenshäufigkeit ableiten.

### R2 — nativer Bear-Crash-Pfad

Bear-Row/Setup/5m-Zusatz liefern keinen Spread; Crash-Klassifizierung verlangt
Execution-Health bereits **vor** finaler Quote (`api.py:9885,26770–26788,26903`).
Health deckelt unvollständige Execution auf79; Gate verlangt mindestens80 und
TRADEABLE (`trade_health.py:474–480,870–872`). Dieser konkrete Produzentenpfad
ist so blockiert. Nicht ohne Daten auf alle historischen Crash-Mails erweitern.
Crash-Mail ist dabei explizit `info`/„Kein Sofort-Short“, nicht automatisch eine
handelbare Orderaufforderung. Reparatur muss die Semantik erhalten.

### R3 — Intraday-Reminder

Raw Strategy-/BI-Cache-Auflösung liefert nicht zwingend die drei Top-Level-
Zustände, die der Intraday-Consumer prüft (`api.py:31563–31578`). Dadurch entsteht
409 trotz geeigneter Planstruktur. Der zitierte Test injiziert den fehlenden
Zustand per Mock, statt den echten Produzenten zu verbinden. Serverzustand
aus realer Quelle normalisieren; keine clientseitige Freigabe akzeptieren.
**Ausnahme:** `structure_1d` nutzt einen anderen, funktionierenden Vertragszweig.
„Alle Aktien-Reminder immer409“ ist deshalb nicht belegt.

### U1 — Metadaten-Fallback

Frontend-Präsentation liefert ohne `visibility_status` keinen Kandidatenhinweis
(`frontend/index.html:3350,3681–3682`), während Grade/Score erscheinen können.
Defensiver Fallback fehlt. Der normale `/api/scan-results`-Pfad setzt jedoch
diesen Status für jede sichtbare Instrumentzeile. Fixture-Beweis ist kein Beweis
eines aktuellen normalen API-Ausfalls oder dessen Häufigkeit.

Diese ausgewählten Pfade wurden read-only parallel gegengeprüft und von Root
an den entscheidenden Grenzen nachgelesen. **Keine Reparatur in dieser Runde.**
Übrige T/K/L/U-Befunde des 647-Zeilen-Berichts noch nicht vollständig einzeln
validiert; nicht pauschal „alles erneut auditiert“ melden.

## 4. Abnahme-/Release-Wahrheit

Die bereits freigegebene Windows-Testhilfe ist ein getrenntes Arbeitspaket.
Letzte Stage03-Probe: tatsächliches RED3 mit 2 Fehlern/1 PASS; danach 15 PASS,
0 Fehler/Errors/Skips. Witness entfernt seinen DEBUG-/BASH_ENV-Hook nach dem
ersten tatsächlichen Start, ohne Timeout/Qualifier/Assertions abzuschwächen.
Das ist **kein** vollständiger Projekt-PASS. Zwei Gesamtversuche wurden
kontrolliert beendet, ohne finale XML/gesamtgültiges Urteil. Frühere 97 Fehler
nicht als aktuellen fertigen Dauerzustand darstellen; gezielte PASS-Fälle
ersetzen umgekehrt keine abgeschlossene Suite.

Private Stage03-Verpackung abgeschlossen; unabhängiger read-only scoped Review
akzeptiert beide eigenen Patches ohne Befund. Root hat Manifest, Own-Diffs,
Quellhashes und tatsächliche RED/GREEN-XML erneut gelesen. Kein neuer Testlauf
während dieser Gegenprüfung; finaler eingefrorener Gesamtlauf bleibt offen.
Serverrevision, Signalzustellung, Backtest-Auswertung und tatsächliche
Optionsrechte sind weiterhin getrennte Live-Gates.
Keine Keys, privaten Mailadressen oder rohen Diagnoseexports in einen Commit.
