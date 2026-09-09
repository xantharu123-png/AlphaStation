# Scanner-Reparatur nach Hetzner-Evidenz vom 09.09.2026

## Auftrag und tatsächlicher Ausgangsbefund

Freigabe: „alles machen“ nach dem Audit des privaten Exports vom
09.09.2026, 20:28:30 UTC. Technische Ausgangsversion: `93df14b1903a`.
Private Signalzeilen und Exporte bleiben unter `output/profitability/`; dieses
Verzeichnis wird zusätzlich gegen versehentliches Git-Staging ignoriert.

- BI Long: 5.293 Aktien geprüft, 4.200 analysiert, keine mit mindestens 17/20.
  Maximum 15, keine fehlenden Indikatoren oder Analyse-/Datenfehler. Kein Beleg,
  dass der gesamte Aktienmarkt unhandelbar wäre; es erfüllte keiner diesen Vertrag.
- BI Short: Abbruch bei 1.649 von 5.297 Aktien durch `invalid_bar_value`.
  Die nachfolgenden 3.648 Aktien wurden überhaupt nicht geprüft. Ein vollständiges
  Nullergebnis ließ sich daraus nicht ableiten.
- Der Long-Lauf 19:41–20:23 UTC ging über 16:00 Uhr New Yorker Zeit hinweg.
  Im alten Code wurde die Kerzenabschlussgrenze je Aktie neu berechnet.
- Alte Momentum-Cachewerte sind keine Diagnose des heutigen Fehlversuchs.
  Im Code konnte eine Exception der ersten Strategie den gesamten automatischen
  Aktien-Strategielauf beenden.
- Der bisherige Export enthielt keinen hinreichenden aktuellen Nachweis über
  Mail-Sperren oder Versandfehler. Fehlende Tradezeilen sind kein SMTP-Befund.

## Umgesetzter technischer Vertrag

1. Fehlerhafte BI-Einzelserien werden isoliert, andere Aktien weiter analysiert.
   Jeder Datenfehler hält den Lauf dennoch unvollständig: kein frischer Final-Cache,
   keine BI-Mail aus dem unvollständigen Lauf. Provider-/Netzwerkfehler bleiben
   sofortige Abbrüche. Fehlende Daten werden weder zu Preisen noch zu Nullsignalen.
2. Ein fester UTC-Laufstichtag bestimmt die abgeschlossenen Analysekerzen und
   Planstruktur. Tageszuordnung erfolgt in New York, einschließlich Sommerzeit.
   Aktualisierte Einzelpreise sind keine gleichzeitig aufgenommenen Quotes.
3. Aktien-BI verwendet dieselbe adaptive Range für Plan und Fibonacci-Faktor S18.
   Vertragsversion `stock-bi-20-v3`; Planmodell `bi_shared_structure_v3` bleibt
   unverändert. Die 17/20-Regel, 20 Faktoridentitäten, 6%-Rangegrenze, bestätigte
   30-Kerzen-Fibonacci-Swings und sämtliche Ausführungsgates bleiben erhalten.
4. Momentum, Gap Long, Gap Short und Cup-and-Handle werden im automatischen
   Aktienlauf unabhängig versucht. Nur aktuelle erfolgreiche Rows gelangen in
   die bestehende gemeinsame Mailprüfung. Rangfolge, 25 Kandidaten je Strategie,
   das globale Prüflimit, Dedupe und Klumpenwarnung bleiben erhalten. Bei Teilfehlern
   wird der vorherige vollständige Gesamtcache nicht ersetzt.
5. Aggregierte BI-Diagnose ergänzt gemeinsam rote Faktorpaare und Range-Längen.
   Keine Watchlist, keine unter-17-Row, kein unter-17-Tracking und kein Shadow-Trade.
6. Der allein lesende Serverexport erweitert die festen Scanner-Cachepfade sowie
   Mail-Outbox- und Sperrgrundaggregate. Datenbankzugriff erfolgt nach dauerhaftem
   Wechsel zum Dienstbenutzer in begrenzten `mode=ro`/`query_only`-Transaktionen.
   Unbekannte/fehlende Dateien oder Schemata bleiben unbekannt statt null.
7. Generische Aktienstrategien speichern zusätzlich einen separaten letzten
   Versuch: `running`/`complete`/`error`, Revision, Lauf-ID, UTC-Zeiten und feste
   Diagnosezähler, ohne Ergebniszeilen. Schreibfehler dürfen die Handelsprüfung
   nicht ändern. Ein prozesslokaler Schutz verhindert, dass ein älterer Worker
   einen neueren Versuch überschreibt. Der Export liest diese Metadaten für die
   vier Sweep-Strategien und den Gesamtlauf; weitere Scanner sind damit nicht
   automatisch mit vollständigen Versuchsdaten instrumentiert.

