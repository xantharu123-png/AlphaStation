# Reminder-Erhalt beim ersten Update

Die neue Version speichert in `ALPHA_DATA_DIR/trade_reminders.json`, ohne Override
in `/home/tradingbot/app/data_cache/trade_reminders.json`. Die vorherige Version
verwendet `/tmp/alphastation_trade_reminders.json` **im Namespace des API-Dienstes**.
Die Repository-Unit bindet `/var/lib/alpha-station-runtime` nach `/tmp`; eine alte
installierte Unit kann davon abweichen. Das Host-`/tmp` ist kein Ersatz.

## Vor dem ersten Neustart

Solange die alte API noch laeuft, diesen **nur lesenden** Check aus der lokalen
Windows-PowerShell ausfuehren. Er importiert keinen Code aus dem Servercheckout.

```powershell
Get-Content -Raw "C:\Projekt\TradingBot\scripts\check_reminder_deploy.py" | ssh -T -o StrictHostKeyChecking=yes root@178.104.69.209 "/usr/bin/python3 -I -"
```

Das Passwort bleibt im Terminal. Der Check gibt nur Pfade, Mengen, Dateirechte,
Hashwerte und eine Entscheidung aus, keine Nutzer, Ticker, Secrets oder Mailtexte.
Bis zum Abschluss des Updates keine neuen Reminder anlegen oder bestehende aendern.

- `hold_restart_check_failed`: **nicht neu starten**. Identitaet, Dateirechte,
  Schema oder der stabile Snapshot konnten nicht bestaetigt werden. Ab Schema 2
  nennen `error_code` und `stage` den festen, datensparsamen Fehlergrund.
- `hold_restart_for_migration_review`: **nicht blind kopieren oder neu starten**.
  Alte Reminder sind vorhanden; ein quieszierter Migrationsschritt muss geplant
  werden. Bestehende Zieldateien werden niemals pauschal ueberschrieben.
- `no_legacy_records_at_snapshot`: Zum Pruefzeitpunkt sind keine alten Datensaetze
  vorhanden. Das ist kein automatisches Deploy und keine Aussage ueber danach
  angelegte Reminder.

Eine vom alten Writer mit Modus `0644` angelegte, nur vom bestaetigten
Eigentuemer schreibbare Altdaten-Datei darf der Check lesen. Er meldet dabei
`legacy_record_not_private` als Datenschutzwarnung, statt eine leere Datei
pauschal zu blockieren. Fremdschreibrechte, falsche Besitzer, Symlinks oder
Hardlinks werden weiterhin abgewiesen. Fuer die neue persistente Zieldatei
bleibt der private Dateimodus erforderlich. Der Check korrigiert keine Rechte.

Alte JSON-Listen koennen verwaiste Reminder enthalten. Der Check zaehlt auch
solche Eintraege anonym: `invalid_records` und `invalid_reason_counts` nennen
etwa `missing_owner`, `invalid_owner`, `missing_id` oder `duplicate_id`.
`schema_valid=false` ist keine Freigabe. Diese Eintraege bleiben erhalten und
werden konservativ als ungeklaert gezaehlt; bei vorhandenen Altdaten bleibt
`hold_restart_for_migration_review` bestehen. Die neue Zieldatei wird weiterhin
streng validiert. Keine automatische Nutzerzuordnung oder Loeschung.

## Weshalb keine automatische Kopie bei laufender API?

Die alte API besitzt keinen Quiesce-/Export-Endpunkt fuer diese Datei. Eine
scheinbar korrekte Kopie kann unmittelbar danach veralten: der Worker kann einen
Trigger oder eine SMTP-Annahme speichern. Das Zurueckspielen der vorherigen Kopie
koennte dann doppelte Hinweise ausloesen. Ein Hashvergleich verhindert dieses
Zeitfenster nach dem Vergleich nicht. Ein `SIGSTOP` mitten in SMTP macht die
Zustellung ebenfalls nicht eindeutig.

Bei vorhandenen Altdaten braucht die Migration deshalb ein ausdruecklich
abgestimmtes Wartungsfenster: Schreibzugriffe kontrolliert stilllegen, eine
vollstaendige root-private Sicherung sichern, unklare SMTP-Zustaende konservativ
beibehalten, eine bereits vorhandene Zieldatei separat abgleichen und erst danach
den Zustand mit Besitzer `tradingbot:tradingbot`, Modus `0600`, ohne Symlinks oder
Hardlinks installieren. Die API wird anschliessend separat aktualisiert/gestartet.
**Dieser Check fuehrt keinen dieser schreibenden Schritte durch.** Nicht einfach
erst den Dienst stoppen: ein nicht persistent eingebundenes PrivateTmp kann dabei
bereits verschwinden. Bei aktiven oder unklar versandten Altdaten zuerst die
konkrete Ausgabe pruefen lassen.

Die automatische Migration im neuen Anwendungscode hilft nur, wenn die alte
Datei im neuen Namespace noch existiert und keine persistente Datei vorhanden
ist. Sie stellt keine bereits verlorene PrivateTmp-Datei wieder her.
