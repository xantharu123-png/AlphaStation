"""
Pre-Market Module — PM Evaluation, Session Bars, SPY Tracking (V70.0)
"""
import os
import json
import time
import datetime as dt
from datetime import datetime, timedelta
try:
    import pytz
except ImportError:
    pytz = None
from modules.data_fetchers import rate_limited_get
from modules.scorers import calculate_pm_quality_score
from modules.strategies import classify_pm_setup

# Constants
PM_TRACKER_FILE = "/tmp/alpha_station_pm_tracker.json"

def _debug_log(msg, error=None):
    """Simple debug logger (stub — real version in scanner.py logs to UI)."""
    pass  # Silent in module context


def get_pm_session_bars(poly_key, ticker, date_str):
    """
    Holt die Pre-Market Session Bars (4:00-9:30 ET) via Aggregates API.
    DST-KORRIGIERT: Nutzt pytz für korrekte ET → UTC Konvertierung.
    Returns: dict mit pm_high, pm_low, pm_volume, pm_open, pm_vwap
    """
    try:
        # 1-Minute Bars für PM Session
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10).json()
        bars = resp.get("results", [])
        
        if not bars:
            return None
        
        # DST-KORRIGIERT: Berechne PM Session Grenzen dynamisch
        # 4:00 AM ET und 9:30 AM ET → UTC konvertieren (funktioniert für EST und EDT)
        et_tz = pytz.timezone('America/New_York')
        trade_date = datetime.strptime(date_str, "%Y-%m-%d")
        
        # PM Start: 4:00 AM ET an diesem Tag
        pm_start_et = et_tz.localize(trade_date.replace(hour=4, minute=0, second=0))
        pm_start_utc = pm_start_et.astimezone(pytz.utc)
        pm_start_ts = pm_start_utc.timestamp()
        
        # PM End: 9:30 AM ET an diesem Tag
        pm_end_et = et_tz.localize(trade_date.replace(hour=9, minute=30, second=0))
        pm_end_utc = pm_end_et.astimezone(pytz.utc)
        pm_end_ts = pm_end_utc.timestamp()
        
        # Filtere Bars innerhalb PM Session
        pm_bars = []
        for bar in bars:
            bar_ts = bar.get("t", 0) / 1000  # ms to seconds
            if pm_start_ts <= bar_ts <= pm_end_ts:
                pm_bars.append(bar)
        
        if not pm_bars:
            return None
        
        pm_high = max(b.get("h", 0) for b in pm_bars)
        pm_low = min(b.get("l", 999999) for b in pm_bars)
        pm_volume = sum(b.get("v", 0) for b in pm_bars)
        pm_open = pm_bars[0].get("o", 0)
        pm_close = pm_bars[-1].get("c", 0)
        
        # VWAP Berechnung
        total_value = sum(b.get("vw", b.get("c", 0)) * b.get("v", 0) for b in pm_bars)
        pm_vwap = total_value / pm_volume if pm_volume > 0 else pm_close
        
        # Erste Bewegung (wann kam der Move?) - jetzt in ET anzeigen
        first_big_move_time = None
        for bar in pm_bars:
            if abs((bar.get("c", 0) - pm_open) / pm_open * 100) > 2 if pm_open > 0 else False:
                ts = bar.get("t", 0) / 1000
                move_utc = datetime.utcfromtimestamp(ts).replace(tzinfo=pytz.utc)
                move_et = move_utc.astimezone(et_tz)
                first_big_move_time = move_et.strftime("%H:%M ET")
                break
        
        return {
            "pm_high": pm_high,
            "pm_low": pm_low if pm_low < 999999 else pm_close,
            "pm_volume": pm_volume,
            "pm_open": pm_open,
            "pm_close": pm_close,
            "pm_vwap": pm_vwap,
            "pm_bars_count": len(pm_bars),
            "first_move_time": first_big_move_time
        }
        
    except Exception as e:
        return None


