# Prüfung von Kurslevels Trendlinien und Volumenprofilen

Die Prüfung vom 1. Oktober 2026 korrigiert die gemeinsame Datenbasis für Chartlevels und native Volumenprofile. Bestätigte horizontale Zonen, diagonale Trendlinien und strategieeigene Projektionen bleiben unterschiedliche Nachweise. Aus einer Projektion wird weder automatisch eine Unterstützung noch ein freigegebener Handelsplan.

## Korrigierte Berechnungen

Trendlinien benötigen drei unabhängige bestätigte Wendepunkte. Ihre Geometrie und das ATR-Toleranzband werden aus dem damals verfügbaren Kerzenpräfix festgelegt. Ein späterer bestätigter Schlusskursbruch entwertet die Linie; eine Erholung darf diesen Bruch nicht rückwirkend löschen. Datum, Bestätigung, Zeiteinheit, Anker und Bruch bleiben in den Daten nachvollziehbar. Die Chartdarstellung zeichnet nur aktive lineare Trendlinien. Einzelne unvollständige historische Kerzen verschieben die bestätigten Berührungspunkte nicht mehr.

Chartanalysen frieren den Auswertungszeitpunkt vor dem Datenabruf ein. Offene und zukünftige Kerzen bestätigen keine Struktur. Identische doppelte Kurszeilen werden einmal berücksichtigt; widersprüchliche Duplikate werden ausgeschlossen. Bei US-Tageskerzen gelten tatsächliche Börsensitzungen einschließlich Feiertagen und verkürzten Handelstagen. Die ursprünglichen Chartzeitstempel bleiben erhalten. Zusammengefasste 4H-Kerzen behalten ihr nominelles Ende, auch wenn bisher erst zwei Stunden geliefert wurden.

Die separate Grenzprüfung reproduziert einen weiteren Fehler bei verzögertem Starter-Zugang: Eine 1H-Kerze von 13:00 bis 14:00 UTC konnte um 14:01 als abgeschlossen markiert werden, obwohl die verfügbare Datenzeit erst 13:46 erreicht. Im Starter-US-Aktienpfad gilt deshalb auch im Chart der vorhandene 900-Sekunden-Abzug vor dem Abruf. Requestzeit und verfügbarer Strukturschnitt bleiben getrennt; alle Overlays und Abschlussmarken nutzen denselben verfügbaren Schnitt. Krypto, Yahoo-/Nicht-US-Routen und ausdrücklich gewählter Live-Modus werden dadurch nicht um 15 Minuten verschoben. Die Prüfung verwendet eine simulierte `DELAYED`-Antwort und echte Funktionen, keinen behaupteten Live-Bar-Nachweis.

Das sichtbare Volumenprofil berechnet sich beim Verschieben und Zoomen aus den sichtbaren abgeschlossenen Kerzen neu. Beide Chartansichten verwenden dieselbe Berechnung. Auch die Histogrammposition folgt der aktualisierten Preisskala. Beim Schließen oder Ausschalten werden Zeichenflächen, Preislinien und Abonnements entfernt.

Native Profile bestimmen ihren Preisbereich nur aus gültigen Kerzen mit echtem positivem Volumen. Ein Nullvolumen-Ausreißer konnte zuvor den POC von etwa 100 auf über 722 verschieben. Ungültige OHLC-Werte, umgekehrte Bereiche, boolesche Zahlen und nicht endliche Werte tragen nicht zum Profil bei. Die Volumensumme bleibt erhalten; kleine Kurse und gebrochene Volumenwerte werden nicht pauschal auf zwei Dezimalstellen oder Ganzzahlen gerundet. Es werden keine Ersatzlevels erfunden.

Dieselben Eingabeprüfungen gelten für die separate Anzeige von Volumenlücken. Deren bestehendes Mindestmaß von zehn gültigen Bars bleibt erhalten. Echte Lücken zwischen Mikropreis-Zonen werden nicht mehr durch einen absoluten Toleranzwert zusammengezogen; direkt angrenzende Bereiche werden weiterhin zusammengefasst.

Numerisch gleiche Bin-Volumina verwenden eine gemeinsame relative Toleranz von `1e-12`. Der POC liegt dann deterministisch im niedrigsten gleichwertigen Preisbin; gleichwertige Value-Area-Nachbarn werden zuerst nach oben erweitert. Die Volumenwerte selbst werden nicht verändert. Echte Unterschiede oberhalb dieser engen Toleranz bestimmen weiterhin das Ergebnis.

