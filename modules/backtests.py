"""
Backtest Module — Extrahiert aus scanner.py (V70.0)

Backtesting-Engine für verschiedene Strategien:
- BI V2 Backtest, BioTech Backtest
- Full Backtest (grouped/single)
- Trade Simulation + Statistiken
"""
import time
import math
import datetime as dt
from datetime import datetime, timedelta
from functools import lru_cache
from modules.data_fetchers import rate_limited_get, fetch_grouped_daily
from modules.scorers import calculate_setup_score
from modules.strategies import BACKTEST_STRATEGY_RULES
from modules.analysis import compute_daily_metrics
from modules.helpers import check_signal
from modules.patterns import analyze_breakout_imminent
from modules.scanners import _compute_biotech_technical_from_bars
from modules.data_fetchers import fetch_backtest_daily_data
from modules.trade_levels import trade_geometry
from modules.performance_metrics import chronological_trade_key, profit_factor_metrics
from modules.bi_trade_plan import BI_PLAN_VERSION, build_bi_trade_plan
from modules.volume_metrics import historical_volume_baseline
from modules.vrvp_levels import calculate_wilder_atr


# ── Backtest Universes (kopiert aus scanner.py) ──
BACKTEST_UNIVERSE = [
    # === Tech Large Cap (20) ===
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOG", "AMD", "INTC", "CRM",
    "AVGO", "ORCL", "ADBE", "CSCO", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    # === Tech Mid/Growth (20) ===
    "PLTR", "SOFI", "SQ", "SNAP", "ROKU", "NET", "SHOP", "COIN", "CRWD", "DDOG",
    "ZS", "SNOW", "ABNB", "UBER", "LYFT", "DASH", "PINS", "U", "RBLX", "HOOD",
    # === Finance (15) ===
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK", "AXP", "V",
    "MA", "PYPL", "FIS", "ICE", "CME",
    # === Healthcare (15) ===
    "MRNA", "PFE", "ABBV", "JNJ", "UNH", "LLY", "TMO", "ABT", "BMY", "GILD",
    "AMGN", "REGN", "VRTX", "ISRG", "BIIB",
    # === Energy (10) ===
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL",
    # === Consumer (15) ===
    "WMT", "NKE", "COST", "TGT", "HD", "LOW", "SBUX", "MCD", "CMG", "DPZ",
    "LULU", "DECK", "ROST", "TJX", "DG",
    # === Industrial (10) ===
    "BA", "CAT", "DE", "GE", "HON", "UPS", "FDX", "LMT", "RTX", "NOC",
    # === Volatile/Small Cap (25) ===
    "GME", "AMC", "MARA", "RIOT", "SMCI", "ARM", "IONQ", "RGTI", "RIVN", "LCID",
    "PLUG", "FCEL", "SPCE", "OPEN", "WISH", "CLOV", "BB", "NOK", "TLRY", "SNDL",
    "MSTR", "UPST", "AFRM", "PATH", "AI",
    # === Biotech/Pharma (15) ===
    "NVAX", "BNTX", "DNA", "CRSP", "BEAM", "EDIT", "NTLA", "FATE", "SGEN", "ARKG",
    "EXAS", "HIMS", "DOCS", "ACHR", "JOBY",
    # === Semiconductor (10) ===
    "MRVL", "ON", "SWKS", "QRVO", "WOLF", "SMTC", "CRUS", "ALGM", "POWI", "DIOD",
    # === Real Estate / REITs (10) ===
    "O", "AMT", "PLD", "SPG", "VICI", "MPW", "IRM", "DLR", "CCI", "EQIX",
    # === ETFs (8) ===
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK"
]

BIOTECH_BACKTEST_UNIVERSE = [
    # Large Cap Biotech (>$20B) — kleinere Moves aber liquide
    "AMGN", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "ALNY", "BMRN",
    # Mid Cap Biotech ($2-20B) — Sweet Spot für Catalyst-Trading
    "SRPT", "EXEL", "PCVX", "IONS", "NBIX", "HALO", "INSM", "CRNX",
    "RARE", "MYGN", "FOLD", "ARWR", "IOVA", "KRYS", "ITCI", "CORT",
    "DAWN", "RVMD", "SWTX", "VKTX", "CYTK", "TGTX", "AXSM", "ADMA",
    # Small Cap Biotech ($200M-2B) — hohes Catalyst-Upside
    "AGEN", "ALVR", "ARQT", "AVXL", "BCRX", "CARA", "CLDX", "CPRX",
    "DVAX", "ENTA", "GERN", "HRTX", "IMVT", "KALA", "LQDA", "MNKD",
    "NUVB", "OCUL", "PLRX", "PRAX", "RCKT", "SAGE", "SAVA", "SMMT",
    "TVTX", "VCEL", "VRNA", "XNCR", "ZYME", "APLS", "ACLX", "CDTX",
    # Recent FDA-Active (regelmäßig PDUFA/AdCom Dates)
    "ACAD", "AKBA", "ANIK", "BLUE", "BLTE", "CMPS", "CRSP", "EDIT",
    "NTLA", "BEAM", "MDGL", "KROS", "LNTH", "LGND", "NKTR", "PCRX",
    "PTCT", "RLAY", "ROIV", "RYTM", "SNDX", "TARS", "TECH", "TXRX",
]


def _initial_universe_average_volume(bars, window_size, lookback=20, minimum_periods=10):
    """Rank a backtest universe from information available at test start only."""
    if not bars or window_size <= 0:
        return None
    initial_window = bars[:window_size]
    if len(initial_window) < minimum_periods:
        return None
    return historical_volume_baseline(
        (bar.get("volume") for bar in initial_window),
        lookback=lookback,
        minimum_periods=minimum_periods,
    )


@lru_cache(maxsize=8192)
def _daily_session_gap(previous_date, current_date, calendar="us_equity"):
    """Return unobserved expected sessions between two daily observations.

    Stock studies use the existing NYSE calendar. 24/7 is only an explicit
    asset contract, never inferred from a ticker. Undated legacy fixtures are
    still calculable but cannot claim verified market-data coverage.
    """
    if calendar not in {"us_equity", "24_7"}:
        raise ValueError("session_calendar must be us_equity or 24_7")
    try:
        previous = dt.date.fromisoformat(str(previous_date)[:10])
        current = dt.date.fromisoformat(str(current_date)[:10])
    except (TypeError, ValueError):
        return {"coverage": "legacy_bar_sequence_unverified", "missing_sessions": ()}
    if current <= previous:
        return {"coverage": "invalid_daily_date_order", "missing_sessions": (),
                "reason": "NON_INCREASING_DAILY_DATES"}
    if (current - previous).days == 1:
        return {"coverage": "expected_session_gaps_checked", "missing_sessions": ()}
    try:
        # Optional tracker import stays lazy, preserving defensive API startup.
        from modules.signal_tracker import _is_us_equity_session
    except ImportError:
        if calendar == "us_equity":
            return {"coverage": "calendar_unavailable", "missing_sessions": (),
                    "reason": "SESSION_CALENDAR_UNAVAILABLE"}
    missing = []
    cursor = previous + timedelta(days=1)
    while cursor < current:
        if calendar == "24_7" or _is_us_equity_session(cursor):
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return {"coverage": "missing_expected_sessions" if missing else "expected_session_gaps_checked",
            "missing_sessions": tuple(missing),
            **({"reason": "MISSING_EXPECTED_SESSION"} if missing else {})}


class _BacktestTradeList(list):
    """Preserve strategy->list compatibility while carrying empty-cohort quality."""
    data_quality = None


def _backtest_data_quality(trades, *, failed_fetch_dates=(), unavailable_tickers=()):
    inherited = getattr(trades, "data_quality", None) or {}
    failed = set(failed_fetch_dates) | set(inherited.get("failed_fetch_dates") or ())
    unavailable = set(unavailable_tickers) | set(inherited.get("unavailable_tickers") or ())
    missing = set(inherited.get("missing_expected_sessions") or ())
    affected = 0
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        quality = trade.get("data_quality") or {}
        failed.update(quality.get("failed_fetch_dates") or ())
        unavailable.update(quality.get("unavailable_tickers") or ())
        missing.update(trade.get("missing_expected_sessions") or ())
        if trade.get("evaluation_status") in {
            "MISSING_EXPECTED_SESSION", "NON_INCREASING_DAILY_DATES", "SESSION_CALENDAR_UNAVAILABLE",
        }:
            affected += 1
    partial = bool(failed or unavailable or missing or affected)
    return {
        "status": "PARTIAL" if partial else "NO_KNOWN_FETCH_OR_SESSION_GAP",
        "failed_fetch_days": len(failed), "failed_fetch_dates": sorted(failed),
        "unavailable_tickers": sorted(unavailable),
        "missing_expected_sessions": sorted(missing), "coverage_unresolved_trades": affected,
        "statistics_scope": "observed_decided_paths_only_not_complete_market_cohort",
    }


def _attach_backtest_data_quality(results, *, failed_fetch_dates=(), unavailable_tickers=()):
    for trades in results.values():
        quality = _backtest_data_quality(trades, failed_fetch_dates=failed_fetch_dates,
                                         unavailable_tickers=unavailable_tickers)
        trades.data_quality = quality
        for trade in trades:
            trade["data_quality"] = quality


