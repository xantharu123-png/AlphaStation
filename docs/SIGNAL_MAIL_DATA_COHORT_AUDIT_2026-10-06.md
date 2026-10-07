# Signal-Mail- und Datenfehlerprüfung – 06.10.2026

## Nachkontrolle 18:27 Zürich – neue erfolgreiche Momentum-Ergebnisse

Die neu gelesene Admin-Maildiagnose trägt 06.10.2026, 18:27:40 Zürich: ein Swing-
und ein Kryptoempfänger, 0 SMTP-Annahmen, 50 Auslassungen, 0 Versandfehler,
0 wartend. Weiter nur das begrenzte 50-Ereignis-/24h-Fenster, keine Tagesquote.
Die frühere Nachtkontrolle unten ist historisch; die heutige Erklärung wird
nicht aus deren damaligem `scan_data_invalid` fortgeschrieben.

Aktueller Momentum-Ergebnisstand: 12.607/12.607 geprüft, 46 sichtbare Ergebnisse,
abgeschlossene Tageskerzen vom 05.10.2026, Finalcachezeit 06.10.2026
15:28:14.312302 Serverzeit. ECHO erscheint mit Schlusskurs 98,21 USD, Änderung
4,20%, RVOL 2,01, Trade-Score 83/Grade A und „Im Scan freigegeben“.
Seine Detail-Mailvorprüfung zeigt jedoch ausdrücklich:
`momentum_mail_blocked_daily_quality_below_threshold` / Tagesqualität 70/96,
Mailminimum 78, acht Punkte fehlen. Kein fehlender Rücktest als Ablehnungsgrund.
Die angezeigten fünf Qualitätskomponenten summieren sich zu 70,370138 und werden
als 70 angezeigt. Original-OHLC und sämtliche Formeln wurden in dieser reinen
Nachkontrolle nicht erneut aus Providerdaten berechnet.

Konkreter Vertragsunterschied im gelesenen Quellcode:

- Admin-Sammelvorprüfung `api.py:10771`: allgemeine Klassifikation mit
  `cache_only=True`, nicht zusätzlich `_stock_strategy_mail_quality_state`.
  `10852–10853` kennzeichnet `cache_precheck`, `delivery_evaluated=False`.
- Tatsächlicher Sender `14419–14428`: Momentum-Mailqualitätsprüfung vor
  allgemeiner Klassifikation; Ablehnung beendet diesen Kandidaten vor SMTP.
  `9564–9577` nutzt `Breakout_Continuation_Score`, nicht den Trade-Score.
- Kandidatendetail `17273–17293`: ergänzt den spezifischen Mailcheck und
  zeigt die Qualitätsablehnung. Admin meldet dagegen 46 geprüfte Kandidaten
  und einen allgemeinen Precheck-Pass. Dieser eine Pass ist nicht mailfähig;
  die erste Zwischenbezeichnung „mailfähig“ wurde ausdrücklich korrigiert.

Neu beobachtete Momentum-Skips 16:50/17:50 enthalten ebenfalls die
Tagesqualitätsregel, aber zusätzlich überlappende aggregierte Gründe. Ohne den
zugehörigen anonymen Run-/Mailaudit ist keine tickerbezogene Gleichsetzung mit
diesen einzelnen Ereignissen erlaubt. Der konkrete ECHO-Previewgrund ist dennoch
direkt sichtbar und stimmt mit dem gelesenen Sendervertrag überein.
Andere aktuelle Vorprüfungen: BI Long/Short 0 Kandidaten, Gap Long 28/0 (Score),
Gap Short 2/0 (Barriere), Bear 4/0, Crypto Long 60/0 (Setup wartet), Early Movers
160/0 (BTC-Gegenwind). Auch diese Cacheprüfungen sind keine Versandbelege.

Keine zusätzliche Testmail, Scansteuerung, Einstellungsänderung oder Produktion-
Installation. Kein neuer Codefix/Testlauf in dieser Nachfrage. Lokaler HEAD
unverändert `e83bb1d6abdd`; BI-Startfeedback und Datenkohortenfix weiterhin
uncommittet, jüngster vorhandener Serverexport weiterhin 26.09.2026. Die aktuell
installierte Revision wurde daraus nicht hergeleitet. Offene Produktentscheidung:
zusätzliche Mailqualitätsfilter behalten und Freigabeanzeigen angleichen, oder
auf gültige App-Freigaben umstellen. Kein eigenmächtiges Lockern der Filter.