Die Nachprüfung eines Short-Plans zeigt eine fachlich korrekte neue Sperre: Ein belegter breiterer Profil-Stop senkt den Platz bis zum ersten Ziel von 1,66R auf 1,29R. Die bestehende Mindestgrenze wird nicht abgesenkt. Die Begleitdaten hatten dabei noch den alten Stop, das alte Risiko beziehungsweise den alten Barrierenabstand enthalten. Sie stimmen jetzt mit dem finalen Plan überein. Die ursprüngliche Entscheidung und ihre tatsächlich vorhandenen Levelnachweise bleiben getrennt als historische `pre_vrvp_*`-Daten erhalten.

Ein explizit gesperrter Plan kann auch ohne verschachtelte Entscheidung nicht gleichzeitig `entry_eligible=True` bleiben. Nicht darstellbare gerundete Geometrie wird abgelehnt, ohne Ersatzpreise zu veröffentlichen. Ein Projektionsziel übernimmt keine alte strukturelle Zonenkennung. Die Meldung bei zu wenig Platz bis zur ersten Kursbarriere wird von einer fehlenden Ausbruchsbestätigung unterschieden; dadurch ändern sich weder Mailfreigabe noch Risikogrenze.

## Nachweise je Scannerfamilie

Die gemeinsame Ergebnisdekoration enthält einen strukturierten Levelnachweis mit Herkunft, Zone, Bestätigung, Zeiteinheit und Datenstand. Er verändert keine Freigabeschwelle. Fehlen die Nachweise, bleibt der Level unbestätigt; der Adapter ergänzt keine fiktiven Preise oder Bestätigungszeiten.

| Familie | Weiterhin verwendeter eigener Leveltyp |
| --- | --- |
| Aktien Momentum Gap Cup und Wyckoff | Native Strukturzonen und Volumenkonfluenz aus abgeschlossenen Kerzen |
| BI Aktien | Eigene Range und natives 1D-Profil; weiterhin mindestens 17 von 20 Faktoren |
| Turtle | Donchian-Geometrie und Exit-Level; kein umbenannter bestätigter S/R-Nachweis |
| ORB | Opening-Range-Geometrie und eigenes Intraday-Profil |
| Penny | Eigene Intraday-Struktur und Profilnachweise |
| Krypto | Strategieeigene Geometrie und Profile mit explizitem Berechnungsvertrag |
| Elliott | Musterkontext ohne erfundenen nativen Handelsplan |
| Chart | Horizontale Strukturzonen, separate diagonale Trendlinien und sichtbares Profil |

Alte Aktienstrategiecaches werden durch Version 18 neu berechnet. BI verwendet dafür den eigenen Vertrag `stock-bi-20-v6`; die 20 Faktoren und die 17/20-Schwelle ändern sich nicht. Ein neuer Versionsstempel darf nicht nachträglich auf ein altes Profil geschrieben werden.

Krypto Early Movers und Explosion verwenden den neuen Profilcachevertrag 1; New Listing verwendet zusätzlich seinen eigenen Vertrag 3. Producer schreiben diese Kennung nur auf frisch berechnete Ergebnisse. Ergebnisrouten, Combined-Auswertung, finale Mailprüfung und Startprüfung weisen inkompatible alte Profile zurück, ohne sie neu zu stempeln. Ein frischer Dateizeitstempel allein darf den erforderlichen neuen Scan nicht unterdrücken.

Die finale Pause-/Owner-Prüfung folgt unmittelbar vor der Cacheveröffentlichung. Das Ergänzen der neuen Berechnungskennung erfolgt davor, damit eine zwischenzeitlich angeforderte Pause nicht durch neue Vorbereitungsarbeit nach der letzten Prüfung übergangen wird. Der Gesamttest reproduzierte den Reihenfolgefehler im Explosion-Producer; die korrigierte Reihenfolge besteht alle 110 Pause-/Publikations- und Krypto-Vertragstests.