def _simulate_50_50_daily_path(
    bars,
    start_idx,
    max_hold,
    direction,
    entry_price,
    stop_price,
    tp1_price,
    tp2_price,
    *,
    trail_fraction=0.0,
    exit_slippage=0.0,
    fee_pct=0.2,
    prefer_target=False,
    first_bar_order_unknown=False,
    post_tp1_stop_offset=0.0,
    session_calendar="us_equity",
):
    """Simulate one deterministic ordering of a two-target daily-OHLC trade."""
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if not bars or start_idx >= len(bars) or max_hold <= 0:
        return None

    entry_price = float(entry_price)
    stop_price = float(stop_price)
    tp1_price = float(tp1_price)
    tp2_price = float(tp2_price)
    post_tp1_stop_offset = max(0.0, float(post_tp1_stop_offset or 0.0))
    if (not all(math.isfinite(value) for value in (entry_price, stop_price, tp1_price, tp2_price,
             float(exit_slippage), float(fee_pct), float(trail_fraction), post_tp1_stop_offset))
            or min(entry_price, stop_price, tp1_price, tp2_price) <= 0
            or not 0 <= exit_slippage < 1 or fee_pct < 0 or not 0 <= trail_fraction <= 1):
        return None
    if direction == "LONG":
        geometry_valid = stop_price < entry_price < tp1_price < tp2_price
    else:
        geometry_valid = tp2_price < tp1_price < entry_price < stop_price
    if not geometry_valid:
        return None

    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return None

    current_stop = float(stop_price)
    tp1_hit = False
    bars_held = 0
    exit_price = None
    exit_reason = None
    exit_date = None
    ambiguity_reasons = set()
    session_coverage = "legacy_bar_sequence_unverified"
    missing_expected_sessions = []

    def _level_fill(level):
        multiplier = 1.0 - exit_slippage if direction == "LONG" else 1.0 + exit_slippage
        return float(level) * multiplier

    def _trailed_stop():
        if direction == "LONG":
            return max(entry_price + (tp1_price - entry_price) * trail_fraction,
                       entry_price + post_tp1_stop_offset)
        return min(entry_price - (entry_price - tp1_price) * trail_fraction,
                   entry_price - post_tp1_stop_offset)

    def _unresolved(reason, bar_index):
        return {
            "exit_date": bars[bar_index].get("date"), "exit_price": None,
            "exit_reason": "UNRESOLVED", "outcome": "UNRESOLVED",
            "pnl_pct": None, "r_multiple": None, "bars_held": bars_held,
            "tp1_hit": tp1_hit, "is_winner": False, "ambiguity_reasons": sorted(ambiguity_reasons),
            "evaluation_status": reason, "evaluation_model_version": "daily-causal-exits-v2",
            "roundtrip_fee_pct": fee_pct, "exit_slippage_fraction": exit_slippage,
            "session_calendar": session_calendar, "session_coverage": session_coverage,
            "missing_expected_sessions": list(missing_expected_sessions),
        }

    for day_offset in range(int(max_hold)):
        bar_idx = start_idx + day_offset
        if bar_idx >= len(bars):
            break

        bar = bars[bar_idx]
        if bar_idx > 0:
            coverage = _daily_session_gap(bars[bar_idx-1].get("date"), bar.get("date"), session_calendar)
            session_coverage = coverage["coverage"]
            missing_expected_sessions = list(coverage["missing_sessions"])
            if coverage.get("reason"):
                return _unresolved(coverage["reason"], bar_idx)
        try:
            bar_open, bar_high, bar_low, bar_close = (float(bar[key]) for key in ("open", "high", "low", "close"))
        except (KeyError, ValueError, TypeError, OverflowError):
            return _unresolved("INVALID_OHLC", bar_idx)
        if (not all(math.isfinite(value) and value > 0 for value in (bar_open, bar_high, bar_low, bar_close))
                or bar_high < max(bar_open, bar_low, bar_close) or bar_low > min(bar_open, bar_high, bar_close)):
            return _unresolved("INVALID_OHLC", bar_idx)
        bars_held += 1

        if direction == "LONG":
            gap_through_stop = bar_open > 0 and bar_open <= current_stop
            stop_possible = bar_low <= current_stop
            tp1_possible = bar_high >= tp1_price
            tp2_possible = bar_high >= tp2_price
        else:
            gap_through_stop = bar_open > 0 and bar_open >= current_stop
            stop_possible = bar_high >= current_stop
            tp1_possible = bar_low <= tp1_price
            tp2_possible = bar_low <= tp2_price

        # For a trigger-touch fill the session open predates the intraday entry.
        # It must not be treated as a post-entry gap through the stop.
        trigger_touch_first_bar = bool(first_bar_order_unknown and day_offset == 0)
        # A real gap through the stop occurs at the open and is not an
        # OHLC-order ambiguity once the position was already open.
        if gap_through_stop and not trigger_touch_first_bar:
            runner_exit = _level_fill(bar_open)
            exit_price = (_level_fill(tp1_price) + runner_exit) / 2 if tp1_hit else runner_exit
            exit_reason = "TP1_STOP" if tp1_hit else "STOP"
            exit_date = bar.get("date")
            break

        # The open precedes every intrabar extreme for an existing position.
        # Apply executable limit targets before a later reversal can hit its stop.
        if bar_open > 0 and not trigger_touch_first_bar:
            open_tp2 = bar_open >= tp2_price if direction == "LONG" else bar_open <= tp2_price
            open_tp1 = bar_open >= tp1_price if direction == "LONG" else bar_open <= tp1_price
            if open_tp2:
                exit_price = (_level_fill(tp1_price) + _level_fill(tp2_price)) / 2
                exit_reason, exit_date, tp1_hit = "BLENDED_TP", bar.get("date"), True
                break
            if open_tp1 and not tp1_hit:
                tp1_hit = True
                current_stop = _trailed_stop()
                stop_possible = bar_low <= current_stop if direction == "LONG" else bar_high >= current_stop

        entry_order_unknown = bool(first_bar_order_unknown and day_offset == 0 and stop_possible)
        target_and_stop = bool(stop_possible and ((tp1_possible and not tp1_hit) or tp2_possible))
        if entry_order_unknown:
            ambiguity_reasons.add("entry_bar_pre_post_fill_order_unknown")
        if target_and_stop:
            ambiguity_reasons.add("same_bar_stop_and_target")

        effective_stop_possible = stop_possible
        if prefer_target and entry_order_unknown and not (tp1_possible or tp2_possible):
            # A trigger-touch fill is intrabar. The day's adverse extreme may have
            # happened before the entry; ignoring it defines the favorable bound.
            effective_stop_possible = False

        if prefer_target:
            if tp2_possible:
                exit_price = (_level_fill(tp1_price) + _level_fill(tp2_price)) / 2
                exit_reason = "BLENDED_TP"
                exit_date = bar.get("date")
                tp1_hit = True
                break
            if not tp1_hit and tp1_possible:
                tp1_hit = True
                if direction == "LONG":
                    current_stop = max(
                        entry_price + (tp1_price - entry_price) * trail_fraction,
                        entry_price + post_tp1_stop_offset,
                    )
                    trailed_stop_hit = bar_low <= current_stop
                else:
                    current_stop = min(
                        entry_price - (entry_price - tp1_price) * trail_fraction,
                        entry_price - post_tp1_stop_offset,
                    )
                    trailed_stop_hit = bar_high >= current_stop
                if stop_possible or trailed_stop_hit:
                    ambiguity_reasons.add("same_bar_tp1_and_trailed_stop")
                close_through_trail = (bar_close <= current_stop if direction == "LONG"
                                       else bar_close >= current_stop)
                if close_through_trail:
                    # Unlike the unordered low/high, the close is necessarily
                    # after the TP1 touch. Even the favorable path must have
                    # crossed the newly active stop before this session ended.
                    exit_price = (_level_fill(tp1_price) + _level_fill(current_stop)) / 2
                    exit_reason, exit_date = "TP1_STOP", bar.get("date")
                    break
                # Favorable bound: the adverse extreme may have preceded TP1.
                # The runner therefore remains open into the next bar.
                continue
            if effective_stop_possible:
                runner_exit = _level_fill(current_stop)
                exit_price = (_level_fill(tp1_price) + runner_exit) / 2 if tp1_hit else runner_exit
                exit_reason = "TP1_STOP" if tp1_hit else "STOP"
                exit_date = bar.get("date")
                break
        else:
            if effective_stop_possible:
                runner_exit = _level_fill(current_stop)
                exit_price = (_level_fill(tp1_price) + runner_exit) / 2 if tp1_hit else runner_exit
                exit_reason = "TP1_STOP" if tp1_hit else "STOP"
                exit_date = bar.get("date")
                break
            # If TP2 traded without a stop touch, TP1 necessarily traded first
            # or at the same instant. Waiting for another daily bar understates wins.
            if tp2_possible:
                exit_price = (_level_fill(tp1_price) + _level_fill(tp2_price)) / 2
                exit_reason = "BLENDED_TP"
                exit_date = bar.get("date")
                tp1_hit = True
                break
            if not tp1_hit and tp1_possible:
                tp1_hit = True
                if direction == "LONG":
                    current_stop = max(
                        entry_price + (tp1_price - entry_price) * trail_fraction,
                        entry_price + post_tp1_stop_offset,
                    )
                    trailed_stop_hit = bar_low <= current_stop
                else:
                    current_stop = min(
                        entry_price - (entry_price - tp1_price) * trail_fraction,
                        entry_price - post_tp1_stop_offset,
                    )
                    trailed_stop_hit = bar_high >= current_stop
                if trailed_stop_hit:
                    ambiguity_reasons.add("same_bar_tp1_and_trailed_stop")
                    exit_price = (_level_fill(tp1_price) + _level_fill(current_stop)) / 2
                    exit_reason = "TP1_STOP"
                    exit_date = bar.get("date")
                    break

    if bars_held == 0:
        return None

    if exit_reason is None:
        if bars_held < int(max_hold):
            return _unresolved("INCOMPLETE_HOLDING_WINDOW", min(start_idx + bars_held - 1, len(bars) - 1))
        last_bar_idx = min(start_idx + max_hold - 1, len(bars) - 1)
        runner_exit = _level_fill(float(bars[last_bar_idx].get("close") or entry_price))
        exit_price = (_level_fill(tp1_price) + runner_exit) / 2 if tp1_hit else runner_exit
        exit_reason = "TP1+EOD" if tp1_hit else "EOD"
        exit_date = bars[last_bar_idx].get("date")

    if direction == "LONG":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 - fee_pct
    else:
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100 - fee_pct
    risk_pct = risk / entry_price * 100
    # Do not cap gap-through losses. A stop is an instruction, not a fill
    # guarantee; clipping at -2R would systematically understate tail risk.
    r_multiple = pnl_pct / risk_pct if risk_pct > 0 else 0.0

    return {
        "exit_date": exit_date,
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 4),
        "r_multiple": round(r_multiple, 4),
        "bars_held": bars_held,
        "tp1_hit": tp1_hit,
        "is_winner": pnl_pct > 0,
        "ambiguity_reasons": sorted(ambiguity_reasons),
        "evaluation_model_version": "daily-causal-exits-v2",
        "roundtrip_fee_pct": fee_pct,
        "exit_slippage_fraction": exit_slippage,
        "session_calendar": session_calendar, "session_coverage": session_coverage,
        "missing_expected_sessions": list(missing_expected_sessions),
    }