## Tatsächlich beobachteter Betrieb

Nach der Anmeldung wurde Admin → Mailversand direkt gelesen. Angezeigter
Diagnosestände: 06.10.2026, 00:28:33 und erneut 01:15:24 Zürich. Ein Swing- und ein Kryptoempfänger,
0 SMTP-Annahmen, 50 ausgelassene Entscheidungen, 0 Versandfehler und 0 wartend.
Dies ist der begrenzte RAM-Puffer (maximal 50 seit API-Start, höchstens 24h),
kein Nachweis für alle heutigen Versandentscheidungen oder das Postfach.
Keine weitere technische Testmail gesendet; die früher angekommene Testmail
bleibt ein historischer Einzelversand, kein heutiger Signalnachweis.

Die Vorprüfung zeigt keine mailfähigen Kandidaten im gelesenen Cache:

- BI Long/Short: jeweils 0 Kandidaten.
- Aktienstrategien: 37 Kandidaten, 0 freigegeben; zusammengefasster Grund Score < 80.
- Gap Long/Short: 13/3 Kandidaten, 0 freigegeben; Sitzung des Caches veraltet.
- Biotech: 6 Kandidaten, 0 freigegeben; Grade unter S/A/A+.
- Bear: 1 Kandidat, 0 freigegeben; `entry_quality_watch_only`.
- Crypto Long: 80 Kandidaten, 0 freigegeben; Setup wartet auf Freigabe.
- Early Movers: 160 Kandidaten, 0 freigegeben; BTC-Gegenwind.

Diese Cache-Vorprüfung ersetzt keinen abgeschlossenen neuen Lauf. Insbesondere
ist ein dominanter zusammengefasster Grund nicht automatisch der Grund jeder Zeile.

Momentum zeigt einen fehlgeschlagenen aktuellen Versuch:
`scan_data_invalid`, Phase Aktienanalyse, 2.735/12.607 geprüft, 86 s,
157 Provideraufrufe und 0 Historien-Cachetreffer. Der angezeigte Altstand mit
37 Kandidaten stammt vom 05.10.2026, 20:16:36.550018 Serverzeit,
Analyse-Session 02.10.2026. Ein unvollständiger Versuch ist kein Scan mit null Signalen.

Turtle zeigt `scan_data_incomplete` und den Altstand vom 05.10.2026,
20:14:19.667258 Serverzeit. Ein anschließend automatisch gestarteter Turtle-Lauf
wurde ohne manuellen Start bis zu erneutem Datenfehler beobachtet; der Altstand
blieb erhalten. Die UI liefert weder den konkreten Turtle-Kohärenzgrund noch die
vollständigen anonymen Datenfehlerzähler des Momentum-Attempts.
Admin → System Logs meldet 0 Zeilen und ersetzt das systemd-Journal nicht.

Das normale Öffnen des öffentlichen Port-8000-Health-Endpunkts wurde durch
`ERR_BLOCKED_BY_CLIENT` abgewiesen. Sperre nicht umgangen, Prüftab geschlossen.
Der angemeldete ursprüngliche App-Tab blieb offen. Eine heutige neue Revision
wird aus dieser fehlgeschlagenen Health-Navigation nicht behauptet.
Ein einmaliger direkter, rein lesender SSH-Versuch mit BatchMode und bestätigtem
Hostschlüssel wurde ebenfalls abgewiesen (`publickey,password`); keine
Zugangsdaten gelesen. Die bereits angeforderte anonyme Attempt-Abfrage ist noch
unbeantwortet. Kein weiterer Gesamtexport oder weiterer Testversand angefordert.

