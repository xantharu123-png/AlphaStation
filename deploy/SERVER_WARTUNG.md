# Server-Wartung — Updates & Neustart ohne Schaden

Runbook für `root@178.104.69.209` (Ubuntu, `/home/tradingbot/app`).
Ziel: Ubuntu-Updates + „System restart required" erledigen, **ohne** Scan-Fenster,
Mails oder Sessions zu gefährden.

---

## 0. Gesundheitscheck (Ein-Befehl-Ampel — immer zuerst)

```bash
bash /home/tradingbot/app/deploy/health_check.sh
```

Prüft in ~5 Sekunden: Dienste (active + enabled), `/api/health` samt exakter
Checkout-Revision und versionierter Frontend-Bundle-ID, Abstürze (2h),
Scheduler-Takt, Frische aller Scan-Caches (inkl. Wochenend-/Fenster-Logik),
JWT_SECRET, Auto-Update-Cron + Git-Stand, Festplatte/RAM.
Ausgabe: **grün = OK, gelb = prüfen (am Wochenende/nahtlos meist normal),
rot = handeln** (pro Zeile steht der Fix-Befehl gleich dabei).
Exit-Code 1 bei Fehlern — auch für Monitoring/Skripte nutzbar.

Vor UND nach jeder Wartung einmal laufen lassen.

Nur den Auto-Update-Vertrag pruefen:

```bash
bash /home/tradingbot/app/deploy/health_check.sh --auto-update-only
```

Der Check verlangt den exakten `/etc/cron.d`-Aufruf ueber `root /bin/bash`,
prueft Ownership, Modus und Byte-Gleichheit des root-eigenen Launchers sowie
den letzten terminal erfolgreichen Logstatus (`current|probe|deploy`) mitsamt
Revision und Alter gegen den aktiven Checkout. Zusaetzlich wird der live
gefetchtete Stand gegen `origin/main` verglichen.
Reparatur/erstmalige Einrichtung:

```bash
cd /home/tradingbot/app
sudo /bin/bash deploy/install_auto_update.sh
```

Der Installer ist idempotent, kopiert den geprueften Updater als root:root 0755
nach `/usr/local/sbin/alpha-station-auto-update`, entfernt nur die alte
`auto_update.sh`-Zeile aus der Root-crontab und erhaelt alle fremden Cronjobs.
Sein Probe-Lauf fuehrt `git fetch` und den Revisionsvergleich aus, aber keinen
Deploy. Der fluechtige Lock liegt unter dem root-kontrollierten
`/run/alpha-station`; der Updater legt ihn nach jedem Reboot symlink-sicher neu
an. `/run/lock` wird nicht als Trust-Parent verwendet, da es auf Debian/Ubuntu
absichtlich fuer Lockdateien gemeinsam schreibbar sein kann.
Fuer die Migration stoppt der Installer Cron, veroeffentlicht Launcher und
`/etc/cron.d` atomar, entfernt danach den exakten Legacy-Job und startet Cron
erst zum Schluss. Jeder Fehler nach Transaktionsbeginn stellt Launcher,
Cronfile, Root-crontab sowie den vorherigen active/enabled-Zustand wieder her.

Die beiden Dienste teilen `/tmp` ueber das persistente systemd-
`StateDirectory=alpha-station-runtime` unter `/var/lib`. Der Verzeichnisname
kann vom Service-User nicht gegen einen Symlink ausgetauscht werden, weil sein
Parent root-kontrolliert ist; `data_cache` bleibt separat fuer dauerhafte
Anwendungsdaten schreibbar.

Wenn ein bestehender Server noch den alten direkten Cron-Aufruf oder einen
service-schreibbaren Checkout besitzt, wird **kein einziges Byte daraus als
root ausgefuehrt**. Ein nachtraegliches `chown` macht manipulierte `.git/config`,
Hooks, `venv`, `sitecustomize.py` oder Deploy-Skripte nicht vertrauenswuerdig.
Der Altbaum wird deshalb atomar quarantiniert und durch einen frischen Clone
vom bekannten oeffentlichen Origin ersetzt.

