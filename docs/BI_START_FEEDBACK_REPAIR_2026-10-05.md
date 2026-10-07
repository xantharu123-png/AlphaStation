# BI-Startfeedback und anschließende Signal-Mailprüfung – 05.10.2026

Veröffentlichungsnachtrag 07.10.2026: die beschriebenen Reparaturen sind in
`8d119ad4b8e39bf7c38ecaf956a7555e82f1798f` auf `origin/main` veröffentlicht.
Die folgenden Abschnitte dokumentieren den ursprünglichen Prüfzeitpunkt;
Serverupdate, neuer BI-Lauf und echte Signalzustellung sind weiterhin separat.

## Reparatur

Der Nutzer hat korrekt auf den Startknopf geklickt. Das zufällig offene Menü im
Screenshot ist keine nachgewiesene Ursache. Die konkrete HTTP-/Transportantwort
seines Versuchs wurde nicht aufgezeichnet und bleibt unbekannt.

Ein tatsächlicher Fehler wurde im ausgelieferten Frontend reproduziert:
`ScanControl.startScan` lädt im `finally` erneut Ergebnisse. Ein erfolgreicher
GET des alten Caches löschte in `useScannerFeed.readResults` die gerade gesetzte
Startfehlermeldung. Auch passive Ergebnis-GETs konnten sie löschen. Entfernen
allein dieses `finally` hätte die Ursache nicht vollständig behoben.

Die Startfehlermeldung hat jetzt eigenen Zustand und Evidenzbesitz, getrennt vom
Ergebnis-Lesefehler. Sie bleibt beim unveränderten Altstand erhalten. Ein neuer
bewusster Versuch oder Richtungswechsel verwirft sie; ein anderer belegbar
späterer Lauf kann sie ablösen. Eine Änderung der Run-ID allein, der Abschluss
des bereits beobachteten Laufs und zukünftige/ungültige Zeitstempel reichen nicht.
Transportunsicherheit wird nicht als angenommener Start oder Erfolg ausgegeben.

Die unabhängige Nachprüfung fand zusätzlich zwei behobene Randfälle:

- Ohne bekannte Baseline durfte ein erstmals geladener historischer Cache die
  Startfehlermeldung nicht löschen. Maßgeblich ist dann die bewusste Startzeit.
- Bei ungültiger alter Cache-Zeit, aber gültiger alter Versuchzeit, muss ein
  wirklich späterer validierter finaler Lauf vergleichbar bleiben.

Backend-Startsperren, gemeinsame Aktien-Engine, 17/20-Regel, Signalfreigaben,
SMTP, Dedupe und Serverkonfiguration wurden nicht geändert.

## Verifikation

- TDD: erster realer Hook-/Buttonwrapper-Testlauf 19 fehlgeschlagen / 2 bestanden;
  weitere Randfälle ebenfalls vor ihrer Reparatur rot nachgewiesen.
- 29 neue Regressionstests. Finaler gezielter Lauf: 191 bestanden, 0 Fehler/Skips;
  `output/mail-fix-qa-6cf5937dff4047e79a37ef42c131d7f5/results.xml`.
- Unabhängige finale Prüfung: 112 bestanden, keine verbleibenden Befunde;
  `output/mail-fix-qa-804a293fa2544bf09f0ad6ee9cd7ee19/results.xml`.
- Vollständiger isolierter Offline-Lauf: **11.604 bestanden, 0 fehlgeschlagen,
  0 Fehler, 5 Plattformausnahmen**, ein vorhandener anyio-Rewrite-Hinweis.
  XML umfasst 11.609 Fälle, Dauer 1.093,406 s:
  `output/mail-fix-qa-b0e0e47f1900438291ab7622a18126a6/results.xml`.
  Die fünf Ausnahmen betreffen Windows-Symlinkrechte, drei Linux-
  O_NOFOLLOW/Atomic-Rename/FIFO-Verträge und POSIX-Dateirechte für Gap-Zeitpläne.
  Sie sind keine bestandenen Linux-Prüfungen. Überlappende Läufe nicht addieren.