Die Kontokonfiguration wurde anschließend ausschließlich lesend geprüft;
persönliche Kanal-/Modus-/Watchlist-Einstellungen werden nicht veröffentlicht.
Keine Einstellung geändert. Im Produkt umfasst Swing die regulären
Aktienstrategie-/Gap-Mails; ORB/Penny sind separat Intraday. Der Operator-Fallback
hat nicht dieselbe persönliche Horizontfilterung. Eine Moduswahl allein belegt
weder einen konkret fehlenden Empfänger noch die heutige Ursache.
Die beobachteten Score-/Plan-Skips sind keine `missing_recipient`-Nachweise.
System Health zeigte Scheduler aktiv, einen konfigurierten Mailempfänger,
Cooldown 0 s und dieselben begrenzten 0/50/0 Versandzähler.

## Nachgewiesener zusätzlicher Codefehler

Die generische Aktienstrategie prüfte dieselbe gemeinsame Datenausschlussgrenze
asymmetrisch in zwei Fehlerpfaden:

- lokal defekte OHLCV-Historien: ausschließlich eigener Historienzähler;
- Historie/Referenz-Abweichungen: gemeinsamer `excluded_data_symbols`-Zähler.

Die reale Wrapper-Gegenprobe mit identischen Datensätzen zeigt:

1. 20 lokale Historienfehler, dann 1 Referenzabweichung, dann gültige Aktie:
   Abbruch vor der gültigen Aktie.
2. Die Referenzabweichung zuerst, dann dieselben 20 Historienfehler und gültige Aktie:
   gültige Aktie bleibt erhalten, Abschluss mit 21 Datenausschlüssen.

Dies ist ein Reihenfolgefehler, kein Beweis einer bestimmten heutigen
Providerstörung. Nach Vertragsprüfung ist der Abschluss mit 21 Datenausschlüssen
die Lücke, nicht der Abbruch: Die bestehende Dokumentation begrenzt den Leaf auf
20 ungültige Symbole insgesamt. Die anfänglich getrennten Budgets wurden nach
diesem Review verworfen, statt eine zusätzliche Freigabe als Reparatur auszugeben.
Der aktuelle Live-Sammelcode allein weist dieser Lücke keine heutige Ursache zu.

Lokale Korrektur: beide Fehlerpfade und der spätere Spezialfilter prüfen dieselbe
gemeinsame Grenze 20. `daily_reference_exclusions` bleibt ein rein diagnostischer
Zähler. Bei 19H+1R oder 1H+19R bleibt die gültige Geschwisteraktie unabhängig von
der Reihenfolge erhalten. Gemischte 20H+1R, 1H+20R und 20H+20R werden in beiden
Reihenfolgen abgebrochen und erhalten die Originalbytes des vorherigen Finalcaches.
Homogene 21er-Kohorten bleiben fatal, ebenso Transport-/Zeitstempel- und andere
systemische Fehler. Der neue Diagnosezähler wird nur als
nichtnegativer begrenzter Integer in die anonymen Attempt-Diagnosen projiziert.
Keine Score-, Grade-, Rücktest-, Strukturziel- oder SMTP-Regel gelockert.

## Mailpfad-Gegenprüfung

Die zwei Skip-Ereignisse im 15-Minuten-Abstand passen zu den getrennten Crash-
und Short-Prüfungen derselben Bear-Liste (`_bear_scan_wrapper`). Sie sind keine
zwei SMTP-Versuche. Die UI entfernt Betreffe und zeigt maximal vier bekannte
Gründe in Tabellenreihenfolge, nicht zwingend die wichtigsten.
Ohne dieselben historischen Eingabezeilen/Betreffe bleibt die konkrete
Producerzuordnung der beobachteten Einzelereignisse eine starke Hypothese.
Ein stündlicher unbekannter Grund ist ebenfalls nicht eindeutig zugeordnet.
Kein neuer nachgewiesener Feldverlust gültiger nativer Pläne gefunden.

Turtle besitzt bislang eine separate Fail-Closed-Regel für jede unbrauchbare
Pflichthistorie. Vorhandene Tests verlangen ausdrücklich Abbruch bei einer
veralteten/beschädigten Historie sowie bei einem systemischen Ausfall nach
einer gültigen ersten Aktie. Kein ungeprüfter Wechsel zur BI-Ausschlusspolitik.

