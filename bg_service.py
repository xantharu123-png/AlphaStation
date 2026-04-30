#!/usr/bin/env python3
"""
🔄 TradingBot Background Scanner Service V2
=============================================
Läuft als Systemd Service und führt alle Scanner automatisch aus.
Ergebnisse werden als JSON-Dateien gespeichert, Streamlit liest sie.

Usage:
    python bg_service.py start      # Startet den Service
    python bg_service.py stop       # Stoppt den Service
    python bg_service.py status     # Zeigt Status
    python bg_service.py once       # Einmal alle Scanner laufen lassen

Zeitplan:
    - Crash Monitor:    alle 30 Min
    - BTC Divergenz:    alle 30 Min
    - BI Scanner Long:  alle 2h (versetzt)
    - Bear Scanner:     alle 2h (versetzt um 1h zu BI)
    - Biotech Scanner:  alle 1h
    - New Listing Dump: alle 15 Min (Crypto 24/7)
"""

import os
import sys
import json
import time
import signal
import logging
import threading
import traceback
import smtplib
import re
import atexit
import fcntl
import tempfile
import glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ── Pfade ──
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data_cache"
DATA_DIR.mkdir(exist_ok=True)
PID_FILE = DATA_DIR / "bg_service.pid"
LOG_FILE = DATA_DIR / "bg_service.log"
STATUS_FILE = DATA_DIR / "bg_status.json"

# Modules importieren
sys.path.insert(0, str(BASE_DIR))

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("bg_service")

# ── B-01: Atomic write helper for JSON (prevents corruption) ──
def _atomic_write_json(filepath, data):
    """Atomic JSON write - prevents corruption from concurrent reads."""
    tmp_dir = os.path.dirname(filepath) or "."
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=tmp_dir, delete=False, suffix='.tmp') as f:
            json.dump(data, f)
            tmp_path = f.name
        os.replace(tmp_path, filepath)
    except Exception as e:
        log.warning(f"Atomic write failed for {filepath}: {e}")
        try:
            os.unlink(tmp_path)
        except:
            pass

# ── B-06: PID cleanup on exit ──
atexit.register(lambda: PID_FILE.unlink(missing_ok=True))

# ── B-09: Cache cleanup helper ──
def _cleanup_old_cache():
    """Remove progress files older than 24h."""
    for pattern in ["/tmp/*_progress_*.json", "/tmp/*_scan_*.json", "/tmp/*_cache_*.json"]:
        for f in glob.glob(pattern):
            try:
                if time.time() - os.path.getmtime(f) > 86400:
                    os.unlink(f)
                    log.info(f"Cleaned up old cache: {f}")
            except:
                pass

# ── Cache bei Scan-Start löschen (frische Ergebnisse) ──
_SCAN_CACHE_MAP = {
    "bi_long": "/tmp/bi_cache_long.json",
    "bi_short": "/tmp/bi_cache_short.json",
    "bear_scan": "/tmp/bear_scanner_cache.json",
    "biotech": "/tmp/biotech_scan_results.json",
    "strategies": "/tmp/strategy_scan_results.json",
    "orb": "/tmp/orb_scan_results.json",
}

def _clear_scan_cache(scanner_name):
    """Löscht den alten Cache wenn ein neuer Scan startet."""
    cache_file = _SCAN_CACHE_MAP.get(scanner_name)
    if cache_file and os.path.exists(cache_file):
        try:
            os.unlink(cache_file)
            log.debug(f"Cache gelöscht bei Scan-Start: {cache_file}")
        except Exception:
            pass

# ── API Keys aus secrets.toml laden ──
_EMAIL_CONFIG_KEYS = (
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "ALERT_EMAIL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SSL_PORT",
)


def _parse_kv_file(path: Path) -> dict:
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception as exc:
        log.warning(f"Config konnte nicht gelesen werden ({path}): {exc}")
    return values


def _load_secrets():
    """Load config from secrets files, .env and process env; partial files do not shadow Gmail config."""
    secrets = {}
    paths = [
        Path.home() / ".streamlit" / "secrets.toml",
        BASE_DIR / ".streamlit" / "secrets.toml",
        BASE_DIR / ".env",
    ]
    for secrets_path in paths:
        if secrets_path.exists():
            secrets.update(_parse_kv_file(secrets_path))
    for key in (
        "POLYGON_KEY",
        "BPIQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "FINNHUB_KEY",
        *_EMAIL_CONFIG_KEYS,
    ):
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets


# ── E-Mail Alert System ──
_EMAIL_COOLDOWN = {}  # Verhindert Spam: {ticker: last_sent_ts}
_EMAIL_COOLDOWN_SEC = 3600 * 4  # 4 Stunden Cooldown pro Ticker
_EMAIL_BLOCKED_ETF_TICKERS = {
    "SOXS", "SQQQ", "SPXU", "SPXS", "UVXY", "VIXY", "QID", "SRTY", "TZA", "SDOW", "LABD",
    "SDS", "SH", "PSQ", "DOG", "RWM", "SOXL", "TQQQ", "UPRO", "SPXL", "UDOW", "FNGU",
    "KOLD", "BOIL", "DRIP", "GUSH", "JDST", "JNUG", "NUGT", "DUST", "YANG", "YINN",
    "SVXY", "VXX", "TVIX", "BITI", "BITO", "LABU",
}


def _email_has_blocked_etf_content(subject, body_html):
    """Hard guard: email alerts should contain stock/crypto setups, not ETF/ETP watchlists."""
    content = f"{subject or ''} {body_html or ''}".upper()
    if any(marker in content for marker in (
        "INVERSE ETF",
        "INVERSE ETFS",
        "LEVERAGED ETF",
        "LEVERAGED ETFS",
        "3X SHORT",
        "2X SHORT",
    )):
        return True
    tokens = set(re.findall(r"\b[A-Z]{2,6}\b", content))
    return bool(tokens & _EMAIL_BLOCKED_ETF_TICKERS)


def _cleanup_email_cooldown():
    """Entfernt abgelaufene Cooldown-Einträge (verhindert Memory Leak über Tage/Wochen)"""
    now = time.time()
    expired = [k for k, ts in _EMAIL_COOLDOWN.items() if now - ts > _EMAIL_COOLDOWN_SEC]
    for k in expired:
        del _EMAIL_COOLDOWN[k]
    if expired:
        log.debug(f"  Cooldown cleanup: {len(expired)} abgelaufene Einträge entfernt, {len(_EMAIL_COOLDOWN)} aktiv")


def _send_email_alert(subject, body_html, secrets):
    """Sendet E-Mail Alert via Gmail SMTP. Benötigt GMAIL_USER + GMAIL_APP_PASSWORD in secrets.toml"""
    if _email_has_blocked_etf_content(subject, body_html):
        log.warning(f"E-Mail Alert blockiert (ETF/ETP-Inhalt): {subject}")
        return False
    gmail_user = secrets.get("GMAIL_USER", "")
    gmail_pass = secrets.get("GMAIL_APP_PASSWORD", "")
    alert_to = secrets.get("ALERT_EMAIL", gmail_user)  # Default: an sich selbst
    recipients = [addr.strip() for addr in str(alert_to).split(",") if addr.strip()]

    if not gmail_user or not gmail_pass:
        log.warning("⚠️ E-Mail Alert: GMAIL_USER oder GMAIL_APP_PASSWORD fehlt in secrets.toml")
        return False
    if not recipients:
        log.warning("E-Mail Alert: ALERT_EMAIL/GMAIL_USER Empfaenger fehlt")
        return False

    # B-03: Retry logic with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"TradingBot Alert <{gmail_user}>"
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject

            # Plain-Text Fallback
            plain = body_html.replace("<br>", "\n").replace("</tr>", "\n")
            import re
            plain = re.sub(r"<[^>]+>", "", plain)
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, recipients, msg.as_string())

            log.info(f"📧 E-Mail Alert gesendet: {subject}")
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                log.warning(f"⚠️ E-Mail Fehler (Versuch {attempt+1}/{max_retries}): {e}, warte {wait_time}s...")
                time.sleep(wait_time)
            else:
                log.error(f"❌ E-Mail Fehler nach {max_retries} Versuchen: {e}")
                return False


def _check_and_alert_scan_results(scanner_name, secrets):
    """Prüft Scan-Ergebnisse auf Grade S/A und sendet E-Mail Alert"""
    now = time.time()

    # BI Scanner Cache lesen
    if scanner_name.startswith("bi_"):
        direction = "long" if "long" in scanner_name else "short"
        cache_file = f"/tmp/bi_cache_{direction}.json"
    elif scanner_name == "biotech":
        cache_file = "/tmp/biotech_scan_results.json"
    elif scanner_name == "orb":
        cache_file = "/tmp/orb_scan_results.json"
    else:
        return

    try:
        if not os.path.exists(cache_file):
            return
        with open(cache_file, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                log.debug(f"Corrupt cache file, skipping: {cache_file}")
                return

        results = data.get("results", [])
        if not results:
            return

        # Filter: Nur Grade S oder A
        alerts = []
        for r in results:
            ticker = r.get("ticker", r.get("Ticker", ""))
            grade = r.get("BI_Grade", r.get("Grade", r.get("rating", "")))
            score = r.get("BI_Score", r.get("Score", r.get("score", 0)))

            # Grade-Check: S, A, A+ für alle Scanner
            is_top_grade = grade in ("S", "A", "A+")
            if not is_top_grade:
                continue

            # Cooldown: Nicht denselben Ticker nochmal innerhalb 4h
            cooldown_key = f"{scanner_name}_{ticker}"
            if cooldown_key in _EMAIL_COOLDOWN:
                if now - _EMAIL_COOLDOWN[cooldown_key] < _EMAIL_COOLDOWN_SEC:
                    continue

            _EMAIL_COOLDOWN[cooldown_key] = now
            alerts.append({
                "ticker": ticker,
                "grade": grade,
                "score": score,
                "price": r.get("Preis", r.get("current", 0)),
                "direction": r.get("direction", direction if scanner_name.startswith("bi_") else ""),
                "name": r.get("Name", r.get("name", "")),
                "rvol": r.get("RVOL", r.get("rvol", 0)),
            })

        if not alerts:
            return

        # E-Mail bauen
        scanner_labels = {
            "bi_long": "BI Scanner Long",
            "bi_short": "BI Scanner Short",
            "biotech": "Biotech Scanner",
            "orb": "ORB Scanner",
        }
        label = scanner_labels.get(scanner_name, scanner_name)
        n = len(alerts)
        subject = f"🚨 {n} Top-Setup{'s' if n > 1 else ''} — {label}"

        rows = ""
        for a in alerts:
            emoji = "🏆" if a["grade"] == "S" else "🔥"
            dir_label = "⬆️ LONG" if "long" in str(a.get("direction", "")).lower() else "⬇️ SHORT"
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #eee"><b>{a['ticker']}</b></td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a.get('name', '')[:25]}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{emoji} {a['grade']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a['score']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">${a['price']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{dir_label}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a['rvol']:.1f}x</td>
            </tr>"""

        body_html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
        <h2 style="color:#1a73e8">🚨 TradingBot Alert — {label}</h2>
        <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} starke Setups gefunden</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
            <tr style="background:#f5f5f5">
                <th style="padding:8px;text-align:left">Ticker</th>
                <th style="padding:8px;text-align:left">Name</th>
                <th style="padding:8px;text-align:left">Grade</th>
                <th style="padding:8px;text-align:left">Score</th>
                <th style="padding:8px;text-align:left">Preis</th>
                <th style="padding:8px;text-align:left">Richtung</th>
                <th style="padding:8px;text-align:left">RVOL</th>
            </tr>
            {rows}
        </table>
        <p style="color:#999;font-size:12px;margin-top:20px">
            Automatischer Alert vom TradingBot Background Service.<br>
            Grade S = ELITE (Score ≥113 + 4 Smart Money) | Grade A = STARK (Score ≥99 + 3 SM)
        </p>
        </body></html>"""

        _send_email_alert(subject, body_html, secrets)

    except Exception as e:
        log.error(f"⚠️ Alert-Check {scanner_name}: {e}")


# ── Cache / Status ──
def cache_write(name, data):
    cache_file = DATA_DIR / f"{name}.json"
    meta = {"updated_at": datetime.now().isoformat(), "updated_ts": time.time(), "scanner": name, "data": data}
    try:
        _atomic_write_json(str(cache_file), meta)
        log.info(f"✅ {name} → Cache geschrieben")
    except Exception as e:
        log.error(f"❌ Cache-Write {name}: {e}")


def cache_age(name):
    cache_file = DATA_DIR / f"{name}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r") as f:
            meta = json.load(f)
        return time.time() - meta.get("updated_ts", 0)
    except Exception:
        return None