Der Ablauf stoppt Cron, aktuelle und alle Legacy-Units sowie jeden verbliebenen
Prozess des dedizierten Users. `data_cache` bleibt ausschliesslich im
Quarantaenebaum; es gibt keine automatische Root-Migration. Benoetigte JSON-
Nutzdaten duerfen erst spaeter dateiweise durch einen separaten Parser/Sandbox-
Prozess des Users `tradingbot` in den neuen, validierten Cache uebernommen
werden. Symlinks, Hardlinks, Special Files und Mounts werden dabei verworfen.
Die alte `.env` wird weder kopiert noch gesourct: Secrets nur aus einem
separaten vertrauenswuerdigen Secret-Backup und nur gemaess der Variablen-
Allowlist aus `.env.production.example` neu eintragen.

**Sicherheitsgrenze:** Der folgende In-place-Fresh-Bootstrap ist nur zulaessig,
wenn es **kein Root-Kompromiss-Indikator** gibt und belastbar feststeht, dass
kein manipulierbarer, service-schreibbarer Baum als root ausgefuehrt wurde.
Hat der alte Root-Cron/Updater diesen Checkout bereits ausgefuehrt oder ist das
unklar, den VPS neu provisionieren und Secrets ausserhalb des alten Systems
rotieren — dann ausdrücklich **nicht in-place** weiterarbeiten.

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv acl cron procps

cd /root
sudo /bin/bash <<'ALPHA_FRESH_BOOTSTRAP'
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
cd /root

assert_unit_safely_inactive() {
  local unit="$1" load_state active_state
  if ! load_state="$(systemctl show --property=LoadState --value "$unit" 2>/dev/null)"; then
    echo "systemctl-Abfrage fehlgeschlagen (LoadState): $unit" >&2
    return 1
  fi
  case "$load_state" in
    not-found) return 0 ;;
    loaded) ;;
    *)
      echo "Unit hat unerwarteten LoadState (${load_state:-missing}): $unit" >&2
      return 1
      ;;
  esac
  if ! active_state="$(systemctl show --property=ActiveState --value "$unit" 2>/dev/null)"; then
    echo "systemctl-Abfrage fehlgeschlagen (ActiveState): $unit" >&2
    return 1
  fi
  case "$active_state" in
    inactive|failed) return 0 ;;
    *)
      echo "Unit ist nicht sicher inaktiv ($load_state/$active_state): $unit" >&2
      return 1
      ;;
  esac
}

assert_bootstrap_units_inactive() {
  local unit
  assert_unit_safely_inactive cron.service || return 1
  for unit in tradingbot-api.service tradingbot-bg.service \
    tradingbot.service tradingbot-frontend.service; do
    assert_unit_safely_inactive "$unit" || return 1
  done
  assert_unit_safely_inactive "user@${service_uid}.service"
}

pgrep_activity_checked() {
  local rc=0
  if pgrep "$@" >/dev/null 2>&1; then
    return 0
  else
    rc=$?
  fi
  if [ "$rc" -eq 1 ]; then
    return 1
  fi
  echo "pgrep-Abfrage fehlgeschlagen (rc=$rc): $*" >&2
  return 2
}

service_uid="$(id -u tradingbot)"
systemctl stop cron.service || true
systemctl stop tradingbot-api.service tradingbot-bg.service \
  tradingbot.service tradingbot-frontend.service || true

usermod -L -s /usr/sbin/nologin tradingbot
loginctl disable-linger tradingbot >/dev/null 2>&1 || true
systemctl stop "user@${service_uid}.service" >/dev/null 2>&1 || true
assert_bootstrap_units_inactive
pkill -TERM -u "$service_uid" 2>/dev/null || true
for _ in 1 2 3 4 5; do
  if pgrep_activity_checked -u "$service_uid"; then
    sleep 1
    continue
  fi
  pgrep_rc=$?
  [ "$pgrep_rc" -eq 1 ] && break
  exit "$pgrep_rc"
