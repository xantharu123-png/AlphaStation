#!/bin/bash
# ============================================================
# Alpha Station / TradingBot — Server Setup (Produktion)
# Getestet auf: Ubuntu 22.04 / Debian 12
#
# Produkt-Architektur (siehe Audit LB-3):
#   tradingbot-api.service  -> uvicorn api:app   (127.0.0.1:8000)
#   tradingbot-bg.service   -> python3 bg_service.py
#   nginx                   -> TLS :443, statisches Frontend, /api/-Proxy
#   (KEIN tradingbot-frontend-Service — Frontend ist statisch.)
#
# Migration von Alt-Installationen:
#   tradingbot-frontend (:3000) und tradingbot (Streamlit :8501) werden erst
#   nach erfolgreichem TLS/nginx-Test deaktiviert. tradingbot-bg wird mit der
#   neuen, gehaerteten Unit-Datei ueberschrieben.
# ============================================================
set -e

echo "╔══════════════════════════════════════════╗"
echo "║  Alpha Station Server Setup (FastAPI)    ║"
echo "╚══════════════════════════════════════════╝"

# ── 1) System-Updates ──
echo "📦 System-Updates..."
sudo apt update && sudo apt upgrade -y

# ── 2) Pakete installieren (inkl. nginx + certbot) ──
echo "🐍 Python, nginx, certbot installieren..."
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

# Gemeinsamer, service-isolierter Cache. Die systemd-Units binden dieses
# Verzeichnis als ihr /tmp ein, damit API und Background-Scanner dieselben
# Ergebnisse sehen, ohne das globale Server-/tmp freizugeben.
sudo install -d -m 0700 -o tradingbot -g tradingbot "$APP_DIR/data_cache/runtime"

# ── 6) Virtual Environment + Dependencies ──
# Hinweis: Unit-Dateien und safe_deploy.sh erwarten das venv unter $APP_DIR/venv.
echo "📦 Python Dependencies installieren..."
sudo -u tradingbot bash -c "
    cd $APP_DIR
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
"

echo "Pruefe versioniertes Frontend-Bundle..."
sudo -u tradingbot "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/verify_frontend_bundle.py"

# ── 7) Produktions-.env anlegen (Pflicht — Units starten ohne .env NICHT) ──
# Die App bootet im Commercial-Mode fail-closed: mit Default-Secrets
# (z.B. Repo-Fallback fuer JWT_SECRET) verweigert sie den Start.
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ] || [ ! -s "$ENV_FILE" ]; then
    echo "🔑 Erzeuge $ENV_FILE aus .env.production.example ..."
    sudo -u tradingbot cp "$APP_DIR/.env.production.example" "$ENV_FILE"
    sudo chmod 600 "$ENV_FILE"
    echo ""
    echo "⚠️  PFLICHT: $ENV_FILE jetzt editieren und ALLE Platzhalter ersetzen"
    echo "    (JWT_SECRET, COMMERCE_ENFORCE_AUTH=1, ALLOW_LEGACY_ADMIN_MASTER_KEY=0,"
    echo "     CORS_ORIGINS, Stripe-Live-Keys, Provider-Keys, GMAIL_*)."
    read -p "Weiter mit Enter, wenn .env fertig editiert ist..." -r
else
    echo "✅ $ENV_FILE existiert bereits (wird nicht ueberschrieben)"
fi

# ── 8) Systemd Services installieren ──
echo "⚙️ Systemd Services einrichten..."
sudo cp "$APP_DIR/deploy/tradingbot-api.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/tradingbot-bg.service"  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable tradingbot-api tradingbot-bg
sudo systemctl restart tradingbot-bg
sudo systemctl restart tradingbot-api

disable_legacy_frontends() {
    for legacy_unit in tradingbot-frontend.service tradingbot.service; do
        if systemctl list-unit-files "$legacy_unit" --no-legend 2>/dev/null | grep -q "^$legacy_unit"; then
            echo "Migration: deaktiviere Legacy-Unit $legacy_unit ..."
            sudo systemctl disable --now "$legacy_unit" || true
            sudo rm -f "/etc/systemd/system/$legacy_unit"
        fi
    done
    sudo systemctl daemon-reload
}

echo "✅ Services gestartet (tradingbot-api, tradingbot-bg)"

# ── 10) Nginx + TLS (Let's Encrypt) ──
echo "🌐 Nginx + TLS konfigurieren..."
echo "   (Die Domain muss bereits per DNS-A-Record auf diesen Server zeigen.)"
read -p "Domain fuer die App (z.B. app.deine-domain.de, leer = Schritt ueberspringen): " DOMAIN

if [ -n "$DOMAIN" ]; then
    # nginx (www-data) muss das Frontend unter /home/tradingbot/app/frontend lesen koennen.
    sudo chmod 755 /home/tradingbot
    sudo chmod -R a+rX "$APP_DIR/frontend"

    # Webroot fuer ACME-Renewals (Port-80-Block der nginx-Config).
    sudo mkdir -p /var/www/certbot

    # Zertifikat ZUERST holen — der 443-vHost referenziert die Cert-Dateien,
    # ohne sie schlaegt `nginx -t` fehl. certbot nutzt dafuer den gerade
    # installierten nginx (Default-Site reicht aus).
    if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
        echo "🔐 Hole Let's-Encrypt-Zertifikat fuer $DOMAIN ..."
        sudo certbot certonly --nginx -d "$DOMAIN"
    else
        echo "✅ Zertifikat fuer $DOMAIN existiert bereits"
    fi

    # vHost installieren: ${DOMAIN}-Platzhalter ersetzen, dann aktivieren.
    sudo sed "s/\${DOMAIN}/$DOMAIN/g" "$APP_DIR/deploy/nginx-tradingbot.conf" \
        | sudo tee /etc/nginx/sites-available/tradingbot > /dev/null
    sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t && sudo systemctl reload nginx

    # Verify the replacement first while the old frontend is still available.
    sudo env APP_DIR="$APP_DIR" ENV_FILE="$ENV_FILE" ALLOW_LEGACY_FRONTENDS=1 \
        bash "$APP_DIR/deploy/verify_commercial_edge.sh"

    # Retire direct legacy frontends only after their TLS replacement is live.
    disable_legacy_frontends
    sudo env APP_DIR="$APP_DIR" ENV_FILE="$ENV_FILE" \
        bash "$APP_DIR/deploy/verify_commercial_edge.sh"

    echo "✅ Nginx aktiv: https://$DOMAIN  (Renewal laeuft automatisch via certbot.timer)"
else
    echo "⏭️  Nginx/TLS uebersprungen. Spaeter manuell:"
    echo "    sudo certbot certonly --nginx -d DEINE_DOMAIN"
    echo "    sudo sed 's/\${DOMAIN}/DEINE_DOMAIN/g' $APP_DIR/deploy/nginx-tradingbot.conf | sudo tee /etc/nginx/sites-available/tradingbot"
    echo "    sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx"
    echo "    Commercial launch remains BLOCKED; legacy services stay untouched until the TLS replacement is verified."
fi

# ── 11) Abschluss ──
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ SETUP FERTIG!                                    ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  App:        https://\$DOMAIN  (nginx, TLS)           ║"
echo "║  API lokal:  http://127.0.0.1:8000/api/health        ║"
echo "║  Logs API:   journalctl -u tradingbot-api -f          ║"
echo "║  Logs BG:    journalctl -u tradingbot-bg -f           ║"
echo "║  Deploy:     bash deploy/safe_deploy.sh               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Naechste Schritte (siehe COMMERCIAL_LAUNCH_CHECKLIST.md):"
echo "  curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool"