def _simulate_50_50_daily_exit(
    bars,
    start_idx,
    max_hold,
    direction,
    entry_price,
    stop_price,
    tp1_price,
    tp2_price,
    *,
    trail_fraction=0.0,
    exit_slippage=0.0,
    fee_pct=0.2,
    first_bar_order_unknown=False,
    post_tp1_stop_offset=0.0,
    session_calendar="us_equity",
):
    """Return conservative and favorable bounds for an unresolved daily path."""
    shared = dict(
        bars=bars,
        start_idx=start_idx,
        max_hold=max_hold,
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        trail_fraction=trail_fraction,
        exit_slippage=exit_slippage,
        fee_pct=fee_pct,
        first_bar_order_unknown=first_bar_order_unknown,
        post_tp1_stop_offset=post_tp1_stop_offset,
        session_calendar=session_calendar,
    )
    lower = _simulate_50_50_daily_path(**shared, prefer_target=False)
    upper = _simulate_50_50_daily_path(**shared, prefer_target=True)
    if lower is None or upper is None:
        return None

    ambiguity_reasons = sorted(set(lower["ambiguity_reasons"]) | set(upper["ambiguity_reasons"]))
    if lower["exit_reason"] == "UNRESOLVED" or upper["exit_reason"] == "UNRESOLVED":
        # Even if one hypothetical ordering has exited, its alternate path may
        # still be open. Do not turn the missing tail into a neutral or decided R.
        result = dict(lower)
        unresolved_path = lower if lower["exit_reason"] == "UNRESOLVED" else upper
        result.update({
            "outcome": "UNRESOLVED", "exit_reason": "UNRESOLVED", "exit_price": None,
            "pnl_pct": None, "r_multiple": None, "is_winner": False,
            "exit_date_upper": upper["exit_date"], "exit_price_upper": None,
            "exit_reason_upper": upper["exit_reason"], "pnl_pct_upper": None,
            "r_multiple_upper": None, "tp1_hit_upper": upper["tp1_hit"], "is_winner_upper": False,
            "intrabar_ambiguous": bool(ambiguity_reasons),
            "ambiguity_reason": ",".join(ambiguity_reasons) if ambiguity_reasons else None,
            "evaluation_status": (lower.get("evaluation_status") or upper.get("evaluation_status")
                                  or "INCOMPLETE_HOLDING_WINDOW"),
            "ohlc_path_policy": "lower_stop_first_upper_target_first",
            "session_coverage": unresolved_path["session_coverage"],
            "missing_expected_sessions": unresolved_path["missing_expected_sessions"],
        })
        return result
    # The favorable path is an upper bound, never a second performance claim.
    if upper["r_multiple"] < lower["r_multiple"]:
        upper = dict(lower)
    result = dict(lower)
    result.update({
        "exit_date_upper": upper["exit_date"],
        "exit_price_upper": upper["exit_price"],
        "exit_reason_upper": upper["exit_reason"],
        "pnl_pct_upper": upper["pnl_pct"],
        "r_multiple_upper": upper["r_multiple"],
        "tp1_hit_upper": upper["tp1_hit"],
        "is_winner_upper": upper["is_winner"],
        "intrabar_ambiguous": bool(ambiguity_reasons or upper["r_multiple"] != lower["r_multiple"]),
        "ambiguity_reason": ",".join(ambiguity_reasons) if ambiguity_reasons else None,
        "ohlc_path_policy": "lower_stop_first_upper_target_first",
    })
    return result


def _backtest_uncertainty_metrics(trades):
    """Summarize conservative results and favorable OHLC-path bounds."""
    trades = [trade for trade in (trades or [])
              if str(trade.get("outcome") or "").upper() not in {"NO_FILL", "UNRESOLVED"}]
    if not trades:
        return {
            "ambiguous_trades": 0,
            "ambiguity_rate": 0.0,
            "win_rate_upper": 0.0,
            "avg_pnl_upper": 0.0,
            "total_pnl_upper": 0.0,
            "avg_r_upper": 0.0,
            "total_r_upper": 0.0,
            "ohlc_path_policy": "lower_stop_first_upper_target_first",
        }
    total = len(trades)
    ambiguous = sum(1 for trade in trades if trade.get("intrabar_ambiguous"))
    upper_pnls = [float(trade.get("pnl_pct_upper", trade.get("pnl_pct", 0)) or 0) for trade in trades]
    upper_rs = [float(trade.get("r_multiple_upper", trade.get("r_multiple", 0)) or 0) for trade in trades]
    upper_wins = sum(1 for pnl in upper_pnls if pnl > 0)
    return {
        "ambiguous_trades": ambiguous,
        "ambiguity_rate": round(ambiguous / total * 100, 1),
        "win_rate_upper": round(upper_wins / total * 100, 1),
        "avg_pnl_upper": round(sum(upper_pnls) / total, 2),
        "total_pnl_upper": round(sum(upper_pnls), 2),
        "avg_r_upper": round(sum(upper_rs) / total, 2),
        "total_r_upper": round(sum(upper_rs), 2),
        "ohlc_path_policy": "lower_stop_first_upper_target_first",
    }


def simulate_50_50_daily_exit(*args, **kwargs):
    """Public entry point for the canonical daily-OHLC execution model."""
    return _simulate_50_50_daily_exit(*args, **kwargs)


def backtest_uncertainty_metrics(trades):
    """Public entry point for lower/upper OHLC-path summary metrics."""
    return _backtest_uncertainty_metrics(trades)


_BASE_RULE_SIGNAL_KEYS = {
    "change_pct_min",
    "change_pct_max",
    "gap_pct_min",
    "gap_pct_max",
    "rvol_min",
    "rvol_max",
    "close_pos_min",
    "close_pos_max",
    "prev_change_pct_min",
    "prev_change_pct_max",
}
_STRUCTURAL_RULE_SIGNAL_KEYS = {
    "upper_wick_pct_max",
    "breakout_lookback_days",
    "breakout_proximity_min",
    "breakdown_lookback_days",
    "breakdown_proximity_max",
}


def evaluate_rule_signal(bars, signal_idx, strategy):
    """Evaluate one rule signal without silently dropping structural gates.

    The former API-only implementation understood wick and breakout proximity,
    while the universe backtests called ``check_signal`` directly. That made the
    same named strategy mean different things in different backtest endpoints.
    This is now the single rule-evaluation entry point for all of them.
    """
    if signal_idx < 1 or signal_idx >= len(bars):
        return None

    signal_rules = dict((strategy or {}).get("signal") or {})
    unsupported = set(signal_rules) - _BASE_RULE_SIGNAL_KEYS - _STRUCTURAL_RULE_SIGNAL_KEYS
    if unsupported:
        raise ValueError(
            "Unsupported backtest signal rule(s): " + ", ".join(sorted(unsupported))
        )

    metrics = compute_daily_metrics(bars, signal_idx)
    if not metrics:
        return None

    rvol = metrics.get("rvol")
    if ("rvol_min" in signal_rules or "rvol_max" in signal_rules) and rvol is None:
        return None

    base_rules = {
        key: value for key, value in signal_rules.items()
        if key in _BASE_RULE_SIGNAL_KEYS
    }
    if not check_signal(metrics, base_rules):
        return None

    bar = bars[signal_idx]
    open_price = float(bar.get("open") or 0)
    high = float(bar.get("high") or 0)
    low = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    candle_range = high - low
    upper_wick_pct = (
        max(0.0, high - max(open_price, close)) / candle_range * 100.0
        if candle_range > 0 else 0.0
    )
    metrics["upper_wick_pct"] = upper_wick_pct
    if (
        "upper_wick_pct_max" in signal_rules
        and upper_wick_pct > float(signal_rules["upper_wick_pct_max"])
    ):
        return None

    breakout_lookback = int(signal_rules.get("breakout_lookback_days", 0) or 0)
    if breakout_lookback:
        if signal_idx < breakout_lookback:
            return None
        prior_high = max(
            float(candidate.get("high") or 0)
            for candidate in bars[signal_idx - breakout_lookback:signal_idx]
        )
        if prior_high <= 0:
            return None
        proximity = (close - prior_high) / prior_high
        metrics["breakout_prior_high"] = prior_high
        metrics["breakout_proximity"] = proximity
        if proximity < float(signal_rules.get("breakout_proximity_min", 0.0) or 0.0):
            return None

    breakdown_lookback = int(signal_rules.get("breakdown_lookback_days", 0) or 0)
    if breakdown_lookback:
        if signal_idx < breakdown_lookback:
            return None
        prior_low = min(
            float(candidate.get("low") or 0)
            for candidate in bars[signal_idx - breakdown_lookback:signal_idx]
        )
        if prior_low <= 0:
            return None
        proximity = (close - prior_low) / prior_low
        metrics["breakdown_prior_low"] = prior_low
        metrics["breakdown_proximity"] = proximity
        if proximity > float(signal_rules.get("breakdown_proximity_max", 0.0) or 0.0):
            return None

    return metrics


def conservative_trade_exit_index(trade, date_to_index, fallback_index):
    """Return the later lower/upper-path exit so simulated trades cannot overlap."""
    if str(trade.get("outcome") or "").upper() == "UNRESOLVED":
        # A data gap is not an exit; do not reopen this instrument while the
        # unresolved first position might still be occupied.
        return max([fallback_index, *date_to_index.values()])
    candidates = [fallback_index]
    for key in ("exit_date", "exit_date_upper"):
        raw_exit_date = trade.get(key)
        exit_index = date_to_index.get(raw_exit_date)
        if exit_index is None and raw_exit_date is not None:
            exit_index = date_to_index.get(str(raw_exit_date))
        if exit_index is not None:
            candidates.append(exit_index)
    return max(candidates)


_NON_DECIDED_BACKTEST_OUTCOMES = frozenset({"NO_FILL", "UNRESOLVED"})