## Unveränderte Grenzen und offene Produktionsnachweise

Die Outbox enthält Wiederholungen/ungeklärte Zustellungen, nicht sämtliche Mails.
Ein Queue-Status `sent` beweist keine Zustellung im Posteingang. Sperrgründe können
sich überschneiden; Stundenaggregate sind keine Anzahl unterschiedlicher Signale.
Zustelljournal und Ausweichjournale werden nicht vollständig erfasst. API-Status-
Endpunkte mit möglichen Queue-Nebenwirkungen werden nicht als reine Lesediagnose
aufgerufen. Der Export enthält keine Empfänger, Mailtexte oder Providerantworten.
Ein im Versuch vermerkter Mailstatus `guarded` bedeutet nur, dass die bestehende
Prüfung aufgerufen wurde. Die Dedupe bleibt strategiebezogen: dieselbe Aktie in
zwei unterschiedlichen Strategien kann weiterhin zwei Mailidentitäten haben.

Diese Reparatur erlaubt keine Aussage „jetzt höhere Trefferquote“. 17 von 20
korrelierten Kriterien sind nicht 85% Gewinnwahrscheinlichkeit. Auch ein korrekter
Fibonacci-Faktor allein macht aus höchstens 15/20 noch keinen 17/20-Treffer.
Benötigt werden neue, zeitlich getrennte Produktionskohorten pro Scanner/Version,
inklusive Kosten, ungeklärter Fälle und ausführbarer Einstiege. Das bestehende
Profitabilitätsprotokoll bleibt dafür maßgeblich. Schwellen werden nicht für eine
gewünschte Trefferanzahl oder einen gewünschten Tagesgewinn gelockert.

Keine historischen Trackerzeilen umgeschrieben, keine Brokerorders ausgelöst,
keine Testmails versandt, kein Cron eingerichtet und kein Serverupdate ausgeführt.
Lokale Tests, Git-Push, Deployment und neue Marktergebnisse sind getrennte Nachweise.

## Verifikation

Die neue Regression umfasst 48 Sweep-/Versuchsfälle und 71 Range-/Fibonacci-Fälle.
Ein zusätzlicher Offline-Vergleich bestätigte identische vollständige Rückgaben
für 1.440 gültige synthetische Alt-/Neu-Planinputs **ohne** nachgelagerte
VRVP-Struktur-Anpassung (`apply_structure=False`). Das ist kein vollständiger
VRVP-End-to-End- oder Produktionsvergleich.

Unabhängige Gegenprüfung: Netzwerkfehler-Abgrenzung, Zukunftskerzen einschließlich
kurzer Historien, Datenquarantäne, Cache-/Mail-Gates, Krypto-Abgrenzung,
Datensparsamkeit und echte Produzent-/Collector-Kompatibilität. Gefundene
Schnittstellenfehler wurden vor der finalen Abnahme korrigiert.

Abschließende Gesamtsuite auf eingefrorenem Code: **4.544 bestanden, 4 übersprungen**
in 410,19 Sekunden. Alle Tests mit isolierten Daten-, Laufzeit-, Dedupe- und
temporären Pfaden, deaktiviertem automatischem Plugin-Laden und ohne Python-
Bytecode-/Pytest-Cache. Lokales JUnit-Protokoll:
`tmp/scanner_acceptance_283f8eddefbe41979f0aad16af78bd7b/results.xml`.
Die Skips betreffen Betriebssystem-/Dateisystemfähigkeiten, keinen bestätigten
Linux-Produktionslauf. Der vorherige Zwischenlauf während paralleler Bearbeitung
war keine Abnahme; seine alten Fehlercode-Erwartungen und Source-Pin-Mischstände
sind durch den vollständigen Schlusslauf ersetzt.

Zusätzliche Prüfungen: AST/Syntax der 11 geänderten Python-Quell-/neuen Testdateien,
ASCII-Vertrag des über Windows-PowerShell übergebenen Standalone-Collectors,
`git diff --check`, private Pfade nicht gestaged. Das unveränderte Frontend-Bundle
wurde zur Quelle geprüft (`df791ada6247`); keine neue visuelle Abnahme behauptet.
Referenz: [BI-Diagnosevertrag](BI_CONFLUENCE_DIAGNOSTICS.md).