done
pkill -KILL -u "$service_uid" 2>/dev/null || true

legacy_app=/home/tradingbot/app
legacy_root_pattern='(/usr/local/sbin/alpha-station-auto-update|/home/tradingbot/app/deploy/(auto_update|safe_deploy)\.sh|/tmp/alpha-safe-deploy)'
legacy_root_activity() {
  local proc proc_uid cmdline link target pgrep_rc
  # Ein trotz gestoppter Unit noch laufender cron/crond-Daemon kann unmittelbar
  # vor dem Swap einen alten Root-Job starten und gilt daher selbst als Aktivitaet.
  if pgrep_activity_checked -x cron; then
    return 0
  else
    pgrep_rc=$?
    [ "$pgrep_rc" -eq 1 ] || return "$pgrep_rc"
  fi
  if pgrep_activity_checked -x crond; then
    return 0
  else
    pgrep_rc=$?
    [ "$pgrep_rc" -eq 1 ] || return "$pgrep_rc"
  fi
  if pgrep_activity_checked -f -- "$legacy_root_pattern"; then
    return 0
  else
    pgrep_rc=$?
    [ "$pgrep_rc" -eq 1 ] || return "$pgrep_rc"
  fi

  # Auch ein verwaister git/pip/python-Child kann den Wrapper ueberleben. Alle
  # Root-Prozesse werden daher auf argv sowie CWD/EXE/offene FDs im Altbaum
  # oder Target-Temp geprueft; Root-Updater werden nicht per pkill "bereinigt".
  for proc in /proc/[0-9]*; do
    [ -r "$proc/status" ] || continue
    proc_uid="$(awk '$1 == "Uid:" { print $2; exit }' "$proc/status" 2>/dev/null || true)"
    [ "$proc_uid" = "0" ] || continue
    cmdline="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
    if [[ "$cmdline" =~ $legacy_root_pattern ]]; then
      return 0
    fi
    for link in "$proc/cwd" "$proc/exe" "$proc"/fd/*; do
      [ -L "$link" ] || continue
      target="$(readlink -- "$link" 2>/dev/null || true)"
      target="${target% (deleted)}"
      case "$target" in
        "$legacy_app"|"$legacy_app"/*|/tmp/alpha-safe-deploy*) return 0 ;;
      esac
    done
  done
  return 1
}

wait_for_legacy_root_activity_to_end() {
  local _ activity_rc
  for _ in {1..30}; do
    if legacy_root_activity; then
      sleep 1
      continue
    fi
    activity_rc=$?
    if [ "$activity_rc" -eq 1 ]; then
      return 0
    fi
    echo "Root-Prozessabfrage ist fehlgeschlagen; Bootstrap abgebrochen" >&2
    return "$activity_rc"
  done
  echo "Alter Root-Updater oder Descendant blieb nach 30s aktiv" >&2
  return 1
}

assert_bootstrap_quiescent() {
  local pgrep_rc activity_rc
  assert_bootstrap_units_inactive || return 1
  if pgrep_activity_checked -u "$service_uid"; then
    echo "tradingbot-Prozesse konnten nicht sicher beendet werden" >&2
    return 1
  else
    pgrep_rc=$?
    if [ "$pgrep_rc" -ne 1 ]; then
      echo "tradingbot-Prozessabfrage ist fehlgeschlagen; Bootstrap abgebrochen" >&2
      return 1
    fi
  fi
  if legacy_root_activity; then
    echo "Alter Root-Updater/Launcher/Target-Deploy laeuft noch; abwarten und Bootstrap neu starten" >&2
    return 1
  else
    activity_rc=$?
    if [ "$activity_rc" -ne 1 ]; then
      echo "Root-Prozessabfrage ist fehlgeschlagen; Bootstrap abgebrochen" >&2
      return 1
    fi
  fi
  return 0
}
wait_for_legacy_root_activity_to_end
assert_bootstrap_quiescent

if [ -L /home/tradingbot ] || [ ! -d /home/tradingbot ]; then
  echo "/home/tradingbot ist kein reales Verzeichnis" >&2
  exit 1
fi
chown --no-dereference root:root /home/tradingbot
chmod 0755 /home/tradingbot

quarantine_root="$(mktemp -d /root/alpha-station-quarantine.XXXXXX)"
chmod 0700 "$quarantine_root"

# Zweite, unmittelbar vor dem Swap liegende Schranke. Cron, bekannte Units,
# User-Linger und Login sind bereits aus; ein dennoch neu erschienener Prozess
# stoppt den Bootstrap, bevor der untrusted Altbaum angefasst wird.
assert_bootstrap_quiescent
if [ -e /home/tradingbot/app ] || [ -L /home/tradingbot/app ]; then
  app_kind="$(LC_ALL=C stat -c '%F' -- /home/tradingbot/app)"
  case "$app_kind" in
    "symbolic link")
      readlink -- /home/tradingbot/app > "$quarantine_root/rejected-app-symlink-target.txt"
      chmod 0600 "$quarantine_root/rejected-app-symlink-target.txt"
      mv -T -- /home/tradingbot/app "$quarantine_root/rejected-app-symlink"
      chown --no-dereference root:root "$quarantine_root/rejected-app-symlink"
      unlink -- "$quarantine_root/rejected-app-symlink"
      ;;
    "directory")
      if mountpoint -q -- /home/tradingbot/app; then
        echo "Alter APP_DIR ist ein Mountpoint; Bootstrap abgebrochen" >&2
        exit 1
      fi
      mv -T -- /home/tradingbot/app "$quarantine_root/app"
      chown --no-dereference root:root "$quarantine_root/app"
      chmod 0700 "$quarantine_root/app"
      ;;
    *)
      echo "Alter APP_DIR hat unzulässigen lstat-Typ '$app_kind'; Bootstrap abgebrochen" >&2
      exit 1
      ;;
  esac
fi
echo "Untrusted Altzustand/Typnachweis liegt root-privat unter: $quarantine_root"

fresh="$(mktemp -d /home/tradingbot/.alpha-station-fresh.XXXXXX)"
cleanup_fresh() {
  [ -z "${fresh:-}" ] || rm -rf -- "$fresh"
}
trap cleanup_fresh EXIT
git clone --branch main --single-branch \
  https://github.com/xantharu123-png/AlphaStation.git "$fresh"
test "$(git -C "$fresh" remote get-url origin)" = \
  "https://github.com/xantharu123-png/AlphaStation.git"
test -z "$(git -C "$fresh" status --porcelain=v1)"
test -d "$fresh/.git"
test ! -e "$fresh/venv"
chown -hR root:root "$fresh"
chmod -R go-w "$fresh"
mv -T -- "$fresh" /home/tradingbot/app
fresh=""
trap - EXIT
ALPHA_FRESH_BOOTSTRAP

# Erst ab hier wird Code ausgefuehrt: ausschliesslich aus dem frischen Clone.
# install.sh erzeugt ein neues root-eigenes venv mit System-`python3 -m venv`;
# alte venv/sitecustomize- oder .git/config-Bytes bleiben in Quarantaene.
cd /home/tradingbot/app
sudo /bin/bash deploy/install.sh

bash /home/tradingbot/app/deploy/health_check.sh --auto-update-only
```

Scheitert der Fresh-Clone-Bootstrap, bleiben Cron und Dienste absichtlich
gestoppt. Keinesfalls den Quarantaenebaum zurueckkopieren oder dessen Python,
Git-Konfiguration, Hooks bzw. Skripte als root starten. Erst wenn Installer und
letzter Healthcheck gruen sind, gilt der automatische Pull als aktiv.

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

# d) Cache-Frische = Scans laufen? Nicht per ls pruefen — die Dienste schreiben
#    in PrivateTmp-Namespaces, die root im direkten /tmp nicht sieht.
#    Stattdessen: bash /home/tradingbot/app/deploy/health_check.sh  (Abschnitt [4])
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
