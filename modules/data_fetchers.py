"""
Data Fetchers Module — Extrahiert aus scanner.py (V69.9)

Reine API-Funktionen ohne Streamlit-Abhängigkeiten:
- rate_limited_get: Rate-limiter für alle API-Calls
- CoinGecko: Candles, Historical Data
- Polygon.io: Stock Data, OHLCV, News, Details
- Alpaca: Realtime Prices
- Backtest: Daily Data
"""
import time
import threading
import requests
import datetime as dt
from datetime import datetime, timedelta

# Rate limiter state (thread-safe)
_rate_lock = threading.Lock()
_last_api_call = 0
_api_call_count = 0
_api_call_window_start = 0

# Candle analysis cache (in-memory, resets on restart)
_CANDLE_ANALYSIS_CACHE = {}
_CANDLE_CACHE_TTL = 300  # 5 Minuten

# BPIQ catalyst cache — real loader is in scanner.py (needs st.secrets)
# This stub returns empty dict when called standalone
_BPIQ_CATALYST_CACHE = {}
_BPIQ_CACHE_TIMESTAMP = 0

def _load_bpiq_catalyst_cache():
    """Stub — real implementation in scanner.py (needs Streamlit secrets)."""
    return _BPIQ_CATALYST_CACHE

# Catalyst detection keywords (used by _detect_catalyst)
CATALYST_KEYWORDS = {
    " EARNINGS": {"keywords": ["earnings", "revenue", "profit", "EPS", "guidance", "quarterly", "fiscal", "beat", "miss", "outlook"], "sentiment": "neutral"},
    " FDA/BIO": {"keywords": ["FDA", "approval", "trial", "phase", "drug", "clinical", "PDUFA", "NDA", "breakthrough", "therapy", "patent"], "sentiment": "neutral"},
    "[!!] OFFERING": {"keywords": ["offering", "dilution", "shelf", "secondary", "ATM", "warrant", "convertible", "raise", "registered direct", "public offering"], "sentiment": "bearish"},
    " M&A": {"keywords": ["acquisition", "merger", "takeover", "buyout", "deal", "purchase agreement"], "sentiment": "bullish"},
    " CONTRACT": {"keywords": ["contract", "awarded", "partnership", "agreement", "collaboration", "deal with"], "sentiment": "bullish"},
    " LEGAL": {"keywords": ["lawsuit", "SEC", "investigation", "settlement", "subpoena", "fraud", "class action", "indictment"], "sentiment": "bearish"},
    "UP UPGRADE": {"keywords": ["upgrade", "price target", "buy rating", "overweight", "outperform"], "sentiment": "bullish"},
    "DN DOWNGRADE": {"keywords": ["downgrade", "sell rating", "underweight", "underperform", "cut"], "sentiment": "bearish"},
    "[!!] REVERSE SPLIT": {"keywords": ["reverse split", "reverse stock split", "r/s"], "sentiment": "bearish"},
    " STOCK SPLIT": {"keywords": ["stock split", "forward split"], "sentiment": "bullish"},
    " DIVIDEND": {"keywords": ["dividend", "payout", "distribution"], "sentiment": "bullish"},
    " INSIDER": {"keywords": ["insider", "CEO buy", "director purchase", "10b5"], "sentiment": "bullish"},
    "[>>] PRODUCT": {"keywords": ["launch", "release", "new product", "unveil", "announce"], "sentiment": "bullish"},
    " BANKRUPTCY": {"keywords": ["bankruptcy", "chapter 11", "chapter 7", "delisting", "going concern"], "sentiment": "bearish"},
}


