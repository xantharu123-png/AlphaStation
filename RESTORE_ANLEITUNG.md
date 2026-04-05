# Cowork Backup — TradingBot Session 04.04.2026

## Was wurde gemacht

### 1. New Listing Scanner Fix + Binance
- **Bug**: Bei leerem Cache wurden ALLE ~1500 Perps als "neu" angezeigt (inkl. BTC, ETH etc.)
- **Fix**: First-Run-Seed — beim ersten Lauf wird Cache angelegt ohne alles als "neu" zu melden
- **Neu**: Binance Futures als 4. Exchange hinzugefuegt (581 Perps, onboardDate)
- **Ergebnis**: 4 Exchanges (Binance, MEXC, Bitget, Crypto.com) = 2113 PERP-Kontrakte
- **Dateien**: `modules/new_listing_scanner.py`, `api.py`

### 2. Anti-Copy Schutz (aus vorheriger Session)
- Rechtsklick blockiert, DevTools blockiert, Text-Selektion aus
- **Datei**: `frontend/index.html`

### 3. Mega Menu + Glassmorphism Design (aus vorheriger Session)
- 4 Kategorien: Scanner, Analyse, Tools, Krypto
- Dark Theme passend zur Landing Page
- **Datei**: `frontend/index.html`

## Restore-Optionen

### Option A: Git Patch anwenden (empfohlen)
```bash
cd /home/tradingbot/app/
git apply changes.patch
```

### Option B: Dateien direkt ersetzen
```bash
cp api.py /home/tradingbot/app/api.py
cp new_listing_scanner.py /home/tradingbot/app/modules/new_listing_scanner.py
cp index.html /home/tradingbot/app/frontend/index.html
```

### Nach dem Restore: Server deployen
```bash
cd /home/tradingbot/app/
rm -f data_cache/nls_cache_*.json
sudo systemctl restart tradingbot-api.service tradingbot-bg.service tradingbot-frontend.service
```

## Dateien in diesem Backup
- `changes.patch` — Git-Diff aller Aenderungen
- `api.py` — Komplette Backend-API
- `new_listing_scanner.py` — Kompletter New Listing Scanner (mit Binance)
- `index.html` — Komplettes Frontend (Mega Menu + Anti-Copy + Glassmorphism)
- `RESTORE_ANLEITUNG.md` — Diese Datei