## Verifikation

Die folgenden ersten Läufe gelten nur als historische Gegenproben der danach
verworfenen getrennten Budgets, nicht als Freigabe des finalen Grenzvertrags:

- ursprüngliche Agent-Gegenprobe: 1 fehlgeschlagen, 3 bestanden;
  `output/mail-fix-qa-2baba4e665ce4224a863113281df3ed9/results.xml`.
- vollständiges RED der 8 neuen Fälle: 2 fehlgeschlagen, 6 bestanden;
  `output/mail-fix-qa-f6541d4db39344a4960ef10112ed54c0/results.xml`.
- unter der ersten Korrektur 176 gezielte Tests bestanden, 0 Fehler/Skips; enthält positive
  native Plan-/simulierte SMTP-/Ledger-Prüfungen sowie sämtliche 8 neuen Fälle;
  `output/mail-fix-qa-80445724fa0f4601869e0dbe77ef8f3e/results.xml`.
- erste unabhängige Nachprüfung: 92 bestanden; anschließend durch dokumentierten
  wichtigen Vertragsbefund überholt, keine finale Patchfreigabe daraus;
  `output/mail-fix-qa-e3136ed9ab354b65808beeb5ca90c321/results.xml`.
- Turtle-Vertragsgegenproben: 14 bestanden, 70 abgewählt;
  `output/mail-fix-qa-871ccd4675fe4adaa27911cfa7a9ee82/results.xml`.
- erster vollständiger isolierter Gesamtlauf wegen des Vertragsbefunds bewusst
  unterbrochen; keine vollständige grüne XML-Auswertung, kein Gesamtpassnachweis:
  `output/mail-fix-qa-d9b432955fff4e5f965e1ca72ebfd7c5/`.

Finaler gemeinsamer Vertrag:

- RED mit echtem isoliertem Finalcachewriter: 17 Fälle, 7 fehlgeschlagen,
  10 bestanden. Alle sieben Überbudgetfälle überschrieben unter den getrennten
  Budgets tatsächlich den alten Cache, einschließlich Cup-Spezialfilter.
  `output/mail-fix-qa-d564a7ee9b544bcd84cf8fb9ece912ec/results.xml`.
- GREEN: 443 bestanden, 0 Fehler/Skips; tatsächliche XML-Zähler gelesen.
  Enthält native Plan-/simulierte SMTP-/Ledger-Prüfungen sowie Gap/BI, Cup und
  Elliott-Verträge. Überlappende Prüfzahlen werden nicht addiert.
  `output/mail-fix-qa-6a87127d801e40da9cabd17f14e3a142/results.xml`.
- Finale unabhängige Nachprüfung: 208 bestanden, 0 Fehler/Skips, XML bestätigt;
  keine blockierenden Befunde. Gemeinsamer Guard ausdrücklich geprüft.
  `output/mail-fix-qa-70d90d7feb744b0cb888e8f5f6c4d59d/results.xml`.
- Vollständiger isolierter Gesamtlauf abgeschlossen: 11.620 bestanden,
  1 fehlgeschlagen, 5 übersprungen, 0 Sammelfehler; XML mit 11.626 Fällen gelesen.
  Der Fehlschlag ist ein `subprocess.TimeoutExpired` nach 30 s in
  `test_auto_update_passes_safe_directory_to_target_deploy`, kein fachlich
  fehlgeschlagener Scanner-/Mail-Assert. Dieser Test benutzt temporäre
  Installationen und Fake-Git; kein echter Pull/Deploy. Vier Skips sind
  Linux/POSIX-Verträge, ein Skip ist fehlendes Windows-Verzeichnissymlinkrecht.
  Dies ist ausdrücklich kein vollständig grüner Gesamtlauf:
  `output/mail-fix-qa-d60de01b4e5b41b6a2d65db2fe4e92f7/results.xml`.
- Unveränderten fehlgeschlagenen Test isoliert wiederholt: 1 bestanden,
  0 Fehler/Skips, XML gelesen; pytest 18,32 s statt des 30-s-Abbruchs.
  Der Timeout wurde isoliert nicht reproduziert; dessen konkrete Laufzeitursache
  ist nicht bewiesen. Keine Timeout-Anhebung oder Testabschaltung vorgenommen.
  `output/mail-fix-qa-e3d276d90be7409baaa5245f5e97d611/results.xml`.
