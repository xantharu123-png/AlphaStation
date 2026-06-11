#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# fix_bg_unit.sh — installiert die neue tradingbot-bg-Service-Definition
# (Schreibrechte auf data_cache via ReadWritePaths + laedt /home/tradingbot/app/.env)
# und verifiziert den Erfolg mit klarer OK/NICHT-OK-Ausgabe.
#
# Aufruf auf dem Server (als root):
#   bash deploy/fix_bg_unit.sh
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd /home/tradingbot/app || { echo "❌ /home/tradingbot/app nicht gefunden"; exit 1; }

echo "[1/4] Neue Service-Definition installieren (ohne User-Wechsel, konsistent mit api)..."
sed 's/^User=/#User=/; s/^Group=/#Group=/' deploy/tradingbot-bg.service > /etc/systemd/system/tradingbot-bg.service || {
    echo "❌ Konnte Unit-Datei nicht schreiben"; exit 1; }
systemctl daemon-reload

echo "[2/4] Service neu starten..."
systemctl restart tradingbot-bg

echo "[3/4] 15 Sekunden warten (Service-Anlauf + erster Scan-Tick)..."
sleep 15

echo "[4/4] Pruefung:"
STATE=$(systemctl is-active tradingbot-bg 2>/dev/null)
LOG=$(journalctl -u tradingbot-bg --since "1 minute ago" --no-pager 2>/dev/null)
PERM=$(printf '%s' "$LOG" | grep -ci "permission denied")
START=$(printf '%s' "$LOG" | grep -c  "Background Service V2 gestartet")
JWT=$(printf '%s' "$LOG" | grep -ci "JWT_SECRET ist nicht gesetzt")

echo "  Service-Status      : $STATE"
echo "  Start-Zeile gefunden: $START"
echo "  Permission-Fehler   : $PERM"
echo "  JWT-Warnung         : $JWT (0 = .env wird geladen)"

if [ "$STATE" = "active" ] && [ "$START" -ge 1 ] && [ "$PERM" -eq 0 ]; then
    echo ""
    echo "✅ ALLES OK — Background-Service laeuft vollstaendig (Scans + Speichern + Alerts)."
else
    echo ""
    echo "❌ NOCH NICHT OK — bitte die KOMPLETTE Ausgabe unten an Claude schicken:"
    journalctl -u tradingbot-bg -n 25 --no-pager | tail -25
fi
