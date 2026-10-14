# LEAPS: schmale Produktionsanbindung

Stand: 14.10.2026. **Schriftlicher Entwurf zur Prüfung, keine Implementierungs-
oder Produktionsfreigabe.** Nutzerfreigabe dieser Stufe: „Ja, schmalen Entwurf
vorbereiten“. Die bestehende LEAPS-Kernrechnung bleibt unverändert.

## 1. Gewünschtes Ergebnis und Grenzen

Der vorhandene eigenständige Scanner soll tatsächliche langfristige Calls auf
US-Aktien/ADR im Fenster von 12–24 Kalendermonaten finden. Kein Beschränken auf
Momentum-/BI-Treffer oder eine kleine Tickerliste. Kontrakte, beobachteter Ask,
Prämienkosten und Verfalls-Szenarien sollen in der vorhandenen kompakten App-
Ansicht erscheinen. Vergleich und Szenario sind keine Order oder Kursprognose.

Diese Anbindung darf bestehende Aktien-, BI- und Mailprüfungen nicht verdrängen.
Scannerbedingungen, Mailgrenzen, Zeitpläne, Abos und Brokerzugänge bleiben
unverändert. Keine neuen Options-Mails, automatische Orders, Tarifbuchungen,
Installationsmigration oder Wiederherstellung des gelöschten Deployskripts.

## 2. Tatsächlicher Ausgangspunkt

Provider, Rechnung, Speicher, Laufsteuerung und API-Verträge sind vorhanden.
Die produktiven Policy-/Backend-Factories in `api.py` sind noch nicht gebunden.
Echte Terms-/Sitzungsproduzenten und vollständige gemeinsame Prioritätsmeldung
fehlen. Ein API-Key oder Feature-Schalter allein schließt diese Lücken nicht.

Die auf Nutzerauftrag durchsuchte verbundene Gmail-Mailbox enthält keine
passende Massive-/Polygon-Abo- oder Zugangsbestätigung. Das beweist weder das
Fehlen eines Abos noch Rechte anderer Konten. Tatsächliche Options-/Aktien-
Quote-Rechte, Feed-Verzögerung und zulässige App-Anzeige bleiben ungeprüft.

## 3. Gewählter schmaler Ansatz

Empfohlen ist eine feste Composition mit realen Referenzdaten, konservativ
klassifizierten Standardkontrakten und zunächst belegten regulären Sitzungen.
Unbelegte Tageshistorie bleibt ausdrücklich eingeschränkt, anstatt den gesamten
Callvergleich zu sperren. Kein zusätzlicher Datenanbieter wird beschafft.

Alternativen: bestätigte Tagesabschlussdaten zusätzlich anbinden; oder bereits
verfügbare echte OCC-/Börsenartefakte für stärkere Termsbelege verwenden. Beide
erfordern nachgewiesenen vorhandenen Zugang und gehören nicht automatisch zum
ersten Anschluss. Es werden keine Testfixtures zu Produktivbelegen umbenannt.

| Baustein | Aufgabe / bestehender Anschluss |
| --- | --- |
| Trusted Composition | Private, serverseitig übergebene Konfiguration; feste Provider-/Backend-/Policy-Factories, kein Browser-Importpfad. |
| Quellenbelege | Datierte, versionsgebundene Account-, Feed-, Kontrakt- und Sitzungsbelege; fehlend/abgelaufen bedeutet ungeprüft. |
| Terms-/Sitzungsadapter | Reine Zuordnung tatsächlicher Referenzzeilen zu den bestehenden Terms-/`VerifiedSession`-Verträgen. |
| Admission-Anschluss | Bestehenden gemeinsamen Zähler und tatsächliche Prozess-/Owner-Teilnahme qualifizieren; keine parallele Schattenquote. |
| Runner / API | Vorhandene `DiscoveryProvider`, `LeapsStore`, `LeapsRunner` und singleton `LeapsApiService` verbinden. |

Import, Policy-Prüfung und Statuslesen führen keinen Providerabruf aus. Start
und Fortsetzung sind explizite Adminaktionen; kein neuer automatischer Zeitplan.
Der tatsächliche angemeldete Account bestimmt Berechtigungen. Private Schlüssel
gehen ausschließlich in Transport-Header, nie in DTOs, URLs oder Protokolle.