def _update_status(scanner_name, status, detail=""):
    # B-02: File locking to prevent race condition
    try:
        # Ensure file exists
        if not STATUS_FILE.exists():
            STATUS_FILE.write_text("{}")

        with open(STATUS_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                existing = json.load(f)
            except (json.JSONDecodeError, ValueError):
                existing = {}

            existing[scanner_name] = {"status": status, "detail": detail, "ts": datetime.now().isoformat()}
            existing["_service"] = {
                "running": True, "pid": os.getpid(),
                "started": existing.get("_service", {}).get("started", datetime.now().isoformat()),
                "last_activity": datetime.now().isoformat(),
            }
            f.seek(0)
            f.truncate()
            json.dump(existing, f, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        log.debug(f"Non-critical error in _update_status: {e}")


# ══════════════════════════════════════════════════════════════
# SCANNER FUNKTIONEN
# ══════════════════════════════════════════════════════════════

def _fetch_crash_monitor(poly_key):
    """Crash Monitor: SPY + VIX + Sektoren + Safe Havens + Credit + Breadth"""
    log.info("🔴 Crash Monitor...")
    _update_status("crash_monitor", "fetching")
    from modules.data_fetchers import rate_limited_get

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=400)
        result = {"spy": {}, "vix": {}, "sectors": [], "breadth": {}, "signals": [], "fear_score": 0}
        fear = 0

        # SPY
        url = f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=15)

        if resp.status_code == 200:
            bars = resp.json().get("results", [])
            if bars and len(bars) >= 50:
                closes = [b["c"] for b in bars]
                volumes = [b["v"] for b in bars]
                highs = [b["h"] for b in bars]
                lows = [b["l"] for b in bars]
                current = closes[-1]
                prev_close = closes[-2]

                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-50:]) / 50
                sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

                # RSI
                rsi_data = closes[-100:]
                gains, losses_l = [], []
                for i in range(1, 15):
                    d = rsi_data[i] - rsi_data[i-1]
                    gains.append(max(0, d)); losses_l.append(max(0, -d))
                ag = sum(gains)/14; al = sum(losses_l)/14
                for i in range(15, len(rsi_data)):
                    d = rsi_data[i] - rsi_data[i-1]
                    ag = (ag*13 + max(0, d))/14; al = (al*13 + max(0, -d))/14
                rsi = 100 - (100 / (1 + ag/max(0.001, al)))

                high_252 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
                drawdown = ((current - high_252) / high_252) * 100

                chg_5d = ((closes[-1]-closes[-6])/closes[-6])*100 if len(closes)>=6 else 0
                chg_20d = ((closes[-1]-closes[-21])/closes[-21])*100 if len(closes)>=21 else 0

                vol_avg20 = sum(volumes[-20:]) / 20
                down_vol = sum(volumes[i] for i in range(-20, 0) if closes[i] < closes[i-1])
                total_vol = sum(volumes[-20:])
                sell_pressure = down_vol / max(1, total_vol)

                # Fear Score
                if drawdown <= -20: fear += 25
                elif drawdown <= -10: fear += 18
                elif drawdown <= -5: fear += 12
                elif drawdown <= -3: fear += 7
                if current < sma50: fear += 8
                if sma200 and current < sma200: fear += 12
                if rsi <= 30: fear += 10
                elif rsi <= 40: fear += 6
                if sell_pressure > 0.65: fear += 8
                if chg_5d <= -5: fear += 6
                elif chg_5d <= -2: fear += 3

                result["spy"] = {
                    "price": round(current, 2), "change_pct": round((current-prev_close)/prev_close*100, 2),
                    "sma50": round(sma50, 2), "sma200": round(sma200, 2) if sma200 else None,
                    "rsi": round(rsi, 1), "drawdown": round(drawdown, 1),
                    "chg_5d": round(chg_5d, 2), "chg_20d": round(chg_20d, 2),
                    "sell_pressure": round(sell_pressure*100, 1),
                }

        # VIX
        for vix_etf in ["UVXY", "VIXY"]:
            try:
                vr = rate_limited_get(
                    f"https://api.polygon.io/v2/aggs/ticker/{vix_etf}/range/1/day/{(end_date-timedelta(days=60)).strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}",
                    params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)
                if vr.status_code == 200:
                    vb = vr.json().get("results", [])
                    if vb and len(vb) >= 10:
                        vc = vb[-1]["c"]; va20 = sum(b["c"] for b in vb[-20:])/20
                        spike = vc/max(0.01, va20)
                        result["vix"] = {"ticker": vix_etf, "price": round(vc, 2), "spike_ratio": round(spike, 2)}
                        if spike > 1.5: fear += 12
                        elif spike > 1.2: fear += 7
                        break
            except Exception as e:
                log.debug(f"Non-critical error: {e}")

        # Breadth
        try:
            snap = rate_limited_get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                                     params={"apiKey": poly_key}, timeout=30)
            if snap.status_code == 200:
                tickers = snap.json().get("tickers", [])
                adv = dec = 0
                for t in tickers:
                    td = t.get("todaysChangePerc", 0) or 0
                    if td > 0: adv += 1
                    elif td < 0: dec += 1
                ad_ratio = adv / max(1, dec)
                result["breadth"] = {"advancing": adv, "declining": dec, "ad_ratio": round(ad_ratio, 2)}
                if ad_ratio < 0.4: fear += 12
                elif ad_ratio < 0.6: fear += 8
                elif ad_ratio < 0.8: fear += 4
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        result["fear_score"] = min(100, fear)
        cache_write("crash_monitor", result)
        _update_status("crash_monitor", "ok", f"Fear: {fear}/100")
        log.info(f"  Fear Score: {fear}/100")
        return result
    except Exception as e:
        log.error(f"❌ Crash Monitor: {e}")
        _update_status("crash_monitor", "error", str(e))
        return None


def _run_bi_scanner(poly_key, direction="long"):
    """BI Scanner via Polygon Snapshot → _bi_background_scan"""
    _clear_scan_cache(f"bi_{direction}")
    label = "BI Long" if direction == "long" else "Bear Short"
    log.info(f"🔮 {label} Scanner...")
    _update_status(f"bi_{direction}", "fetching")

    import requests as req

    try:
        # 1) Polygon Snapshot
        snap = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                        params={"apiKey": poly_key}, timeout=30)
        if snap.status_code != 200:
            log.error(f"  Polygon HTTP {snap.status_code}")
            _update_status(f"bi_{direction}", "error", f"HTTP {snap.status_code}")
            return

        tickers = snap.json().get("tickers", [])
        log.info(f"  {len(tickers)} Aktien geladen")

        # 2) Basis-Daten extrahieren
        raw = []
        for t in tickers:
            try:
                lt = t.get("lastTrade", {}) or {}
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = lt.get("p") or day.get("c") or day.get("vw") or prev.get("c") or 0
                if not price or price <= 0: continue
                change_pct = t.get("todaysChangePerc") or 0
                vol = day.get("v") or 0
                prev_vol = prev.get("v") or 0
                # Wenn Markt zu (vol=0), nutze prevDay Volume
                effective_vol = vol if vol > 0 else prev_vol
                rvol = vol / prev_vol if prev_vol > 0 and vol > 0 else (1.0 if prev_vol > 0 else 0)
                dollar_vol = price * effective_vol
                raw.append({
                    "Ticker": t.get("ticker", ""), "Name": t.get("name", "") or "",
                    "Preis": round(price, 2), "Change%": round(change_pct, 2),
                    "RVOL": round(rvol, 2), "Volume": vol, "DollarVol": dollar_vol,
                })
            except Exception as e:
                log.debug(f"Non-critical error: {e}")
                continue

        # 3) CS-Whitelist
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                with open(cs_file, "r") as f:
                    cs_set = set(json.load(f))
                log.info(f"  CS-Liste aus Cache: {len(cs_set)} Ticker")
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        if not cs_set:
            try:
                from modules.data_fetchers import rate_limited_get
                url = "https://api.polygon.io/v3/reference/tickers"
                params = {"type": "CS", "market": "stocks", "active": "true", "limit": 1000, "apiKey": poly_key}
                next_url = None
                for _ in range(20):
                    if next_url:
                        resp = rate_limited_get(next_url, timeout=30)
                    else:
                        resp = rate_limited_get(url, params=params, timeout=30)
                    data = resp.json()
                    for r in data.get("results", []):
                        cs_set.add(r.get("ticker", "").upper())
                    next_url = data.get("next_url")
                    if next_url:
                        next_url = f"{next_url}&apiKey={poly_key}"
                    else:
                        break
                if cs_set:
                    _atomic_write_json(cs_file, list(cs_set))
                    log.info(f"  CS-Liste von API: {len(cs_set)} Ticker")
            except Exception as e:
                log.warning(f"  CS-Liste Fehler: {e}")

        # 4) Filter
        if direction == "long":
            # Long: Alle liquiden CS-Aktien
            filtered = [s for s in raw if s.get("Preis", 0) >= 5
                        and s.get("DollarVol", 0) >= 200_000
                        and (not cs_set or s.get("Ticker", "").upper() in cs_set)]
        else:
            # Short: Stärkerer Downtrend ODER hohes Volume bei Schwäche
            # Verschärft: Change <= -2% ODER (RVOL >= 1.8 UND Change <= -1%)
            filtered = [s for s in raw if s.get("Preis", 0) >= 5
                        and s.get("DollarVol", 0) >= 500_000
                        and (not cs_set or s.get("Ticker", "").upper() in cs_set)
                        and (s.get("Change%", 0) <= -2.0
                             or (s.get("RVOL", 0) >= 1.8 and s.get("Change%", 0) <= -1.0))]

        log.info(f"  {len(filtered)} Kandidaten nach Filter")

        if not filtered:
            _update_status(f"bi_{direction}", "no_candidates", f"0 Kandidaten von {len(raw)}")
            return

        # 5) Progress-Datei schreiben damit Streamlit-UI den Fortschritt sieht
        progress_file = f"/tmp/bi_scan_progress_{direction}.json"
        _atomic_write_json(progress_file, {"status": "running", "checked": 0, "total": len(filtered),
                       "hits": 0, "detail": f"{len(filtered)} Kandidaten", "timestamp": time.time()})

        # 6) Analyse starten — nutze _bi_background_scan aus scanner.py
        # Das ist der gleiche Code den der Streamlit-Thread nutzt
        try:
            # Importiere die Scan-Funktion
            from modules.scanners import _bi_background_scan_standalone
            _bi_background_scan_standalone(poly_key, direction, filtered, progress_file)
        except ImportError:
            # Fallback: Rufe die Funktion direkt auf
            # _bi_background_scan ist in scanner.py definiert, nicht importierbar
            # Wir triggern den Scan über die Progress-Datei — Streamlit picked es auf
            log.warning(f"  _bi_background_scan_standalone nicht verfügbar — nutze direkte Analyse")
            _run_bi_analysis_direct(poly_key, direction, filtered, progress_file)

        _update_status(f"bi_{direction}", "ok", f"Scan abgeschlossen")

    except Exception as e:
        log.error(f"❌ {label}: {e}\n{traceback.format_exc()}")
        _update_status(f"bi_{direction}", "error", str(e))