def get_spy_pm_change(poly_key):
    """Holt SPY Pre-Market Change für Relative Strength Berechnung."""
    try:
        url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/SPY"
        resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=10).json()
        ticker_data = resp.get("ticker", {})
        
        prev_close = ticker_data.get("prevDay", {}).get("c", 0)
        last_price = ticker_data.get("lastTrade", {}).get("p", 0)
        
        if prev_close > 0 and last_price > 0:
            return ((last_price - prev_close) / prev_close) * 100
        return 0
    except Exception as e:
        return 0


def _save_pm_setups(pm_data):
    """Speichert PM Setups mit Timestamp für späteres Tracking"""
    try:
        # Lade bestehende Daten
        existing = _load_pm_tracker()
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Vereinfache die Setups für JSON
        simplified = []
        for item in pm_data:
            setups_clean = []
            for s in item.get("Setups", []):
                setups_clean.append({
                    "name": s.get("name", ""),
                    "emoji": s.get("emoji", ""),
                    "entry": round(s.get("entry", 0), 2),
                    "stop": round(s.get("stop", 0), 2),
                    "tp1": round(s.get("tp1", 0), 2),
                    "tp2": round(s.get("tp2", 0), 2),
                    "risk": round(s.get("risk", 0), 2),
                    "risk_pct": round(s.get("risk_pct", 0), 1),
                })
            
            simplified.append({
                "ticker": item["Ticker"],
                "direction": "LONG" if item["PM_Chg%"] > 0 else "SHORT",
                "pm_change": item["PM_Chg%"],
                "pm_price": item["PM_Preis"],
                "pm_high": item["PM_High"],
                "pm_low": item["PM_Low"],
                "pm_vwap": item["PM_VWAP"],
                "gap_pct": item.get("Gap%", 0),
                "setup_type": item.get("Setup_Type", ""),
                "entry_signal": item.get("Entry_Signal", ""),
                "primary_idx": item.get("Primary_Idx", 0),
                "alt_idx": item.get("Alt_Idx", 1),
                "setups": setups_clean,
                "results": None,  # Wird nach Auswertung gefüllt
            })
        
        existing[today] = {
            "date": today,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(simplified),
            "tickers": simplified,
        }
        
        # Behalte nur die letzten 30 Tage
        sorted_dates = sorted(existing.keys(), reverse=True)[:30]
        existing = {d: existing[d] for d in sorted_dates}
        
        with open(PM_TRACKER_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        
        return True
    except Exception as e:
        _debug_log("PM Tracker save failed", error=e)
        return False


def _load_pm_tracker():
    """Lädt alle gespeicherten PM Tracker Daten"""
    try:
        with open(PM_TRACKER_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def evaluate_pm_setups(poly_key, date_str, setups_data):
    """
    Wertet PM Setups gegen echte Intraday-Daten aus.
    
    Holt 5-Minuten Bars für Regular Session (9:30-16:00 ET).
    Simuliert jedes Setup: Entry getriggert? Stop oder TP1/TP2 zuerst?
    
    Returns: Liste von Setup-Ergebnissen
    """
    results = []
    
    if not setups_data or not poly_key:
        return results
    
    tickers = setups_data.get("tickers", [])
    
    for item in tickers[:20]:  # Max 20 Ticker auswerten (API Limit)
        ticker = item["ticker"]
        direction = item["direction"]
        
        try:
            # 5-Min Bars für den ganzen Tag holen
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{date_str}/{date_str}"
            resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)
            
            if resp.status_code != 200:
                continue
            
            bars = resp.json().get("results", [])
            if not bars:
                continue
            
            # Filtere Regular Session (9:30-16:00 ET)
            et_tz = pytz.timezone('America/New_York')
            trade_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            rs_start_et = et_tz.localize(trade_date.replace(hour=9, minute=30))
            rs_start_ts = rs_start_et.astimezone(pytz.utc).timestamp()
            rs_end_et = et_tz.localize(trade_date.replace(hour=16, minute=0))
            rs_end_ts = rs_end_et.astimezone(pytz.utc).timestamp()
            
            session_bars = [b for b in bars if rs_start_ts <= b.get("t", 0) / 1000 <= rs_end_ts]
            
            if not session_bars:
                continue
            
            # Open und Close des Tages
            day_open = session_bars[0].get("o", 0)
            day_close = session_bars[-1].get("c", 0)
            day_high = max(b.get("h", 0) for b in session_bars)
            day_low = min(b.get("l", 999999) for b in session_bars)
            
            # Evaluiere jedes Setup
            setup_results = []
            for si, setup in enumerate(item.get("setups", [])):
                entry = setup.get("entry", 0)
                stop = setup.get("stop", 0)
                tp1 = setup.get("tp1", 0)
                tp2 = setup.get("tp2", 0)
                
                if entry <= 0:
                    continue
                
                # Walk through bars chronologisch
                entry_hit = False
                entry_bar_idx = -1
                stop_hit = False
                tp1_hit = False
                tp2_hit = False
                exit_price = 0
                exit_reason = "OPEN"  # Noch offen
                
                for bi, bar in enumerate(session_bars):
                    bar_high = bar.get("h", 0)
                    bar_low = bar.get("l", 999999)
                    
                    if not entry_hit:
                        # Check ob Entry getriggert wird
                        if direction == "LONG":
                            if bar_high >= entry:
                                entry_hit = True
                                entry_bar_idx = bi
                        else:  # SHORT
                            if bar_low <= entry:
                                entry_hit = True
                                entry_bar_idx = bi
                    
                    elif entry_hit:
                        # Entry ist aktiv — check Stop und TPs
                        if direction == "LONG":
                            # Stop zuerst checken (konservativ)
                            if bar_low <= stop:
                                stop_hit = True
                                exit_price = stop
                                exit_reason = "STOP"
                                break
                            if bar_high >= tp2:
                                tp2_hit = True
                                tp1_hit = True
                                exit_price = tp2
                                exit_reason = "TP2"
                                break
                            if bar_high >= tp1 and not tp1_hit:
                                tp1_hit = True
                        else:  # SHORT
                            if bar_high >= stop:
                                stop_hit = True
                                exit_price = stop
                                exit_reason = "STOP"
                                break
                            if bar_low <= tp2:
                                tp2_hit = True
                                tp1_hit = True
                                exit_price = tp2
                                exit_reason = "TP2"
                                break
                            if bar_low <= tp1 and not tp1_hit:
                                tp1_hit = True
                
                # Wenn Trade noch offen: Close at EOD
                if entry_hit and not stop_hit and not tp2_hit:
                    exit_price = day_close
                    if tp1_hit:
                        exit_reason = "TP1+EOD"
                    else:
                        exit_reason = "EOD"
                
                # P&L berechnen
                if entry_hit:
                    if direction == "LONG":
                        pnl_dollar = exit_price - entry
                    else:
                        pnl_dollar = entry - exit_price
                    pnl_pct = (pnl_dollar / entry * 100) if entry > 0 else 0
                    r_multiple = pnl_dollar / setup.get("risk", 1) if setup.get("risk", 0) > 0 else 0
                else:
                    pnl_dollar = 0
                    pnl_pct = 0
                    r_multiple = 0
                    exit_reason = "NO ENTRY"
                
                setup_results.append({
                    "setup_name": setup.get("name", ""),
                    "setup_idx": si,
                    "is_primary": si == item.get("primary_idx", 0),
                    "entry": entry,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "entry_hit": entry_hit,
                    "stop_hit": stop_hit,
                    "tp1_hit": tp1_hit,
                    "tp2_hit": tp2_hit,
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "pnl_dollar": round(pnl_dollar, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "r_multiple": round(r_multiple, 2),
                })
            
            results.append({
                "ticker": ticker,
                "direction": direction,
                "pm_change": item.get("pm_change", 0),
                "setup_type": item.get("setup_type", ""),
                "day_open": round(day_open, 2),
                "day_close": round(day_close, 2),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
                "day_change_pct": round((day_close - day_open) / day_open * 100, 2) if day_open > 0 else 0,
                "setup_results": setup_results,
            })
            
        except Exception as e:
            _debug_log(f"Tracker eval failed for {ticker}", error=e)
            continue
    
    return results