## 4. Tatsächliche Daten statt Ersatzwerte

**Kontrakte:** Tatsächliches Call-Symbol, Strike, Verfall und US-Aktien-/ADR-
Notierung müssen zusammenpassen. Tatsächlicher CFI-Code `OCASPS`, American-
Style, 100 Shares und keine zusätzlichen Liefergegenstände werden gegen die
OCC-Klassifikation und Standardproduktregeln geprüft. Die Ableitung von
physischer Standardlieferung und 100er-Prämienmultiplikator wird als datierte
quellenübergreifende Ableitung gespeichert, nicht als erfundenes Massive-Feld.
Fehlende/widersprüchliche CFI-, Währungs- oder Aliasbelege bleiben ausgeschlossen.
Aktuell einfache Standardbedingungen beweisen keine nie angepasste Historie.
[Massive-Vertragsschema](https://massive.com/docs/rest/options/contracts/all-contracts),
[OCC-CFI-Zuordnung](https://www.theocc.com/getmedia/5bb03145-905b-4d83-b647-eaa2cb076e93/ENCORE_DDS_Guide_Non_Proprietary_Transmissions.pdf),
[OCC-Standardproduktregeln](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications).

**Sitzungen:** Ein tatsächlicher Kontrakt wird mit Basiswert, Klasse, Marktdatum,
Kalender und gültiger Regelversion verbunden. Zunächst nur belegte reguläre
Sitzungsintervalle, einschließlich Feiertagen/verkürzten Tagen. Keine allgemeine
Aktien-RTH-Flagge als vollständiger Options-Sitzungsbeleg. Erweiterte Sitzungen
werden nicht aus einem Blog oder Testsymbol aktiviert; getrennte Zeitfenster
und klassenspezifische Berechtigung bleiben nötig.
[Cboe-Sitzungs-/Klassenregeln](https://www.cboe.com/document/tech-spec/document/technical-specifications/equity-options-extended-trading-hours-faq).

**Quotes:** Originalzeiten, Feedmodus und belegter Lag für Option und Aktie
getrennt. Kein Last-/Mid-/FMV-Ersatz für Ask, keine Uhrerneuerung beim Lesen.
Vorhandene Grenzen für Quotealter/Zeitversatz bleiben unverändert. Ohne passende
Aktienquote kein aktueller Aktienkostenvergleich; eine gültige isolierte
Verfallsrechnung kann weiterhin datiert sichtbar sein. Keine Fill-Garantie.

**Historie:** Ohne wirklichen Tagesabschlussbeleg kein EMA-/Historien-Claim.
`CachedUnderlyingLoader` unterstützt diesen begrenzten Modus bereits; Daily-
Completion ist kein allgemeines Scan-Gate. Aktive EMA-Filter mit fehlender
Evidenz bleiben „nicht prüfbar“. Ein empfangener REST-Bar oder eine vergangene
Uhrzeit ist kein Abschlussbeleg; unadjusted Flat Files sind nicht automatisch
identisch zur adjusted REST-Historie. SEC-Daten bleiben getrennt und gecacht.
[Massive-Datengrundlagen](https://massive.com/docs/flat-files/stocks/overview).

## 5. Gemeinsame Laststeuerung: ausdrücklich keine erfundene Freigabe

Vor Bindung von `FileBudget` sind tatsächliche Account-Allokation, installierter
Limiter-Modus, native gemeinsame Lock-/State-Identität und alle Verbraucher
nachzuweisen. Der Anwendungs-Default 200/min ist kein Accountbeleg. Der alte
Limiter besitzt Selbstheilung, lokalen Fallback und einen Wartezeit-Bypass;
deshalb reicht sein passendes JSON-Format nicht für einen harten Budgetbeleg.
Nicht qualifizierbare Pfade bleiben für LEAPS blockiert. Eine dafür notwendige
Änderung alten Limitierverhaltens darf nicht unter dieser Anbindung versteckt
werden: konkret abgrenzen und vor Umsetzung separat entscheiden.

Prioritätsstarts in API und Hintergrunddienst müssen denselben kurzen nativen
Reservation-Guard benutzen. Ein vollständiger tatsächlicher Owner-Roster
umfasst auch Alert-/Schutzabrufe und pausierte/noch endende Worker. Zeitpunkte
jedes beteiligten Prozesses bleiben eigenständig; ein frischer Publisher darf
die fehlende Aktualität eines anderen Prozesses nicht erneuern. Unbekannt,
Workerabsturz oder fehlende Teilnahme ist nicht „idle“. Die bestehenden fünf
Sekunden Frischegrenze und Lock-Identitätsprüfung bleiben erhalten.

Kein HTTP unter dem gemeinsamen kurzen Guard. Ein bereits zugelassener Abruf
darf enden, wenn danach ein Prioritätsjob startet; neue LEAPS-Abrufe warten.
Der eigene Request-Lease bleibt bis zum tatsächlichen Transportende gehalten.
Retries zählen als eigene physische Versuche. Kein künstliches Minimum von
einem Request, wenn der bestehende LEAPS-Budgetvertrag null zulässt.

## 6. Berechtigungen, Bedienung und Ressourcen

Standard bleibt **OFF**. Erst tatsächlich geprüfte Quellen- und für das konkrete
Publikum gültige Anzeigerechte erlauben Start/Lesen. Zunächst nur der tatsächliche
Admin; auch dafür keine erfundene Displaylizenz. Eine erfolgreiche API-Antwort
bestätigt technischen Zugriff, nicht automatisch die Mehrnutzer-Lizenz.

Bestehende kompakte Ansicht, keine neuen Textblöcke unter Aktien. Fehlender
Zugang oder wartende Priorität ergibt einen kurzen Status; Belegdetails bleiben
in der Diagnose. Quelle-/Budgetfehler ergeben partiell/blockiert, nicht null
Möglichkeiten. Es wird kein neuer Score, Entry/Stop/TP oder WATCH/JETZT-Kanal
eingeführt.

Die bisherige 2.000-Kontrakt-Simulation ist kein Vollmarkt-Kapazitätsnachweis.
Vor Aktivierung werden repräsentativer Bestand, Speicher/Checkpoint-Kosten,
kalte Resultatabfrage, API-Reaktionszeit, Gesamtzeit und Quotealter unter realem
Budget gemessen. Keine künstliche Acht-Ticker-Grenze. Wenn Vollmarkt unter dem
vorhandenen Speichermodell nicht tragfähig ist, bleibt Aktivierung gesperrt;
eine chunkweise Datenhaltung wäre ein gesonderter begründeter Entwurf.

## 7. Abnahme und Reihenfolge

1. Schriftlichen Entwurf prüfen; danach erst Bauplan und Ausführungsmethode.
2. Private Konfiguration ohne IO beim Import, fehlende/abgelaufene/mismatched
   Belege, tatsächliche Accountidentität und Geheimnisfreiheit offline testen.
3. Reale Referenz-/CFI-Zuordnung, Sitzung/DST/Feiertag, Feedlag und Originalzeit
   gegen unabhängige Fälle prüfen; eingeschränkte Historie nicht schönrechnen.
4. Mehrprozess-Reservation, gleichzeitig startenden Prioritätsjob, stale Owner,
   Prozessabsturz, Budget-/Inodefehler und physische Retryzählung nachweisen.
   Fehlt vollständige Teilnahme oder harte Budgetqualifikation: OFF erhalten.
5. Unveränderte Scanner-/Mailregeln, Laufsteuerung und Desktop/Mobil regressieren.
   Repräsentative Vollmarktlast separat qualifizieren, nicht aus Testzahlen ableiten.
6. Nur gesondert autorisiert höchstens drei physische technische Provider-
   Probeversuche; Access/Lag-Ergebnis und Displaylizenz getrennt dokumentieren.
7. Erst nach geprüfter Veröffentlichung und tatsächlichem Serverzugang normal
   installieren, native Lock-/Pfad-/Dienstrealität bestätigen und ausdrücklich
   gestarteten realen Lauf abnehmen. Kein Serverumbau aus dieser Spec.

Ein Kern-Test-PASS ist keine Livefreigabe. Dieser Entwurf kauft keine Daten,
aktiviert keinen Scanner und behauptet weder echte Optionsabdeckung noch
SMTP-/Broker-Ausführung. Unabhängige offene BI-/Signal-Mail-Arbeit bleibt getrennt.
