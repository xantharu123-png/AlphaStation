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
# Auf dem Server:
apt install -y git
cd /tmp
git clone https://github.com/DEIN_USERNAME/TradingBot.git
cp -r TradingBot/* /home/tradingbot/app/ 2>/dev/null || mkdir -p /home/tradingbot/app && cp -r TradingBot/* /home/tradingbot/app/
```

**Option B — SCP (von deinem PC):**
```bash
# Von deinem PC aus:
scp -r ./TradingBot/* root@DEINE_SERVER_IP:/tmp/tradingbot-upload/
# Dann auf dem Server:
ssh root@DEINE_SERVER_IP
mkdir -p /home/tradingbot/app
cp -r /tmp/tradingbot-upload/* /home/tradingbot/app/
```

## Schritt 4: Install-Script starten

```bash
cd /home/tradingbot/app/deploy
chmod +x install.sh
./install.sh
```

Das Script:
- Installiert Python, nginx, certbot, Dependencies
- Erstellt User "tradingbot"
- Legt `/home/tradingbot/app/.env` aus `.env.production.example` an (Platzhalter ersetzen!)
- Richtet die systemd-Units `tradingbot-api` + `tradingbot-bg` ein (Auto-Start bei Reboot)
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

# Secrets ändern
nano /home/tradingbot/app/.env
sudo systemctl restart tradingbot-api tradingbot-bg
```

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