def _run_bi_analysis_direct(poly_key, direction, candidates, progress_file):
    """Direkte BI-Analyse ohne scanner.py Import"""
    from modules.data_fetchers import rate_limited_get, fetch_grouped_daily
    from modules.patterns import analyze_breakout_imminent
    from modules.analysis import _detect_chart_patterns, calculate_sr_from_historical, calculate_short_bonus_signals

    results = []
    checked = 0
    total = len(candidates)
    top_score = 0
    threshold = 75 if direction == "short" else 85

    for cand in candidates:
        ticker = cand["Ticker"]
        checked += 1

        # Progress update alle 10 Ticker
        if checked % 10 == 0:
            try:
                _atomic_write_json(progress_file, {"status": "running", "checked": checked, "total": total,
                               "hits": len(results), "top_score": top_score,
                               "detail": f"Analysiere {ticker}...", "timestamp": time.time()})
            except Exception as e:
                log.debug(f"Non-critical error: {e}")

        try:
            # Daily Bars laden — Short braucht 300 Tage für SMA200
            end = datetime.now()
            fetch_days = 320 if direction == "short" else 120
            start = end - timedelta(days=fetch_days)
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
            resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)

            if resp.status_code != 200:
                continue

            bars_raw = resp.json().get("results", [])
            if not bars_raw or len(bars_raw) < 20:
                continue

            bars = [{"date": b.get("t", 0), "open": b["o"], "high": b["h"],
                     "low": b["l"], "close": b["c"], "volume": b["v"]} for b in bars_raw]

            # BI Analyse
            bi_result = analyze_breakout_imminent(bars, direction=direction)
            if len(bi_result) >= 6:
                is_valid, bi_score, bi_max, details, confidence, grade = bi_result[:6]
                sm_fires = bi_result[6] if len(bi_result) > 6 else 0
                sm_hits = bi_result[7] if len(bi_result) > 7 else 0
            else:
                continue

            if not is_valid or bi_score < threshold:
                if bi_score > top_score:
                    top_score = bi_score
                continue

            if bi_score > top_score:
                top_score = bi_score

            # 🐻 Short Trend-Validierung: Aktie MUSS in Downtrend sein
            # Verhindert dass Long-Setups auch als Short-Setups erscheinen
            if direction == "short" and len(bars) >= 20:
                closes_recent = [b["close"] for b in bars]
                sma20 = sum(closes_recent[-20:]) / 20
                sma10 = sum(closes_recent[-10:]) / 10
                current = closes_recent[-1]
                # Preis muss unter SMA20 ODER SMA10 fallend sein
                if current > sma20 * 1.02 and sma10 > sma20:
                    # Aktie ist im Uptrend → kein Short-Setup
                    continue

            # S/R Levels — bars_raw in Tuple-Format konvertieren
            # calculate_sr_from_historical erwartet (date, open, high, low, close, volume)
            # Gibt zurück: ((supports_list, resistances_list), fib_info_dict)
            current_price = cand["Preis"]
            bars_tuples = [(b.get("t", 0), b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars_raw]
            try:
                sr_result = calculate_sr_from_historical(bars_tuples, current_price)
                (supports_list, resistances_list), fib_info = sr_result
                # Nächste Resistance über Preis, nächster Support unter Preis
                resistance = resistances_list[0] if resistances_list else current_price * 1.05
                support = supports_list[0] if supports_list else current_price * 0.95
            except Exception:
                resistance = current_price * 1.05
                support = current_price * 0.95

            # Entry/Stop/Target berechnen
            if direction == "long":
                entry = round(resistance, 2)
                stop = round(support, 2)
                risk = entry - stop
                tp1 = round(entry + risk * 1.5, 2) if risk > 0 else round(entry * 1.03, 2)
                tp2 = round(entry + risk * 2.5, 2) if risk > 0 else round(entry * 1.05, 2)
            else:
                # SHORT: Entry bei Resistance (Pullback-Short), Stop knapp darüber
                entry = round(resistance * 0.995, 2)
                stop = round(resistance * 1.015, 2)
                risk = stop - entry
                tp1 = round(entry - risk * 2.0, 2) if risk > 0 else round(entry * 0.95, 2)
                tp2 = round(entry - risk * 3.5, 2) if risk > 0 else round(entry * 0.90, 2)

            rr = round(abs(entry - tp1) / max(0.01, abs(entry - stop)), 1) if abs(entry - stop) > 0 else 0

            # V2.8: Pattern Warnings — nur informativ, KEINE Score-Penalties
            # (konsistent mit modules/scanners.py)
            pattern_warnings = _detect_chart_patterns(bars, direction=direction)
            pattern_label = "Clean"
            if pattern_warnings:
                high_w = [w for w in pattern_warnings if w.get("severity") == "high"]
                if high_w:
                    pattern_label = " | ".join(w.get("pattern", "?") for w in high_w)
                else:
                    pattern_label = " | ".join(w.get("pattern", "?") for w in pattern_warnings)

            # 🐻 Short Bonus Signals (nur für Bear Scanner)
            short_bonus_score = 0
            short_bonus_details = []
            if direction == "short":
                try:
                    bonus_result = calculate_short_bonus_signals(
                        ticker, bars, poly_key=poly_key, mode="swing"
                    )
                    short_bonus_score = bonus_result.get("bonus_score", 0)
                    short_bonus_details = bonus_result.get("details", [])
                    bi_score += short_bonus_score
                    bi_score = max(0, bi_score)
                except Exception as e:
                    log.warning(f"  Short Bonus Fehler {ticker}: {e}")

            # V2.8: Grade — proportional skaliert (max_score 188), mit SM-Bestätigung
            # Konsistent mit patterns.py + modules/scanners.py
            _cand_rvol = cand.get("RVOL", 0)

            if bi_score >= 113 and sm_fires >= 4:
                grade = "S"; grade_label = "S — ELITE"
            elif bi_score >= 99 and sm_fires >= 3:
                grade = "A"; grade_label = "A — STARK"
            elif bi_score >= 85 and sm_hits >= 2:
                grade = "B"; grade_label = "B — SOLIDE"
            elif bi_score >= 75:
                grade = "C"; grade_label = "C — WATCH"
            else:
                grade = "D"; grade_label = "D — SCHWACH"

            # RVOL Guard: Ohne Volumen kein Top-Grade
            if _cand_rvol < 0.7 and grade in ("S", "A"):
                grade = "B"; grade_label = "B — SOLIDE (RVOL zu niedrig)"
            elif _cand_rvol < 0.5 and grade == "B":
                grade = "C"; grade_label = "C — WATCH (RVOL zu niedrig)"

            results.append({
                "Ticker": ticker, "Name": cand["Name"],
                "Preis": cand["Preis"], "Change%": cand["Change%"],
                "BI_Score": bi_score, "BI_MaxScore": bi_max,
                "BI_Grade": grade, "BI_GradeLabel": grade_label,
                "BI_Confidence": confidence,
                "BI_Details": details,
                "Entry": entry, "StopLoss": stop, "TP1": tp1, "TP2": tp2,
                "RiskReward": rr,
                "RangeHigh": round(resistance, 2), "RangeLow": round(support, 2),
                "RVOL": cand["RVOL"], "DollarVol": cand["DollarVol"],
                "PatternLabel": pattern_label,
                "PatternWarnings": pattern_warnings,
                "ShortBonusScore": short_bonus_score,
                "ShortBonusDetails": short_bonus_details,
            })

        except Exception as e:
            _exc_score = locals().get("bi_score", 0) or 0
            if _exc_score >= threshold:
                log.warning(f"  ⚠️ {ticker} Score {_exc_score} aber Exception: {e}")
            continue

    # Sortiere + Speichere
    results.sort(key=lambda x: x.get("BI_Score", 0), reverse=True)
    results = results[:50]

    # Cache schreiben (gleicher Pfad den Streamlit liest)
    cache_file = f"/tmp/bi_cache_{direction}.json"
    try:
        _atomic_write_json(cache_file, {"results": results, "timestamp": time.time(), "ts": time.time(),
                       "direction": direction, "count": len(results)})
    except Exception as e:
        log.debug(f"Non-critical error: {e}")

    # Progress: Done
    _detail = f"✅ {len(results)} Treffer"
    if len(results) == 0:
        _detail = f"0 Treffer (Top Score: {top_score}, Threshold: {threshold}) — {total} analysiert"
    try:
        _atomic_write_json(progress_file, {"status": "done", "checked": checked, "total": total,
                       "hits": len(results), "top_score": top_score,
                       "detail": _detail, "timestamp": time.time()})
    except Exception as e:
        log.debug(f"Non-critical error: {e}")

    log.info(f"  {len(results)} Treffer (von {total} analysiert, Top: {top_score}, Threshold: {threshold})")


def _run_bear_scanner(poly_key, secrets):
    """
    V2.8: Bear Scanner im Background Service — findet Crash-Kandidaten und sendet Alerts.
    Nutzt Polygon /losers Endpoint + History für Score/Grade.
    """
    _clear_scan_cache("bear_scan")
    import requests as req
    log.info("Bear Scanner (bg_service)...")
    _update_status("bear_scan", "running")

    try:
        # 1) Polygon Losers Endpoint
        snap_resp = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/losers",
                           params={"apiKey": poly_key, "limit": 250}, timeout=30)
        if snap_resp.status_code != 200:
            log.error(f"  Losers HTTP {snap_resp.status_code}")
            _update_status("bear_scan", "error", f"HTTP {snap_resp.status_code}")
            return

        tickers = snap_resp.json().get("tickers", [])
        log.info(f"  {len(tickers)} Losers geladen")

        # ETF Blacklist
        _etf_tickers = {"SOXS","SQQQ","SPXU","SPXS","UVXY","VIXY","QID","SRTY","TZA","SDOW","LABD",
                       "SDS","SH","PSQ","DOG","RWM","SOXL","TQQQ","UPRO","SPXL","UDOW","FNGU",
                       "AMPL","KOLD","BOIL","DRIP","GUSH","JDST","JNUG","NUGT","DUST","YANG","YINN",
                       "SVXY","VXX","TVIX","BITI","BITO"}

        losers = []
        for t in tickers:
            try:
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = day.get("c", 0) or (t.get("lastTrade", {}) or {}).get("p", 0)
                prev_close = prev.get("c", 0)
                if not price or not prev_close or price < 3:
                    continue
                vol = day.get("v", 0)
                dollar_vol = price * vol
                if dollar_vol < 300_000:
                    continue
                chg_pct = ((price - prev_close) / prev_close) * 100
                if chg_pct > -3:
                    continue

                ticker_sym = t.get("ticker", "")
                _tk_up = ticker_sym.upper()
                if len(_tk_up) > 5 or "." in _tk_up:
                    continue
                if _tk_up in _etf_tickers:
                    continue
                if len(_tk_up) >= 4 and _tk_up[-1] in ("X","Q") and _tk_up[-2] in ("X","Q","S"):
                    continue

                # History für RVOL + MA
                rvol = 0
                ma20_dist = 0
                has_history = False
                try:
                    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker_sym}/range/1/day/2024-01-01/2099-12-31"
                    resp = rate_limited_get(url, params={"apiKey": poly_key, "limit": 60, "sort": "desc"}, timeout=10)
                    if resp.status_code == 200:
                        bars = resp.json().get("results", [])
                        if len(bars) >= 21:
                            has_history = True
                            ma20 = sum(b.get("c", 0) for b in bars[1:21]) / 20
                            ma20_dist = round((price - ma20) / ma20 * 100, 2) if ma20 > 0 else 0
                            avg_vol = sum(b.get("v", 0) for b in bars[1:21]) / min(20, len(bars) - 1)
                            rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0
                except Exception:
                    pass

                if not has_history:
                    continue

                # Scoring (0-100)
                score = 0
                abs_chg = abs(chg_pct)
                if abs_chg >= 15: score += 25
                elif abs_chg >= 10: score += 20
                elif abs_chg >= 6: score += 15
                elif abs_chg >= 4: score += 10
                else: score += 5

                if rvol >= 3.0: score += 20
                elif rvol >= 2.0: score += 15
                elif rvol >= 1.5: score += 10
                elif rvol >= 1.0: score += 5

                if ma20_dist < -10: score += 20
                elif ma20_dist < -5: score += 15
                elif ma20_dist < -2: score += 10
                elif ma20_dist < 0: score += 5
                else: score -= 5

                if dollar_vol >= 10_000_000: score += 10
                elif dollar_vol >= 5_000_000: score += 7
                elif dollar_vol >= 1_000_000: score += 4
                else: score += 1

                if 10 <= price <= 200: score += 10
                elif 5 <= price < 10: score += 5
                elif price > 200: score += 7

                if score >= 80: grade = "S"
                elif score >= 65: grade = "A"
                elif score >= 50: grade = "B"
                elif score >= 35: grade = "C"
                else: grade = "D"

                losers.append({
                    "ticker": ticker_sym, "price": round(price, 2),
                    "change_pct": round(chg_pct, 2), "volume": vol,
                    "dollar_volume": round(dollar_vol, 0), "rvol": rvol,
                    "ma20_dist": ma20_dist, "score": score, "grade": grade,
                })
            except Exception:
                continue

        losers.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_losers = losers[:30]

        # Cache speichern (gleiche Struktur wie api.py bear scanner)
        cache_data = {
            "inverse_etfs": [],  # ETFs werden nur in api.py geladen
            "short_candidates": [],
            "breakdown_stocks": top_losers,
        }
        _atomic_write_json("/tmp/bear_scanner_cache.json",
                          {"results": [cache_data], "timestamp": time.time(),
                           "cached_at": datetime.now().isoformat()})

        log.info(f"  {len(top_losers)} Crash-Kandidaten (Top: {top_losers[0]['ticker'] if top_losers else '–'} {top_losers[0]['score'] if top_losers else 0})")
        _update_status("bear_scan", "done", f"{len(top_losers)} Kandidaten")

        # Crash Alert: Grade S/A + Drop >= -10%
        now = time.time()
        crash_stocks = [l for l in top_losers if l["grade"] in ("S", "A") and l["change_pct"] <= -10 and l["score"] >= 60]
        if crash_stocks:
            _crash_ck = f"crash_bg_{datetime.now().strftime('%Y%m%d_%H')}"  # Stündlicher Cooldown
            if _crash_ck not in _EMAIL_COOLDOWN:
                _EMAIL_COOLDOWN[_crash_ck] = now
                _crash_rows = ""
                for cs in crash_stocks[:8]:
                    _gc = {"S": "#7c3aed", "A": "#16a34a"}.get(cs["grade"], "#666")
                    _crash_rows += (
                        f"<tr><td style='padding:6px 8px;font-weight:bold;color:{_gc}'>{cs['grade']}</td>"
                        f"<td style='padding:6px 8px;font-weight:bold'>{cs['ticker']}</td>"
                        f"<td style='padding:6px 8px;text-align:right'>${cs['price']:.2f}</td>"
                        f"<td style='padding:6px 8px;text-align:right;color:#dc2626;font-weight:bold'>{cs['change_pct']:.1f}%</td>"
                        f"<td style='padding:6px 8px;text-align:right'>{cs['rvol']:.1f}x</td>"
                        f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{cs['score']}</td></tr>"
                    )
                _body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
                <h2 style="color:#dc2626">Short Alert — {len(crash_stocks)} Crash-Kandidaten</h2>
                <p style="color:#666;font-size:13px">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET</p>
                <table style="border-collapse:collapse;width:100%;font-size:13px">
                <tr style="background:#fef2f2"><th style="padding:6px 8px;text-align:left">Grd</th>
                <th style="padding:6px 8px;text-align:left">Ticker</th>
                <th style="padding:6px 8px;text-align:right">Preis</th>
                <th style="padding:6px 8px;text-align:right">Drop</th>
                <th style="padding:6px 8px;text-align:right">RVOL</th>
                <th style="padding:6px 8px;text-align:right">Score</th></tr>
                {_crash_rows}</table>
                <p style="color:#999;font-size:11px;margin-top:12px">Automatischer Short/Crash Alert vom Background Service (stündlich)</p>
                </body></html>'''
                _send_email_alert(f"Short Alert: {len(crash_stocks)} Crash-Kandidaten ({crash_stocks[0]['ticker']} {crash_stocks[0]['change_pct']:.0f}%)", _body, secrets)
                log.info(f"  CRASH ALERT sent: {[c['ticker'] for c in crash_stocks]}")

    except Exception as e:
        log.error(f"Bear Scanner: {e}\n{traceback.format_exc()}")
        _update_status("bear_scan", "error", str(e))


def _run_strategy_scanner(poly_key, secrets):
    """
    Stündlicher Aktien-Strategien Scanner.
    Prüft alle wichtigen Strategien auf starke Setups und sendet E-Mail Alerts.

    Nutzt Polygon Snapshot API (wie _run_bi_scanner) und wendet Strategie-Filter an.
    """
    _clear_scan_cache("strategies")
    import requests as req
    log.info("📊 Strategie-Scanner (stündlich)...")
    _update_status("strategy_scan", "running")

    try:
        # 1) Polygon Snapshot laden
        snap = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                       params={"apiKey": poly_key}, timeout=30)
        if snap.status_code != 200:
            log.error(f"  Polygon HTTP {snap.status_code}")
            return
        tickers = snap.json().get("tickers", [])
        log.info(f"  {len(tickers)} Aktien geladen")

        # 2) CS-Whitelist laden
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                with open(cs_file, "r") as f:
                    cs_set = set(json.load(f))
        except Exception:
            pass

        # 3) Daten extrahieren
        stocks = []
        for t in tickers:
            try:
                lt = t.get("lastTrade", {}) or {}
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = lt.get("p") or day.get("c") or day.get("vw") or prev.get("c") or 0
                if not price or price <= 0:
                    continue

                ticker = t.get("ticker", "")
                if len(ticker) > 5 or "." in ticker:
                    continue
                if cs_set and ticker.upper() not in cs_set:
                    continue

                vol = day.get("v") or 0
                prev_vol = prev.get("v") or 0
                prev_close = prev.get("c") or 0
                change_pct = t.get("todaysChangePerc") or 0
                rvol = vol / prev_vol if prev_vol > 0 and vol > 0 else 0
                dollar_vol = price * vol if vol > 0 else price * prev_vol

                # Close Position: (Close - Low) / (High - Low)
                day_high = day.get("h") or 0
                day_low = day.get("l") or 0
                day_close = day.get("c") or price
                close_pos = (day_close - day_low) / (day_high - day_low) if day_high > day_low else 0.5

                # Gap%: (Open - PrevClose) / PrevClose
                day_open = day.get("o") or 0
                gap_pct = ((day_open - prev_close) / prev_close * 100) if prev_close > 0 and day_open > 0 else 0

                # Vortag%: (PrevClose - PrevOpen) / PrevOpen — Kerzen-Performance
                prev_open = prev.get("o") or 0
                vortag_pct = ((prev_close - prev_open) / prev_open * 100) if prev_open > 0 else 0

                stocks.append({
                    "Ticker": ticker, "Name": (t.get("name", "") or "")[:30],
                    "Preis": round(price, 2), "Change%": round(change_pct, 2),
                    "RVOL": round(rvol, 2), "Close Position": round(close_pos, 3),
                    "Volume": vol, "DollarVol": dollar_vol,
                    "Gap%": round(gap_pct, 2), "Vortag%": round(vortag_pct, 2),
                })
            except Exception:
                continue

        log.info(f"  {len(stocks)} Aktien nach Basis-Filter")

        # 4) Strategien definieren — nur die wichtigsten für Alerts
        ALERT_STRATEGIES = {
            "Breakout Long": {
                "filters": {"Change %": (3.0, 50.0), "RVOL": (1.5, 50.0), "Close Position": (0.65, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Breakdown Short": {
                "filters": {"Change %": (-50.0, -3.0), "RVOL": (0.8, 50.0), "Close Position": (0.0, 0.35)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Crash Short": {
                # V2.8: Massive Drops brauchen kein hohes RVOL — der Drop selbst ist das Signal
                "filters": {"Change %": (-60.0, -10.0)},
                "direction": "short", "min_price": 3.0, "min_dv": 300000,
            },
            "Volume Surge": {
                "filters": {"RVOL": (2.0, 50.0), "Change %": (2.0, 100.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Whale Watch": {
                "filters": {"RVOL": (3.0, 100.0), "Change %": (2.0, 100.0), "Close Position": (0.55, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 500000,
            },
            "Whale Watch Short": {
                "filters": {"RVOL": (2.5, 100.0), "Change %": (-100.0, -2.0), "Close Position": (0.0, 0.45)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Early Momentum": {
                "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0), "Close Position": (0.6, 1.0), "Preis": (5.0, 500.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Gap Up Momentum": {
                "filters": {"Gap%": (2.0, 30.0), "Close Position": (0.55, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Gap Down Short": {
                "filters": {"Gap%": (-30.0, -2.0), "Close Position": (0.0, 0.45)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Reversal Hunter": {
                "filters": {"Vortag%": (-50.0, -3.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
        }

        # 5) Jede Strategie durchlaufen und Matches finden
        all_alerts = []
        now = time.time()

        for strat_name, strat in ALERT_STRATEGIES.items():
            matches = []
            for s in stocks:
                # Min Price + Dollar Volume
                if s["Preis"] < strat.get("min_price", 5.0):
                    continue
                if s["DollarVol"] < strat.get("min_dv", 200000):
                    continue

                # Strategie-Filter anwenden
                passed = True
                for filter_key, (fmin, fmax) in strat["filters"].items():
                    # Filter-Key Mapping
                    data_key = {
                        "Change %": "Change%", "RVOL": "RVOL",
                        "Close Position": "Close Position", "Preis": "Preis",
                        "Gap%": "Gap%", "Vortag%": "Vortag%",
                    }.get(filter_key, filter_key)
                    val = s.get(data_key, 0)
                    if not (fmin <= val <= fmax):
                        passed = False
                        break

                if passed:
                    # Score berechnen: gewichtete Kombination der Signalstärke
                    score = 0
                    change = abs(s["Change%"])
                    rvol = s["RVOL"]
                    close_pos = s["Close Position"]

                    # Change-Stärke (max 40)
                    score += min(40, change * 4)
                    # RVOL-Stärke (max 30)
                    score += min(30, rvol * 8)
                    # Close Position Qualität (max 20) — 1.0 = perfekt für Long, 0.0 für Short
                    if strat["direction"] == "long":
                        score += close_pos * 20
                    else:
                        score += (1.0 - close_pos) * 20
                    # DollarVol Bonus (max 10)
                    if s["DollarVol"] >= 5_000_000:
                        score += 10
                    elif s["DollarVol"] >= 1_000_000:
                        score += 5

                    matches.append({**s, "_score": round(score), "_strategy": strat_name,
                                    "_direction": strat["direction"]})

            # Top 5 pro Strategie nach Score
            matches.sort(key=lambda x: x["_score"], reverse=True)
            top = matches[:5]

            for m in top:
                # Cooldown pro Ticker+Strategie
                ck = f"strat_{strat_name}_{m['Ticker']}"
                if ck in _EMAIL_COOLDOWN and now - _EMAIL_COOLDOWN[ck] < _EMAIL_COOLDOWN_SEC:
                    continue
                # Nur starke Setups: Score >= 60
                if m["_score"] >= 60:
                    all_alerts.append(m)
                    _EMAIL_COOLDOWN[ck] = now

        # 6) Cache speichern
        cache_file = "/tmp/strategy_scan_results.json"
        try:
            _atomic_write_json(cache_file, {"results": all_alerts, "timestamp": time.time(),
                           "total_stocks": len(stocks)})
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        log.info(f"  {len(all_alerts)} starke Setups gefunden (Score >= 60)")
        _update_status("strategy_scan", "done", f"{len(all_alerts)} Alerts")

        # 7) E-Mail senden wenn Alerts vorhanden
        if all_alerts:
            # Gruppiere nach Strategie
            by_strat = {}
            for a in all_alerts:
                sn = a["_strategy"]
                if sn not in by_strat:
                    by_strat[sn] = []
                by_strat[sn].append(a)

            subject = f"📊 {len(all_alerts)} Strategie-Setups gefunden"
            rows = ""
            for sn, items in by_strat.items():
                for a in items:
                    dir_emoji = "⬆️" if a["_direction"] == "long" else "⬇️"
                    rows += f"""<tr>
                        <td style="padding:6px;border-bottom:1px solid #eee"><b>{a['Ticker']}</b></td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a.get('Name', '')}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{sn}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{dir_emoji}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['_score']}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">${a['Preis']}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['Change%']:+.1f}%</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['RVOL']:.1f}x</td>
                    </tr>"""

            body_html = f"""
            <html><body style="font-family:Arial,sans-serif;max-width:750px;margin:0 auto">
            <h2 style="color:#1a73e8">📊 Strategie-Scanner Alert</h2>
            <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {len(all_alerts)} Setups (Score ≥ 60)</p>
            <table style="width:100%;border-collapse:collapse;font-size:13px">
                <tr style="background:#f5f5f5">
                    <th style="padding:6px;text-align:left">Ticker</th>
                    <th style="padding:6px;text-align:left">Name</th>
                    <th style="padding:6px;text-align:left">Strategie</th>
                    <th style="padding:6px;text-align:left">Dir</th>
                    <th style="padding:6px;text-align:left">Score</th>
                    <th style="padding:6px;text-align:left">Preis</th>
                    <th style="padding:6px;text-align:left">Change</th>
                    <th style="padding:6px;text-align:left">RVOL</th>
                </tr>
                {rows}
            </table>
            <p style="color:#999;font-size:12px;margin-top:20px">
                Automatischer Strategie-Alert | Score = Change×4 + RVOL×8 + ClosePos×20 + VolBonus<br>
                Nur Setups mit Score ≥ 60 werden gemeldet | 4h Cooldown pro Ticker
            </p>
            </body></html>"""

            _send_email_alert(subject, body_html, secrets)

    except Exception as e:
        log.error(f"❌ Strategy Scanner: {e}\n{traceback.format_exc()}")
        _update_status("strategy_scan", "error", str(e))


def _run_orb_scanner(poly_key):
    """ORB Scanner — läuft nur Mo-Fr 9:45-11:00 ET, speichert Ergebnisse als Cache"""
    _clear_scan_cache("orb")
    import pytz
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    hour, minute = now_et.hour, now_et.minute
    time_val = hour * 60 + minute
    weekday = now_et.weekday()

    # Nur während ORB-Fenster laufen: 9:45-11:00 ET, Mo-Fr
    if weekday >= 5 or time_val < 585 or time_val >= 660:  # 9:45=585, 11:00=660
        log.info("🔔 ORB Scanner — außerhalb Fenster (nur 9:45-11:00 ET Mo-Fr), übersprungen")
        return

    log.info("🔔 ORB Scanner...")
    _update_status("orb", "running")

    try:
        # fetch_orb_scanner ist in scanner.py mit @st.cache_data — wir rufen die Funktion
        # direkt auf, der Decorator wird ignoriert wenn kein Streamlit-Kontext da ist.
        # Stattdessen: Funktion manuell aufrufen und Cache-File schreiben
        import importlib, sys

        # Mock st.cache_data damit der Import nicht crasht
        class _FakeST:
            @staticmethod
            def cache_data(*a, **kw):
                def dec(f): return f
                return dec
            def __getattr__(self, name):
                return lambda *a, **kw: None

        # scanner.py braucht streamlit — wir importieren es mit Mock
        # Besser: Die Kern-Logik direkt nutzen
        from modules.data_fetchers import rate_limited_get, fetch_grouped_daily

        orb_progress = "/tmp/orb_scan_progress.json"
        orb_results = "/tmp/orb_scan_results.json"

        today_str = now_et.strftime("%Y-%m-%d")
        yesterday = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
        if weekday == 0:
            yesterday = (now_et - timedelta(days=3)).strftime("%Y-%m-%d")

        prev_data = fetch_grouped_daily(poly_key, yesterday)
        if not prev_data:
            day_before = (now_et - timedelta(days=2)).strftime("%Y-%m-%d")
            if weekday == 0:
                day_before = (now_et - timedelta(days=4)).strftime("%Y-%m-%d")
            prev_data = fetch_grouped_daily(poly_key, day_before)

        if not prev_data:
            _update_status("orb", "error", "Keine Vortages-Daten")
            return

        # V2.8: Snapshot API statt fetch_grouped_daily für heutige Daten
        # fetch_grouped_daily liefert während Handelszeit KEINE Daten (nur nach Börsenschluss)
        today_data = {}
        try:
            import requests as _req
            snap_resp = _req.get(
                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"apiKey": poly_key}, timeout=30
            )
            if snap_resp.status_code == 200:
                for t in snap_resp.json().get("tickers", []):
                    sym = t.get("ticker", "")
                    day = t.get("day", {}) or {}
                    lt = t.get("lastTrade", {}) or {}
                    if day.get("o"):
                        today_data[sym] = {
                            "o": day.get("o", 0),
                            "h": day.get("h", 0),
                            "l": day.get("l", 0),
                            "c": day.get("c", 0) or lt.get("p", 0),
                            "v": day.get("v", 0),
                        }
                log.info(f"[ORB] Snapshot: {len(today_data)} Ticker mit Intraday-Daten")
            else:
                log.warning(f"[ORB] Snapshot HTTP {snap_resp.status_code} — Fallback grouped daily")
                today_data = fetch_grouped_daily(poly_key, today_str) or {}
        except Exception as e:
            log.warning(f"[ORB] Snapshot Fehler: {e} — Fallback grouped daily")
            today_data = fetch_grouped_daily(poly_key, today_str) or {}

        # CS-Whitelist laden
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                cs_set = set(json.load(open(cs_file)))
        except Exception:
            pass

        mins_since_open = max(1, time_val - 570)  # 570 = 9:30
        total_market_mins = 390

        candidates = []
        for ticker, prev in prev_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            if cs_set and ticker.upper() not in cs_set:
                continue
            prev_close = prev.get("c", 0)
            if prev_close < 5 or prev_close > 2000:
                continue
            prev_vol = prev.get("v", 0)
            if prev_vol < 500000:
                continue

            today = today_data.get(ticker, {}) if today_data else {}
            today_open = today.get("o", 0)
            today_vol = today.get("v", 0)
            today_high = today.get("h", 0)
            today_low = today.get("l", 0)
            today_close = today.get("c", 0)

            if today_open <= 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0

            if mins_since_open <= 30:
                expected_vol_fraction = 0.20 * (mins_since_open / 30)
            elif mins_since_open <= 60:
                expected_vol_fraction = 0.20 + 0.10 * ((mins_since_open - 30) / 30)
            else:
                expected_vol_fraction = 0.30 + 0.70 * ((mins_since_open - 60) / (total_market_mins - 60))
            expected_vol_fraction = max(0.01, expected_vol_fraction)
            expected_vol = prev_vol * expected_vol_fraction
            rvol = today_vol / expected_vol if expected_vol > 0 else 0

            if abs(gap_pct) < 2 and rvol < 1.5:
                continue

            candidates.append({
                "ticker": ticker, "prev_close": round(prev_close, 2),
                "open": round(today_open, 2), "current": round(today_close or today_open, 2),
                "high": round(today_high, 2), "low": round(today_low, 2),
                "gap_pct": round(gap_pct, 2), "rvol": round(rvol, 2), "volume": today_vol,
            })

        candidates.sort(key=lambda x: abs(x["gap_pct"]) * 0.6 + min(x["rvol"], 5) * 0.4, reverse=True)
        candidates = candidates[:40]

        # 5-Min Candles für Breakout Detection
        market_open_ms = int(now_et.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
        or_end_ms = int(now_et.replace(hour=9, minute=45, second=0, microsecond=0).timestamp() * 1000)
        breakouts = []

        for cand in candidates:
            ticker = cand["ticker"]
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{today_str}/{today_str}"
                resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50000}, timeout=10)
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if not bars or len(bars) < 2:
                    continue
                bars = [b for b in bars if b.get("t", 0) >= market_open_ms]
                if len(bars) < 2:
                    continue

                or_bars = [b for b in bars if b.get("t", 0) < or_end_ms]
                if not or_bars:
                    or_bars = bars[:3]
                or_high = max(b.get("h", 0) for b in or_bars)
                or_low = min(b.get("l", 999999) for b in or_bars)

                # VWAP
                total_vwap_num = sum((b.get("h",0)+b.get("l",0)+b.get("c",0))/3 * b.get("v",0) for b in bars)
                total_vol = sum(b.get("v", 0) for b in bars)
                vwap = total_vwap_num / total_vol if total_vol > 0 else (or_high + or_low) / 2

                current_price = bars[-1].get("c", 0)
                post_or = [b for b in bars if b.get("t", 0) >= or_end_ms]
                bars_above = sum(1 for b in post_or if b.get("c", 0) > or_high)
                bars_below = sum(1 for b in post_or if b.get("c", 0) < or_low)

                breakout_dir = None
                if current_price > or_high and bars_above >= 2:
                    breakout_dir = "LONG"
                elif current_price < or_low and bars_below >= 2:
                    breakout_dir = "SHORT"

                if breakout_dir:
                    breakouts.append({
                        **cand,
                        "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                        "vwap": round(vwap, 2), "direction": breakout_dir,
                        "current_price": round(current_price, 2),
                    })
            except Exception:
                continue

        # Ergebnisse speichern
        result = {
            "breakouts": breakouts, "candidates": candidates[:20],
            "stats": {"scanned": len(prev_data), "candidates": len(candidates), "breakouts": len(breakouts)},
            "or_phase": "active", "market_time": now_et.strftime("%H:%M ET"),
            "timestamp": time.time()
        }
        _atomic_write_json(orb_results, result)

        _update_status("orb", "ok", f"{len(breakouts)} Breakouts")
        log.info(f"  ✅ ORB: {len(breakouts)} Breakouts (von {len(candidates)} Kandidaten)")

    except Exception as e:
        _update_status("orb", "error", str(e))
        log.error(f"  ❌ ORB Fehler: {e}")


def _run_biotech_scanner(poly_key):
    """Biotech Scanner — ruft _biotech_background_scan aus modules/scanners.py auf"""
    _clear_scan_cache("biotech")
    log.info("🧬 Biotech Scanner...")
    _update_status("biotech", "running")

    try:
        from modules.scanners import _biotech_background_scan
        _biotech_background_scan(poly_key)
        _update_status("biotech", "ok", "Scan abgeschlossen")
        log.info("  ✅ Biotech Scan abgeschlossen")
    except Exception as e:
        _update_status("biotech", "error", str(e))
        log.error(f"  ❌ Biotech Scan Fehler: {e}")


def _run_btc_divergence(poly_key=None):
    """BTC-Divergenz Scanner — nutzt CoinGecko (kein Polygon nötig)
    V2: Berechnet Timing, ExhScore, SellProb etc. (vorher fehlten diese Felder)"""
    log.info("📉 BTC-Divergenz Scanner...")
    _update_status("btc_divergence", "fetching")

    import requests as req

    # Import Scoring-Funktionen (kein Streamlit nötig)
    try:
        from modules.scorers import (calculate_exhaustion_score,
                                     calculate_close_position,
                                     get_exhaustion_grade)
    except ImportError as ie:
        log.error(f"  scorers import fehlgeschlagen: {ie}")
        _update_status("btc_divergence", "error", f"Import: {ie}")
        return

    try:
        # CoinGecko laden (4 Seiten)
        all_coins = []
        for page in range(1, 5):
            try:
                resp = req.get("https://api.coingecko.com/api/v3/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc",
                            "per_page": 250, "page": page, "sparkline": False,
                            "price_change_percentage": "1h,24h,7d,14d,30d"},
                    timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        all_coins.extend(data)
                elif resp.status_code == 429:
                    log.warning(f"  CoinGecko Rate Limit bei Seite {page}")
                    break
            except Exception as e:
                log.warning(f"  CoinGecko Seite {page}: {e}")
                if page > 1: break
            if page < 4:
                time.sleep(3)

        if not all_coins:
            _update_status("btc_divergence", "error", "Keine CoinGecko Daten")
            return

        # In Datei-Cache speichern (für Streamlit)
        cg_cache = "/tmp/coingecko_markets_cache.json"
        _atomic_write_json(cg_cache, {"coins": all_coins, "ts": time.time()})

        log.info(f"  {len(all_coins)} Coins geladen, speichere für Streamlit")

        # Progress-Datei für Streamlit
        div_progress = "/tmp/div_scan_progress.json"
        _atomic_write_json(div_progress, {"status": "running", "checked": 0, "total": len(all_coins),
                       "hits": 0, "detail": f"📊 {len(all_coins)} Coins geladen", "timestamp": time.time()})

        # BTC Benchmark
        btc_data = None
        for c in all_coins:
            if c.get("symbol", "").lower() == "btc" or c.get("id") == "bitcoin":
                btc_data = {
                    "price": c.get("current_price", 0),
                    "change_1h": c.get("price_change_percentage_1h_in_currency") or 0,
                    "change_24h": c.get("price_change_percentage_24h") or 0,
                    "change_7d": c.get("price_change_percentage_7d_in_currency") or 0,
                    "change_14d": c.get("price_change_percentage_14d_in_currency") or 0,
                    "change_30d": c.get("price_change_percentage_30d_in_currency") or 0,
                    "market_cap": c.get("market_cap", 0),
                }
                break

        if not btc_data:
            _atomic_write_json(div_progress, {"status": "error", "detail": "BTC nicht gefunden", "timestamp": time.time()})
            return

        btc_7d = btc_data.get("change_7d", 0)
        btc_14d = btc_data.get("change_14d", 0)
        btc_30d = btc_data.get("change_30d", 0)

        # ── FIX 2: BTC Dominance als Makro-Filter ──
        btc_dominance = None
        try:
            dom_resp = req.get("https://api.coingecko.com/api/v3/global", timeout=15)
            if dom_resp.status_code == 200:
                gdata = dom_resp.json().get("data", {})
                btc_dominance = gdata.get("market_cap_percentage", {}).get("btc", None)
                # BTC Dom > 55% = Risk-Off → Shorts auf Alts besser
                # BTC Dom < 45% = Altseason → Shorts auf Alts riskanter
                log.info(f"  BTC Dominance: {btc_dominance:.1f}%")
        except Exception as e:
            log.warning(f"  BTC Dominance Fehler: {e}")

        # ── FIX 3: Real RSI via CoinGecko OHLC (Top-Hits) ──
        # Wird NACH dem Scan für Top-Kandidaten nachgeladen
        def _calc_rsi_from_ohlc(coin_id, days=30, period=14):
            """Berechne echten RSI aus CoinGecko OHLC-Daten.
            days=30 gibt Daily Candles (days=14 gibt nur 4h-Kerzen bei CoinGecko)."""
            try:
                ohlc_resp = req.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
                                    params={"vs_currency": "usd", "days": days},
                                    timeout=15)
                if ohlc_resp.status_code != 200:
                    return None
                ohlc = ohlc_resp.json()  # [[ts, open, high, low, close], ...]
                if not ohlc or len(ohlc) < period + 1:
                    return None
                closes = [c[4] for c in ohlc if len(c) >= 5]
                gains, losses = [], []
                for i in range(1, len(closes)):
                    diff = closes[i] - closes[i - 1]
                    gains.append(max(0, diff))
                    losses.append(max(0, -diff))
                if len(gains) < period:
                    return None
                avg_gain = sum(gains[:period]) / period
                avg_loss = sum(losses[:period]) / period
                for i in range(period, len(gains)):
                    avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                    avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                if avg_loss == 0:
                    return 100.0
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                return round(rsi, 1)
            except Exception:
                return None

        # ── FIX 5: OI Change Delta 24h (Cache-basiert) ──
        oi_cache_file = "/tmp/oi_cache_prev.json"
        oi_prev = {}
        try:
            if os.path.exists(oi_cache_file):
                with open(oi_cache_file, "r") as f:
                    oi_prev = json.load(f)
        except Exception:
            pass

        # ── FIX 4: Liquidation Daten (Coinglass Free) ──
        liquidation_data = {}
        try:
            liq_resp = req.get("https://open-api.coinglass.com/public/v2/liquidation/info",
                               params={"time_type": 1}, timeout=10,  # 24h
                               headers={"accept": "application/json"})
            if liq_resp.status_code == 200:
                liq_list = liq_resp.json().get("data", [])
                for liq in liq_list:
                    sym = (liq.get("symbol") or "").upper()
                    if sym:
                        liquidation_data[sym] = {
                            "long_liq": liq.get("longVolUsd", 0),
                            "short_liq": liq.get("shortVolUsd", 0),
                            "total_liq": liq.get("volUsd", 0),
                        }
                if liquidation_data:
                    log.info(f"  Liquidation Daten: {len(liquidation_data)} Coins")
        except Exception:
            pass  # Coinglass free tier kann fehlen

        # ── FIX 5: OI Daten holen (für Delta-Berechnung) ──
        oi_current = {}
        try:
            oi_resp = req.get("https://open-api.coinglass.com/public/v2/open_interest",
                              params={"time_type": 0}, timeout=10,
                              headers={"accept": "application/json"})
            if oi_resp.status_code == 200:
                oi_list = oi_resp.json().get("data", [])
                for oi_item in oi_list:
                    sym = (oi_item.get("symbol") or "").upper()
                    oi_val = oi_item.get("openInterest", 0)
                    if sym and oi_val:
                        oi_current[sym] = oi_val
                if oi_current:
                    log.info(f"  OI Daten: {len(oi_current)} Coins")
        except Exception:
            pass

        results = []
        checked = 0

        for coin in all_coins:
            checked += 1
            try:
                symbol = coin.get("symbol", "").upper()
                if symbol in ("BTC", "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD"):
                    continue

                price = coin.get("current_price") or 0
                if price <= 0: continue

                change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
                change_24h = coin.get("price_change_percentage_24h") or 0
                change_7d = (coin.get("price_change_percentage_7d_in_currency")
                             or coin.get("price_change_percentage_7d") or 0)
                change_14d = coin.get("price_change_percentage_14d_in_currency") or 0
                change_30d = coin.get("price_change_percentage_30d_in_currency") or 0
                market_cap = coin.get("market_cap") or 0
                vol_24h = coin.get("total_volume") or 0
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price

                if vol_24h < 5_000_000 or market_cap < 10_000_000:
                    continue

                # Multi-Timeframe Divergenz
                div_7d = change_7d - btc_7d
                div_14d = (change_14d - btc_14d) if change_14d else 0
                div_30d = (change_30d - btc_30d) if change_30d else 0

                best_div = max(div_7d, div_14d, div_30d)
                best_tf = "7d"
                if best_div == div_30d and div_30d >= 10:
                    best_tf = "30d"
                elif best_div == div_14d and div_14d >= 10:
                    best_tf = "14d"

                if best_div < 10:
                    continue
                best_change = {"7d": change_7d, "14d": change_14d, "30d": change_30d}[best_tf]
                if best_change < 8:
                    continue

                # OHLC + Wick-Berechnung
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                open_price = max(low_24h, min(high_24h, open_price))
                candle_range = high_24h - low_24h if high_24h > low_24h else 0
                range_pct = (candle_range / low_24h * 100) if low_24h > 0 else 0

                if range_pct >= 0.5 and candle_range > 0:
                    body_top = max(open_price, price)
                    body_bottom = min(open_price, price)
                    upper_wick_pct = ((high_24h - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - low_24h) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0

                close_pos = calculate_close_position(high_24h, low_24h, price, min_range_pct=0.3)

                # Exhaustion Score
                exh_score, exh_details = calculate_exhaustion_score(
                    change_24h=change_24h, change_7d=change_7d,
                    btc_change_7d=btc_7d, rvol=None, close_pos=close_pos,
                    upper_wick_pct=upper_wick_pct, lower_wick_pct=lower_wick_pct,
                    market_cap=market_cap, high_24h=high_24h, low_24h=low_24h,
                    price=price, vol_24h=vol_24h, change_1h=change_1h,
                    change_14d=change_14d, change_30d=change_30d,
                    btc_change_14d=btc_14d, btc_change_30d=btc_30d,
                    funding_rate=None, oi_volume_ratio=None,
                )
                grade, grade_emoji, grade_label = get_exhaustion_grade(exh_score)

                # Short-Timing V4 (AUDIT FIX: Schwellen gesenkt, realistisch)
                # ExhScore erreicht in bg_service praktisch max ~75 (kein Funding/OI)
                # Daher: Schwellen 65→50 für JETZT, 50→40 für BEREIT/WATCH
                # cp = Close Position in 24h-Range (0.0=Low, 1.0=High)
                # 0.70+ = oberes Drittel (ideal für Short-Entry)
                # 0.40-0.70 = Mitte (warten auf Bounce/Breakdown)
                # <0.40 = unteres Drittel (zu spät für Short)
                cp = close_pos if close_pos is not None else 0.5
                price_near_high = cp >= 0.70
                price_mid_range = 0.40 <= cp < 0.70
                price_near_low = cp < 0.40

                if price_near_low and change_24h < -3:
                    timing = "⚫ ZU SPÄT — Preis schon {:.0f}% vom High, Move gelaufen".format((1 - cp) * 100)
                elif exh_score >= 55 and price_near_high and change_1h < -1.5:
                    timing = "🔴 JETZT SHORTEN — Nahe High, 1h kippt ({:+.1f}%)".format(change_1h)
                elif exh_score >= 50 and price_near_high and change_1h < -0.5:
                    timing = "🟢 JETZT — Nahe High, erste Schwäche (1h {:+.1f}%)".format(change_1h)
                elif exh_score >= 45 and price_near_high and change_1h < -2.0:
                    timing = "🟢 JETZT — Nahe High, starker 1h-Dump ({:+.1f}%)".format(change_1h)
                elif exh_score >= 50 and price_near_high:
                    timing = "🟡 BEREIT — Nahe High, warte auf rote 1h-Kerze"
                elif exh_score >= 50 and price_mid_range:
                    timing = "🟡 BEREIT — Warte auf Bounce Richtung High für besseren Entry"
                elif exh_score >= 40 and price_mid_range and change_1h < 0:
                    timing = "🟠 WATCHLIST — Mittlerer Bereich, könnte noch bounzen"
                elif exh_score >= 50 and price_near_low:
                    timing = "⚫ ZU SPÄT — Preis schon {:.0f}% vom High gefallen".format((1 - cp) * 100)
                elif exh_score >= 40:
                    timing = "🟠 WATCHLIST — Noch nicht reif"
                else:
                    timing = "⚪ ZU FRÜH"

                # Timing-Qualitätsstufe (für SellProb)
                _timing_quality = 0
                if "🔴 JETZT" in timing:
                    _timing_quality = 5
                elif "🟢 JETZT" in timing:
                    _timing_quality = 4
                elif "🟡 BEREIT" in timing:
                    _timing_quality = 3
                elif "🟠 WATCHLIST" in timing:
                    _timing_quality = 2
                elif "ZU SPÄT" in timing:
                    _timing_quality = -1

                # RVOL
                if market_cap > 0 and vol_24h > 0:
                    turnover = (vol_24h / market_cap) * 100
                    mc = market_cap
                    if mc > 100_000_000_000:   bl = 3.0
                    elif mc > 10_000_000_000:  bl = 6.0
                    elif mc > 1_000_000_000:   bl = 10.0
                    elif mc > 100_000_000:     bl = 20.0
                    else:                      bl = 30.0
                    rvol = round(turnover / bl, 2)
                else:
                    rvol = 0.8  # Unter Durchschnitt bei fehlenden Daten (konservativ)

                # ── SellProb V4: HYBRID ──
                # bg_service nutzt CoinGecko (kein Funding/OI verfügbar, immer None)
                # → Exhaustion-Komponenten 7+8 (OI, Funding) fehlen immer
                # → Theoretisches Max: 90 Punkte, Empirisches Max: ~75-80
                # Normalisierung /75 statt /100: ExhScore 65→87%, 70→93%, 75→100%
                # Schritt 1: Kern-Score aus 3 Faktoren (gewichtet, 0-100)
                exh_pct = min(100, exh_score * 100 / 75)           # 0-100 (75+ = 100%)
                timing_pct = {5: 100, 4: 85, 3: 60, 2: 35, 0: 5, -1: 0}.get(_timing_quality, 0)
                # pos_pct: Linear 0-100 statt ×130 Cliff
                # cp 0.0→0%, 0.5→50%, 0.8→80%, 1.0→100% (glatter Verlauf)
                pos_pct = min(100, max(0, (cp or 0.3)) * 100)
                kern_score = exh_pct * 0.40 + timing_pct * 0.35 + pos_pct * 0.25

                # Schritt 2: Volume als Skalierung (0.5 - 1.2)
                volume_mult = max(0.5, min(1.2, (rvol or 0.5) / 1.5))

                # BTC Dominance Boost (FIX 2)
                dom_factor = 1.0
                if btc_dominance:
                    if btc_dominance >= 58:
                        dom_factor = 1.15  # Starke BTC Dom → Alts sehr anfällig
                    elif btc_dominance >= 52:
                        dom_factor = 1.05  # Moderat hohe BTC Dom
                    elif btc_dominance >= 45:
                        dom_factor = 0.95  # Neutrale Zone — leichter Malus
                    elif btc_dominance <= 42:
                        dom_factor = 0.75  # Altseason → Shorts riskant

                # Liquidation Boost (FIX 4)
                liq_factor = 1.0
                liq_info = liquidation_data.get(symbol, {})
                long_liq = liq_info.get("long_liq", 0)
                short_liq = liq_info.get("short_liq", 0)
                if long_liq > 0 and short_liq > 0:
                    liq_ratio = long_liq / max(1, short_liq)
                    if liq_ratio >= 3.0:
                        liq_factor = 1.20  # Massive Long-Liquidationen → Short optimal
                    elif liq_ratio >= 1.5:
                        liq_factor = 1.10
                    elif liq_ratio <= 0.3:
                        liq_factor = 0.80  # Short Squeeze Gefahr

                # ── FIX 5: OI Change Delta 24h ──
                oi_delta_pct = None
                oi_now = oi_current.get(symbol, 0)
                oi_before = oi_prev.get(symbol, 0)
                if oi_now > 0 and oi_before > 0:
                    oi_delta_pct = round(((oi_now - oi_before) / oi_before) * 100, 1)

                # OI-Faktor: OI steigt stark + Preis nahe High = Longs überhebelt → Short gut
                oi_factor = 1.0
                if oi_delta_pct is not None:
                    if oi_delta_pct >= 20 and cp >= 0.7:
                        oi_factor = 1.15  # OI explodiert bei Highs → überhebelt
                    elif oi_delta_pct >= 10:
                        oi_factor = 1.05  # OI steigt moderat
                    elif oi_delta_pct <= -20:
                        oi_factor = 0.85  # OI sinkt stark → weniger Squeeze-Potential
                    elif oi_delta_pct <= -10:
                        oi_factor = 0.92  # OI sinkt leicht

                # Schritt 3: Alle Modifier zusammen (dom, liq, oi = Boosts/Malus)
                # Cap bei 1.4 (max +40%) und Floor bei 0.4 (max -60%)
                # Verhindert dass 4 kleine Boosts sich zu 1.9x multiplizieren
                combined_mod = max(0.4, min(1.4, volume_mult * dom_factor * liq_factor * oi_factor))
                sell_prob = max(0, min(100, round(kern_score * combined_mod)))

                results.append({
                    "Ticker": symbol,
                    "Name": coin.get("name", symbol),
                    "Preis": price,
                    "1h%": round(change_1h, 2),
                    "24h%": round(change_24h, 2),
                    "7d%": round(change_7d, 2),
                    "14d%": round(change_14d, 2),
                    "30d%": round(change_30d, 2),
                    "BTC_7d%": round(btc_7d, 2),
                    "BTC_14d%": round(btc_14d, 2),
                    "BTC_30d%": round(btc_30d, 2),
                    "Divergenz%": round(best_div, 1),
                    "BestTF": best_tf,
                    "Div7d%": round(div_7d, 1),
                    "Div14d%": round(div_14d, 1),
                    "Div30d%": round(div_30d, 1),
                    "ExhScore": exh_score,
                    "ExhGrade": grade,
                    "GradeEmoji": grade_emoji,
                    "Timing": timing,
                    "TimingQuality": _timing_quality,
                    "SellProb": sell_prob,
                    "RVOL": rvol,
                    "UpperWick%": round(upper_wick_pct, 1),
                    "ClosePos": close_pos,
                    "MarketCap": market_cap,
                    "Vol24h": vol_24h,
                    "ExhDetails": exh_details,
                    "CoinId": coin.get("id", ""),
                    "FundingRate": None,
                    "OI_Ratio": None,
                    "HasPerp": False,
                    "Exchanges": [],
                    "BestExchange": "",
                    # Neue Felder V3
                    "BTCDominance": btc_dominance,
                    "LiqLong": long_liq,
                    "LiqShort": short_liq,
                    "LiqFactor": round(liq_factor, 2),
                    "DomFactor": round(dom_factor, 2),
                    # Neue Felder V4 (Fix 3 + 5)
                    "RSI14": None,  # Wird für Top-Hits nachgeladen
                    "OI_Delta%": oi_delta_pct,
                    "OI_Factor": round(oi_factor, 2),
                })
            except Exception:
                continue

            if checked % 100 == 0:
                try:
                    _atomic_write_json(div_progress, {"status": "running", "checked": checked, "total": len(all_coins),
                                   "hits": len(results), "detail": f"{checked}/{len(all_coins)}",
                                   "timestamp": time.time()})
                except Exception as e:
                    log.debug(f"Non-critical error: {e}")

        results.sort(key=lambda x: x.get("Divergenz%", 0), reverse=True)

        # ── FIX 3: RSI nachladen für Top-30 Kandidaten (Buffer für Re-Ranking) ──
        # Top-30 statt Top-20: RSI-Boost kann Ranking ändern (Coin #25 → Top-10)
        top_for_rsi = results[:30]
        rsi_loaded = 0
        for r in top_for_rsi:
            coin_id = r.get("CoinId", "")
            if not coin_id:
                continue
            try:
                rsi_val = _calc_rsi_from_ohlc(coin_id, days=30, period=14)
                if rsi_val is not None:
                    r["RSI14"] = rsi_val
                    rsi_loaded += 1
                    # AUDIT FIX: RSI in SellProb einrechnen (nicht nur anzeigen!)
                    old_sp = r["SellProb"]
                    if rsi_val >= 75:
                        r["SellProb"] = min(100, old_sp + 12)  # Stark überkauft
                    elif rsi_val >= 70:
                        r["SellProb"] = min(100, old_sp + 6)   # Überkauft
                    elif rsi_val <= 35:
                        r["SellProb"] = max(0, old_sp - 15)    # Überverkauft → kein Short!
                    elif rsi_val <= 45:
                        r["SellProb"] = max(0, old_sp - 5)     # Neutral-niedrig
                time.sleep(1.5)  # CoinGecko Rate Limit
            except Exception:
                pass
        if rsi_loaded:
            log.info(f"  RSI14 für {rsi_loaded}/{len(top_for_rsi)} Top-Hits geladen")
        # Re-Sort nach RSI-Adjustment (Ranking kann sich geändert haben)
        results.sort(key=lambda x: x.get("SellProb", 0), reverse=True)

        # ── FIX 5: OI Cache speichern für nächsten Delta-Vergleich ──
        if oi_current:
            try:
                _atomic_write_json(oi_cache_file, oi_current)
            except Exception as e:
                log.debug(f"Non-critical error: {e}")

        # Speichere für Streamlit
        div_results = "/tmp/div_scan_results.json"
        _atomic_write_json(div_results, {"results": results, "btc": btc_data,
                       "stats": {"scanned": checked, "candidates": len(results), "btc_7d": btc_7d},
                       "ts": time.time()})

        _atomic_write_json(div_progress, {"status": "done", "detail": f"✅ {len(results)} Divergenzen",
                       "timestamp": time.time()})

        _update_status("btc_divergence", "ok", f"{len(results)} Divergenzen")
        log.info(f"  {len(results)} Divergenzen gefunden")

    except Exception as e:
        log.error(f"❌ BTC-Divergenz: {e}\n{traceback.format_exc()}")
        _update_status("btc_divergence", "error", str(e))


# ══════════════════════════════════════════════════════════════
# NEW LISTING DUMP SCANNER
# ══════════════════════════════════════════════════════════════

def _alert_nls_signals(results, secrets):
    """Sendet E-Mail wenn NLS Short-Signale mit Grade S/A gefunden werden."""
    if not results:
        return
    signals = results.get("signals", [])
    if not signals:
        return

    now = time.time()
    alerts = []
    for entry in signals:
        sig = entry.get("signal", {})
        symbol = entry.get("symbol", "")
        grade = sig.get("grade", "")

        # Nur Grade S oder A
        if grade not in ("S", "A"):
            continue

        # Cooldown
        cooldown_key = f"nls_{symbol}"
        if cooldown_key in _EMAIL_COOLDOWN:
            if now - _EMAIL_COOLDOWN[cooldown_key] < _EMAIL_COOLDOWN_SEC:
                continue

        _EMAIL_COOLDOWN[cooldown_key] = now
        alerts.append({
            "symbol": symbol,
            "exchange": entry.get("exchange", ""),
            "grade": grade,
            "grade_label": sig.get("grade_label", ""),
            "timing": sig.get("timing", ""),
            "entry": sig.get("entry", 0),
            "stop": sig.get("stop", 0),
            "tp1": sig.get("tp1", 0),
            "tp2": sig.get("tp2", 0),
            "rr1": sig.get("rr1", 0),
            "rr2": sig.get("rr2", 0),
            "exh_score": sig.get("exh_score", 0),
            "pump_pct": sig.get("pump_pct", 0),
        })

    if not alerts:
        return

    n = len(alerts)
    subject = f"🔴 {n} Dump-Short Signal{'e' if n > 1 else ''} — New Listing Scanner"

    rows = ""
    for a in alerts:
        emoji = "🏆" if a["grade"] == "S" else "🔥"
        rows += f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{a['symbol']}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['exchange']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{emoji} {a['grade_label']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['timing']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['entry']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['stop']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['tp1']:.4f} / ${a['tp2']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['rr1']:.1f}x / {a['rr2']:.1f}x</td>
        </tr>"""

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
    <h2 style="color:#dc3545">🔴 New Listing Dump — SHORT Signale</h2>
    <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} Dump-Signal{'e' if n > 1 else ''} erkannt</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Symbol</th>
            <th style="padding:8px;text-align:left">Exchange</th>
            <th style="padding:8px;text-align:left">Grade</th>
            <th style="padding:8px;text-align:left">Timing</th>
            <th style="padding:8px;text-align:left">Entry</th>
            <th style="padding:8px;text-align:left">Stop</th>
            <th style="padding:8px;text-align:left">TP1 / TP2</th>
            <th style="padding:8px;text-align:left">R:R</th>
        </tr>
        {rows}
    </table>
    <p style="color:#999;font-size:12px;margin-top:20px">
        Automatischer Alert vom TradingBot — New Listing Dump Scanner.<br>
        Nur Grade S + A Signale. 4h Cooldown pro Symbol.
    </p>
    </body></html>
    """

    _send_email_alert(subject, body_html, secrets)
    log.info(f"📧 NLS Alert: {n} Dump-Signale gesendet ({', '.join(a['symbol'] for a in alerts)})")


def _run_new_listing_scanner():
    """Wrapper für den New Listing Dump Scanner."""
    log.info("🆕 Starte New Listing Scanner...")
    _update_status("new_listing", "running")
    try:
        from modules.new_listing_scanner import run_new_listing_scanner, seed_instrument_cache
        # Beim ersten Start: Cache seeden (keine Falsch-Positiven)
        seed_instrument_cache()
        results = run_new_listing_scanner()
        sig_count = len(results.get("signals", []))
        watch_count = len(results.get("watchlist", []))
        mon_count = len(results.get("monitoring", []))
        _update_status("new_listing", "ok",
                       f"{sig_count} Signale, {watch_count} Watchlist, {mon_count} monitoring")
        return results
    except Exception as e:
        log.error(f"❌ New Listing Scanner: {e}\n{traceback.format_exc()}")
        _update_status("new_listing", "error", str(e))
        return None


# ══════════════════════════════════════════════════════════════
# SERVICE LOOP
# ══════════════════════════════════════════════════════════════

_running = True

def _signal_handler(sig, frame):
    global _running
    log.info("⏹️ Stop-Signal empfangen...")
    _running = False

def run_service():
    global _running
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    secrets = _load_secrets()
    poly_key = secrets.get("POLYGON_KEY", "")
    if not poly_key:
        log.error("❌ POLYGON_KEY fehlt!")
        return

    PID_FILE.write_text(str(os.getpid()))
    log.info(f"Email alerts: {'AKTIV' if secrets.get('GMAIL_USER') and secrets.get('GMAIL_APP_PASSWORD') else 'INAKTIV (GMAIL_USER/GMAIL_APP_PASSWORD fehlt)'}")
    log.info(f"🚀 Background Service V2 gestartet (PID: {os.getpid()})")
    _update_status("_service", "running", f"PID {os.getpid()}")

    # ── Zeitplan: Feste Uhrzeiten (ET = US Eastern) ──
    # Aktien-Scanner basieren auf Daily Bars → ändern sich kaum untertägig
    # Crypto (BTC Divergenz) ist 24/7, schwankt stärker → häufiger
    # ORB braucht schnelle Checks bei Market Open
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    # Format: (stunde_ET, minute_ET)
    # V2: Stündlich während Handelszeiten (9:30-16:00 ET) + E-Mail Alerts
    SCHEDULE_TIMES = {
        "bi_long":        [(10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0), (16, 0)],  # Stündlich
        "bi_short":       [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)],      # Stündlich, 30min versetzt
        "crash_monitor":  [(9, 50), (13, 50)],              # 2x/Tag (10min vor BI, kein Konflikt)
        "biotech":        [(10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 15)],      # Stündlich, 15min versetzt
        "strategies":     [(10, 5), (11, 5), (12, 5), (13, 5), (14, 5), (15, 5), (16, 5)],   # Stündlich, 5min versetzt
        "bear_scan":      [(10, 20), (12, 20), (14, 20), (15, 45)],  # V2.8: 4x/Tag — Crash/Short Alerts
    }
    # Interval-basiert (unverändert)
    SCHEDULE_INTERVAL = {
        "btc_divergence":  7200,   # alle 2 Stunden (Crypto 24/7)
        "new_listing":      900,   # alle 15 Min (Crypto 24/7 — neue Listings erkennen)
        "orb":              300,   # 5 Min (nur aktiv 9:45-11:00 ET Mo-Fr)
    }

    last_run = {}
    _today_done = {}  # Track welche festen Zeiten heute schon gelaufen sind
    _running_scanners = set()  # B-05: Prevent concurrent scanner execution

    def _check_fixed_schedule(scanner_name, now_et):
        """Prüft ob ein Scanner mit fester Uhrzeit jetzt laufen soll."""
        times = SCHEDULE_TIMES.get(scanner_name, [])
        today_str = now_et.strftime("%Y-%m-%d")
        for h, m in times:
            slot_key = f"{scanner_name}_{today_str}_{h:02d}:{m:02d}"
            if slot_key in _today_done:
                continue
            # B-05: Check if already running
            if scanner_name in _running_scanners:
                log.debug(f"⚠️ {scanner_name} bereits im Betrieb, überspringe")
                continue
            # Scanner soll laufen wenn aktuelle Zeit >= geplante Zeit
            # und nicht mehr als 30 Min danach (damit er nicht um 23:00 nachholt)
            sched_min = h * 60 + m
            now_min = now_et.hour * 60 + now_et.minute
            if sched_min <= now_min <= sched_min + 30:
                return (True, slot_key)  # Return slot_key for marking after completion
        return (False, None)

    # ── Initialer Load (einmal beim Start) ──
    log.info("📡 Initialer Load...")
    try:
        _fetch_crash_monitor(poly_key)
        last_run["crash_monitor"] = time.time()
    except Exception as e:
        log.error(f"Init Crash: {e}")

    time.sleep(5)

    try:
        _run_btc_divergence(poly_key)
        last_run["btc_divergence"] = time.time()
    except Exception as e:
        log.error(f"Init BTC-Div: {e}")

    time.sleep(5)

    try:
        _nls_init = _run_new_listing_scanner()
        _alert_nls_signals(_nls_init, secrets)
        last_run["new_listing"] = time.time()
    except Exception as e:
        log.error(f"Init New Listing: {e}")

    time.sleep(10)

    try:
        _run_bi_scanner(poly_key, "long")
        last_run["bi_long"] = time.time()
    except Exception as e:
        log.error(f"Init BI Long: {e}")

    time.sleep(10)

    try:
        _run_bi_scanner(poly_key, "short")
        last_run["bi_short"] = time.time()
    except Exception as e:
        log.error(f"Init BI Short: {e}")

    # Biotech Scanner nach 2 Min starten (nicht sofort — spart API-Calls beim Init)
    time.sleep(120)
    try:
        _run_biotech_scanner(poly_key)
        last_run["biotech"] = time.time()
    except Exception as e:
        log.error(f"Init Biotech: {e}")

    log.info("✅ Initialer Load abgeschlossen. Service läuft.")
    log.info(f"📅 Zeitplan: BI 3x/Tag, Crash+Biotech 2x/Tag, BTC-Div alle 2h, ORB 5min bei Open")

    # B-09: Cache cleanup at startup
    _cleanup_old_cache()
    last_cleanup = time.time()

    # ── Hauptschleife ──
    while _running:
        now = time.time()
        try:
            now_et = datetime.now(ET)
        except Exception:
            now_et = datetime.now()

        # ── Reset _today_done um Mitternacht ET ──
        _today_key = now_et.strftime("%Y-%m-%d")
        _done_keys = list(_today_done.keys())
        for dk in _done_keys:
            if _today_key not in dk:
                del _today_done[dk]

        # ── Cooldown-Cleanup (verhindert Memory Leak) ──
        _cleanup_email_cooldown()

        # B-09: Periodic cache cleanup (every 24h)
        if now - last_cleanup > 86400:
            _cleanup_old_cache()
            last_cleanup = now

        # ── Feste Zeitplan-Scanner (Aktien) — stündlich + E-Mail Alert ──
        for scanner_name in SCHEDULE_TIMES:
            should_run, slot_key = _check_fixed_schedule(scanner_name, now_et)
            if should_run:
                _running_scanners.add(scanner_name)  # B-05: Mark as running
                try:
                    log.info(f"⏰ {scanner_name} — geplante Zeit erreicht ({now_et.strftime('%H:%M')} ET)")
                    if scanner_name == "crash_monitor":
                        _fetch_crash_monitor(poly_key)
                    elif scanner_name == "bi_long":
                        _run_bi_scanner(poly_key, "long")
                        _check_and_alert_scan_results("bi_long", secrets)
                    elif scanner_name == "bi_short":
                        _run_bi_scanner(poly_key, "short")
                        _check_and_alert_scan_results("bi_short", secrets)
                    elif scanner_name == "biotech":
                        _run_biotech_scanner(poly_key)
                        _check_and_alert_scan_results("biotech", secrets)
                    elif scanner_name == "bear_scan":
                        _run_bear_scanner(poly_key, secrets)
                    elif scanner_name == "strategies":
                        _run_strategy_scanner(poly_key, secrets)
                    last_run[scanner_name] = time.time()
                    # B-04: Mark as done AFTER successful completion
                    _today_done[slot_key] = True
                except Exception as e:
                    log.error(f"❌ {scanner_name}: {e}")
                    _update_status(scanner_name, "error", str(e))
                finally:
                    _running_scanners.discard(scanner_name)  # B-05: Mark as not running
                time.sleep(5)

        # ── Interval-basierte Scanner (Crypto + ORB) ──
        for scanner_name, interval in SCHEDULE_INTERVAL.items():
            if now - last_run.get(scanner_name, 0) >= interval:
                # B-05: Check for overlap
                if scanner_name in _running_scanners:
                    log.debug(f"⚠️ {scanner_name} bereits im Betrieb, überspringe")
                    continue
                _running_scanners.add(scanner_name)
                try:
                    if scanner_name == "btc_divergence":
                        _run_btc_divergence(poly_key)
                    elif scanner_name == "new_listing":
                        _nls_results = _run_new_listing_scanner()
                        _alert_nls_signals(_nls_results, secrets)
                    elif scanner_name == "orb":
                        _run_orb_scanner(poly_key)
                        _check_and_alert_scan_results("orb", secrets)
                    last_run[scanner_name] = time.time()
                except Exception as e:
                    log.error(f"❌ {scanner_name}: {e}")
                    _update_status(scanner_name, "error", str(e))
                finally:
                    _running_scanners.discard(scanner_name)
                time.sleep(5)

        # 30 Sekunden schlafen
        for _ in range(30):
            if not _running: break
            time.sleep(1)

    if PID_FILE.exists():
        PID_FILE.unlink()
    _update_status("_service", "stopped")
    log.info("👋 Service beendet.")


def run_once():
    secrets = _load_secrets()
    poly_key = secrets.get("POLYGON_KEY", "")
    if not poly_key:
        print("❌ POLYGON_KEY fehlt!")
        return

    print("📡 Crash Monitor...")
    r = _fetch_crash_monitor(poly_key)
    if r: print(f"   Fear: {r.get('fear_score', '?')}/100")

    print("\n📡 BTC-Divergenz...")
    _run_btc_divergence(poly_key)

    print("\n📡 BI Scanner Long...")
    _run_bi_scanner(poly_key, "long")
    _check_and_alert_scan_results("bi_long", secrets)

    print("\n📡 Bear Scanner Short...")
    _run_bear_scanner(poly_key, secrets)
    # Bear Scanner hat eigene Alert-Logik in _run_bear_scanner()

    print("\n📡 Biotech Scanner...")
    try:
        _run_biotech_scanner(poly_key)
        _check_and_alert_scan_results("biotech", secrets)
    except Exception as e:
        print(f"   ❌ Biotech: {e}")

    print("\n📡 Strategien Scanner...")
    try:
        _run_strategy_scanner(poly_key, secrets)
    except Exception as e:
        print(f"   ❌ Strategien: {e}")