def _is_decided_backtest_trade(trade):
    """Return True only when a filled trade has an evaluable exit outcome."""
    outcome = str((trade or {}).get("outcome") or "").upper()
    return bool(outcome) and outcome not in _NON_DECIDED_BACKTEST_OUTCOMES


def _bi_retest_close_entry(
    bar,
    direction,
    zone_lower,
    zone_upper,
    stop,
    previous_extreme,
    slippage,
):
    """Model a BI retest fill at the close without same-bar look-ahead.

    `previous_extreme` must come from completed bars before `bar`.  The current
    bar may touch the retest zone, but its own high/low cannot retroactively
    prove that price first extended and then retested during the same daily bar.
    """
    direction = str(direction or "").lower()
    open_price = float(bar.get("open") or 0)
    high = float(bar.get("high") or 0)
    low = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    if min(open_price, high, low, close) <= 0:
        return None

    zone_lower = float(zone_lower or 0)
    zone_upper = float(zone_upper or 0)
    stop = float(stop or 0)
    previous_extreme = float(previous_extreme or 0)
    slippage = max(0.0, float(slippage or 0))
    if not (0 < zone_lower <= zone_upper):
        return None

    if direction == "long":
        extended_before_bar = previous_extreme > zone_upper
        touched_retest = low <= zone_upper
        held_invalidation = low > stop
        closed_in_or_above_zone = close >= zone_lower
        if not all((extended_before_bar, touched_retest, held_invalidation, closed_in_or_above_zone)):
            return None
        return close * (1 + slippage)

    if direction == "short":
        extended_before_bar = 0 < previous_extreme < zone_lower
        touched_retest = high >= zone_lower
        held_invalidation = high < stop
        closed_in_or_below_zone = close <= zone_upper
        if not all((extended_before_bar, touched_retest, held_invalidation, closed_in_or_below_zone)):
            return None
        return close * (1 - slippage)

    return None