# ── rate_limited_get (originally line 895) ──
def rate_limited_get(url, params=None, timeout=15, calls_per_minute=200, **kwargs):
    """Rate-limited requests.get() — thread-safe, wartet automatisch wenn zu viele Calls.

    Default 200 calls/min (Polygon paid plans erlauben deutlich mehr als 75).
    Akzeptiert alle kwargs die requests.get() auch akzeptiert (headers, etc.)
    """
    global _last_api_call, _api_call_count, _api_call_window_start

    sleep_time = 0
    with _rate_lock:
        now = time.time()

        # Reset Counter jede Minute
        if now - _api_call_window_start > 60:
            _api_call_count = 0
            _api_call_window_start = now

        # Warte wenn Limit erreicht
        if _api_call_count >= calls_per_minute:
            sleep_time = max(0, 60 - (now - _api_call_window_start))

        # Minimum 0.05s zwischen Calls (20/sec max)
        if sleep_time == 0:
            elapsed = now - _last_api_call
            if elapsed < 0.05:
                sleep_time = 0.05 - elapsed

    # Sleep AUSSERHALB des Locks (andere Threads nicht blockieren)
    if sleep_time > 0:
        time.sleep(sleep_time)

    with _rate_lock:
        # Nach dem Sleep: Counter ggf. resetten
        now = time.time()
        if now - _api_call_window_start > 60:
            _api_call_count = 0
            _api_call_window_start = now
        _last_api_call = now
        _api_call_count += 1

    return requests.get(url, params=params, timeout=timeout, **kwargs)


# ── fetch_daily_candles_crypto (originally line 1358) ──
def fetch_daily_candles_crypto(coin_id, days=30):
    """
    Holt Daily Candles für Krypto von CoinGecko mit Cache (5 Min TTL).
    Konvertiert in gleiches Format wie Polygon: list of dicts mit o, h, l, c, v
    """
    import time as _t

    cache_key = f"crypto_{coin_id}_{days}"
    cached = _CANDLE_ANALYSIS_CACHE.get(cache_key)
    if cached and (_t.time() - cached["ts"]) < _CANDLE_CACHE_TTL:
        return cached["data"]

    try:
        # CoinGecko market_chart gibt stündliche Daten für days <= 90
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": min(days, 90)}
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        prices = data.get("prices", [])
        volumes = data.get("total_volumes", [])

        if not prices or len(prices) < 24:
            return []

        # Stündliche Daten zu Daily OHLCV aggregieren
        from datetime import datetime as _dt
        daily = {}
        for i, (ts, p) in enumerate(prices):
            day_key = _dt.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            vol = volumes[i][1] if i < len(volumes) and len(volumes[i]) > 1 else 0
            if day_key not in daily:
                daily[day_key] = {"o": p, "h": p, "l": p, "c": p, "v": vol, "t": ts}
            else:
                daily[day_key]["h"] = max(daily[day_key]["h"], p)
                daily[day_key]["l"] = min(daily[day_key]["l"], p)
                daily[day_key]["c"] = p
                daily[day_key]["v"] += vol

        bars = [daily[k] for k in sorted(daily.keys())]
        _CANDLE_ANALYSIS_CACHE[cache_key] = {"data": bars, "ts": _t.time()}
        return bars
    except Exception:
        return []


# ── fetch_daily_candles (originally line 1406) ──
def fetch_daily_candles(poly_key, ticker, days=30):
    """
    Holt Daily Candles von Polygon mit Cache (5 Min TTL).
    Returns: list of dicts mit o, h, l, c, v, t oder leere Liste
    """
    import time as _t
    from datetime import datetime as _dt, timedelta as _td

    cache_key = f"{ticker}_{days}"
    cached = _CANDLE_ANALYSIS_CACHE.get(cache_key)
    if cached and (_t.time() - cached["ts"]) < _CANDLE_CACHE_TTL:
        return cached["data"]

    try:
        end_date = _dt.utcnow().date()
        start_date = end_date - _td(days=days + 10)  # Extra Buffer für Wochenenden/Feiertage
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": days + 10}, timeout=5)
        if resp.status_code != 200:
            return []
        bars = resp.json().get("results", [])
        _CANDLE_ANALYSIS_CACHE[cache_key] = {"data": bars, "ts": _t.time()}
        return bars
    except Exception:
        return []


