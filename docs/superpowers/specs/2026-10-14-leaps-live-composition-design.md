# LEAPS: schlanker Anschluss mit echten Bid/Ask-Daten

Stand: 14.10.2026, Revision des Entwurfs nach Claudes Gegenprüfung.
**Schriftlicher Entwurf zur Prüfung, keine Code-/Produktionsfreigabe.**
Ersetzt den Anschlussentwurf aus `d88584a`, der so nicht umgesetzt werden soll.
Die Nutzerwahl ist ausdrücklich **„Nur echte Bid/Ask-Daten“**. Kein Richtpreis-Modus.

## 1. Ergebnis, Vorschlag und Grenzen

Langfristige Standard-Calls auf liquide US-Aktien/ADR, Verfall in 12–24
Kalendermonaten, mit echten Kontrakten, Bid/Ask, Kosten und Verfalls-Szenarien.
Eigenständiges Universum, nicht nur Momentum-/BI-Treffer. Vorgeschlagen ist ein
langsam laufender Nacht-Screener; keine Sofort-Kaufempfehlung oder neue Mailart.

Empfohlen: reine bestehende Kontrakt-/Verfallsrechnung weiterverwenden,
Provider-/Laufsteuerungsfehler beheben und die tatsächlichen Produktionsadapter
in **einem** Baupaket anschließen. Nicht obligatorische Mechanik in weitere
Entwürfe auslagern. Alternativen: unveränderten schweren Anschluss bauen
(verworfen), oder allein manuelle Einzelaktien abfragen (verfehlt Scanauftrag).

Neue Liquiditätsfilter und Nachtplanung unten sind **Vorschläge für LEAPS**,
keine Änderung bereits vereinbarter Aktien-/BI-/Krypto-Scannerbedingungen,
Mailgrenzen oder Zeitpläne. Keine Orders, Abobuchung, Brokeranbindung,
Landingpage-Umschreibung, Installationsmigration oder Wiederherstellung des
gelöschten Deployskripts. Andere Befunde aus dem Claude-Audit sind getrennte Arbeit.

## 2. Nachgewiesene Anschlussfehler — im selben Baupaket beheben

| Stelle | Tatsächliches Problem | Ziel |
| --- | --- | --- |
| `provider.py:371–398` | Greeks/IV bleiben nur im Rohdatensatz; normalisierte Werte fehlen. | Delta/IV wirklich zuordnen, dokumentierte Anbieter-Einheiten verwenden. Nur `units_evidence` ergänzen reicht nicht. |
| `provider.py:235,264–266`, `runtime.py` | 429 und temporäre Admission-Ablehnung führen zu persistentem Pausenzustand. | Temporär warten und automatisch fortsetzen; 401/403 und manuelle Pause getrennt lassen. |
| `runtime.py:534–547` | Gleichheit mit heutigem ET-Datum verhindert Wiederaufnahme über Mitternacht. | Festen Bewertungsstand erhalten; Restcursor wiederaufnehmen, überholten Lauf ehrlich abschließen. |
| `service.py:79–99,372`, `api_contract.py` | Datiertes Ask kann mit anderem Aktienzeitstand relative Kennzahlen und Rang erhalten. | Vergleichsfähigkeit **vor** Filtern/Ranking prüfen; veraltete Zeilen separat ohne Rang. |
| Live-Factories, Terms, Priorität | Produktionsadapter fehlen; Fixtures sind keine echten Quellen. | Kleine reale Adapter, API-/BG-Anschluss und Speicher im selben Paket, nicht nur Featureflag. |
| Probe | UTC-Tag statt gemeinsamem ET-Bewertungsdatum. | Kalender-/Zeitzonenlogik wiederverwenden, auch vor UTC-/ET-Tageswechsel testen. |

429 ist heute nicht irreparabel: explizite Reconciliation/Resume existiert.
Das Problem ist die fehlende automatische Erholung. Fehlende Greeks können
auch ein legitimes Anbieterergebnis sein; kein Nullwert und kein geratenes Delta.

## 3. Datenzugang — vorhandenen Polygon-Account prüfen