def run_full_backtest_grouped(poly_key, strategies=None, months=6, min_price=5.0,
                               min_volume=100000, progress_callback=None):
    """
    Backtest über ALLE US-Aktien mit Grouped Daily Bars.
    
    Zwei-Pass Ansatz:
    1. Lade alle Tage → baue per-Ticker History auf
    2. Scanne Signale und simuliere Trades mit vollständiger History
    """
    from modules.signal_tracker import _is_us_equity_session

    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=months * 30 + 30)  # +30 für RVOL Lookback
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if _is_us_equity_session(current.date()):
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    if not trading_days:
        return {s: [] for s in strategies}, 0
    
    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}  # ticker → list of bars (chronologisch)
    total_tickers_seen = set()
    failed_fetch_days = 0  # NACHAUDIT H3-Rest: hart gescheiterte Fetch-Tage sichtbar machen
    failed_fetch_dates = []
    
    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.7,  # 70% für Laden
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )
        
        day_data = fetch_grouped_daily(poly_key, date_str)
        if day_data is None:
            # NACHAUDIT H3-Rest: None = Fetch nach Retries hart gescheitert
            # (kein valider Leertag). Nicht still verschlucken, sondern zaehlen —
            # an diesem Tag sind Stop-/TP-Treffer fuer ALLE Ticker unsichtbar.
            failed_fetch_days += 1
            failed_fetch_dates.append(date_str)
            continue
        if not day_data:
            continue
        
        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            
            # Leveraged/Inverse ETFs und Krypto-ETPs rausfiltern
            _t = ticker.upper()
            _skip_prefixes = (
                "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
                "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
                "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
                "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
                "XRPT","XXRP","XETH","BITO","GBTC","ETHE","BITW","CONL",
                "MSOX","BTFX","SOLT","NEBX","AREC","MAXI","TNA","TZA"
            )
            if any(_t.startswith(p) for p in _skip_prefixes):
                continue
            
            price = r.get("c", 0)
            volume = r.get("v", 0)
            
            if price <= 0:
                continue

            # Preserve the complete path. Filtering history here can hide
            # later crash bars and stop hits; eligibility is checked only on
            # the signal bar below.
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
            }
            
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)
    
    if failed_fetch_days:
        # NACHAUDIT H3-Rest: Ergebnis-Shape (Tuple) nicht brechen — aber der
        # Lauf darf nicht so tun, als waere die Historie vollstaendig.
        print(f"[Backtest] WARNUNG: {failed_fetch_days} Handelstag(e) ohne Daten "
              f"(Fetch nach Retries gescheitert) — Stop-/TP-Treffer dieser Tage fehlen.")

    # ============================================================
    # PASS 2: Signale erkennen + Trades simulieren
    # (Jetzt hat jeder Ticker seine VOLLSTÄNDIGE History)
    # ============================================================
    all_results = {s: _BacktestTradeList() for s in strategies}
    tickers_with_data = [t for t, bars in ticker_history.items() if len(bars) >= 30]
    seen_signals = set()  # Dedup: max 1 Signal pro Strategie/Ticker/Tag
    
    for t_idx, ticker in enumerate(tickers_with_data):
        if progress_callback and t_idx % 500 == 0:
            progress_callback(
                0.7 + (t_idx / len(tickers_with_data)) * 0.3,  # 30% für Simulation
                f" Scanne {ticker} ({t_idx+1}/{len(tickers_with_data)})"
            )
        
        bars = ticker_history[ticker]
        date_to_index = {bar.get("date"): index for index, bar in enumerate(bars)}
        last_exit_by_strategy = {name: -1 for name in strategies}
        
        for idx in range(21, len(bars)):
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics or metrics["price"] <= 0:
                continue
            if metrics["price"] < min_price or float(bars[idx].get("volume") or 0) < min_volume:
                continue
            
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                if idx <= last_exit_by_strategy[strat_name]:
                    continue
                if metrics["price"] < strat.get("min_price", 1.0):
                    continue

                signal_metrics = evaluate_rule_signal(bars, idx, strat)
                if signal_metrics:
                    # Dedup: Max 1 Trade pro Ticker pro Tag
                    dedup_key = (ticker, bars[idx]["date"], strat_name)
                    if dedup_key in seen_signals:
                        continue
                    
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        seen_signals.add(dedup_key)
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(signal_metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(signal_metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
                        last_exit_by_strategy[strat_name] = conservative_trade_exit_index(
                            trade,
                            date_to_index,
                            idx,
                        )
    
    # Memory aufräumen
    del ticker_history
    
    if progress_callback:
        progress_callback(1.0, f"[OK] Fertig! {len(total_tickers_seen)} Aktien gescannt")

    _attach_backtest_data_quality(all_results, failed_fetch_dates=failed_fetch_dates)
    return all_results, len(total_tickers_seen)


def _simulate_bi_plan_daily(bars, start_idx, plan, direction, *, horizon_bars=10):
    """Execute the production plan against future daily bars, with explicit limits.

    Signal-close market orders use the next open; a daily study has no SMTP or
    quote evidence and never pretends to reproduce the live-delivery pipeline.
    """
    # Keep the optional tracker dependency off the generic simulator/API import
    # path: a tracker outage must not prevent the entire application starting.
    from modules.signal_tracker import validate_fill_quality

    side = direction.upper()
    entry, stop, tp1, tp2 = (float(plan[key]) for key in ("Entry", "StopLoss", "TP1", "TP2"))
    method = plan["entry_method"]
    result = {
        "entry_target": entry, "stop_target": stop, "tp1_target": tp1, "tp2_target": tp2,
        "entry_method": method, "plan_version": plan["plan_version"],
        "execution_model": "daily_next_session_50_50_be_after_tp1_v2",
        "live_delivery_equivalent": False, "entry_filled": False,
        "outcome": "UNRESOLVED", "exit_reason": "UNRESOLVED", "pnl_pct": None,
        "r_multiple": None, "is_winner": False, "bars_held": 0,
        "evaluation_status": "INCOMPLETE_ENTRY_WINDOW",
    }
    observed = 0
    for index in range(start_idx, min(len(bars), start_idx + horizon_bars)):
        bar = bars[index]
        observed += 1
        result["exit_date"] = bar["date"]
        if index > 0:
            coverage = _daily_session_gap(bars[index-1].get("date"), bar.get("date"), "us_equity")
            result.update(session_calendar="us_equity", session_coverage=coverage["coverage"],
                          missing_expected_sessions=list(coverage["missing_sessions"]))
            if coverage.get("reason"):
                return dict(result, evaluation_status=coverage["reason"])
        try:
            opening, high, low, closing = (float(bar[key]) for key in ("open", "high", "low", "close"))
        except (ValueError, TypeError, KeyError, OverflowError):
            return dict(result, evaluation_status="INVALID_OHLC")
        if (not all(math.isfinite(value) and value > 0 for value in (opening, high, low, closing))
                or high < max(opening, low, closing) or low > min(opening, high, closing)):
            return dict(result, evaluation_status="INVALID_OHLC")
        if ((side == "LONG" and (opening <= stop or opening >= tp1))
                or (side == "SHORT" and (opening >= stop or opening <= tp1))):
            return dict(result, outcome="NO_FILL", exit_reason="NO_FILL", evaluation_status="OPEN_INVALIDATED_PLAN")
        fill = None
        intrabar = False
        if method == "market_at_signal":
            fill = opening
        elif method == "stop_breakout":
            if opening >= entry:
                fill = opening
            elif high >= entry:
                fill, intrabar = entry, True
                if low <= stop:
                    return dict(result, evaluation_status="ENTRY_STOP_ORDER_UNRESOLVED")
        elif method == "limit_pullback":
            if opening >= entry:
                fill = opening
            elif high >= entry:
                fill, intrabar = entry, True
                if low <= tp1:
                    return dict(result, evaluation_status="LIMIT_ENTRY_TARGET_ORDER_UNRESOLVED")
        else:
            return dict(result, evaluation_status="UNKNOWN_ENTRY_METHOD")
        if fill is None:
            invalidated = low <= stop if side == "LONG" else high >= stop
            if invalidated:
                return dict(result, outcome="NO_FILL", exit_reason="NO_FILL", evaluation_status="PLAN_INVALIDATED_BEFORE_ENTRY")
            continue
        # Adverse assumed slippage for market/stop execution; a passive sell
        # limit cannot execute below its limit price.
        slippage = .001
        if method != "limit_pullback":
            fill *= 1 + slippage if side == "LONG" else 1 - slippage
        quality = validate_fill_quality("bi_long" if side == "LONG" else "bi_short", entry, fill, stop, tp1, tp2, side)
        if not quality.get("valid"):
            return dict(result, outcome="NO_FILL", exit_reason="NO_FILL", evaluation_status="FILL_QUALITY_REJECTED")
        simulated = _simulate_50_50_daily_exit(
            bars, index, horizon_bars, side, fill, stop, tp1, tp2,
            fee_pct=.2, exit_slippage=slippage, first_bar_order_unknown=intrabar,
        )
        result.update(entry_filled=True, actual_entry=fill, entry_date=bar["date"])
        if simulated is not None:
            result.update(simulated)
            result["outcome"] = simulated["exit_reason"]
            if result["outcome"] != "UNRESOLVED":
                result["evaluation_status"] = "DECIDED"
        return result
    if observed >= horizon_bars:
        result.update(outcome="NO_FILL", exit_reason="NO_FILL", evaluation_status="ENTRY_NOT_REACHED")
    return result


def run_bi_v2_backtest(poly_key, direction="long", months=6, max_tickers=200,
                        min_price=5.0, min_volume=200000, progress_callback=None):
    """
     BI historical study: production signal/plan, separately labelled daily fills.

    The strict indicator contract and plan prices are shared with production.
    Daily data cannot reproduce the exact mail, quote or BE-update timestamps.

    Für jeden Tag im Backtest-Zeitraum:
    1. Nimm 50-Tage Fenster als Input für analyze_breakout_imminent()
    2. Gemeinsamer Produktionsplan und explizite Stop-/Limit-/Markt-Entry-Methode
    3. Folgesession-Fill, 50/50-Management, BE nach TP1 als eigene Daily-Variante
    4. Tracke Ergebnis nach Grade (S/A/B/C)

    Args:
        poly_key: Polygon API Key
        direction: "long" oder "short"
        months: Backtest-Zeitraum in Monaten
        max_tickers: Maximal analysierte Ticker (Performance-Limit)
        min_price: Mindestpreis Filter
        min_volume: Mindestvolumen/Tag Filter
        progress_callback: (pct, text) Callback für UI

    Returns:
        dict mit trades, stats_by_grade, summary
    """
    from modules.signal_tracker import _infer_stock_horizon_bars, _is_us_equity_session

    end_dt = datetime.now() - timedelta(days=1)
    window_size = 50  # 50 Bars für MACD (braucht 35+) und bessere Pattern-Erkennung
    trade_hold_bars = _infer_stock_horizon_bars("bi_long" if direction.lower() == "long" else "bi_short", None, None)
    # 90 completed sessions for the shared structural profile, not just the
    # 50 indicator bars. Calendar padding includes weekends and holidays.
    structure_window = 90
    start_dt = end_dt - timedelta(days=months * 30 + 150)
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if _is_us_equity_session(current.date()):
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if not trading_days:
        return {"trades": [], "stats_by_grade": {}, "summary": {}, "n_tickers": 0}

    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}
    total_tickers_seen = set()
    failed_fetch_days = 0  # NACHAUDIT H3-Rest: hart gescheiterte Fetch-Tage sichtbar machen
    failed_fetch_dates = []

    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.5,
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )

        day_data = fetch_grouped_daily(poly_key, date_str)
        if day_data is None:
            # NACHAUDIT H3-Rest: None = Fetch nach Retries hart gescheitert
            # (kein valider Leertag). Nicht still verschlucken, sondern zaehlen —
            # an diesem Tag sind Stop-/TP-Treffer fuer ALLE Ticker unsichtbar.
            failed_fetch_days += 1
            failed_fetch_dates.append(date_str)
            continue
        if not day_data:
            continue

        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue

            _t = ticker.upper()
            _skip_prefixes = (
                "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
                "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
                "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
                "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
                "XRPT","XXRP","XETH","BITO","GBTC","ETHE","BITW","CONL",
                "MSOX","BTFX","SOLT","NEBX","AREC","MAXI","TNA","TZA"
            )
            if any(_t.startswith(p) for p in _skip_prefixes):
                continue

            price = r.get("c", 0)
            volume = r.get("v", 0)
            if price <= 0:
                continue
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
                "time": date_str,
            }
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)

    # Sortiere und filtere Ticker — Mid-Caps (500K-10M Vol) sind BI-Goldzone
    ticker_avg_vol = {}
    for t, bars_list in ticker_history.items():
        if len(bars_list) >= (window_size + 5):  # Genug History für Window + Simulation
            avg_vol = _initial_universe_average_volume(bars_list, window_size)
            if avg_vol is not None:
                ticker_avg_vol[t] = avg_vol

    # Priorisiere Mid-Cap-Volumen (500K-10M) — hier passieren die besten Breakouts
    # Aber schliesse High-Volume nicht komplett aus (niedrigere Prio)
    midcap_tickers = {t: v for t, v in ticker_avg_vol.items() if 500_000 <= v <= 10_000_000}
    largecap_tickers = {t: v for t, v in ticker_avg_vol.items() if v > 10_000_000}

    # Mid-Caps zuerst (sortiert nach Vol), dann Large-Caps auffüllen
    sorted_midcap = sorted(midcap_tickers.keys(), key=lambda t: midcap_tickers[t], reverse=True)
    sorted_largecap = sorted(largecap_tickers.keys(), key=lambda t: largecap_tickers[t], reverse=True)
    tickers_to_test = (sorted_midcap + sorted_largecap)[:max_tickers]

    # ============================================================
    # PASS 2: Rolling-Window Breakout Imminent Analyse + Trade Sim
    # ============================================================
    all_trades = []
    signals_found = 0
    blocked_until = {}  # ticker -> latest bar occupied by a pending/open trade

    for t_idx, ticker in enumerate(tickers_to_test):
        if progress_callback and t_idx % 20 == 0:
            progress_callback(
                0.5 + (t_idx / len(tickers_to_test)) * 0.5,
                f" Analysiere {ticker} ({t_idx+1}/{len(tickers_to_test)}) | {signals_found} Signale"
            )

        bars = ticker_history[ticker]
        date_to_index = {bar.get("date"): index for index, bar in enumerate(bars)}

        # Für jeden Tag ab test_start: 50-Bar Fenster → BI V2 Analyse
        for idx in range(window_size, len(bars)):
            if bars[idx]["date"] < test_start:
                continue

            # Do not open a second simulated setup while the first is pending
            # or active. A fixed cooldown can still create overlapping trades.
            if idx <= blocked_until.get(ticker, -1):
                continue

            # 50-Bar Rolling Window (genug für MACD 26+9=35)
            window = bars[idx-window_size:idx]
            if (
                float(window[-1].get("close") or 0) < min_price
                or float(window[-1].get("volume") or 0) < min_volume
            ):
                continue

            result = analyze_breakout_imminent(window, direction=direction)
            # V2.1+: Returns 8 values (mit smart_money_fires, smart_money_hits)
            if len(result) == 8:
                is_valid, bi_score, bi_max, details, confidence, grade, sm_fires, sm_hits = result
            else:
                is_valid, bi_score, bi_max, details, confidence, grade = result
                sm_fires, sm_hits = 0, 0

            if not is_valid:
                continue

            # Scanner and historical study consume the same plan builder.
            # Execution remains explicitly a daily approximation, not SMTP replay.
            signal_day = bars[idx-1]["date"]
            as_of = datetime.fromisoformat(str(signal_day)[:10]).replace(
                hour=23, minute=59, second=59, tzinfo=dt.timezone.utc,
            )
            plan = build_bi_trade_plan(
                bars[max(0, idx-structure_window):idx], direction=direction,
                range_days=getattr(result, "consolidation_days", None),
                live_price=float(window[-1]["close"]), as_of=as_of,
            )
            if not plan.get("accepted"):
                continue
            signals_found += 1
            trade_result = _simulate_bi_plan_daily(
                bars, idx, plan, direction, horizon_bars=trade_hold_bars,
            )
            trade_result.update({
                "ticker": ticker, "signal_date": signal_day, "grade": grade,
                "score": bi_score, "max_score": bi_max, "confidence": confidence,
                "smart_money_fires": sm_fires, "smart_money_hits": sm_hits,
                "direction": direction.upper(), "rr_planned": plan["geometry"]["rr"],
                "trade_hold_bars": trade_hold_bars, "entry_wait_bars": trade_hold_bars,
            })
            if trade_result["outcome"] == "UNRESOLVED":
                blocked_until[ticker] = len(bars) - 1
            else:
                blocked_until[ticker] = conservative_trade_exit_index(
                    trade_result, date_to_index, idx,
                )
            all_trades.append(trade_result)

    # ============================================================
    # STATISTIKEN nach Grade
    # ============================================================
    stats_by_grade = {}
    for g in ["S", "A", "B", "C", "D"]:
        grade_trades = [
            t for t in all_trades
            if t["grade"] == g and _is_decided_backtest_trade(t)
        ]
        if not grade_trades:
            continue

        winners = [t for t in grade_trades if t["is_winner"]]
        losers = [t for t in grade_trades if not t["is_winner"]]

        total_pnl = sum(t["pnl_pct"] for t in grade_trades)
        avg_pnl = total_pnl / len(grade_trades) if grade_trades else 0
        avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loser = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0

        win_rate = len(winners) / len(grade_trades) * 100 if grade_trades else 0

        # Profit Factor
        gross_profit = sum(t["pnl_pct"] for t in winners)
        gross_loss = abs(sum(t["pnl_pct"] for t in losers))
        profit_factor_summary = profit_factor_metrics(gross_profit, gross_loss)

        # Avg R
        avg_r = sum(t["r_multiple"] for t in grade_trades) / len(grade_trades) if grade_trades else 0

        tp1_hits = sum(1 for t in grade_trades if t.get("tp1_hit", False))
        tp2_hits = sum(1 for t in grade_trades if t.get("outcome") in {"TP2", "BLENDED_TP"})

        stats_by_grade[g] = {
            "total": len(grade_trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor_summary["value"],
            "profit_factor_display": profit_factor_summary["display"],
            "profit_factor_unbounded": profit_factor_summary["unbounded"],
            "avg_r": round(avg_r, 2),
            "tp1_rate": round(tp1_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
            "tp2_rate": round(tp2_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
        }
        stats_by_grade[g].update(_backtest_uncertainty_metrics(grade_trades))

    decided_trades = sorted(
        (t for t in all_trades if _is_decided_backtest_trade(t)),
        key=chronological_trade_key,
    )
    filled_trades = [
        t for t in all_trades
        if t.get("entry_filled") is True
    ]
    no_fill_count = sum(
        1 for t in all_trades
        if str(t.get("outcome") or "").upper() == "NO_FILL"
    )
    unresolved_count = sum(
        1 for t in all_trades
        if str(t.get("outcome") or "").upper() == "UNRESOLVED"
    )

    # Calculate Max Drawdown from equity curve
    equity = 10000
    peak = equity
    max_dd = 0
    for trade in decided_trades:
        equity *= (1 + trade["pnl_pct"] / 100)
        peak = max(peak, equity)
        dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    summary = {
        "total_signals": signals_found,
        "total_filled": len(filled_trades),
        "total_decided": len(decided_trades),
        "no_fill": no_fill_count,
        "unresolved": unresolved_count,
        "win_rate": round(sum(1 for t in decided_trades if t["is_winner"]) / len(decided_trades) * 100, 1) if decided_trades else 0,
        "avg_pnl": round(sum(t["pnl_pct"] for t in decided_trades) / len(decided_trades), 2) if decided_trades else 0,
        "total_pnl": round(sum(t["pnl_pct"] for t in decided_trades), 2) if decided_trades else 0,
        "max_drawdown": round(max_dd, 2),
        "n_tickers": len(tickers_to_test),
        "n_tickers_total": len(total_tickers_seen),
        "failed_fetch_days": failed_fetch_days,
        "data_quality": _backtest_data_quality(all_trades, failed_fetch_dates=failed_fetch_dates),
        "n_midcap": len(sorted_midcap),
        "n_largecap": len(sorted_largecap),
        "direction": direction,
        "months": months,
        "setup_horizons": {
            "entry_wait_bars": trade_hold_bars,
            "trade_hold_bars": trade_hold_bars,
        },
        "methodology": "shared_production_bi_plan_daily_execution_variant",
        "plan_version": BI_PLAN_VERSION,
        "parity_scope": "scanner_contract_and_plan_not_entire_universe_gate_parity",
        "execution_model": "daily_next_session_50_50_be_after_tp1_v2",
        "live_delivery_equivalent": False,
        "methodology_warnings": [
            "daily_ohlc_intrabar_path_bounded_when_ambiguous",
            "current_static_universe_survivorship_bias",
            "signal_close_market_orders_use_next_session_open",
            "be_after_tp1_is_not_live_delivered_be_after_plus_one_r",
            "smtp_quote_and_delivery_filters_not_simulated",
            "live_liquidity_spac_pump_rvol_grade_and_universe_gates_not_fully_replayed",
            "structural_history_uses_up_to_90_completed_sessions",
        ],
    }
    summary.update(_backtest_uncertainty_metrics(decided_trades))

    del ticker_history

    if progress_callback:
        progress_callback(1.0, f"[OK] BI V2 Backtest fertig! {signals_found} Signale, {len(decided_trades)} entschieden")

    return {"trades": all_trades, "stats_by_grade": stats_by_grade, "summary": summary}


def run_biotech_backtest(poly_key, months=6, max_tickers=100,
                          min_price=2.0, min_volume=100000, progress_callback=None):
    """
     BioTech Catalyst Backtest — Technisches Setup + Volume Confirmation.

    KONZEPT: Biotech-Aktien werden CATALYST-GETRIEBEN getradet.
    Da historische Catalyst-Daten (FDA Dates, News) nicht verfügbar sind,
    nutzt dieser Backtest VOLUME SPIKES als Proxy für Catalyst-Aktivität:
    - Unusual Volume (RVOL >= 2.0) = Smart Money kauft vor Catalyst
    - Kombiniert mit Technical Setup Score für Qualitätsfilter
    - Biotech-spezifische Parameter: breitere Stops, größere Targets

    Entry-Logik (NICHT Breakout-Retest wie BI, sondern Momentum):
    1. Signal: RVOL >= 2.0 + Technical Score >= 10/20 + Uptrend
    2. Entry: Next Day Open (Momentum-Einstieg)
    3. Stop: 1.5 × ATR unter Entry (breiter wegen Biotech-Volatilität)
    4. TP1: 2.0R, TP2: 4.0R (Biotech-Moves sind größer als normale Aktien)
    5. Max Hold: 15 Tage (Catalyst-Trades sind kürzer)

    Architektur: 2-Pass wie BI V2 (Grouped Daily API)
    - Pass 1: Lade alle Tage, filtere auf Biotech-Ticker
    - Pass 2: Rolling-Window Analyse + Trade Simulation

    Args:
        poly_key: Polygon API Key
        months: Backtest-Zeitraum in Monaten
        max_tickers: Maximal analysierte Ticker
        min_price: Mindestpreis ($2 für Biotech — Penny Stocks inkl.)
        min_volume: Mindestvolumen/Tag
        progress_callback: (pct, text) Callback für UI

    Returns:
        dict mit trades, stats_by_grade, summary
    """
    from modules.signal_tracker import _is_us_equity_session

    end_dt = datetime.now() - timedelta(days=1)
    trade_hold_bars = 15
    window_size = 50  # 50 Bars für technische Indikatoren (SMA50 braucht 50)
    start_dt = end_dt - timedelta(days=months * 30 + window_size + 20)
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Biotech Universum — nutze kuratierte Liste
    biotech_set = set(t.upper() for t in BIOTECH_BACKTEST_UNIVERSE)

    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if _is_us_equity_session(current.date()):
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if not trading_days:
        return {"trades": [], "stats_by_grade": {}, "summary": {}, "n_tickers": 0}

    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}
    total_tickers_seen = set()
    failed_fetch_days = 0  # NACHAUDIT H3-Rest: hart gescheiterte Fetch-Tage sichtbar machen
    failed_fetch_dates = []

    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.5,
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )

        day_data = fetch_grouped_daily(poly_key, date_str)
        if day_data is None:
            # NACHAUDIT H3-Rest: None = Fetch nach Retries hart gescheitert
            # (kein valider Leertag). Nicht still verschlucken, sondern zaehlen —
            # an diesem Tag sind Stop-/TP-Treffer fuer ALLE Ticker unsichtbar.
            failed_fetch_days += 1
            failed_fetch_dates.append(date_str)
            continue
        if not day_data:
            continue

        for ticker, r in day_data.items():
            # Nur Biotech-Ticker aus kuratieter Liste
            if ticker.upper() not in biotech_set:
                continue

            if len(ticker) > 5 or "." in ticker:
                continue

            price = r.get("c", 0)
            volume = r.get("v", 0)
            if price <= 0:
                continue
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
                "time": date_str,
            }
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)

    # Filtere Ticker mit genug History
    valid_tickers = {t: bars for t, bars in ticker_history.items()
                     if len(bars) >= (window_size + 5)}

    # Sortiere nach avg Volumen (aktivste zuerst)
    ticker_avg_vol = {}
    for t, bars_list in valid_tickers.items():
        avg_vol = _initial_universe_average_volume(bars_list, window_size)
        if avg_vol is not None:
            ticker_avg_vol[t] = avg_vol

    tickers_to_test = sorted(ticker_avg_vol.keys(),
                              key=lambda t: ticker_avg_vol[t], reverse=True)[:max_tickers]

    # ============================================================
    # PASS 2: Rolling-Window Analyse + Trade Simulation
    # ============================================================
    all_trades = []
    signals_found = 0
    blocked_until = {}  # ticker -> latest bar occupied by an open trade

    for t_idx, ticker in enumerate(tickers_to_test):
        if progress_callback and t_idx % 10 == 0:
            progress_callback(
                0.5 + (t_idx / len(tickers_to_test)) * 0.5,
                f" Analysiere {ticker} ({t_idx+1}/{len(tickers_to_test)}) | {signals_found} Signale"
            )

        bars = ticker_history[ticker]
        date_to_index = {bar.get("date"): index for index, bar in enumerate(bars)}

        for idx in range(window_size, len(bars)):
            if bars[idx]["date"] < test_start:
                continue

            # Never count overlapping positions for the same ticker.
            if idx <= blocked_until.get(ticker, -1):
                continue

            # 50-Bar Rolling Window
            window = bars[idx-window_size:idx]
            if (
                float(window[-1].get("close") or 0) < min_price
                or float(window[-1].get("volume") or 0) < min_volume
            ):
                continue

            # === BIOTECH TECHNICAL SCORE (offline) ===
            tech_result = _compute_biotech_technical_from_bars(window)
            tech_score = tech_result["technical_score"]
            rvol = tech_result["rvol"]

            # === SIGNAL-FILTER ===
            # 1. RVOL >= 2.0 = Unusual Volume (Proxy für Catalyst-Aktivität)
            if rvol < 2.0:
                continue

            # 2. Technical Score >= 10/20 (mindestens mittlere Qualität)
            if tech_score < 10:
                continue

            # 3. Trend-Filter: SMA20 muss über SMA50 (kein Abwärtstrend)
            w_closes = [b["close"] for b in window]
            sma20 = sum(w_closes[-20:]) / 20
            sma50 = sum(w_closes[-50:]) / 50
            if sma20 <= sma50 * 0.97:
                continue  # Deutlicher Abwärtstrend → kein Entry

            # 4. Price > $2 und Volumen-Minimum nochmal prüfen
            if window[-1]["close"] < min_price:
                continue

            # === GRADING nach Technical Score + RVOL ===
            combined = tech_score + min(10, int(rvol * 2))  # Max 10 Bonus für RVOL
            if combined >= 26:
                grade = "S"
            elif combined >= 22:
                grade = "A"
            elif combined >= 18:
                grade = "B"
            else:
                grade = "C"

            # === ATR für Stop/Target Berechnung ===
            atr_10 = calculate_wilder_atr(window, period=10)
            if atr_10 <= 0:
                continue

            # === ENTRY/STOP/TARGET BERECHNUNG ===
            # Entry: Next Day Open (Momentum-Einstieg nach Volume Spike)
            if idx >= len(bars):
                continue

            entry_bar = bars[idx]
            entry_price = entry_bar["open"]
            if entry_price <= 0:
                continue

            # Slippage: 0.1% (Biotech-Spreads sind breiter als Blue Chips)
            slippage = 0.001
            entry_price *= (1 + slippage)

            # Stop: 1.5 × ATR unter Entry (breiter wegen Biotech-Vola)
            stop_distance = atr_10 * 1.5
            stop_price = entry_price - stop_distance

            if stop_price <= 0 or stop_price >= entry_price:
                continue

            # Grade-abhängige Targets
            # S/A: TP1=2.0R, TP2=4.0R (starkes Setup → größere Targets)
            # B/C: TP1=1.5R, TP2=3.0R (schwächeres Setup → konservativere Targets)
            if grade in ("S", "A"):
                tp1_rr, tp2_rr = 2.0, 4.0
            else:
                tp1_rr, tp2_rr = 1.5, 3.0

            risk = stop_distance
            tp1_price = entry_price + risk * tp1_rr
            tp2_price = entry_price + risk * tp2_rr

            # R:R Minimum Check: use the same blended TP1/TP2 model as alerts.
            rr = (tp1_rr + tp2_rr) / 2.0
            if rr < 1.5:
                continue

            signals_found += 1

            # === TRADE SIMULATION (Biotech-spezifisch) ===
            trade_result = {
                "ticker": ticker,
                "signal_date": bars[idx - 1]["date"],
                "grade": grade,
                "score": tech_score,
                "max_score": 20,
                "rvol": rvol,
                "direction": "LONG",
                "entry_target": round(entry_price, 4),
                "stop_target": round(stop_price, 4),
                "tp1_target": round(tp1_price, 4),
                "tp2_target": round(tp2_price, 4),
                "rr_planned": round(rr, 2),
                "trade_hold_bars": trade_hold_bars,
            }

            # Biotech fills at the next session open, so that session's full
            # OHLC is post-entry. The shared engine reports a conservative
            # stop-first result and a target-first upper bound whenever daily
            # candles cannot reveal the true intrabar order.
            simulated = _simulate_50_50_daily_exit(
                bars,
                idx,
                trade_hold_bars,
                "LONG",
                entry_price,
                stop_price,
                tp1_price,
                tp2_price,
                trail_fraction=0.66,
                exit_slippage=slippage,
                fee_pct=0.2,
                first_bar_order_unknown=False,
            )
            if simulated is None:
                # The next-session open was a real modelled fill. Missing
                # follow-up bars make the outcome unknown, not unfilled.
                trade_result["outcome"] = "UNRESOLVED"
                trade_result["pnl_pct"] = 0
                trade_result["r_multiple"] = 0
                trade_result["is_winner"] = False
                trade_result["actual_entry"] = round(entry_price, 4)
                trade_result["entry_date"] = entry_bar["date"]
                trade_result["evaluation_status"] = "NO_POST_ENTRY_DATA"
                blocked_until[ticker] = len(bars) - 1
            else:
                trade_result["actual_entry"] = round(entry_price, 4)
                trade_result["entry_date"] = entry_bar["date"]
                trade_result.update(simulated)
                trade_result["outcome"] = simulated["exit_reason"]
                blocked_until[ticker] = conservative_trade_exit_index(
                    trade_result,
                    date_to_index,
                    idx,
                )

            all_trades.append(trade_result)

    # ============================================================
    # STATISTIKEN nach Grade
    # ============================================================
    stats_by_grade = {}
    for g in ["S", "A", "B", "C"]:
        grade_trades = [
            t for t in all_trades
            if t["grade"] == g and _is_decided_backtest_trade(t)
        ]
        if not grade_trades:
            continue

        winners = [t for t in grade_trades if t["is_winner"]]
        losers = [t for t in grade_trades if not t["is_winner"]]

        total_pnl = sum(t["pnl_pct"] for t in grade_trades)
        avg_pnl = total_pnl / len(grade_trades) if grade_trades else 0
        avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loser = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0
        win_rate = len(winners) / len(grade_trades) * 100 if grade_trades else 0

        gross_profit = sum(t["pnl_pct"] for t in winners)
        gross_loss = abs(sum(t["pnl_pct"] for t in losers))
        profit_factor_summary = profit_factor_metrics(gross_profit, gross_loss)

        avg_r = sum(t["r_multiple"] for t in grade_trades) / len(grade_trades) if grade_trades else 0

        tp1_hits = sum(1 for t in grade_trades if t.get("tp1_hit", False))
        tp2_hits = sum(1 for t in grade_trades if t.get("outcome") in {"TP2", "BLENDED_TP"})

        stats_by_grade[g] = {
            "total": len(grade_trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor_summary["value"],
            "profit_factor_display": profit_factor_summary["display"],
            "profit_factor_unbounded": profit_factor_summary["unbounded"],
            "avg_r": round(avg_r, 2),
            "tp1_rate": round(tp1_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
            "tp2_rate": round(tp2_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
        }
        stats_by_grade[g].update(_backtest_uncertainty_metrics(grade_trades))

    decided_trades = sorted(
        (t for t in all_trades if _is_decided_backtest_trade(t)),
        key=chronological_trade_key,
    )
    filled_trades = [
        t for t in all_trades
        if str(t.get("outcome") or "").upper() != "NO_FILL"
    ]
    no_fill_count = sum(
        1 for t in all_trades
        if str(t.get("outcome") or "").upper() == "NO_FILL"
    )
    unresolved_count = sum(
        1 for t in all_trades
        if str(t.get("outcome") or "").upper() == "UNRESOLVED"
    )

    # Calculate Max Drawdown from equity curve
    equity = 10000
    peak = equity
    max_dd = 0
    for trade in decided_trades:
        equity *= (1 + trade["pnl_pct"] / 100)
        peak = max(peak, equity)
        dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    summary = {
        "total_signals": signals_found,
        "total_filled": len(filled_trades),
        "total_decided": len(decided_trades),
        "no_fill": no_fill_count,
        "unresolved": unresolved_count,
        "win_rate": round(sum(1 for t in decided_trades if t["is_winner"]) / len(decided_trades) * 100, 1) if decided_trades else 0,
        "avg_pnl": round(sum(t["pnl_pct"] for t in decided_trades) / len(decided_trades), 2) if decided_trades else 0,
        "total_pnl": round(sum(t["pnl_pct"] for t in decided_trades), 2) if decided_trades else 0,
        "max_drawdown": round(max_dd, 2),
        "n_tickers": len(tickers_to_test),
        "n_tickers_total": len(total_tickers_seen),
        "failed_fetch_days": failed_fetch_days,
        "data_quality": _backtest_data_quality(all_trades, failed_fetch_dates=failed_fetch_dates),
        "n_biotech_universe": len(biotech_set),
        "months": months,
        "trade_hold_bars": trade_hold_bars,
        "methodology": "technical_volume_proxy_not_historical_catalyst_backtest",
        "methodology_warnings": [
            "volume_spike_proxy_not_historical_catalyst",
            "current_static_universe_survivorship_bias",
        ],
    }
    summary.update(_backtest_uncertainty_metrics(decided_trades))

    del ticker_history

    if progress_callback:
        progress_callback(1.0, f"[OK] BioTech Backtest fertig! {signals_found} Signale, {len(decided_trades)} entschieden")

    return {"trades": all_trades, "stats_by_grade": stats_by_grade, "summary": summary}


def simulate_trade(bars, signal_idx, strategy):
    """
    Simuliert einen Trade basierend auf Signal-Tag und Strategie-Regeln.
    """
    direction = strategy["direction"]
    entry_type = strategy["entry"]
    stop_pct = strategy["stop_pct"]
    tp1_rr = strategy["tp1_rr"]
    tp2_rr = strategy["tp2_rr"]
    target_rr = (float(tp1_rr) + float(tp2_rr)) / 2.0
    max_hold = strategy["max_hold_days"]
    
    signal_day = bars[signal_idx]
    
    # === ENTRY BESTIMMEN ===
    entry_trigger = None
    entry_fill_basis = entry_type
    if entry_type == "next_open":
        if signal_idx + 1 >= len(bars):
            return None
        trade_start_idx = signal_idx + 1
        entry_price = bars[trade_start_idx]["open"]
        entry_date = bars[trade_start_idx]["date"]
    elif entry_type == "at_close":
        entry_price = signal_day["close"]
        trade_start_idx = signal_idx + 1
        entry_date = signal_day["date"]
    elif entry_type == "prev_high":
        if signal_idx < 1 or signal_idx + 1 >= len(bars):
            return None
        trade_start_idx = signal_idx + 1
        trigger_bar = bars[trade_start_idx]
        entry_trigger = bars[signal_idx - 1]["high"]
        if direction == "long":
            if trigger_bar["open"] >= entry_trigger:
                entry_price = trigger_bar["open"]
                entry_fill_basis = "gap_open_above_trigger"
            elif trigger_bar["high"] >= entry_trigger:
                entry_price = entry_trigger
                entry_fill_basis = "trigger_touch"
            else:
                return None
        else:
            # Built-in prev_high is LONG-only; keep custom short rules symmetric.
            if trigger_bar["open"] <= entry_trigger:
                entry_price = trigger_bar["open"]
                entry_fill_basis = "gap_open_below_trigger"
            elif trigger_bar["low"] <= entry_trigger:
                entry_price = entry_trigger
                entry_fill_basis = "trigger_touch"
            else:
                return None
        entry_date = trigger_bar["date"]
    else:
        return None
    
    if entry_price <= 0:
        return None
    
    # === SLIPPAGE: 0.05% pro Seite (realistisch für Liquid Stocks) ===
    slippage = 0.0005
    if direction == "long":
        entry_price *= (1 + slippage)  # Kaufe leicht höher
    else:
        entry_price *= (1 - slippage)  # Shorte leicht tiefer
    
    # === MINDESTENS 1 Folgetag nötig für sinnvolle Simulation ===
    if trade_start_idx >= len(bars):
        return None
    
    # === STOP & TARGETS BERECHNEN ===
    risk = entry_price * stop_pct
    
    if direction == "long":
        stop_price = entry_price - risk
        tp1_price = entry_price + risk * tp1_rr
        tp2_price = entry_price + risk * tp2_rr
        blended_target_price = entry_price + risk * target_rr
    else:  # short
        stop_price = entry_price + risk
        tp1_price = entry_price - risk * tp1_rr
        tp2_price = entry_price - risk * tp2_rr
        blended_target_price = entry_price - risk * target_rr
    initial_stop_price = stop_price
    
    simulated = _simulate_50_50_daily_exit(
        bars,
        trade_start_idx,
        max_hold,
        direction.upper(),
        entry_price,
        stop_price,
        tp1_price,
        tp2_price,
        trail_fraction=0.0,
        exit_slippage=slippage,
        fee_pct=0.2,
        first_bar_order_unknown=entry_fill_basis == "trigger_touch",
    )
    if simulated is None:
        return None

    result = {
        "signal_date": signal_day["date"],
        "entry_date": entry_date,
        "entry_trigger": round(entry_trigger, 4) if entry_trigger is not None else None,
        "entry_fill_basis": entry_fill_basis,
        "entry_price": round(entry_price, 2),
        "stop_price": round(initial_stop_price, 2),
        "tp1_price": round(tp1_price, 2),
        "tp2_price": round(tp2_price, 2),
        "blended_target_price": round(blended_target_price, 2),
        "target_model": "50_50_tp1_tp2",
    }
    result.update(simulated)
    for key in ("exit_price", "exit_price_upper", "pnl_pct", "pnl_pct_upper", "r_multiple", "r_multiple_upper"):
        if result.get(key) is not None:
            result[key] = round(float(result[key]), 2)
    return result


def run_full_backtest(poly_key, strategies=None, tickers=None, months=6, progress_callback=None):
    """
    Führt vollständigen Backtest über alle Strategien und Ticker durch.
    
    Args:
        poly_key: Polygon API Key
        strategies: Liste von Strategie-Namen (None = alle)
        tickers: Liste von Tickern (None = BACKTEST_UNIVERSE)
        months: Anzahl Monate zurück
        progress_callback: Funktion für Fortschrittsanzeige
    
    Returns:
        dict mit allen Ergebnissen
    """
    import time
    from datetime import datetime, timedelta
    
    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    if tickers is None:
        tickers = BACKTEST_UNIVERSE
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=months * 30 + 30)).strftime("%Y-%m-%d")  # +30 für RVOL Lookback
    
    test_start = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    all_results = {s: _BacktestTradeList() for s in strategies}
    ticker_data_cache = {}
    seen_signals = set()  # Dedup per strategy, ticker and signal date.
    
    total_tickers = len(tickers)
    skipped_no_data = 0
    unavailable_tickers = []
    skipped_too_short = 0
    total_signals = 0
    
    for t_idx, ticker in enumerate(tickers):
        if progress_callback:
            progress_callback(t_idx / total_tickers, f" {ticker} ({t_idx+1}/{total_tickers})")
        
        # Daten holen (mit Cache)
        if ticker not in ticker_data_cache:
            bars = fetch_backtest_daily_data(poly_key, ticker, start_date, end_date)
            if not bars:
                skipped_no_data += 1
                unavailable_tickers.append(ticker)
                continue
            if len(bars) < 30:
                skipped_too_short += 1
                continue
            ticker_data_cache[ticker] = bars
        
        bars = ticker_data_cache[ticker]
        date_to_index = {bar.get("date"): index for index, bar in enumerate(bars)}
        last_exit_by_strategy = {name: -1 for name in strategies}
        
        # Für jeden Tag: Metriken berechnen und Signale prüfen
        for idx in range(21, len(bars)):  # Start bei 21 für RVOL-Lookback
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics:
                continue
            
            # Min-Preis Filter
            if metrics["price"] <= 0:
                continue
            
            # Jede Strategie prüfen
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                if idx <= last_exit_by_strategy[strat_name]:
                    continue
                
                # Preis-Filter
                min_price = strat.get("min_price", 1.0)
                if metrics["price"] < min_price:
                    continue
                
                # Signal prüfen
                signal_metrics = evaluate_rule_signal(bars, idx, strat)
                if signal_metrics:
                    # Different strategies may legitimately fire on the same ticker/day.
                    dedup_key = (ticker, bars[idx]["date"], strat_name)
                    if dedup_key in seen_signals:
                        continue
                    
                    total_signals += 1
                    # Trade simulieren
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        seen_signals.add(dedup_key)
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(signal_metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(signal_metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
                        last_exit_by_strategy[strat_name] = conservative_trade_exit_index(
                            trade,
                            date_to_index,
                            idx,
                        )
    
    if progress_callback:
        loaded = len(ticker_data_cache)
        progress_callback(1.0, f"[OK] Fertig! {loaded} geladen, {skipped_no_data} keine Daten, {skipped_too_short} zu kurz, {total_signals} Signale")
    
    _attach_backtest_data_quality(all_results, unavailable_tickers=unavailable_tickers)
    return all_results


def compute_backtest_stats(trades):
    """Berechnet Performance-Statistiken für eine Liste von Trades."""
    data_quality = _backtest_data_quality(trades or ())
    # Preserve attached coverage even for an empty strategy list.
    if isinstance(trades, _BacktestTradeList):
        data_quality = _backtest_data_quality(trades)
    input_trades = list(trades or [])
    no_fill_count = sum(
        1 for trade in input_trades
        if str((trade or {}).get("outcome") or "").upper() == "NO_FILL"
    )
    unresolved_count = sum(
        1 for trade in input_trades
        if str((trade or {}).get("outcome") or "").upper() == "UNRESOLVED"
    )
    total_filled = sum(1 for trade in input_trades
                       if str((trade or {}).get("outcome") or "").upper() != "NO_FILL"
                       and (trade or {}).get("entry_filled") is not False)
    # Legacy callers may omit ``outcome``. Only rows that explicitly declare
    # themselves non-decided are excluded from performance mathematics.
    trades = [
        trade for trade in input_trades
        if str((trade or {}).get("outcome") or "").upper()
        not in _NON_DECIDED_BACKTEST_OUTCOMES
    ]
    if not trades:
        empty = {
            "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0,
            "avg_pnl": 0, "avg_win": 0, "avg_loss": 0,
            "avg_r": 0, "best_r": 0, "worst_r": 0,
            "avg_hold": 0, "tp1_rate": 0, "tp2_rate": 0, "stop_rate": 0,
            "full_stop_rate": 0, "post_tp1_stop_rate": 0, "eod_rate": 0,
            "profit_factor": 0, "profit_factor_display": "0.00",
            "profit_factor_unbounded": False, "expectancy": 0, "total_r": 0,
            "total_input_trades": len(input_trades),
            "total_filled": total_filled,
            "total_decided": 0,
            "no_fill": no_fill_count,
            "unresolved": unresolved_count,
            "statistics_scope": "decided_filled_trades_only",
            "data_quality": data_quality,
        }
        empty.update(_backtest_uncertainty_metrics([]))
        return empty
    
    winners = [t for t in trades if t["is_winner"]]
    losers = [t for t in trades if not t["is_winner"]]
    
    total = len(trades)
    win_count = len(winners)
    
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total
    avg_r = sum(t["r_multiple"] for t in trades) / total
    total_r = sum(t["r_multiple"] for t in trades)
    
    avg_win = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    avg_loss = abs(sum(t["pnl_pct"] for t in losers) / len(losers)) if losers else 0
    
    gross_profit = sum(t["r_multiple"] for t in winners) if winners else 0
    gross_loss = abs(sum(t["r_multiple"] for t in losers)) if losers else 0
    profit_factor_summary = profit_factor_metrics(gross_profit, gross_loss)
    
    tp1_reasons = {"TP1", "TP2", "BLENDED_TP", "TP1_STOP", "TP1+EOD"}
    tp2_reasons = {"TP2", "BLENDED_TP"}
    tp1_count = sum(
        1 for trade in trades
        if bool(trade.get("tp1_hit"))
        or str(trade.get("exit_reason") or "").upper() in tp1_reasons
    )
    tp2_count = sum(
        1 for trade in trades
        if str(trade.get("exit_reason") or "").upper() in tp2_reasons
    )
    full_stop_count = sum(
        1 for trade in trades
        if str(trade.get("exit_reason") or "").upper() == "STOP"
    )
    post_tp1_stop_count = sum(
        1 for trade in trades
        if str(trade.get("exit_reason") or "").upper() == "TP1_STOP"
    )
    stop_count = full_stop_count + post_tp1_stop_count
    eod_count = sum(
        1 for trade in trades
        if str(trade.get("exit_reason") or "").upper() in {"EOD", "TP1+EOD"}
    )
    
    stats = {
        "total_trades": total,
        "winners": win_count,
        "losers": len(losers),
        "win_rate": round(win_count / total * 100, 1) if total > 0 else 0,
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_r": round(avg_r, 2),
        "total_r": round(total_r, 1),
        "best_r": round(max(t["r_multiple"] for t in trades), 2) if trades else 0,
        "worst_r": round(min(t["r_multiple"] for t in trades), 2) if trades else 0,
        "avg_hold": round(sum(t["bars_held"] for t in trades) / total, 1) if total > 0 else 0,
        "tp1_rate": round(tp1_count / total * 100, 1) if total > 0 else 0,
        "tp2_rate": round(tp2_count / total * 100, 1) if total > 0 else 0,
        "stop_rate": round(stop_count / total * 100, 1) if total > 0 else 0,
        "full_stop_rate": round(full_stop_count / total * 100, 1) if total > 0 else 0,
        "post_tp1_stop_rate": round(post_tp1_stop_count / total * 100, 1) if total > 0 else 0,
        "eod_rate": round(eod_count / total * 100, 1) if total > 0 else 0,
        "profit_factor": profit_factor_summary["value"],
        "profit_factor_display": profit_factor_summary["display"],
        "profit_factor_unbounded": profit_factor_summary["unbounded"],
        "expectancy": round(avg_r, 2),
        "total_input_trades": len(input_trades),
        "total_filled": total_filled,
        "total_decided": total,
        "no_fill": no_fill_count,
        "unresolved": unresolved_count,
        "statistics_scope": "decided_filled_trades_only",
        "data_quality": data_quality,
    }
    stats.update(_backtest_uncertainty_metrics(trades))
    return stats