# ── fetch_multi_day_data (originally line 1461) ──
def fetch_multi_day_data(ticker, api_key, days=5):
    """
    Holt Multi-Day OHLCV Daten von Polygon für echte Pattern-Analyse.
    
    Returns: Liste von Dictionaries mit {date, open, high, low, close, volume}
             Sortiert von ältestem zu neuestem Tag
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        # Buffer fuer Wochenenden + Feiertage: ~1.5x fuer kurze, ~1.4x fuer laengere Zeitraeume
        buffer = max(7, int(days * 0.5))
        start_date = end_date - timedelta(days=days + buffer)
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"adjusted": "true", "sort": "asc", "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return []
        
        results = []
        for bar in data["results"][-days:]:  # Letzte N Tage
            results.append({
                "date": datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"),
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar["v"]
            })
        
        return results
    except Exception as e:
        return []


# ── fetch_historical_data_crypto (originally line 6104) ──
def fetch_historical_data_crypto(coin_id, days):
    """
    Holt historische OHLC-Daten von CoinGecko via market_chart (hourly → daily aggregation).

    CoinGecko /ohlc Endpoint gibt für >30 Tage nur 4-Tages-Candles (zu wenig Daten).
    Stattdessen: /market_chart mit days≤90 gibt stündliche Preise (24/Tag),
    die wir zu echten täglichen OHLC-Bars aggregieren.

    Returns: [[timestamp_ms, open, high, low, close], ...] — tägliche Bars
    """
    from datetime import datetime as _dt

    # CoinGecko gibt stündliche Daten nur für days ≤ 90
    fetch_days = min(days, 90)

    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": fetch_days}

        # CoinGecko Free API: ~10 Calls/Min → 7s Pause + Retry bei 429
        for attempt in range(3):
            time.sleep(7 if attempt > 0 else 2)  # 2s vor erstem Call, 7s bei Retry
            resp = rate_limited_get(url, params=params, timeout=15)
            if resp.status_code == 200:
                break
            elif resp.status_code == 429:
                time.sleep(15)  # Extra Cooldown bei Rate Limit
                continue
            else:
                return None

        if resp.status_code != 200:
            return None

        data = resp.json()
        prices = data.get("prices", [])
        if not prices or len(prices) < 48:  # Mindestens 2 Tage stündliche Daten
            return None

        # Prüfe ob wir wirklich stündliche Daten haben (Intervall < 4h)
        if len(prices) > 1:
            interval_h = (prices[1][0] - prices[0][0]) / 3_600_000
            if interval_h > 4:  # Tägliche Daten statt stündliche → kein echtes H/L
                return None

        # Aggregiere stündliche Preise zu täglichen OHLC-Bars
        daily_map = {}
        for ts, price in prices:
            day_key = _dt.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if day_key not in daily_map:
                daily_map[day_key] = {"ts": ts, "open": price, "high": price, "low": price, "close": price}
            else:
                daily_map[day_key]["high"] = max(daily_map[day_key]["high"], price)
                daily_map[day_key]["low"] = min(daily_map[day_key]["low"], price)
                daily_map[day_key]["close"] = price

        # Konvertiere zurück zu [[ts, o, h, l, c], ...] Format
        result = []
        for day_key in sorted(daily_map.keys()):
            d = daily_map[day_key]
            result.append([d["ts"], d["open"], d["high"], d["low"], d["close"]])

        return result if len(result) >= 5 else None

    except Exception:
        return None


# ── fetch_historical_data_stocks (originally line 6171) ──
def fetch_historical_data_stocks(ticker, days, poly_key):
    """Holt historische Daten — Polygon für US, Yahoo für internationale Aktien"""
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _is_intl = any(ticker.upper().endswith(s) for s in _intl_suffixes)
    
    if _is_intl:
        return _fetch_historical_yahoo(ticker, days)
    
    # US-Aktien: Polygon
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "limit": days}
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                return [[r["t"], r["o"], r["h"], r["l"], r["c"], r.get("v", 0)] for r in results]
    except Exception as e:
        pass
    return None


# ── fetch_ohlcv_for_chart (originally line 6262) ──
def fetch_ohlcv_for_chart(ticker, poly_key, timeframe="1H", bars=300):
    """
    Holt OHLCV Daten für Chart-Darstellung.
    
    V67.5: Yahoo Finance Fallback für internationale Aktien + Forex + Futures + Krypto
    
    Args:
        ticker: Ticker Symbol (z.B. "AAPL", "VNA.DE", "EURUSD=X", "BTC-USD")
        poly_key: Polygon API Key
        timeframe: "5m", "15m", "1H", "4H", "1D", "1W"
        bars: Anzahl Bars (wird pro Timeframe angepasst)
        
    Returns:
        List of dicts with time, open, high, low, close, volume
    """
    # Erkennung: Ist das ein internationaler/Yahoo-Ticker?
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _yahoo_patterns = ("=X", "=F", "-USD", "-EUR", "-GBP")
    _is_yahoo = any(ticker.upper().endswith(s) for s in _intl_suffixes + _yahoo_patterns)
    
    # Krypto-Tickers (CoinGecko IDs → Yahoo Format)
    if not _is_yahoo and ticker.upper() in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC"):
        ticker = f"{ticker.upper()}-USD"
        _is_yahoo = True
    
    if _is_yahoo:
        return _fetch_ohlcv_yahoo(ticker, timeframe)
    else:
        return _fetch_ohlcv_polygon(ticker, poly_key, timeframe)


# ── fetch_realtime_price_alpaca (originally line 8045) ──
def fetch_realtime_price_alpaca(ticker, alpaca_key, alpaca_secret):
    """
    Holt REALTIME Preis von Alpaca (kostenlos mit Account!)
    
    Alpaca bietet kostenlose Realtime-Daten für US-Aktien.
    Erstelle Account auf alpaca.markets und hole API Keys.
    
    Returns: dict mit price, change, change_pct, timestamp oder None
    """
    try:
        # Alpaca Latest Quote API
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest"
        headers = {
            "APCA-API-KEY-ID": alpaca_key,
            "APCA-API-SECRET-KEY": alpaca_secret
        }
        
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            quote = data.get("quote", {})
            
            # Bid/Ask Midpoint als Preis
            bid = quote.get("bp", 0)
            ask = quote.get("ap", 0)
            
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2
                timestamp = quote.get("t", "")
                
                return {
                    "price": round(price, 2),
                    "bid": bid,
                    "ask": ask,
                    "spread": round(ask - bid, 4),
                    "timestamp": timestamp,
                    "source": "Alpaca Realtime"
                }
        
        # Fallback: Latest Trade
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            trade = data.get("trade", {})
            price = trade.get("p", 0)
            
            if price > 0:
                return {
                    "price": round(price, 2),
                    "timestamp": trade.get("t", ""),
                    "source": "Alpaca Realtime"
                }
    except Exception as e:
        pass
    return None


# ── fetch_realtime_price_polygon (originally line 8103) ──
def fetch_realtime_price_polygon(ticker, poly_key):
    """
    Holt REALTIME Preis von Polygon (benötigt Stocks Starter oder höher!)
    
    Nutzt den Single-Ticker Snapshot für schnellste Updates.
    
    Returns: dict mit price, change_pct, volume oder None
    """
    try:
        # Single Ticker Snapshot = schnellster Realtime-Endpoint
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        params = {"apiKey": poly_key}
        
        resp = rate_limited_get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            ticker_data = data.get("ticker", {})
            
            if ticker_data:
                last_trade = ticker_data.get("lastTrade", {})
                day = ticker_data.get("day", {})
                prev = ticker_data.get("prevDay", {})
                
                # Preis aus lastTrade (REALTIME!)
                price = last_trade.get("p", 0) or day.get("c", 0)
                
                if price > 0:
                    prev_close = prev.get("c", 0)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    
                    # Timestamp vom letzten Trade
                    timestamp = last_trade.get("t", 0)
                    if timestamp:
                        # Nanoseconds to datetime
                        from datetime import datetime
                        try:
                            ts_seconds = timestamp / 1e9
                            trade_time = datetime.fromtimestamp(ts_seconds)
                            time_str = trade_time.strftime("%H:%M:%S")
                        except Exception as e:
                            time_str = ""
                    else:
                        time_str = ""
                    
                    return {
                        "price": round(price, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": day.get("v", 0),
                        "high": day.get("h", price),
                        "low": day.get("l", price),
                        "time": time_str,
                        "source": "Polygon Realtime"
                    }
    except Exception as e:
        pass
    return None


# ── get_ticker_news (originally line 10461) ──
def get_ticker_news(poly_key, ticker, limit=3):
    """
    Holt die neuesten News für einen Ticker via Polygon News API.
    NEU: Katalysator-Erkennung (Earnings, FDA, Offering, etc.)
    Returns: List of news items with title, sentiment, published date, catalyst
    """
    
    try:
        url = f"https://api.polygon.io/v2/reference/news"
        resp = rate_limited_get(url, params={"ticker": ticker, "limit": limit, "apiKey": poly_key}, timeout=5).json()
        results = resp.get("results", [])
        
        news_items = []
        detected_catalysts = []
        
        for item in results[:limit]:
            # Parse published date
            pub_date = item.get("published_utc", "")[:10]  # YYYY-MM-DD
            
            # Sentiment analysieren (wenn vorhanden)
            insights = item.get("insights", [])
            sentiment = "neutral"
            sentiment_score = 0
            for insight in insights:
                if insight.get("ticker") == ticker:
                    sentiment = insight.get("sentiment", "neutral")
                    sentiment_score = insight.get("sentiment_reasoning", "")
                    break
            
            # Katalysator erkennen
            title = item.get("title", "")
            catalyst = _detect_catalyst(title)
            if catalyst and catalyst not in detected_catalysts:
                detected_catalysts.append(catalyst)
            
            news_items.append({
                "title": title[:80],  # Kürzen
                "publisher": item.get("publisher", {}).get("name", ""),
                "published": pub_date,
                "sentiment": sentiment,
                "url": item.get("article_url", ""),
                "catalyst": catalyst,
            })
        
        # Haupt-Katalysator an alle News-Items anhängen
        for n in news_items:
            n["all_catalysts"] = detected_catalysts
        
        return news_items
    except Exception as e:
        return []


# ── get_ticker_details (originally line 10514) ──
def get_ticker_details(poly_key, ticker):
    """
    Holt Ticker Details: Shares Outstanding, Market Cap, etc.
    Returns: dict mit shares_outstanding, market_cap, float_category
    """
    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=5).json()
        results = resp.get("results", {})
        
        shares_out = results.get("share_class_shares_outstanding", 0) or results.get("weighted_shares_outstanding", 0)
        market_cap = results.get("market_cap", 0)
        
        # Float Kategorie schätzen (Shares Outstanding als Proxy)
        # Echtes Float = Shares - Insider - Institutional, aber das haben wir nicht
        float_category = "UNKNOWN"
        float_emoji = "?"
        
        if shares_out > 0:
            shares_millions = shares_out / 1_000_000
            if shares_millions < 10:
                float_category = "MICRO"
                float_emoji = "[*][*][*]"  # Sehr explosiv
            elif shares_millions < 20:
                float_category = "LOW"
                float_emoji = "[*][*]"  # Explosiv
            elif shares_millions < 50:
                float_category = "MEDIUM"
                float_emoji = "[*]"
            else:
                float_category = "HIGH"
                float_emoji = ""
        
        return {
            "shares_outstanding": shares_out,
            "shares_millions": round(shares_out / 1_000_000, 1) if shares_out > 0 else 0,
            "market_cap": market_cap,
            "market_cap_millions": round(market_cap / 1_000_000, 1) if market_cap > 0 else 0,
            "float_category": float_category,
            "float_emoji": float_emoji,
            "name": results.get("name", ""),
            "description": results.get("description", "")[:100] if results.get("description") else ""
        }
    except Exception as e:
        return {
            "shares_outstanding": 0,
            "shares_millions": 0,
            "market_cap": 0,
            "market_cap_millions": 0,
            "float_category": "UNKNOWN",
            "float_emoji": "?",
            "name": "",
            "description": ""
        }


# ── fetch_backtest_daily_data (originally line 12113) ──
def fetch_backtest_daily_data(poly_key, ticker, start_date, end_date):
    """
    Holt tägliche OHLCV-Daten von Polygon für Backtesting.
    Includes retry logic für Rate Limits (429).
    """
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": poly_key}
    
    for attempt in range(3):
        try:
            resp = rate_limited_get(url, params=params, timeout=15)
            
            if resp.status_code == 429:
                time.sleep(12 + attempt * 5)
                continue
            
            if resp.status_code != 200:
                # Speichere Fehler für Debug-Anzeige
                _err = f"{ticker}: HTTP {resp.status_code}"
                try:
                    _err += f" | {resp.text[:150]}"
                except:
                    pass
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            data = resp.json()
            
            if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
                _err = f"{ticker}: status={data.get('status')} results={data.get('resultsCount',0)} | {data.get('error','')}{data.get('message','')}"
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            bars = []
            for r in data["results"]:
                ts = r.get("t", 0)
                dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
                bars.append({
                    "date": dt,
                    "open": r.get("o", 0),
                    "high": r.get("h", 0),
                    "low": r.get("l", 0),
                    "close": r.get("c", 0),
                    "volume": r.get("v", 0),
                    "vwap": r.get("vw", 0)
                })
            return bars
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            continue
    
    return []


# ── fetch_grouped_daily (originally line 12172) ──
def fetch_grouped_daily(poly_key, date_str):
    """Holt ALLE US-Aktien für einen Tag (Grouped Daily Bars)."""
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    params = {"apiKey": poly_key, "adjusted": "true"}
    try:
        resp = rate_limited_get(url, params=params, timeout=30)
        data = resp.json()
        if data.get("status") in ("OK", "DELAYED") and data.get("results"):
            return {r["T"]: r for r in data["results"] if r.get("c", 0) > 0}
        return {}
    except:
        return {}



# ── Weitere Data-Fetcher (V70.4) ──

def get_binance_tradingview_symbol(coin_symbol):
    """
    Convert CoinGecko symbol to TradingView BINANCE pair.

    Handles:
    - Stablecoins (USDT, USDC, BUSD, DAI, TUSD, etc.) → Use BNB or ETH instead
    - Symbols with different Binance names (MIOTA → IOTA)
    - Missing USDT pairs → Try BUSD or other alternatives

    Args:
        coin_symbol: CoinGecko symbol (e.g., "BTC", "ETH", "USDT", "MIOTA")

    Returns:
        TradingView symbol ready for BINANCE: prefix (e.g., "BTCUSDT", "ETHUSDT")
    """
    if not coin_symbol:
        return "BTCUSDT"  # Safe fallback

    coin_symbol = coin_symbol.upper().strip()

    # Mapping for symbols that differ between CoinGecko and Binance
    special_mappings = {
        "MIOTA": "IOTA",        # IOTA token
        "IOT": "IOTA",          # Alternative
        "XDG": "DOGE",          # Dogecoin alternative
        "VET": "VET",           # Vechain
        "ONE": "ONE",           # Harmony
        "SCRT": "SCRT",         # Secret
        "RUNE": "RUNE",         # Thorchain
    }

    # Stablecoins - use major trading pairs instead
    stablecoins = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDN", "USDP", "GUSD", "PAX"}
    if coin_symbol in stablecoins:
        # Use BNBUSDT or ETHUSDT instead of problematic pairs
        return "BNBUSDT"

    # Apply special mappings
    symbol = special_mappings.get(coin_symbol, coin_symbol)

    # Default: SYMBOL + USDT
    return f"{symbol}USDT"


def _get_bpiq_catalysts(ticker):
    """
    Holt BPIQ-Catalyst-Daten für einen Ticker.
    Returns: dict kompatibel mit dem bestehenden Readout-System.

    Felder:
    - readout_score: 0-15 (Bonus für Score-Berechnung)
    - readout_label: UI-Label ([-] PDUFA, [~] Readout etc.)
    - catalyst_readouts: Liste der Catalyst-Events
    - bpiq_available: True wenn BPIQ-Daten vorhanden
    """
    cache = _load_bpiq_catalyst_cache()
    drugs = cache.get(ticker.upper(), [])

    if not drugs:
        return {
            "readout_score": 0,
            "readout_label": "",
            "catalyst_readouts": [],
            "bpiq_available": False,
        }

    # Score berechnen: Gewichtet nach Kategorie und Phase
    readout_score = 0
    catalyst_readouts = []

    for drug in drugs:
        cat = drug["category"]
        pm = drug["phase_mult"]

        if cat == "OVERDUE":
            readout_score += 4 * pm
        elif cat == "IMMINENT":
            readout_score += 5 * pm   # BPIQ-IMMINENT ist stärker als CT.gov (kuratiert!)
        elif cat == "UPCOMING":
            readout_score += 2 * pm
        elif cat == "LATER":
            readout_score += 0.5 * pm

        if cat in ("OVERDUE", "IMMINENT", "UPCOMING"):
            catalyst_readouts.append(drug)

    readout_score = min(15, int(readout_score))

    # Label: Bestes (nächstes) Event
    readout_label = ""
    if catalyst_readouts:
        top = catalyst_readouts[0]
        days = top["days_until"]
        stage = top["full_label"]
        drug_name = top["drug_name"][:25]
        cat = top["category"]

        if "PDUFA" in stage:
            # PDUFA = besonders wichtig, eigenes Format
            if cat == "IMMINENT":
                readout_label = f"[-] PDUFA {top['catalyst_date_text']} — {drug_name}"
            elif cat == "UPCOMING":
                readout_label = f"[~] PDUFA in {days}d — {drug_name}"
            elif cat == "OVERDUE":
                readout_label = f"[-] PDUFA ÜBERFÄLLIG ({abs(days)}d) — {drug_name}"
        else:
            if cat == "OVERDUE":
                readout_label = f"[-] {stage} ÜBERFÄLLIG ({abs(days)}d) — {drug_name}"
            elif cat == "IMMINENT":
                readout_label = f"[~] {stage} in {days}d — {drug_name}"
            elif cat == "UPCOMING":
                readout_label = f"[+] {stage} in {days}d — {drug_name}"

    return {
        "readout_score": readout_score,
        "readout_label": readout_label,
        "catalyst_readouts": catalyst_readouts[:5],
        "bpiq_available": True,
    }


def _calculate_biotech_catalyst_score(catalyst_score, pipeline_score, technical_score, risk_score, news_momentum_score, rvol=0):
    """
    Berechnet den finalen Biotech Catalyst Score (0-100).

    Bonus: Catalyst + Volume Confirmation = stärkeres Signal.
    Wenn ein Catalyst gefunden wird UND das Volumen ungewöhnlich hoch ist,
    ist das Signal deutlich stärker (Smart Money bestätigt die News).
    """
    # Weighted: Catalyst is primary driver (2x weight)
    total = (catalyst_score * 2) + pipeline_score + technical_score + risk_score + news_momentum_score
    # Normalize to 0-100 scale (max: 60 + 0 + 20 + 15 + 15 = 110)
    total = min(100, int(total * 100 / 110))

    # Catalyst-Volume Confirmation Bonus (max 10 Extra-Punkte)
    if catalyst_score > 0 and rvol >= 1.5:
        if rvol >= 3.0:
            total = min(100, total + 10)  # Extrem: Catalyst + 3x Volume = Hot
        elif rvol >= 2.0:
            total = min(100, total + 7)   # Stark: Catalyst + 2x Volume
        else:
            total = min(100, total + 4)   # Moderat: Catalyst + 1.5x Volume

    # V68: Finaler Cap NACH allen Boni — kein Score über 100
    # Readout-Bonus wird VOR dem Cap addiert (nicht danach)
    return min(100, max(0, total))


def _detect_catalyst(title):
    """Erkennt Katalysator-Typ aus News-Titel."""
    title_lower = title.lower()
    for catalyst_type, cat_data in CATALYST_KEYWORDS.items():
        for kw in cat_data["keywords"]:
            if kw.lower() in title_lower:
                return catalyst_type
    return None


def _fetch_historical_yahoo(ticker, days):
    """Yahoo Finance historische Daily-Daten für internationale Aktien"""
    try:
        # Days to Yahoo range string
        if days <= 30:
            yf_range = "1mo"
        elif days <= 90:
            yf_range = "3mo"
        elif days <= 180:
            yf_range = "6mo"
        elif days <= 365:
            yf_range = "1y"
        elif days <= 730:
            yf_range = "2y"
        else:
            yf_range = "5y"
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Format: [[timestamp_ms, open, high, low, close, volume], ...]
        result = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            result.append([
                timestamps[i] * 1000,  # seconds → ms für Kompatibilität
                opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                closes[i],
                volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            ])
        
        return result if result else None
    except Exception:
        return None


def _fetch_ohlcv_yahoo(ticker, timeframe="1H"):
    """Yahoo Finance OHLCV für internationale Aktien, Forex, Futures, Krypto."""
    try:
        # Yahoo Finance interval & range mapping
        tf_map = {
            "5m":  ("5m",  "60d",  500),    # 60 Tage 5-min
            "15m": ("15m", "60d",  500),    # 60 Tage 15-min
            "1H":  ("1h",  "730d", 500),    # 2 Jahre 1H
            "4H":  ("1h",  "730d", 500),    # 2 Jahre 1H → aggregiere zu 4H
            "1D":  ("1d",  "2y",   500),    # 2 Jahre Daily
            "1W":  ("1wk", "5y",   260),    # 5 Jahre Weekly
            "1M":  ("1mo", "max",  120),    # Max Monthly
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        yf_interval, yf_range, max_bars = tf_map[timeframe]
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": yf_interval, "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Build raw bars (filter None values)
        raw_bars = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            raw_bars.append({
                "t": timestamps[i],
                "o": opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                "h": highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                "l": lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                "c": closes[i],
                "v": volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            })
        
        if not raw_bars:
            return None
        
        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            aggregated = []
            for i in range(0, len(raw_bars), 4):
                chunk = raw_bars[i:i+4]
                if chunk:
                    aggregated.append({
                        "t": chunk[0]["t"],
                        "o": chunk[0]["o"],
                        "h": max(c["h"] for c in chunk),
                        "l": min(c["l"] for c in chunk),
                        "c": chunk[-1]["c"],
                        "v": sum(c.get("v", 0) for c in chunk)
                    })
            raw_bars = aggregated
        
        # Formatiere für Lightweight Charts
        effective_bars = min(max_bars, len(raw_bars))
        ohlcv = []
        for bar in raw_bars[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"],  # Yahoo timestamps sind schon in Sekunden
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv if ohlcv else None
        
    except Exception as e:
        return None


def _fetch_ohlcv_polygon(ticker, poly_key, timeframe="1H"):
    """Polygon.io OHLCV für US-Aktien."""
    try:
        # Timeframe mapping: (multiplier, span, days_back, max_bars)
        tf_map = {
            "5m": ("5", "minute", 7, 500),
            "15m": ("15", "minute", 21, 500),
            "1H": ("1", "hour", 90, 500),
            "4H": ("1", "hour", 180, 500),
            "1D": ("1", "day", 730, 500),
            "1W": ("1", "week", 1825, 260),
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        mult, span, days_back, max_bars = tf_map[timeframe]
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50000}
        
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        results = data.get("results", [])
        
        if not results:
            return None
        
        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            aggregated = []
            for i in range(0, len(results), 4):
                chunk = results[i:i+4]
                if len(chunk) >= 1:
                    aggregated.append({
                        "t": chunk[0]["t"],
                        "o": chunk[0]["o"],
                        "h": max(c["h"] for c in chunk),
                        "l": min(c["l"] for c in chunk),
                        "c": chunk[-1]["c"],
                        "v": sum(c.get("v", 0) for c in chunk)
                    })
            results = aggregated
        
        effective_bars = min(max_bars, len(results))
        ohlcv = []
        for bar in results[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"] // 1000,  # Polygon ms → seconds
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv
        
    except Exception as e:
        return None