- SHA256 von API, HTML, Bundle und neuer Regression vor/nach Gesamtlauf identisch:
  `6be9ebc5093becd68b0a77ee11596317ae785bae8d1259e4c4941a4b5bab45d8`,
  `dc1a0e65f4dfaa187406578284fdf9df5da46f55417b426318eee18ecb0bab05`,
  `a0c6305f02de6905a21431b542d06bc62da7fa601a16359810d73217133e35f4`,
  `69050f5f1df029c7e4320c560a564281c0b6b0d33f891f8360840590d156410f`.

Gezielte Läufe haben den bekannten anyio-Import-/Rewrite-Hinweis.
Keine echten Zugangsdaten verwendet, Außenverbindungen und echter SMTP durch
`scripts/run_offline_tests.py` gesperrt. Überlappende Testzahlen nicht addieren.

## Offene Grenze / nächste Schritte

Zusätzlicher, getrennter Offlinebefund: Der generische Wrapper wählt seinen
Validierungszeitpunkt erst nach dem Bulkabruf. In einer reinen AST-Gegenprobe mit
synthetischer Uhr und unveränderten Validierungsfunktionen kann das Überschreiten
der Sitzungsschwelle 20:15 UTC eine zuvor gültige tägliche Referenz ungültig machen.
Das globale `all(...)` fällt dann auf `live_snapshot` zurück; eine tägliche
Bulkzeile ohne `lastTrade.p` wird als fehlender Livepreis abgelehnt. Reproduzierter
Code dort: `scan_data_unavailable`, nicht der aktuell beobachtete
`scan_data_invalid`. Die Uhrzeit passt ebenfalls nicht zum heutigen Versuch.
Separat untersuchen und mit dauerhaftem Regressionstest absichern; nicht als
heutigen Mailfix verkaufen oder für den Versand stale Daten zurückdatieren.
Die Bulk-/Einzelabrufe sind außerdem keine atomare Provideraufnahme: eine
stabile Revision innerhalb derselben Session bleibt eine Annahme. Keine konkrete
heutige Providerrevision nachgewiesen, keine Datenregel deshalb gelockert.

Offizielle Anbietergrundlage für diese offene Annahme: [nachträgliche
Stornos/Korrekturen](https://massive.com/knowledge-base/article/how-much-does-massives-feeds-handle-canceled-trades)
können REST-Bars auch nach der Sitzung verändern. [Vor-/Nachbörse](https://massive.com/knowledge-base/article/does-massive-offer-pre-market-and-after-hours-data)
kann insbesondere Volumen aktualisieren, ohne OHLC zu ändern. In den geprüften
[Grouped-Daily-REST-Dokumenten](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
ist kein atomarer gemeinsamer Revisionsstand mit Einzelhistory zugesagt.
Die ungefähr 11:00 ET am Folgetag verfügbare Finalisierung der
[unadjustierten Flat Files](https://massive.com/docs/flat-files/stocks/overview)
ist keine REST-Finalitätsgarantie. Keine echten Providerdaten abgefragt; kein
Nachweis, dass eine solche Revision den aktuellen Lauf betroffen hat.

Die Änderungen sind lokal und uncommittet; kein Push, Pull oder Neustart.
Der bereits zuvor lokale BI-Startfeedbackfix bleibt getrennt vom Mailpfad.
Keine Einstellungen, Produktionsdaten, Reminder oder Scansteuerung verändert.

Für die tatsächliche aktuelle Momentum-Ursache den anonymen selben Error-Attempt
lesen: `stock_history_error_counts`, `invalid_history_symbols`,
`excluded_data_symbols`, `rejected.daily_reference:*`, Revision und Zeit.
Für Turtle ist dessen aktueller Kohärenzgrund erforderlich. Ein beobachteter
neuer erfolgreicher Lauf, konkrete finale Mailfreigabe, SMTP-Annahme und
Postfachzustellung sind weiterhin nicht bestätigt.