- Gerenderte Vorher-/Nachherprüfung im internen Browser, Desktop und 390 x 844:
  echte App und lokaler synthetischer Server. Ablehnung überlebt Button-Refresh,
  manuellen GET und automatische Altcache-GETs; angenommener Folgeversuch und
  Long/Short-Wechsel verhalten sich korrekt. Kein echter Scan oder Versand.
  Nachweise: `output/playwright/bi-start-feedback-browser-proof.md` und die
  zugehörigen vier PNG-Dateien. Temporäre Tabs/Server geschlossen, Größe zurückgesetzt.
- Bundle neu gebaut und Quellhash unabhängig verglichen:
  `18bd91c87dcc503706b88f305c8451efc96f5e7213510df330bda3907f241e9e`.
  SHA256 vor/nach Gesamtlauf unverändert: HTML
  `dc1a0e65f4dfaa187406578284fdf9df5da46f55417b426318eee18ecb0bab05`,
  Bundle `a0c6305f02de6905a21431b542d06bc62da7fa601a16359810d73217133e35f4`,
  neuer Test `faf37f984bec030efda43a4ed2ae884d65ca6c99c1e9cd850da9471db49cc397`.
  API und Telemetriemodul ebenfalls unverändert. Scoped `git diff --check` sauber.

## Signal-Mails: weiter geprüft, nicht abgeschlossen

Die technische Testmail wurde vom Nutzer als angekommen bestätigt. Sie ersetzt
keinen aktuellen Nachweis einer regulären Signal-Mail.

Zusätzliche unabhängige Quellprüfung des aktuellen finalen Swing-Pfads fand kein
neues reproduzierbares mechanisches False-Suppression-Problem oder verlorenen
Senderaufruf. Daily-Pläne erhalten keine neue 5m/4H-Abhängigkeit; nach bestandener
finaler Prüfung folgt der Sender. Fehlender Rücktest allein blockiert einen
bestätigten Ausbruch nicht. Die vorhandenen positiven Versandtests wurden im
Gesamtlauf erneut ausgeführt, ausschließlich mit simuliertem SMTP.

Die neue Live-Mailentscheidung lässt sich hier noch nicht lesen: Port 8000 war
im internen Browser mit `ERR_BLOCKED_BY_CLIENT` blockiert, der bestehende Tab
zeigt weiterhin die Landingpage. Keine Sicherheitssperre oder Anmeldung umgangen.
Einmalig die anonymen, rein lesenden `suppression_buckets`-Zähler angefordert;
Ausgabe beim Abschluss noch nicht eingegangen. Diese stündlichen, überlappenden
Grund-Vorkommen sind keine Zahl geeigneter Signale oder Senderaufrufe.

Für die anschließende Kettenprüfung stehen bereits `diagnostics.mail_audit`
mit `candidate_rows`, `reason_occurrences`, `trade_sender_called`, `trade_accepted`,
`trade_partial`, `trade_unknown` und `trade_failed` zur Verfügung. Erst konkrete
aktuelle Evidenz darf eine weitere Produktkorrektur begründen. Die Originalzonen
von DAC und echte Signalzustellung bleiben offen; keine künstlichen TP-Projektionen
als Strukturziele freigeben und keine Kriterien auf Verdacht lockern.

## Veröffentlichung und Betrieb

Lokale Reparatur, noch kein Commit/Push dieses Pakets. Kein Betreiberpull,
Serverneustart, manueller Produktionsscan, Einstellungs-/DB-Schreibzugriff oder
Mailversand durch diese Arbeit. Live zuletzt `healthy`, `e83bb1d6abdd`, Bundle
`67924e9f894a` bestätigt: das ältere Diagnosepaket ist installiert, dieser neue
Frontendfix nicht. Fremdes Deploy-/Dokumentations-/Test-WIP bleibt unangetastet.