ORB- und Penny-Ergebnisanzeigen haben eigene kurze Gültigkeitsfenster von 15 beziehungsweise 12 Minuten. Neue Einstiegsprüfungen berechnen Profile neu; sie spielen keine alten Ergebnispläne als neuen Einstieg ab. Für einen Vergleich nach dem Update ist trotzdem ein neuer vollständiger Scan erforderlich. Bestehende Penny-Positionen und ihre ursprünglichen Managementlevels bleiben erhalten. Turtle benötigt für diese Profiländerung keine eigene Invalidierung, weil sein eigener Pfad keine nativen Volumenprofile berechnet.

## Ausgeführte Prüfungen

Die isolierte Python-Prüfung verwendet künstliche Zugangsdaten und eigene Datenverzeichnisse. Externe Kursabrufe und SMTP sind gesperrt. Tests prüfen unter anderem abgeschlossene Kerzen, Bestätigungszeitpunkte, historische Zwischenbrüche, eingefrorene Toleranzen, Duplikate, Feiertage, wachsende 4H-Kerzen, winzige Kurse, Volumenerhaltung und die Trennung von Projektion und Struktur.

Die Browserprüfung verwendet das tatsächlich gebaute Frontend mit synthetischen Kerzen und gesperrten externen Anfragen. Beide Chartansichten wurden mit 1440 und 390 Pixel Breite geprüft. Der Wechsel zwischen zwei sichtbaren Bereichen änderte den POC von 22,784375 auf 104,284375; ein großer offener Volumen-Ausreißer blieb ausgeschlossen. Die Histogrammkoordinaten entsprechen der Chartpreisskala nach dem Zoomen. Alle vier Ansichten blieben ohne Browserfehler. Das Frontend trägt den Quellhash `41f168c9f109`.

Acht deterministische Vergleiche führen die tatsächliche JavaScript- und Python-Berechnung mit denselben abgeschlossenen Kerzen aus. Die 24 Bins, ihre Volumina, POC und Value Area stimmen innerhalb der numerischen Toleranz überein. Die Fälle enthalten Dojis, sehr kleine Kurse und stark unterschiedliche gebrochene Volumenwerte.

Die abschließende Metadaten- und Mailplanprüfung besteht 452 gezielte Tests und sechs separate unabhängige Long-/Short-Gegenprüfungen. 294 Krypto-Vertragstests enthalten ausdrücklich alte, fehlende, boolesche und textförmige Versionsangaben, auch in verschachtelten Ergebnissen. Frische Testdefaults werden vor expliziten Overrides gesetzt; ein Negativtest darf sich nicht selbst nachträglich neu stempeln. Die kompakte Warnungsdarstellung besteht 83 Tests mit Ausführung der echten JavaScript-Funktionen. Diese fokussierten Gruppen überschneiden sich und werden nicht zu einer vermeintlichen Gesamttestzahl addiert.

Für den finalen App-Gesamtlauf wurde der selektiv vorbereitete Git-Index in ein separates lokales Quellverzeichnis exportiert. Fremde Handbuch- und Installer-Änderungen sind darin nicht enthalten; aus `test_commerce_hardening.py` wird ausschließlich eine passende frische Krypto-Fixture übernommen. Der Lauf schließt `test_deploy*.py` aus, weil diese unabhängigen Installations-/Updaterpfade nicht Gegenstand dieses Updates sind. Im vorherigen vollständigen Arbeitskopie-Lauf liefen neun Windows-Bash-Aufrufe dieser Dateien in ihre äußeren Testzeitlimits. Das wird weder als erfolgreicher Installer-Test noch als geänderte Produkt-Zeitgrenze ausgegeben.

Der erste Index-Export-Lauf endete mit 9.929 bestandenen Tests, einem Windows-Permissions-Skip und drei Fehlern in unverändertem Mailjournal-/Datenbank-Reparaturcode. Die verschachtelten Testpfade waren dafür zu lang: Ein SQLite-Backup hatte bereits 252 Zeichen vor dem zusätzlichen Journalnamen; das atomare Mailjournal hängt weitere Prozess-/Thread-/UUID-Komponenten an. Dieselben drei Fälle bestehen mit identischem Quellcode an kurzen separaten QA-Datenpfaden. Der gesamte Index-Gesamtlauf wird unter dieser Windows-kompatiblen Testkonfiguration wiederholt, ohne Produktionscode, Tests oder Risikobudgets dafür zu verändern.

