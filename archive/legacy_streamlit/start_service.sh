#!/bin/bash
# TradingBot Background Service Launcher
# Startet den Background-Service + Streamlit Dashboard

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 TradingBot Starting..."
echo "================================"

# 1. Background Service starten (im Hintergrund)
echo "🔄 Starte Background Data Service..."
python bg_service.py start &
BG_PID=$!
echo "   Background Service PID: $BG_PID"

# 2. Warte kurz bis erste Daten da sind
echo "⏳ Warte auf initiale Daten (ca. 30s)..."
sleep 30

# 3. Streamlit Dashboard starten
echo "🖥️  Starte Dashboard..."
streamlit run scanner.py --server.port 8501 --server.headless true

# Cleanup: Background Service beenden wenn Streamlit beendet wird
echo "⏹️  Beende Background Service..."
python bg_service.py stop
echo "👋 TradingBot beendet."
