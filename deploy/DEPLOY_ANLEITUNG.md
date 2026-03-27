# TradingBot Server Deployment

## Was du brauchst

- Einen VPS mit Ubuntu 22.04 oder Debian 12
- SSH-Zugang (root oder sudo-User)
- Deine Polygon API Keys (aus Streamlit Cloud Secrets)

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
- Installiert Python, Nginx, Dependencies
- Erstellt User "tradingbot"
- Fragt nach deinen API Keys
- Richtet Systemd Services ein (auto-start bei Reboot)
- Konfiguriert Nginx Reverse Proxy

## Schritt 5: Prüfen

```bash
# App läuft?
systemctl status tradingbot

# Background Service läuft?
systemctl status tradingbot-bg

# Logs ansehen
journalctl -u tradingbot -f        # Streamlit App
journalctl -u tradingbot-bg -f     # Background Scanner

# Im Browser öffnen
# http://DEINE_SERVER_IP
```

## Nützliche Befehle

```bash
# Neustart (nach Code-Update)
sudo systemctl restart tradingbot
sudo systemctl restart tradingbot-bg

# Logs
journalctl -u tradingbot --since "1 hour ago"

# Code updaten (wenn Git)
cd /home/tradingbot/app && git pull && sudo systemctl restart tradingbot

# Secrets ändern
nano /home/tradingbot/.streamlit/secrets.toml
sudo systemctl restart tradingbot
```

## Optional: SSL (HTTPS)

Wenn du eine Domain hast:

```bash
# In nginx-tradingbot.conf: server_name deine-domain.de;
sudo certbot --nginx -d deine-domain.de
```

Kostenlos via Let's Encrypt, erneuert sich automatisch.

## Optional: Passwort-Schutz

Streamlit hat keinen eingebauten Login. Einfachster Schutz:

```bash
# Nginx Basic Auth
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd miroslav
# Dann in nginx-tradingbot.conf unter location / hinzufügen:
#   auth_basic "TradingBot";
#   auth_basic_user_file /etc/nginx/.htpasswd;
sudo systemctl reload nginx
```

## Was läuft auf dem Server

| Service | Was es macht | Auto-Start |
|---------|-------------|------------|
| `tradingbot` | Streamlit Web-App (Port 8501) | Ja (bei Reboot) |
| `tradingbot-bg` | bg_service.py — Hintergrund-Scans nach Zeitplan | Ja (bei Reboot) |
| `nginx` | Reverse Proxy (Port 80 → 8501) | Ja |

## Vorteile vs. Streamlit Cloud

- bg_service.py läuft 24/7 — Scans auch wenn Browser zu
- Kein Spinner/Sleep wenn keiner zuschaut
- Volle Kontrolle über Memory, CPU, Timeouts
- Kein 1GB RAM Limit (Streamlit Cloud)
- Schneller (dedicated statt shared)
- Deine Daten bleiben bei dir
