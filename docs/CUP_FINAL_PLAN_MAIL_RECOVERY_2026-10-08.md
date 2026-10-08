# Cup-Finalplan / Signal-Mailpfad – 08.10.2026

## Befund und Grenze

Cup ersetzte Entry, Handle-Stop und Ziele eines vorher erstellten nativen Plans,
behielt aber dessen Entscheidungsfelder und Strukturbelege. Damit konnten
Warnung, Freigabe und R-Abstand einen anderen Plan beschreiben. Das ist ein
nachgewiesener Verarbeitungsfehler vor SMTP, kein Nachweis, dass allein er alle
historischen fehlenden Signal-Mails verursacht hat.

Hetzner wurde vom Betreiber inzwischen auf `5dbad583d3e9` aktualisiert. Health
erneut healthy, Frontend-Bundle `8f8a6c0c5bae`. Admin zeigte am 08.10. den neuen
automatischen Aktien-Sammellauf um 11:18:55 abgeschlossen mit 13 Kandidaten;
Cup-Blattlauf um 11:18:52 abgeschlossen. Blattläufe verwenden bewusst keinen
eigenen Versand. Das Mailfenster um 11:55:49 hatte SMTP 0, ausgelassen 8,
Fehler 0, Warteschlange 0. Fehlende Transportzähler im Versuch = unbekannt (`—`),
nicht 0. Eine Zusammenfassung von Gründen kann mehrere Kandidaten betreffen.
Technische Testmail war angekommen; keine weitere Testmail veranlasst.

Der gesonderte lesende Sender-/Publisher-Abgleich bestätigt keinen Verlust des
Sammellauf-Audits: das Capture startet mit leerem `transport_events` und erzeugt
Ereignisschlüssel erst bei den jeweiligen Aufrufen. Abgelehnte Kandidaten können
deshalb eine bekannte Kandidatenzahl und fehlende Transportspalten besitzen.
Der Reader stellt fehlende Schlüssel als `—` dar, nicht als explizite Null.

## Umsetzung

- Reale unveränderliche `StructureSnapshot`-Referenz aus der nativen Enrichment-
  Stufe intern an Cup übergeben; nicht als öffentlicher JSON-Rohdatensatz speichern.
- Strukturziele am Cup-Entry mit Handle-Stop erneut auswählen. Exakt gleiche
  Zonen und geschlossene Kerzen, kein Löschen naher Gegenbarrieren. Pattern-Stop
  nicht nach innen verschieben; gemeinsame Rauschdistanz und Stopcap erhalten.
- VRVP darf diesen Stop nicht ersetzen. Measured Cup TP1/TP2 bleiben gesonderter
  Kontext; strukturelle Ziele, Projektionen, ihre Herkunft und Quote-/Plan-R:R
  getrennt. Strukturelles TP1 mit projiziertem TP2 bleibt explizit unterscheidbar.
- Finaler Cup-Vertrag `cup_causal_structure_v1` bindet Preis-Aliase, Richtungen,
  Risiko, Ziele, Status, Grund, Projektionen, Barriere und Entscheidung. Das ist
  ein Konsistenzvertrag, keine eigenständige positive Handelsfreigabe.
- Sender und beide Kurs-Revalidatoren weisen alte/inkonsistente Pläne vor
  Providerabrufen ab; fehlende Strukturbelege dürfen nicht stillschweigend
  aus einem früheren Plan stammen. Einzelner Bool/string ist keine Preisevidenz.
- Negative Entscheidung nach TP1-Projektion explizit REJECT. Konkrete bereits
  negative Strukturgründe und Barrieren bleiben erhalten. Bestätigungszeit des
  Stops = Muster-Schlusszeit; Analyse-Cutoff bleibt separates Feld. Ohne Datum
  keine datierte Pattern-Stop-Autorität.
- Cup-Watch speichert begrenzt die benötigten skalaren und verschachtelten
  Plan-/Barrierenbelege. Queue, Monitor und Promotion prüfen Kohärenz erneut;
  Trigger allein hebt eine negative Strukturentscheidung nicht auf.
- Startup, REST und neue Reminder lesen denselben Vertrag. Inkompatible Cup-
  Caches sind nicht frisch/repariert, obwohl ihr genereller Cacheumschlag aktuell
  ist. Eine schreibfreie GET-Projektion darf keine erfolgreiche Recovery erfinden.
  Empty-Caches und gültige negative Kandidaten bleiben sinnvoll nutzbar.

