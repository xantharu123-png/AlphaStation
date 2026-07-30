# Server-Wartung — Updates & Neustart ohne Schaden

Runbook für `root@178.104.69.209` (Ubuntu, `/home/tradingbot/app`).
Ziel: Ubuntu-Updates + „System restart required" erledigen, **ohne** Scan-Fenster,
Mails oder Sessions zu gefährden.

---

## 0. Gesundheitscheck (Ein-Befehl-Ampel — immer zuerst)

```bash
bash /home/tradingbot/app/deploy/health_check.sh
```

Prüft in ~5 Sekunden: Dienste (active + enabled), API-Antwort, Abstürze (2h),
Scheduler-Takt, Frische aller Scan-Caches (inkl. Wochenend-/Fenster-Logik),
JWT_SECRET, Auto-Update-Cron + Git-Stand, Festplatte/RAM.
Ausgabe: **grün = OK, gelb = prüfen (am Wochenende/nahtlos meist normal),
rot = handeln** (pro Zeile steht der Fix-Befehl gleich dabei).
Exit-Code 1 bei Fehlern — auch für Monitoring/Skripte nutzbar.

Vor UND nach jeder Wartung einmal laufen lassen.

---

## 1. Wann? (Timing ist der einzige heikle Punkt)

Der Bot hat feste Fenster (Sommerzeit MESZ):

| Fenster | Zeitraum MESZ | Was läuft |
|---|---|---|
| Pre-Market-Radar | 13:00–15:25 | PM-Scan + PM-Mails |
| Opening-Takt | 15:25–17:30 | 10-Min-Strategy-Takt |
| Reguläre Aktien-Scans | 16:00–22:05 | BI/Strategie stündlich |
| **Wochenreport (nur Fr!)** | **Fr 22:15 – Sa 05:00** | Freitags-Bilanz-Mail |
| Crypto-Scans | 24/7 | alle 15–30 Min (Cache überlebt Neustart) |

**Beste Wartungsfenster:**

1. **Samstag oder Sonntag** (jederzeit) — Markt zu, kein Aktien-Fenster aktiv.
2. Mo–Do **nach 22:30 MESZ** — nach dem letzten Aktien-Scan, vor dem nächsten Morgen.
3. **NIEMALS Fr 22:00 – Sa 06:00 MESZ** — der Wochenreport würde verloren gehen
   (sein Slot verfällt, er holt nicht nach).

Ein Reboot kostet 1–3 Minuten. Crypto-Scans überspringen höchstens einen Takt;
Caches bleiben erhalten, der Scheduler setzt nahtlos fort.

---

## 2. Vorbereitung (30 Sekunden)

```bash
cd /home/tradingbot/app
git log --oneline -1          # merken, was gerade läuft
uptime                        # Last ok?
```

## 3. Updates einspielen

```bash
apt update
apt list --upgradable          # kurz anschauen, was kommt
DEBIAN_FRONTEND=noninteractive apt upgrade -y
apt autoremove -y
```

Danach **immer** die Bot-Dienste einmal sauber neu starten (manche Updates
tauschen Libraries unter laufenden Prozessen aus):

```bash
systemctl restart tradingbot-api tradingbot-bg
```

## 4. Reboot nötig?

```bash
[ -f /var/run/reboot-required ] && cat /var/run/reboot-required || echo "Kein Reboot nötig"
```

Nur wenn „reboot-required" gemeldet wird (meist Kernel-Update):

```bash
systemctl reboot
# Achtung: SSH-Sitzung bricht sofort ab. Nach ~1 Minute neu verbinden:
# ssh root@178.104.69.209
```

## 5. Nach dem Neustart: 60-Sekunden-Verifikation

```bash
# a) Dienste laufen UND sind fuer Autostart aktiviert?
systemctl is-active tradingbot-api tradingbot-bg      # erwartet: active / active
systemctl is-enabled tradingbot-api tradingbot-bg     # erwartet: enabled / enabled

# b) Scheduler lebt, keine JWT-Warnung (seit 30.07. gesetzt)?
journalctl -u tradingbot-api --since '3 min ago' --no-pager | grep -E '\[Scheduler\]|JWT_SECRET' | tail -5

# c) bg-Dienst: Ownership + Waechter aktiv?
journalctl -u tradingbot-bg --since '3 min ago' --no-pager | grep -E 'Ownership|Waechter|Signal-Tracker' | tail -5

# d) Frische Caches = Scans laufen (nach ein paar Minuten):
ls -la --time-style=+%H:%M /tmp/*cache*.json 2>/dev/null | head -8
```

**Falls `is-enabled` NICHT `enabled` sagt** (Dienste würden nach Reboot nicht
automatisch starten):

```bash
systemctl enable tradingbot-api tradingbot-bg
systemctl start tradingbot-api tradingbot-bg
```

## 6. Wenn etwas klemmt

```bash
# Fehlerbilder ansehen:
journalctl -u tradingbot-api -n 50 --no-pager
journalctl -u tradingbot-bg -n 50 --no-pager

# Klassiker: nach git-pull vergessener Restart
cd /home/tradingbot/app && git log --oneline -1 && systemctl restart tradingbot-api tradingbot-bg
```

---

## TL;DR (alles hintereinander, Samstagmittag)

```bash
cd /home/tradingbot/app && git log --oneline -1
apt update && DEBIAN_FRONTEND=noninteractive apt upgrade -y && apt autoremove -y
[ -f /var/run/reboot-required ] && systemctl reboot || systemctl restart tradingbot-api tradingbot-bg
# (nach Reboot neu verbinden)
systemctl is-active tradingbot-api tradingbot-bg
journalctl -u tradingbot-bg --since '3 min ago' --no-pager | tail -10
```