**Massive ist der neue Name von Polygon.io**, kein neuer Anbieterauftrag.
Stocks- und Options-Berechtigung werden getrennt im vorhandenen Dashboard
festgestellt: Tarif, Datenklassen, Feedverzögerung, zulässiges Publikum, Prüfdatum.
Die frühere erfolglose Mailboxsuche bestätigt oder widerlegt kein Abo.
[Umbenennung](https://www.massive.com/blog/polygon-is-now-massive).

Options-Referenzdaten stehen auch im Basic-Tarif bereit. Starter liefert
Greeks/IV und OI, aber keine Bid/Ask-Quotes; individuelles Advanced enthält
Optionsquotes. Aktienpreise im Snapshot hängen vom **Stocks**-Zugang ab.
Ein bestehendes Aktienabo beweist keinen Optionsquote-Zugang.
[Tarife](https://massive.com/pricing?product=options),
[Snapshot-Schema](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot).

Anschluss bleibt standardmäßig OFF, zunächst nur für den wirklichen Admin.
Bei Anzeige für andere Personen ist der erlaubte Vertrag separat nachzuweisen,
auch für Tester und abgeleitete Werte. API-Zugriff ist kein Lizenznachweis.
[Anzeigerechte](https://massive.com/knowledge-base/article/which-plan-do-i-need-to-show-massive-data-in-my-app).
Keine Abos buchen, keine veröffentlichten Schlüssel für Probes verwenden.

## 4. Universum und tatsächliche Kontrakte

Vorgeschlagene Voreinstellung: US-CS/ADR, Preis des Zielsitzungsschlusses
≥10 USD, höchstens sieben Kalendertage alte Market Cap ≥2 Mrd. USD,
Mittelwert der täglichen `Close × Volume` der letzten 20
vollständigen Handelssitzungen ≥20 Mio. USD. Watchlist und Scanner-Treffer werden
ergänzend durchsucht, **nicht** von diesen Liquiditäts-/Kontraktprüfungen befreit.
Fehlender Cap-/Historienbeleg heißt unprüfbar, nicht automatisch passend.
Kein verstecktes Acht-/200-Tickerlimit und keine Behauptung „gesamter Aktienmarkt“.

Referenz: `/v3/reference/options/contracts`, datiert höchstens sieben Tage
cachen, bei abweichendem Snapshot neu prüfen. Call, `cfi == OCASPS`, American,
100 Shares, keine Zusatzliefergegenstände, passender Basiswert/Strike/Verfall.
Währung und Symbolalias müssen geklärt sein; Anpassungen oder Widersprüche
bleiben ausgeschlossen. `OCASPS` allein genügt nicht: Zusatzliefergegenstände
gesondert prüfen. Der 100er-Prämienmultiplikator ist eine dokumentierte
Standardproduktregel, kein erfundenes API-Feld oder einzelnes Zertifikat.
[Referenzschema](https://massive.com/docs/rest/options/contracts/all-contracts),
[Standardprodukt](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications).

Je Ticker gefilterter Chain-Snapshot für Calls im 12–24-Monatsfenster.
**Alle** `next_url`-Seiten folgen, gleiche Verträge deduplizieren, Schema prüfen.
Eine Chain hat bis 250 Einträge je Seite; ein Aufruf oder zwei Seiten sind kein
Vollständigkeitsnachweis. Unterbrochene Chain = partiell, nicht null Treffer.
Keine blind befolgten Fremd-URLs oder Schlüssel in URL/DTO/Protokoll.

## 5. Preisbasis, Griechen und Historie

Nur tatsächliche positive, ungekreuzte Bid/Ask-Werte mit Originalquellzeit;
Spread auf den Mid bezogen. Abrufzeit separat, niemals Quellzeit erneuern.
Last Trade, Mid und FMV sind **kein** Ask-Ersatz. Ask ist beobachteter Angebotskurs,
kein garantierter Fill. Modell-Delta/-IV aus dem Snapshot normalisieren, Quelle
und Einheiten festhalten; endliches Call-Delta in [0,1], endliche IV ≥0.
Eine dort fehlende Modellzeit bleibt unbekannt.
Keine Einheiten aus der Wertehöhe raten, keine erfundene griechische Modellzeit.

Zwei ausdrücklich verschiedene Preisstände:

- **Aktueller Vergleich während regulärer Sitzung:** Beide Quotes tatsächlich
  aktuell, Echtzeitfeeds mit Lag0, originaler Zeitversatz ≤30s; bisherige
  Alters-/Größenprüfungen nicht zu einer bloßen Warnung abschwächen. Dieser
  Status kann nur nach tatsächlicher erneuter Quote-Abfrage entstehen.
  Verzögerte Quotes bleiben datiert und heißen auch tagsüber nicht Livekurs.
- **Nacht-Ergebnis:** Datiertes Research mit dem letzten abgeschlossenen
  Handelstag. Für ein Schlussstand-Ranking Optionsquote aus den letzten
  15 Minuten der regulären Sitzung und tatsächlicher Aktien-Tagesschluss
  derselben Sitzung; Referenz ist ausdrücklich `session_close`, kein Bid/Ask.
  Standard-US-Aktienoptionen, Kalender/DST/verkürzte Sitzung beachten.
  Keine erweiterten Sitzungen automatisch übernehmen. Datenquelle des Bars,
  Originaldatum und abgeleitetes Sitzungsende getrennt speichern. Das ist ein
  **datierter Vergleich**, weder synchrones NBBO noch aktuell handelbarer Preis.

Die zweite Basis erweitert bewusst den Research-Vertrag, **nicht** die aktuelle
Quote-Freigabe. Fehlender/älterer Ask, andere Sitzung oder außerhalb des
Schlussfensters: isolierter datierter Prämien-Payoff möglich, aber **kein**
Extrinsik-/BE-Ranking und keine preisabhängige Filterfreigabe. Beim aktuellen
Vergleich verwendet jeder Preisvergleich denselben passenden Quote-Zeitstand.
Das reine Ablaufdatum/Strike/Ask-Payoff bleibt von fehlender Aktienhistorie getrennt.

Historie: vorhandene datierte adjusted Tagesbars mit Kalender, eindeutigen
Sitzungen, plausiblen OHLCV und ausgeschlossenem offenem Tag verwenden. Keine
separate `DailyCompletionProof`-Hierarchie als allgemeine Scanvoraussetzung.
Adjustmentbasis dokumentieren; fehlende Sequenz sperrt die betroffene
HV-/EMA-Kennzahl, nicht die gesamte Kontraktliste. SEC-Daten nur soweit nötig
und separat cachen; SEC-Rate-Limit ist keine Massive-Berechtigungssperre.

## 6. Kennzahlen — deskriptiv, keine Gewinnwahrscheinlichkeit

`A` = Ask je Aktie, `K` = Strike, `S` = deklarierter vergleichbarer Aktienstand,
`n` = positive ganzzahlige Kontraktzahl, `T` = Tage bis Verfall /365
(ACT/365F, Bewertungsdatum fix); endliche positive A/K/S/T für relative Kennzahlen.
Kosten, Steuern, Finanzierung und Dividenden sind nicht erfundene Nullwerte.

| Kennzahl | Definition / Grenze |
| --- | --- |
| Kosten / Prämienrisiko | `100 × n × A`; möglicher Verlust der **gesamten** bezahlten Prämie zzgl. Kosten für den voll bezahlten Einzel-Long-Call. Risiko nach Ausübung/Finanzierung separat. |
| Verfalls-P&L | `100 × n × (max(S_T − K,0) − A)`. Keine Vor-Verfallsbewertung oder identische Exposure zu 100 Aktien behaupten. |
| Break-even | `K + A`; notwendige jährliche Aktienpreisänderung `(BE/S)^(1/T) − 1`, keine Optionsrendite oder Prognose. |
| Extrinsik | `E = A − max(S − K,0)`; absolut, `E/S`, monatlich `E/(12×T)`, jährliche Rangbasis `E/(S×T)`. Negatives E = inkonsistente Vergleichsbasis, kein Schnäppchen. |
| Delta-Hebel | `Delta × S/A`; lokale Modell-Exposure, nicht konstante Hebelwirkung über die Laufzeit. Delta0 ist gültig, fehlend bleibt unbekannt. |
| Spread / OI / Volumen | `(Ask−Bid)/Mid`; OI vom vorherigen Tagesende, Volumen mit eigener Sitzung/Quelle. Nicht austauschbar. |
| IV vs HV | 252 vollständige logarithmische Sessionrenditen aus 253 adjusted Closes: Stichproben-Standardabweichung (Nenner251) ×√252. IV−HV in Prozentpunkten; Quotient nur bei HV>0. Unterschiedliche Horizonte, kein Überpreisbeweis. |
| Dividendenvergleich | Nur datierte bekannte Ausschüttungen zeigen. Kein Yield×Laufzeit als gesicherte Gesamtsumme. Kein vollständiger Zukunftsbeleg = Gesamtwert unbekannt; nicht zum Options-BE addieren. |

Decimal für Geld/ungerundete Schwellen. Potenzen/Logarithmen numerisch mit
festgelegter Präzision berechnen; Rundung erst für Anzeige, keine Behauptung
exakter irrationaler Ergebnisse. Extrinsik p.a. ist eine lineare Aufteilung der
Prämie, **nicht** Theta, Effektivzins oder erwarteter jährlicher Verlust.

Default-Filter vorgeschlagen: Delta 0,70–0,85, Mid-Spread ≤10%, OI ≥100.
Fehlende Werte nicht bestehen lassen. Ranking aufsteigend `E/(S×T)` ausschließlich
im vergleichbaren Zielsession-Bestand; Moneyness/Delta und BE daneben zeigen.
Ältere Ergebnisse separat ohne Rang, Gleichstand deterministisch nach Symbol.
Kein zusammengewürfelter „profitabel“-Score; IV/HV und Dividenden nicht zwingende
Scan-Gates. Nachgemessene Quellenlimits bleiben sichtbar in der Diagnose.

## 7. Nachtlauf, temporäres Warten und gemeinsame Priorität

Vorschlag: `bg_service` täglich 01:00 America/New_York, erster Lauf für die
letzte abgeschlossene US-Sitzung. Kein erneuter Lauf für dieselbe bereits
fertige Zielsession über Wochenende/Feiertag; Admin-Start/Abbruch bleiben möglich.
Ein Worker, zunächst ein physischer Request/s, keine unbeschränkten Queues.
Bezahlte Anbieterrechte ersetzen nicht die eigene CPU-/Latenzbegrenzung.

429: gültiges `Retry-After` respektieren; sonst exponentiell 15/30/60/120/300s
mit begrenztem Jitter. Nach fünf aufeinanderfolgenden Rate-Limit-Antworten
Restcursor als partiell sichern; nächster regulärer Versuch kann fortsetzen.
401/403: betreffenden Zugang sperren bis wirkliche Konfiguration geändert ist,
kein Backoff-Angriff. Prioritäts-/Budgetwartezeit ist `waiting` mit automatischer
erneuter Prüfung, nicht `admin_paused`. Manuelle Pause verlangt manuelles Fortsetzen.
Retry-Antwortzeiten und tatsächliche Versuche zählen, Cursor nicht vorschnell vorrücken.

Midnight ändert **nicht** den gespeicherten Bewertungsstand oder Quotezeiten.
Fortsetzung derselben Zielsession zulassen, bis ein neuer Nachtlauf sie ersetzt;
alten Lauf dann partiell archivieren und neuer Lauf bekommt neues ET-Datum.
Keine Exception außerhalb der Fehlerbehandlung, die einen Lauf „running“ stehen lässt.

Bestehendes `_PROGRESS` ist prozesslokal, `bg_status.json` ereignisbasiert und
Limiter-JSON nur ein Zähler. Das ist **keine** reale gemeinsame Prioritätsfreigabe.
Nachtzeit allein schützt nicht vor BI-/Aktien-/Schutzjobs, die nachts weiterlaufen.
Im selben Baupaket kleiner prozessübergreifender Guard mit tatsächlichen
Start-/Ende-Meldungen der relevanten API-/BG-Marktjobs, LEAPS-Admission unter
demselben kurzen Guard. Ein laufender Abruf darf enden; danach warten neue
LEAPS-Abrufe. Fehlende Meldung/abgestürzter Worker nicht als idle interpretieren.
Keine neue Owner-Zertifikathierarchie oder pauschale 5s-Inode-Belegpflicht.
Teilnahme und Crash-Recovery müssen **im echten Anschluss** getestet werden.

Physisches HTTP außerhalb kurzer State-Locks; begrenzte Antwortgrößen und
Transportlaufzeit. Deadline umfasst Antwortbody, nicht nur einzelne Socket-
Lesevorgänge. Kein Task als beendet melden, während sein Abruf weiterläuft.
Den wiederverwendbaren Client/Worker im Bauplan festlegen; keinen Prozess pro
Request als obligatorischen Architekturvertrag. Originaler shared Limiter darf
nicht als harter Accountnachweis umetikettiert werden. Keine globale Änderung
seiner Scannerregeln nötig; LEAPS erhält eigene langsame Admission und Priorität.

## 8. Speicher, API und kompakte App

Ein datierter Ergebnis-Snapshot pro Lauf, atomisch über Tempdatei/Replace.
Kleiner Cursor-/Statusstand nach einer Chain-Seite bzw. einem Ticker; nicht den
gesamten N-Kontraktbestand nach jedem Eintrag umschreiben. Nach Abbruch letzte
fertige Auswertung behalten, neue als partiell kennzeichnen. Begrenzte RAM-
Seiten und paginierte Resultat-API; Ressourcenmessung gehört in denselben Bauplan.

Reine Pricing-/Kontraktfunktionen weiterverwenden. Schweren bestehenden
Admission-/Authority-/Watermark-/Checkpoint-Pfad nicht blind löschen oder mit
synthetischen Belegen umgehen: Night-Research-Vertrag explizit getrennt anbinden,
nicht parallel denselben Speicher inkompatibel schreiben. Import-/Statuslesen
ohne Anbieter-I/O; private Konfiguration nur serverseitig.

Chart zuerst, dann schmale Liste mit Laufzeit, Strike, Bid/Ask, Datum, Delta,
Kosten, BE und Extrinsik p.a. Mini-Payoff; Box „Prämie X USD · bis 100% Verlust“.
Einmaliger klarer Hinweis „Daten vom … · Markt geschlossen“ beim Nachtstand.
Keine Bestätigung eines jetzt handelbaren Preises. Rohcodes/Beleglisten in
Prüfdetails, keine Textwände pro Aktie. Adminaktionen nicht für andere anzeigen.
Keine LEAPS-Mails, keine neuen Watch-/Jetzt-Kanäle, keine anderen UI-Texte ändern.

## 9. Abnahme und noch fehlende Entscheidungen

1. Diesen **überarbeiteten** Entwurf freigeben, danach Bauplan und Ausführungsmethode.
2. Zusammenhängendes Baupaket: Normalisierung, Feld-Terms, vollständige Pagination,
   Zeitbasis/Ranking, Cooldown/Resume/Midnight, kleine echte Priorität, Speicher,
   API/kompakte UI. Diese notwendigen Teile nicht in unbestimmte Folgeentwürfe auslagern.
3. Offline Gegenfälle: Einheiten/Missing-Greeks, Zusatz-Deliverables/Alias, Null/
   Kreuzquote, alter Ask/neuer Aktienkurs, Wochenenden/DST/verkürzte Sitzung,
   429/Retry-After/401/403, konkurrierender Job/Crash, Cursor/Replace-Ausfall.
4. Mathematische Gegenrechnung und unveränderte Scanner-/Mailregressionen;
   repräsentativen liquiden Bestand für Laufzeit/RAM/Resultatantwort messen.
5. Wirkliche Stocks-/Optionsquote-Rechte und Publikum im vorhandenen Account
   klären. Nur gesondert autorisiert bis drei physische technische Probe-Requests;
   keine Vollabdeckung oder Lizenz aus diesen drei Antworten ableiten.
6. Erst geprüfte Veröffentlichung, bewusste Serverinstallation und separat
   freigegebener echter Admin-Vollauf. Technische Schlüssel zuvor sicher erneuert.

Ausstehend: reale Tarife/Zugänge und Freigabe der vorgeschlagenen LEAPS-
Liquiditätsfilter, Nachtplanung und datierten Schlussstand-Rankingbasis.
Nur-Bid/Ask-Produktwahl ist geklärt; kein günstiger Ersatzmodus. Dieses Dokument
aktiviert nichts und behauptet weder echte Vollabdeckung noch Mail-/Broker-Ausführung.