Andere Scanner, ihre Schwellen, Morphologie, Universum und SMTP-Transporteinstellungen
wurden nicht gelockert. Frontend unverändert. Kein Neustart, Produktionsscan,
Mailpräferenzwechsel, BPIQ-Abo-/Zugangseingriff oder Produktionsdatenverlust.

## Nachweis

Die reale Testserie verwendet 260 datierte, geschlossene Tageskerzen. Vor dem
180-Tage-Detektorfenster liegen unabhängig bestätigte Angebotspeaks; die 4H-
Ausführungsevidenz rekonstruiert dieselben tatsächlichen Tages-OHLCV, nicht einen
widersprechenden beliebigen 4H-Kurs. Keine produktiven Admission-/Planmocks im
positiven Queue/Sender-End-to-End-Fall.

- Cup-Entry **101,20**, Handle-Stop **92,38**, Risiko **8,82**.
- Nahe echte Widerstandszone **110,7529915–111,2470085**: Platz rund **1,0831R**,
  daher WAIT_BREAK_RECLAIM, nicht künstlich freigegeben.
- Zwei unabhängige ältere Peaks **118/135** ergeben konservative strukturelle
  Ziele **117,75/134,75**; gemessene Cup-Projektionen **114,75/128,30** separat.
  Plan-R:R rund **2,84014**, Quote-R:R bei 101,70 rund **2,63305**.
- Fehlender/falscher/future Snapshot, undatiertes Muster, fehlende Struktur,
  mutierte Preise/Metadaten/Richtungen und alte Versionen bleiben negativ.
- Konkrete RED→GREEN-Fälle für Geometrie, Receipt-Mutationen, Cache-/Startup-/
  Reminder-Widersprüche und Erhaltung spezifischer Ablehnungsgründe beobachtet.
- 262 breite gezielte Tests bestanden; nach finaler Diagnosekorrektur 47
  Cup-/Cache-/Watch-Tests bestanden. Mengen überlappen, nicht addieren.
  XML `output/mail-fix-qa-94e9d35e38544a019285bd92379e2f76/results.xml`.
- Früher exakter Zwischenstand: 12.137 bestanden, 18 alte synthetische Cup-
  Fixture-Fälle fehlgeschlagen, 2 übersprungen. Diese Fixtures wurden auf echte
  Detektor-/Finalplan-Produzenten umgestellt, Schutzregeln nicht abgeschwächt.
- Unabhängige lesende Schlussprüfung: keine offenen konkreten Findings im Paket.
- Finaler exakter Produkt-Index: **12.171 bestanden, 2 übersprungen, 0 Fehler/Errors**,
  454,26s. Tree **`237fdd8199a628d749a8c380058c4a8595ee5f90`**; XML
  `output/release-verification-20261008-cup-release-5bb34e78aea4/qa-57fafe9b2612/results.xml`.
  Vier getrennte Deploy-Testdateien werden wie im vorherigen Produktlauf
  ausdrücklich ausgenommen: `test_deploy_auto_update.py`, `test_deploy_migration.py`,
  `test_deploy_retirement.py`, `test_deploy_security_reaudit.py`. Deshalb kein
  uneingeschränkt grüner Repositoryclaim.

## Veröffentlichung und nächste Freigabe

Zwölf scoped Produkt-/Testdateien unter
**`06d9860d1d7fb590e633d30bea2563229ea9ee1a`** committet und gepusht.
Der Commit-Tree stimmt exakt mit dem QA-Tree überein; Remote `origin/main` per
SHA bestätigt. Dieser Prüfbericht, TODO und Übergabe erhalten einen separaten
nachfolgenden Dokumentationscommit; die Healthrevision kann dessen SHA tragen.
Vererbtes WIP/retiriertes Deployskript und private Exporte bleiben ausgeschlossen.
Nur normaler Betreiber-Pull und API/BG-Neustart nach Ende aktiver Scans vorgesehen.

Die neue Cup-Reparatur ist veröffentlicht, noch nicht hier auf Hetzner installiert.
Echte reguläre Signal-Mail nach deren
Installation bleibt die offene Live-End-to-End-Grenze; Offline-SMTP und die
frühere technische Testmail werden nicht als Postfachnachweis ausgegeben.
