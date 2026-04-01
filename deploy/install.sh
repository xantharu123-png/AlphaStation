#!/bin/bash
# ============================================================
# TradingBot Server Setup — Automatisches Install-Script
# Getestet auf: Ubuntu 22.04 / Debian 12
# ============================================================
set -e

echo "╔══════════════════════════════════════════╗"
echo "║  TradingBot Server Setup                 ║"
echo "╚══════════════════════════════════════════╝"

# ── 1) System-Updates ──
echo "📦 System-Updates..."
sudo apt update && sudo apt upgrade -y

# ── 2) Python 3.11+ installieren ──
echo "🐍 Python installieren..."
sudo apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

# ── 3) User erstellen ──
echo "👤 User 'tradingbot' erstellen..."
if ! id "tradingbot" &>/dev/null; then
    sudo useradd -m -s /bin/bash tradingbot
fi

# ── 4) Projekt-Verzeichnis ──
echo "📁 Projekt aufsetzen..."
APP_DIR="/home/tradingbot/app"
sudo mkdir -p "$APP_DIR"
sudo chown tradingbot:tradingbot "$APP_DIR"

# ── 5) Code kopieren (manuell oder git clone) ──
echo ""
echo "⚠️  Jetzt das Projekt nach $APP_DIR kopieren!"
echo "    Option A: git clone <dein-repo> $APP_DIR"
echo "    Option B: scp -r ./TradingBot/* tradingbot@SERVER:$APP_DIR/"
echo ""
read -p "Ist der Code in $APP_DIR? (j/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Jj]$ ]]; then
    echo "❌ Bitte Code zuerst kopieren, dann Script nochmal starten."
    exit 1
fi

# ── 6) Virtual Environment + Dependencies ──
echo "📦 Python Dependencies installieren..."
sudo -u tradingbot bash -c "
    cd $APP_DIR
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
"

# ── 7) Streamlit Secrets anlegen ──
SECRETS_DIR="/home/tradingbot/.streamlit"
sudo -u tradingbot mkdir -p "$SECRETS_DIR"

if [ ! -f "$SECRETS_DIR/secrets.toml" ]; then
    echo ""
    echo "🔑 Streamlit Secrets konfigurieren..."
    read -p "POLYGON_KEY: " POLY_KEY
    read -p "ANTHROPIC_API_KEY (optional, Enter zum Überspringen): " ANTH_KEY

    sudo -u tradingbot bash -c "cat > $SECRETS_DIR/secrets.toml << TOMLEOF
POLYGON_KEY = \"$POLY_KEY\"
ANTHROPIC_API_KEY = \"${ANTH_KEY:-skip}\"
TOMLEOF"
    echo "✅ Secrets gespeichert in $SECRETS_DIR/secrets.toml"
else
    echo "✅ Secrets existieren bereits"
fi

# ── 8) Streamlit Config ──
sudo -u tradingbot bash -c "cat > $SECRETS_DIR/config.toml << 'TOMLEOF'
[server]
port = 8501
address = \"0.0.0.0\"
headless = true
maxUploadSize = 50

[browser]
gatherUsageStats = false

[theme]
primaryColor = \"#4CAF50\"
TOMLEOF"

echo "✅ Streamlit Config angelegt"

# ── 9) Systemd Services installieren ──
echo "⚙️ Systemd Services einrichten..."

# Streamlit App Service
sudo cp /home/tradingbot/app/deploy/tradingbot.service /etc/systemd/system/
sudo cp /home/tradingbot/app/deploy/tradingbot-bg.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl enable tradingbot-bg
sudo systemctl start tradingbot
sudo systemctl start tradingbot-bg

echo "✅ Services gestartet"

# ── 10) Nginx Reverse Proxy ──
echo "🌐 Nginx konfigurieren..."
sudo cp /home/tradingbot/app/deploy/nginx-tradingbot.conf /etc/nginx/sites-available/tradingbot
sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✅ SETUP FERTIG!                        ║"
echo "╠══════════════════════════════════════════╣"
echo "║  App: http://SERVER_IP:8501              ║"
echo "║  Logs: journalctl -u tradingbot -f       ║"
echo "║  BG:   journalctl -u tradingbot-bg -f    ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Optional: SSL mit Let's Encrypt einrichten:"
echo "  sudo certbot --nginx -d deine-domain.de"
