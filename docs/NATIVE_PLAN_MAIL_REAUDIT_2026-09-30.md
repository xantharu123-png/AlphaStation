# Native Handelspläne und technische Versandkontrolle

Basis: `77d048077321`, Branch `main`, 30.09.2026. Drei Fehler bei der
Übernahme bestätigter Handelsplan-Level sind korrigiert. Der persönliche
Transporttest ist vorbereitet; echte SMTP-Annahme und Empfang bleiben
nach dem Betreiber-Pull auf Hetzner zu prüfen.

## Neu nachgestellt und korrigiert

| Übergang | Fehler | Korrektur |
| --- | --- | --- |
| Strukturplan → VRVP | Ein unabhängig bestätigtes TP2 hatte Zone/Zeit, aber keine Zielidentität; VRVP ersetzte es durch eine Projektion. | Quellfamilie, eindeutige Zone, Zeitrahmen, Bestätigung und Datenstichtag vollständig erhalten. |
| Erste Gegenbarriere → TP1 | Das bereits bestätigte TP1 verlor bei der erneuten Auswahl seinen Validierungsstatus. | Die kanonische Barriere überträgt denselben kausalen Nachweis. |
| Strukturstop → VRVP | Ein näherer Volumenknoten konnte den Stop innerhalb der ursprünglichen Invalidierungszone platzieren. | Ein bestätigter Strukturstop bleibt einschließlich Puffer geschützt. Eine zulässige Verbreiterung berechnet Risiko und R:R neu. |

Die neuen Gegenproben verwenden vollständig synthetische, abgeschlossene
US-Tageskerzen, gespiegelt für Long und Short. Sie durchlaufen den echten
Strukturbuilder, das Volumenprofil, die Levelprüfung und die Mailklassifikation.
Keine Produktionsdaten, API-Zugänge oder echten Mailserver sind nötig.

Zusätzlich geprüft: Eine nähere echte Gegenbarriere darf nicht übersprungen
werden; Projektionsziele erhalten keinen erfundenen Bestätigungsstatus;
spätere Kerzen verändern die früher berechneten Levels nicht.
Die Aktienstrategie-Cacheversion steigt von 16 auf 17. Alte Ergebnisse
verhindern keinen neuen Startlauf und werden nicht als korrigierte Pläne übernommen.

## Transport und tatsächliche Ablehnungen unterscheiden

Die neuen Transportgegenprüfungen durchlaufen reale Gap-Pläne, Freigabe,
Endprüfung, Sender und Zustellungsjournal mit simuliertem SMTP. BPIQ-401
und ausgeschaltete Watch-Mails sperren gültige Swing-Signale nicht global.
Vor SMTP stehen weder endgültige Versandmarkierungen noch erfolgreiche
Zustellungszeiten. Explizite Ablehnung erlaubt einen später neu geprüften
Versuch; ein unklarer DATA-Ausgang wird nicht als Annahme erfunden.

Ein sauberer bestätigter Momentum-Ausbruch wird auch ohne Rücktest freigegeben.
Setup-Score, gewichteter Trade-Score und Tageskerzenqualität sind getrennte
Messgrößen. Die bestehenden Grenzen werden nicht abgesenkt.

Wichtige Nachweisgrenze: Die native Beispielreihe zeigt die drei Planfehler,
beweist aber nicht deren alleinige Verantwortung für ausgebliebene Live-Mails.
Ihre weiteren Timing-Ablehnungen bleiben vor und nach der Reparatur gleich.

## Einmalige persönliche technische Testmail

`POST /api/test-email` bleibt adminpflichtig. Empfänger ist jetzt ausschließlich
die eigene authentifizierte Adminadresse, nicht eine konfigurierte Verteilerliste.
Betreff und Inhalt nennen ausdrücklich eine technische Testmail, kein
Handelssignal und keine Order. Es werden keine Mailpräferenzen verändert.

Die geschützte Ansicht **Admin → Mailversand** bietet einen Button mit
Inline-Bestätigung, Abbrechen und Einmal senden. Ein synchroner Doppelklick
erzeugt nur einen POST. Netzwerkabbruch, ungültige Antwort und unklare SMTP-
Annahme führen nicht zu automatischer Wiederholung. Der technische Test
verwendet keine nachgelagerte Versandwarteschlange; andere Info-Mails behalten
ihr bisheriges Verhalten. SMTP-Annahme bedeutet nicht automatisch Posteingang.

## Abnahme und offene Produktionsgrenze

- Neue kausale Plan-/Cachetests: 16 bestanden.
- Neue Frontend-Handler-/Cookie-/Bestätigungstests: 11 bestanden.
- Neue Backend- und Transportgegenprüfungen: 21 bestanden.
- Eingefrorener Gesamtlauf im vorhandenen Arbeitsbaum: **9.809 bestanden,
  5 Plattform-Skips, keine Fehler**, 1.191,42 Sekunden. Die vorhandene
  `anyio`-Importwarnung bleibt unverändert. SHA256 aller acht geprüften
  Code-/Testdateien vor und nach dem Gesamtlauf identisch.
- Kompiliertes Frontend: `baffb67797ba`.
- Der neue Button wurde lokal gerendert. Die damalige native Bestätigungsbox
  blockierte das Browserwerkzeug; die endgültige Inline-Interaktion wurde
  deshalb zusätzlich durch Ausführung des echten Handlers in Node geprüft.
- Kein Produktivscan, Pull, Neustart, Handel oder echte Testmail ausgeführt.
- Betreiber-Pull, neuer vollständiger Scan und reale Testmail/Empfang bleiben
  nach Veröffentlichung auf Hetzner zu prüfen. Kein weiterer privater Export
  ist für den Testmail-Button erforderlich.
- Veröffentlicht werden ausschließlich die zugehörigen Code-/Testdateien und
  dieser Bericht. Bestehende Deploy-Änderungen und private QA-/Exportdateien
  bleiben außerhalb dieses Reparaturpakets.
