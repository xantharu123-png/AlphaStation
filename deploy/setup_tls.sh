#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_tls.sh — HTTPS fuer Alpha Station (nginx + Let's Encrypt)
#
# VORAUSSETZUNG: Eine Domain, deren A-Record auf diesen Server zeigt
# (z.B. app.deinedomain.com -> 178.104.69.209). Ohne Domain kein Let's-Encrypt-
# Zertifikat — auf nackte IPs stellt Let's Encrypt keine Zertifikate aus!
#
# Aufruf (als root):
#   bash deploy/setup_tls.sh app.deinedomain.com deine@email.com
# ─────────────────────────────────────────────────────────────────────────────
set -u

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR="/home/tradingbot/app"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Aufruf: bash deploy/setup_tls.sh <domain> <email>"
    echo "Beispiel: bash deploy/setup_tls.sh app.alphastation.io miroslav.mikulic@gmail.com"
    exit 1
fi

echo "[1/6] Vorab-Check: zeigt $DOMAIN auf diesen Server?"
SERVER_IP=$(curl -s --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
DOMAIN_IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)
echo "  Server-IP: ${SERVER_IP:-unbekannt} | $DOMAIN -> ${DOMAIN_IP:-NICHT AUFLOESBAR}"
if [ -z "$DOMAIN_IP" ]; then
    echo "❌ $DOMAIN ist nicht aufloesbar. A-Record beim Domain-Anbieter setzen, ein paar Minuten warten, nochmal ausfuehren."
    exit 1
fi
if [ -n "$SERVER_IP" ] && [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
    echo "⚠ $DOMAIN zeigt auf $DOMAIN_IP, Server ist $SERVER_IP — certbot wird scheitern, falls das nicht derselbe Server ist."
    read -r -p "Trotzdem fortfahren? (j/N) " ANSWER
    [ "$ANSWER" = "j" ] || exit 1
fi

echo "[2/6] nginx + certbot installieren..."
apt-get update -qq && apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null

echo "[3/6] nginx-Konfiguration einsetzen (Domain: $DOMAIN)..."
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/nginx-tradingbot.conf" > /etc/nginx/sites-available/tradingbot
# Falls die Repo-Config keinen __DOMAIN__-Platzhalter hat: server_name ersetzen
if ! grep -q "$DOMAIN" /etc/nginx/sites-available/tradingbot; then
    sed -i "s/server_name .*/server_name $DOMAIN;/" /etc/nginx/sites-available/tradingbot
fi
ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot
rm -f /etc/nginx/sites-enabled/default
nginx -t || { echo "❌ nginx-Config fehlerhaft — Ausgabe oben an Claude schicken"; exit 1; }
systemctl reload nginx

echo "[4/6] Let's-Encrypt-Zertifikat holen (certbot)..."
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect || {
    echo "❌ certbot fehlgeschlagen — Ausgabe oben an Claude schicken"; exit 1; }

echo "[5/6] API von aussen absperren (nur noch nginx darf auf :8000)..."
if command -v ufw >/dev/null 2>&1; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw deny 8000/tcp >/dev/null || true
    echo "  ufw: 80/443 offen, 8000 zu"
else
    echo "  ⚠ ufw nicht installiert — Port 8000 manuell in der Firewall des Hosters schliessen!"
fi

echo "[6/6] Verifikation:"
sleep 2
CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/health" || echo "000")
echo "  https://$DOMAIN/api/health -> HTTP $CODE"
if [ "$CODE" = "200" ]; then
    echo ""
    echo "✅ ALLES OK — App laeuft unter https://$DOMAIN (Zertifikat erneuert sich automatisch)."
    echo "   WICHTIG: Frontend ab jetzt ueber https://$DOMAIN aufrufen, nicht mehr ueber die IP:8000."
else
    echo "❌ NOCH NICHT OK — komplette Ausgabe an Claude schicken."
fi