Der Gesamtlauf vor der zusätzlichen Starter-Chart-Grenzkorrektur besteht **9.932 Tests bei einem Skip**, ohne Testfehler, in 483,05 Sekunden. Der Skip betrifft ausschließlich nicht aussagekräftige POSIX-Modebits unter Windows. Die einzige Warnung ist die bereits importierte `anyio`-Testinstrumentierung. Der dabei geprüfte Code-/Test-Indexbaum lautet `10713129e970b429c6bf5bf833b23b5404049a3c`.

Die nachfolgende Starter-Korrektur besteht 156 fokussierte Charttests und eine unabhängige Prüfung aller 54 Tests ihrer neuen Grenzfall-Datei. Der erste neue Gesamtlauf meldet fünf veraltete Fixture-Annahmen: Vier Orderblock-Tests konstruieren ihre Barzeiten relativ zur verfügbaren Marktuhr, setzen aber implizit diese Uhr als Starter-Requestzeit; ein Wyckoff-Test erwartet trotz Starter die unverschobene Uhr. Die ursprünglichen Struktur- und Invalidation-Assertions bleiben unverändert. Beide Tests prüfen nun ausdrücklich Live und Starter. Der Wyckoff-Fall simuliert zusätzlich einen genau 15 Minuten dauernden Datenabruf und unterscheidet damit auch einen falschen Abzug erst nach dem Abruf. Alle 117 Tests dieser beiden Dateien bestehen auf dem exportierten Veröffentlichungsindex. Produktionscode und Schutzregeln wurden für diese Fixture-Korrektur nicht verändert.

Der abschließende vollständige App-Lauf auf dem Code-/Test-Indexbaum `88806fc2c21a2bce66b4e16fe788f954eaac07ca` besteht **9.959 Tests bei einem Skip**, ohne Fehler, in 514,69 Sekunden. Der Skip betrifft die POSIX-Modebits unter Windows; die einzige Warnung ist die bereits importierte `anyio`-Instrumentierung. Der Lauf verwendet kurze separate QA-Datenpfade und schließt ausschließlich die oben genannten unabhängigen `test_deploy*.py`-Dateien aus. Nach dem Test wird nur dieser Prüfbericht ergänzt; der geprüfte Code bleibt unverändert. Frontend-Bundle-Prüfung und JavaScript-Syntaxprüfung bestehen ebenfalls.

Die privaten Prüfartefakte liegen unter `output/playwright/levels-20261001/` und den isolierten `output/mail-fix-qa-*`-Verzeichnissen. Sie werden nicht veröffentlicht.

## Vergleich mit dem Screenshot und Servergrenze

Die weißen Linien im AST-SpaceMobile-Screenshot sind manuell gesetzte Trendlinien. Ihre exakten Anker und die ursprünglichen 8H-Kerzen liegen hier nicht vor; deshalb wurde keine identische Nachberechnung dieser drei Linien behauptet. Die App unterstützt 5m, 15m, 1H, 4H, 1D und 1W. Eine unbekannte Zeiteinheit wird ausdrücklich abgewiesen, statt heimlich 1H-Daten als 8H zu beschriften. [TradingView Trendlinienwerkzeug](https://www.tradingview.com/support/solutions/43000518095-trendline-drawing-tool/)

Das lokale Profil verteilt das Volumen einer OHLCV-Kerze über deren Preisbereich. TradingView verwendet beim sichtbaren Profil ausgewählte kleinere Zeiteinheiten und tickbezogene Zeilen. Gleiche Zeilenzahl bedeutet deshalb nicht identische Daten oder identische Verteilung. Unser Profil ist nachvollziehbar berechnet, rekonstruiert aber weder einzelne Börsenabschlüsse noch den tatsächlichen Kauf-/Verkaufsfluss. [TradingView Visible Range Volume Profile](https://www.tradingview.com/support/solutions/43000703076-visible-range-volume-profile/)

Hetzner wurde in dieser Prüfung nicht aktualisiert. Die rein lesende Health-Abfrage an der bekannten API-Adresse liefert `healthy`, Revision `0a3de8563903` und Frontend-Bundle `baffb67797ba`; das ist der Stand vor diesem Update. Bestehende Reminder, Kontoeinstellungen und Serverdateirechte wurden nicht verändert. Auch eine echte Signalzustellung im Postfach wurde hier nicht bestätigt. Das Update erfolgt weiter per normalem manuellem Git-Pull und Dienstneustart, ohne Deploy-Skript oder Installationsmigration.
