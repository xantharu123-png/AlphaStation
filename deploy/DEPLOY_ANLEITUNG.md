# TradingBot Server Deployment

## Was du brauchst

- Einen VPS mit Ubuntu 22.04 oder Debian 12
- SSH-Zugang (root oder sudo-User)
- Eine Domain mit DNS-A-Record auf den Server (für TLS/Let's Encrypt)
- API Keys + Produktions-Secrets (Vorlage: `.env.production.example` im Repo-Root)

## Empfohlene Server

| Anbieter | Plan | Preis | RAM | CPU | Gut für |
|----------|------|-------|-----|-----|---------|
| **Hetzner** | CX22 | €4.15/Mo | 4 GB | 2 vCPU | Beste Preis/Leistung |
| **Hetzner** | CX32 | €7.49/Mo | 8 GB | 4 vCPU | Wenn viele Scans parallel |
| Contabo | VPS S | €5.99/Mo | 8 GB | 4 vCPU | Viel RAM, etwas langsamer |
| DigitalOcean | Basic | $6/Mo | 1 GB | 1 vCPU | Einfach aber wenig RAM |

**Empfehlung: Hetzner CX22** — €4.15/Mo (~$50/Jahr), Frankfurt Datacenter, schnell.

## Schritt 1: Server mieten

1. Geh zu https://console.hetzner.cloud
2. Neues Projekt erstellen → "TradingBot"
3. Server erstellen:
   - Location: **Falkenstein** oder **Nürnberg** (am nächsten zu dir)
   - Image: **Ubuntu 22.04**
   - Type: **CX22** (Shared vCPU, 2 vCPU, 4 GB)
   - SSH Key hinzufügen (oder Passwort setzen)
4. Server erstellen → IP-Adresse notieren

## Schritt 2: Einloggen

```bash
ssh root@DEINE_SERVER_IP
```

## Schritt 3: Code hochladen

**Option A — Git (empfohlen):**
```bash
# Auf dem Server als root (oder den ganzen Block mit sudo /bin/bash ausfuehren):
sudo /bin/bash <<'ALPHA_SOURCE'
set -Eeuo pipefail
umask 077
origin_url='https://github.com/xantharu123-png/AlphaStation.git'
bootstrap_dir=$(mktemp -d /root/alpha-station-source.XXXXXX)
cleanup() {
  case "$bootstrap_dir" in
    /root/alpha-station-source.*) rm -rf -- "$bootstrap_dir" ;;
    *) echo "Unerwarteter Bootstrap-Pfad: $bootstrap_dir" >&2; exit 1 ;;
  esac
}
trap cleanup EXIT

apt install -y git
git clone --branch main --single-branch "$origin_url" "$bootstrap_dir/source"
test "$(git -C "$bootstrap_dir/source" remote get-url origin)" = "$origin_url"
test -z "$(git -C "$bootstrap_dir/source" status --porcelain=v1)"
install -d -m 0755 -o root -g root /home/tradingbot/app
cp -a "$bootstrap_dir/source/." /home/tradingbot/app/
ALPHA_SOURCE
```

**Option B — SCP (von deinem PC):**
```bash
# Von deinem PC aus in ein nur fuer root zugaengliches Ziel:
scp -r ./TradingBot/. root@DEINE_SERVER_IP:/root/alpha-station-upload/
# Dann auf dem Server als root:
ssh root@DEINE_SERVER_IP
install -d -m 0755 -o root -g root /home/tradingbot/app
cp -a /root/alpha-station-upload/. /home/tradingbot/app/
rm -rf -- /root/alpha-station-upload
```

Option B ist nur fuer einen bewusst geprueften lokalen Quellbaum gedacht. Fuer
eine bestehende oder moeglicherweise manipulierte Server-Installation gilt
stattdessen zwingend der Quarantaene-/Fresh-Clone-Ablauf in
`deploy/SERVER_WARTUNG.md`.

## Schritt 4: Install-Script starten

```bash
cd /home/tradingbot/app/deploy
sudo /bin/bash install.sh
```

Das Script:
- Installiert Python, nginx, certbot, Dependencies
- Erstellt User "tradingbot"
- Legt `/home/tradingbot/app/.env` aus `.env.production.example` an (Platzhalter ersetzen!)
- Richtet die systemd-Units `tradingbot-api` + `tradingbot-bg` ein (Auto-Start bei Reboot)
- Richtet bei einem Git-Checkout den idempotenten Root-Cron unter
  `/etc/cron.d/alpha-station-auto-update` ein, installiert den root-kontrollierten
  Launcher `/usr/local/sbin/alpha-station-auto-update` und prueft den Git-Zugriff
  ohne Deploy
- Haelt Home, Checkout, `.git` und `venv` root-kontrolliert; nur
  `/home/tradingbot/app/data_cache` bleibt fuer die Dienste schreibbar
- Stellt gemeinsames `/tmp`/Library-HOME persistent ueber das systemd-
  `StateDirectory=alpha-station-runtime` unter dem root-kontrollierten
  `/var/lib`-Parent bereit
- Migriert Alt-Installationen: deaktiviert die alte Streamlit-Unit `tradingbot`
- Konfiguriert nginx: TLS via certbot, statisches Frontend, `/api/`-Proxy auf Port 8000

## Schritt 5: Prüfen

```bash
# API läuft?
systemctl status tradingbot-api

# Background Service läuft?
systemctl status tradingbot-bg

# Logs ansehen
journalctl -u tradingbot-api -f    # FastAPI Backend (uvicorn, 127.0.0.1:8000)
journalctl -u tradingbot-bg -f     # Background Scanner

# Health-Check lokal (uvicorn lauscht nur auf localhost)
curl -s http://127.0.0.1:8000/api/health
# Der volle Check bindet diese API-Revision und frontend_bundle-ID exakt an
# den aktiven Checkout und das versionierte Frontend-Bundle:
bash /home/tradingbot/app/deploy/health_check.sh

# Im Browser öffnen (nginx liefert das statische Frontend aus)
# https://DEINE-DOMAIN
```

## Nützliche Befehle

```bash
# Neustart
sudo systemctl restart tradingbot-api
sudo systemctl restart tradingbot-bg

# Logs
journalctl -u tradingbot-api --since "1 hour ago"

# Code updaten (empfohlen — mit Compile-/Test-/Health-Gates):
cd /home/tradingbot/app && bash deploy/safe_deploy.sh
# Frontend-Änderungen sind damit sofort live (statisch, kein Service-Restart nötig).

# Auto-Update einmalig einrichten/reparieren (idempotent, kein Deploy beim Probe-Lauf):
cd /home/tradingbot/app
sudo /bin/bash deploy/install_auto_update.sh
bash deploy/health_check.sh --auto-update-only

# Secrets ändern
nano /home/tradingbot/app/.env
sudo systemctl restart tradingbot-api tradingbot-bg
```

Der verwaltete Cron-Vertrag lautet exakt:

```cron
*/10 * * * * root /bin/bash /usr/local/sbin/alpha-station-auto-update >> /var/log/alpha_autoupdate.log 2>&1
```

Cron fuehrt damit keinen service-schreibbaren Checkout-Pfad als root aus. Der
Installer akzeptiert nur eine root-eigene, nicht gruppen-/welt-schreibbare
Pfadkette und kopiert den versionierten Updater als root:root 0755 in das
root-kontrollierte Launcher-Verzeichnis. Der explizite `/bin/bash`-Aufruf ist
zusaetzlich unabhaengig vom Execute-Bit; im Git-Checkout bleibt das Skript als
ausfuehrbar versioniert. Vor jedem Git-Zugriff prueft der Launcher Home,
Quellbaum, `.git`, `venv`, erlaubte Symlink-Ziele und deren kritische
Parent-Pfade fail-closed. Git vertraut dem Checkout nur fuer den jeweiligen
Befehl; die globale Git-Konfiguration des Servers wird nicht veraendert.

Nach einem erfolgreich verifizierten Deploy wird der Launcher atomar auf die
neuen Bytes synchronisiert. Bei Rollback oder fehlgeschlagenem Deploy bleibt
der bisherige Launcher erhalten. Der Healthcheck meldet einen Byte-Unterschied
zwischen Launcher und versioniertem Updater rot.

Ein bereits zurueckgebliebener Altserver kann den neuen Installer nicht vor dem
ersten Pull aufrufen. Fuer diesen einmaligen Bootstrap den revisionsgebundenen
Recovery-Ablauf aus `SERVER_WARTUNG.md` verwenden; ein normaler weiterer Push
repariert einen nicht laufenden Cron nicht von selbst.

## SSL/TLS (HTTPS) — Pflicht für den kommerziellen Betrieb

`install.sh` erledigt das automatisch (fragt nach der Domain). Manuell — Zertifikat ZUERST holen, dann den vHost aktivieren (der 443-Block referenziert die Cert-Dateien):

```bash
sudo certbot certonly --nginx -d deine-domain.de
sudo sed 's/${DOMAIN}/deine-domain.de/g' /home/tradingbot/app/deploy/nginx-tradingbot.conf | sudo tee /etc/nginx/sites-available/tradingbot
sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Kostenlos via Let's Encrypt, erneuert sich automatisch (`certbot.timer`).

## Passwort-Schutz

Nicht mehr nötig: Die App hat einen eingebauten JWT-Login (`COMMERCE_ENFORCE_AUTH=1` in `.env`), nginx limitiert `/api/auth/login` zusätzlich per Rate-Limit. Nginx Basic Auth ist nur noch relevant, falls das Legacy-Streamlit-UI (Port 8501, auskommentierter Block in `nginx-tradingbot.conf`) intern reaktiviert wird.

## Was läuft auf dem Server

| Service | Was es macht | Auto-Start |
|---------|-------------|------------|
| `tradingbot-api` | FastAPI Backend via uvicorn (`api.py`, 127.0.0.1:8000) | Ja (bei Reboot) |
| `tradingbot-bg` | bg_service.py — Hintergrund-Scans nach Zeitplan | Ja (bei Reboot) |
| `nginx` | TLS :443, statisches Frontend (`frontend/`), `/api/`-Proxy → :8000 | Ja |
| `tradingbot` (alt) | Streamlit-UI `scanner.py` :8501 — wird bei der Migration deaktiviert | Nein |

## Vorteile vs. Streamlit Cloud

- bg_service.py läuft 24/7 — Scans auch wenn Browser zu
- Kein Spinner/Sleep wenn keiner zuschaut
- Volle Kontrolle über Memory, CPU, Timeouts
- Kein 1GB RAM Limit (Streamlit Cloud)
- Schneller (dedicated statt shared)
- Deine Daten bleiben bei dir
