"""
Strategy Definitions Module
============================
Extracted from scanner.py - Contains all strategy definitions and related functions.

Strategy Dictionaries:
  - STRATEGIES: Main stock strategies
  - FUTURES_STRATEGIES: Futures trading strategies
  - FOREX_STRATEGIES: Forex pair strategies
  - CRYPTO_STRATEGIES: Cryptocurrency strategies
  - INTERNATIONAL_STRATEGIES: International stock strategies
  - BACKTEST_STRATEGY_RULES: Backtesting rules for strategies

Functions:
  - get_strategies_for_market(): Returns appropriate strategy dict for market type
  - apply_strategy(): Applies a selected strategy (requires streamlit session_state)
  - classify_pm_setup(): Classifies pre-market setups with technical analysis
"""

try:
    import streamlit as st
except ImportError:  # FastAPI/server and backtests do not need the legacy UI.
    st = None


STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen UND Bewegung",
        "filters": {"RVOL": (2.0, 50.0), "Change %": (2.0, 100.0)},
        "logic": "RVOL > 2.0 + Change > 2% = echtes Interesse mit Richtung"
    },
    "Bull Flag": {
        "description": "Echte Multi-Day Flag: Fahnenstange (2-7d) + Konsolidierung mit 20 Tageskerzen",
        "filters": {"Change %": (-5.0, 5.0), "RVOL": (0.05, 3.0)},
        "logic": "20d History-Analyse: Pole ≥+5% + enge Konsolidierung + sinkendes Volumen + Retracement <50%",
        "needs_history": True,
        "pattern_type": "bull_flag",
        "history_days": 20
    },
    "Bear Flag": {
        "description": "Echte Multi-Day Flag: Fahnenstange (2-7d) + Konsolidierung mit 20 Tageskerzen",
        "filters": {"Change %": (-5.0, 5.0), "RVOL": (0.05, 3.0)},
        "logic": "20d History-Analyse: Pole ≥-5% + enge Konsolidierung + sinkendes Volumen + Retracement <50%",
        "needs_history": True,
        "pattern_type": "bear_flag",
        "history_days": 20
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung + Multi-Day Runner",
        "filters": {"Change %": (3.0, 200.0), "RVOL": (1.5, 50.0), "Close Position": (0.60, 1.0), "Preis": (2.0, 100000.0)},
        "logic": "Anstieg 3%+ mit RVOL >1.5 + Close nahe High. Bei MDR (Vortag >10%): RVOL-Filter entfällt"
    },
    "Turtle Breakout": {
        "description": "Richard Dennis Turtle Trading: Kurs durchbricht 20-Tage-Hoch (Donchian Channel)",
        "filters": {"Preis": (5.0, 100000.0)},
        "logic": "Close > 20-Tage-High = Long Entry. Stop 2x ATR. Exit bei 10-Tage-Low. Trend-Following System.",
        "needs_history": True,
        "history_days": 25,
        "stocks_only": True
    },
    # V2.7: "Breakdown Short" und "Breakout Short" entfernt — redundant mit BI Scanner Short + Bear Scanner
    "Penny Rockets": {
        "description": "Günstige Aktien mit explosivem Volumen (min $100k Volumen)",
        "filters": {"Preis": (0.10, 5.0), "RVOL": (3.0, 100.0), "Change %": (3.0, 100.0)},
        "logic": "Lowcaps unter $5 mit extremem Interesse - NUR liquide!",
        "min_dollar_volume": 100000
    },
    "Dip Buy": {
        "description": "Qualitäts-Assets im Rücksetzer ohne Panik (min $500k Volumen)",
        "filters": {"Preis": (10.0, 100000.0), "Change %": (-8.0, -2.0), "RVOL": (0.6, 1.5)},
        "logic": "Moderater Rücksetzer mit normalem Volumen (RVOL 0.6-1.5) = Kaufchance, kein Panik-Dump",
        "min_dollar_volume": 500000
    },
    "Reversal Hunter": {
        "description": "Bounce nach roter Kerze — echtes Reversal NUR wenn Stock im Downtrend ( Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Vortag %": (-50.0, -3.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Gestern bärische KERZE (-3%+), heute Käufer (+2%+). Bei Uptrend = Continuation Dip Buy, bei Downtrend = Reversal"
    },
    "Early Momentum": {
        "description": "Starker Tagesstart mit Volumen - Preis hält sich oben",
        "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0), "Close Position": (0.6, 1.0), "Preis": (5.0, 500.0)},
        "logic": "Change > 3% + RVOL > 1.5 + Close nahe High = echtes Momentum"
    },
    "Whale Watch": {
        "description": "Extremes Volumen MIT klarer Richtung - Big Player aktiv",
        "filters": {"RVOL": (3.0, 100.0), "Change %": (2.0, 100.0), "Close Position": (0.55, 1.0)},
        "logic": "RVOL > 3.0 + Change > 2% + Close nahe High = echtes Whale Buying (kein Churn)"
    },
    "Whale Watch Short ": {
        "description": "Extremes Volumen + Abverkauf - Big Player verkaufen",
        "filters": {"RVOL": (3.0, 100.0), "Change %": (-100.0, -2.0), "Close Position": (0.0, 0.45)},
        "logic": "RVOL > 3.0 + Change < -2% + Close nahe Low = echtes Whale Selling"
    },
    # =========================================================================
    # EARNINGS / NEWS MOVER - Ersetzt alle PM/AH Strategien
    # =========================================================================
    "Earnings Mover Long": {
        "description": "Starker Gap-Up nach Earnings/News — funktioniert PM, AH und Regular",
        "filters": {"Gap %": (5.0, 100.0), "Change %": (3.0, 200.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 100000.0), "Close Position": (0.50, 1.0)},
        "logic": "Gap >5% + RVOL >2 + Close hält über Gap = Earnings Beat / Catalyst. Preis >$5 filtert Penny-Noise",
        "stocks_only": True
    },
    "Earnings Mover Short": {
        "description": "Starker Gap-Down nach Earnings/News — Fade oder Continuation Short",
        "filters": {"Gap %": (-100.0, -5.0), "Change %": (-200.0, -3.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 100000.0), "Close Position": (0.0, 0.45)},
        "logic": "Gap <-5% + RVOL >2 + Close nahe Low = Earnings Miss / Bad News. Kein Bounce = Continuation Short",
        "stocks_only": True
    },
    # =========================================================================
    # GAP STRATEGIEN - NUR AKTIEN! (Mit Liquiditäts-Filter!)
    # =========================================================================
    "Gap Up": {
        "description": " NUR AKTIEN: Gap nach oben mit Volumen-Bestätigung",
        "filters": {"Gap %": (2.0, 50.0), "RVOL": (1.0, 100.0)},
        "logic": "Gap Up + mindestens normales Volumen = echtes Interesse (nicht dünn gehandelt)",
        "stocks_only": True
    },
    "Gap Down": {
        "description": " NUR AKTIEN: Gap nach unten mit Volumen-Bestätigung",
        "filters": {"Gap %": (-50.0, -2.0), "RVOL": (1.0, 100.0)},
        "logic": "Gap Down + normales Volumen = echtes Selling",
        "stocks_only": True
    },
    "Gap Up (High Vol)": {
        "description": " Gap Up mit HOHEM Volumen - Starkes Momentum",
        "filters": {"Gap %": (3.0, 50.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 500.0)},
        "logic": "Gap + hohes Volumen + liquide Aktie = Momentum-Play",
        "stocks_only": True
    },
    "Gap Down (High Vol)": {
        "description": " Gap Down mit HOHEM Volumen - Panik oder News",
        "filters": {"Gap %": (-50.0, -3.0), "RVOL": (2.0, 100.0), "Preis": (5.0, 500.0)},
        "logic": "Gap Down + hohes Volumen = News-Event, Gap-Fill Trade",
        "stocks_only": True
    },
    # =========================================================================
    # WICK STRATEGIEN - BEIDE MÄRKTE
    # =========================================================================
    "Long Wick Up": {
        "description": "Lange obere Wick = Verkaufsdruck, oft Reversal nach unten",
        "filters": {"Upper Wick %": (35.0, 100.0), "Change %": (-10.0, 3.0)},
        "logic": "Obere Wick > 35% + Change < 3% = Ablehnung höherer Preise (Short-Signal)",
        "min_range_pct": 0.5
    },
    "Long Wick Down": {
        "description": "Lange untere Wick = Kaufdruck, oft Reversal nach oben",
        "filters": {"Lower Wick %": (35.0, 100.0), "Change %": (-5.0, 10.0)},
        "logic": "Untere Wick > 35% = Ablehnung tieferer Preise (Long-Signal, Hammer-Pattern)",
        "min_range_pct": 0.5
    },
    # =========================================================================
    # INSIDER STRATEGIEN - NUR AKTIEN
    # =========================================================================
    "Insider Buying": {
        "description": " NUR AKTIEN: Insider (CEO, CFO, Directors) kaufen eigene Aktien",
        "filters": {"Insider": "BUY"},
        "logic": "Insider kaufen = Sie glauben an die Firma → Bullish Signal",
        "stocks_only": True
    },
    "Insider Selling": {
        "description": " NUR AKTIEN: Insider verkaufen große Mengen",
        "filters": {"Insider": "SELL"},
        "logic": "Große Insider-Verkäufe können Warnsignal sein",
        "stocks_only": True
    },
    # =========================================================================
    # KONSOLIDIERUNGS-STRATEGIEN - (Wyckoff-inspiriert, vereinfacht)
    # HINWEIS: Echte Wyckoff-Analyse erfordert Wochen von Daten!
    # Diese Strategien finden 2-Tage Konsolidierungen, NICHT echte Wyckoff-Patterns.
    # Mit Multi-Day Analyse (5 Tage) für bessere Pattern-Erkennung.
    # =========================================================================
    "Consolidation ": {
        "description": " Multi-Day Seitwärtsphase mit sinkendem Volumen ( Vortag% = Kerze, nicht Tagesperformance)",
        "filters": {"Change %": (-2.0, 2.0), "Vortag %": (-2.0, 2.0), "RVOL": (0.2, 1.2)},
        "logic": "Enge Range (±2%) + kleine Vortags-Kerze + niedriges Volumen = Ruhe vor dem Sturm",
        "needs_history": True,
        "pattern_type": "consolidation",
        "history_days": 5
    },
    "Consolidation Breakout ": {
        "description": "→ Ausbruch aus mehrtaegiger enger Range mit Volumen-Explosion",
        "filters": {"Change %": (1.5, 50.0), "Vortag %": (-3.0, 3.0), "RVOL": (1.5, 50.0)},
        "logic": "Mehrtaegige enge Range + heute Ausbruch (+1.5%+) mit erhoehtem Volumen",
        "needs_history": True,
        "pattern_type": "consolidation_breakout",
        "history_days": 15
    },
    "Reversal Setup ": {
        "description": " Mehrtägiger Abverkauf + heute bullische Umkehr ( Vortag% = Kerze)",
        "filters": {"Change %": (2.0, 15.0), "Vortag %": (-8.0, -2.0), "RVOL": (1.5, 10.0)},
        "logic": "Mehrtägiger Downtrend + heute grün mit erhöhtem Volumen = Boden-Bildung",
        "needs_history": True,
        "pattern_type": "reversal_setup",
        "history_days": 5
    },
    "Tight Range ": {
        "description": " Extrem enge Tagesrange mit niedrigem Volumen - Explosion steht bevor",
        "filters": {"Change %": (-1.0, 1.0), "RVOL": (0.2, 0.8)},
        "logic": "Enge Range + niedriges Volumen = echte Ruhe vor dem Sturm (Richtung unklar)"
    },
    "High Volume Churn ": {
        "description": " Hohes Volumen ohne Preisfortschritt = Smart Money akkumuliert/distribuiert",
        "filters": {"Change %": (-2.0, 2.0), "RVOL": (1.8, 50.0)},
        "logic": "Hohes Volumen (RVOL > 1.8) + enge Tagesrange (<2%) = Churn-Aktivitaet",
        "needs_history": True,
        "pattern_type": "churn",
        "history_days": 10
    },
    # =========================================================================
    # VOLUME VOID STRATEGIEN - Low Volume Node Scanner
    # =========================================================================
    "Volume Void Long ⬆": {
        "description": " Preis UNTER einem Volume Void - Potenzial für schnellen Anstieg!",
        "filters": {"Change %": (-5.0, 10.0), "Preis": (5.0, 500.0)},
        "logic": "Wenig Widerstand über aktuellem Preis → Preis kann schnell durch das 'Loch' steigen",
        "stocks_only": True,
        "needs_volume_profile": True
    },
    "Volume Void Short ⬇": {
        "description": " Preis ÜBER einem Volume Void - Potenzial für schnellen Fall!",
        "filters": {"Change %": (-10.0, 5.0), "Preis": (5.0, 500.0)},
        "logic": "Wenig Support unter aktuellem Preis → Preis kann schnell durch das 'Loch' fallen",
        "stocks_only": True,
        "needs_volume_profile": True
    },
    # =========================================================================
    # HARMONIC PATTERN STRATEGIEN - Fibonacci-basierte Reversal Patterns
    # =========================================================================
    "Harmonic Bullish ⬆": {
        "description": " Bullische Harmonic Patterns (Gartley, Bat, Butterfly, Crab)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "XABCD Pattern mit Fibonacci-Verhältnissen → Long Entry am Punkt D",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "LONG"
    },
    "Harmonic Bearish ⬇": {
        "description": " Bärische Harmonic Patterns (Short-Setups)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "XABCD Pattern mit Fibonacci-Verhältnissen → Short Entry am Punkt D",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "SHORT"
    },
    "Harmonic All Patterns ": {
        "description": " Alle Harmonic Patterns (Long + Short)",
        "filters": {"Preis": (5.0, 500.0)},
        "logic": "Scannt nach allen XABCD Patterns unabhängig von Richtung",
        "stocks_only": True,
        "needs_harmonic": True,
        "harmonic_direction": "ALL"
    },
    # =========================================================================
    # BREAKOUT IMMINENT - Multi-Signal Composite Breakout Prediction
    # Kombiniert 12 Faktoren: ATR-Squeeze, Vol Dry-Up, OBV-Divergenz, ADX,
    # Close Clustering, Range Duration, Boundary Tests, Institutional Days,
    # RSI Drift, Higher Lows/Lower Highs, Resilience, Bollinger-Squeeze
    # =========================================================================
    # Breakout Imminent → eigener Tab " BI Scanner" (entfernt aus Strategie-Dropdown)
    # =========================================================================
    # WYCKOFF STRATEGIEN - Klassische Akkumulations/Distributions-Phasen
    # Erkennt: SC, AR, ST, Spring, SOS (Accumulation) / BC, AR, ST, UT, SOW (Distribution)
    # =========================================================================
    "Wyckoff Accumulation ⬆": {
        "description": " Wyckoff Akkumulation — Smart Money kauft leise in Trading Range",
        "filters": {"Preis": (1.0, 5000.0), "Change %": (-5.0, 5.0)},
        "logic": "Daily: Enge Range + abnehmendes Volumen + OBV-Divergenz = Akkumulation",
        "needs_history": True,
        "pattern_type": "wyckoff_accumulation",
        "history_days": 30
    },
    "Wyckoff Distribution ⬇": {
        "description": " Wyckoff Distribution — Smart Money verkauft leise in Trading Range",
        "filters": {"Preis": (1.0, 5000.0), "Change %": (-5.0, 5.0)},
        "logic": "Daily: Enge Range + abnehmendes Volumen + OBV-Divergenz = Distribution",
        "needs_history": True,
        "pattern_type": "wyckoff_distribution",
        "history_days": 30
    },
    # =========================================================================
    # MA BOUNCE STRATEGIEN - Support/Resistance an Moving Averages
    # =========================================================================
    "SMA 50 Bounce Long ": {
        "description": " Preis nähert sich SMA 50 von OBEN - Support-Zone für Long",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-5.0, 2.0)},
        "logic": "Preis 0-3% über SMA50 + SMA50 steigend = Support-Bounce Setup",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 50,
        "ma_approach": "from_above",  # Preis kommt von oben
        "ma_distance_max": 3.0  # Max 3% über SMA
    },
    "SMA 50 Bounce Short ": {
        "description": " Preis nähert sich SMA 50 von UNTEN - Resistance-Zone für Short",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-2.0, 5.0)},
        "logic": "Preis 0-3% unter SMA50 + SMA50 fallend = Resistance-Bounce Setup",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 50,
        "ma_approach": "from_below",  # Preis kommt von unten
        "ma_distance_max": 3.0
    },
    "SMA 200 Bounce Long ": {
        "description": " Preis nähert sich SMA 200 von OBEN - STARKER Support (Paul Tudor Jones)",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-8.0, 2.0)},
        "logic": "SMA200 ist DER wichtigste MA! Preis 0-3% über SMA200 = Kaufchance",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 200,
        "ma_approach": "from_above",
        "ma_distance_max": 3.0
    },
    "SMA 200 Bounce Short ": {
        "description": " Preis nähert sich SMA 200 von UNTEN - STARKE Resistance",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-2.0, 8.0)},
        "logic": "SMA200 ist starke Resistance! Preis 0-3% unter SMA200 = Short-Chance",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "SMA",
        "ma_period": 200,
        "ma_approach": "from_below",
        "ma_distance_max": 3.0
    },
    "EMA 21 Bounce (Swing) ": {
        "description": " EMA 21 Bounce - Linda Raschke 'Holy Grail' Setup",
        "filters": {"Preis": (5.0, 1000.0), "Change %": (-4.0, 4.0)},
        "logic": "EMA21 ist DER Swing-Trading MA! Pullback zur EMA21 im Uptrend = Entry",
        "stocks_only": True,
        "needs_ma": True,
        "ma_type": "EMA",
        "ma_period": 21,
        "ma_approach": "from_above",
        "ma_distance_max": 2.0  # Enger für EMA21
    },
}


FUTURES_STRATEGIES = {
    " Alle zeigen": {
        "description": "Alle Futures anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Futures"
    },
    # =========================================================================
    # MOMENTUM STRATEGIEN (Any Time)
    # =========================================================================
    "Futures Momentum ": {
        "description": " Starke Bewegung mit Volumen-Bestätigung",
        "filters": {"Change %": (1.0, 20.0)},
        "logic": "Futures mit >1% Tagesbewegung = klares Momentum"
    },
    "Futures Breakdown ": {
        "description": " Starker Abverkauf - Short-Opportunity",
        "filters": {"Change %": (-20.0, -1.0)},
        "logic": "Futures mit <-1% = Verkaufsdruck"
    },
    "Futures Reversal ": {
        "description": " Trendumkehr nach starkem Move ( Vortag% = Session-Kerze)",
        "filters": {"Vortag %": (-10.0, -2.0), "Change %": (0.5, 10.0)},
        "logic": "Letzte Session gefallen, jetzt steigend = potenzielle Umkehr"
    },
    # =========================================================================
    # SESSION-BASIERTE STRATEGIEN (mit Zeitfenster-Hinweis)
    # =========================================================================
    "Globex Gap ": {
        "description": " Overnight Gap vs. Regular Session Close",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Gap zwischen US Close und Asia/Europe Session",
        "best_time": "18:00-08:00 UTC (Globex Overnight)"
    },
    "London Open Momentum 🇬🇧": {
        "description": "🇬🇧 Momentum bei London Börsenöffnung (08:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Europa-Session bringt oft neue Richtung",
        "best_time": "07:00-10:00 UTC"
    },
    "NY Open Breakout ": {
        "description": " Breakout bei US-Börsenöffnung (14:30 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "US-Session mit höchster Liquidität = große Moves",
        "best_time": "13:30-16:00 UTC"
    },
    # =========================================================================
    # SPREAD & STRUKTUR (Any Time)
    # =========================================================================
    "High Volatility ": {
        "description": " Überdurchschnittliche Tagesbewegung",
        "filters": {"Change %": (2.0, 50.0)},
        "logic": "Große Bewegung = Trading-Opportunity"
    },
    "Low Volatility Squeeze ": {
        "description": " Enge Range - Breakout erwartet",
        "filters": {"Change %": (-0.3, 0.3)},
        "logic": "Sehr kleine Bewegung = Ruhe vor dem Sturm"
    },
    "VIX Spike Alert ": {
        "description": " VIX steigt stark - Angst im Markt",
        "filters": {"Change %": (5.0, 100.0)},
        "logic": "Nur für VIX: Starker Anstieg = Absicherung aktiv"
    },
}


FOREX_STRATEGIES = {
    " Alle zeigen": {
        "description": "Alle Forex-Paare anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Paare"
    },
    # =========================================================================
    # PIP-BASIERTE MOMENTUM STRATEGIEN (Any Time)
    # =========================================================================
    "Forex Momentum ": {
        "description": " Starke Pip-Bewegung in eine Richtung",
        "filters": {"Change %": (0.3, 5.0)},
        "logic": "Für Forex ist >0.3% bereits signifikant"
    },
    "Forex Reversal ": {
        "description": " Gegenbewegung nach starkem Vortag ( Vortag% = 24h Kerze)",
        "filters": {"Vortag %": (-3.0, -0.5), "Change %": (0.1, 3.0)},
        "logic": "Letzte 24h gefallen, jetzt steigend = Umkehr-Signal"
    },
    "Pip Hunter ": {
        "description": " Größte Pip-Bewegungen des Tages",
        "filters": {"Change %": (0.5, 10.0)},
        "logic": "Top Movers nach Pips sortiert"
    },
    # =========================================================================
    # SESSION-STRATEGIEN (mit Zeitfenster für optimale Nutzung)
    # =========================================================================
    "Tokyo Session 🇯🇵": {
        "description": "🇯🇵 Bewegungen während Tokyo Session (00:00-09:00 UTC)",
        "filters": {"Change %": (0.1, 3.0)},
        "logic": "Asiatische Session - oft ruhiger, aber JPY-Paare aktiv",
        "best_time": "00:00-09:00 UTC",
        "best_pairs": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"]
    },
    "London Session 🇬🇧": {
        "description": "🇬🇧 Bewegungen während London Session (08:00-17:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Höchste Liquidität - EUR/GBP-Paare besonders aktiv",
        "best_time": "08:00-17:00 UTC",
        "best_pairs": ["EURUSD", "GBPUSD", "EURGBP", "EURJPY"]
    },
    "NY Session ": {
        "description": " Bewegungen während NY Session (13:00-22:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "USD-Paare am aktivsten",
        "best_time": "13:00-22:00 UTC",
        "best_pairs": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]
    },
    "London/NY Overlap ": {
        "description": " Höchste Volatilität: London + NY gleichzeitig (13:00-17:00 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Beste Trading-Zeit - maximale Liquidität und Bewegung",
        "best_time": "13:00-17:00 UTC"
    },
    # =========================================================================
    # SPEZIELLE FOREX-STRATEGIEN (Any Time)
    # =========================================================================
    "Safe Haven Flow ": {
        "description": " Flucht in sichere Währungen (CHF, JPY) - Risk-Off Signal",
        "filters": {"Change %": (-5.0, -0.4)},
        "logic": "USD/CHF oder USD/JPY fallen deutlich = Risk-Off Modus (Investoren kaufen CHF/JPY)",
        "best_pairs": ["USDCHF", "USDJPY", "EURJPY"]
    },
    "Risk-On Rally ": {
        "description": " Risikofreudige Währungen steigen (AUD, NZD) - Risk-On Signal",
        "filters": {"Change %": (0.3, 5.0)},
        "logic": "AUD/USD, NZD/USD steigen deutlich = Risk-On Sentiment (Investoren gehen ins Risiko)",
        "best_pairs": ["AUDUSD", "NZDUSD", "AUDJPY"]
    },
    "Exotic Movers ": {
        "description": " Große Bewegungen in Exotic Pairs",
        "filters": {"Change %": (0.5, 20.0)},
        "logic": "Emerging Market Währungen mit hoher Volatilität"
    },
    "Range Bound ": {
        "description": " Seitwärts-Bewegung - Range Trading",
        "filters": {"Change %": (-0.15, 0.15)},
        "logic": "Minimale Bewegung = Trade die Range"
    },
}


CRYPTO_STRATEGIES = {
    " Alle zeigen": {
        "description": "Alle Krypto-Assets anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Coins"
    },
    "Volume Surge": {
        "description": "Erhoehtes Volumen + starke Bewegung",
        "filters": {"Turnover Intensity": (1.5, 50.0), "Change %": (3.0, 100.0)},
        "logic": "24h Turnover-Intensität > 1.5 + Change > 3%; kein historisches RVOL"
    },
    "Bull Flag": {
        "description": "Konsolidierung nach Aufwärtstrend ( Vortag = 6d Tagesdurchschnitt)",
        "filters": {"Vortag %": (0.5, 30.0), "Change %": (-3.0, 3.0), "Turnover Intensity": (0.1, 1.5)},
        "logic": "6d-Trend positiv, heute flach und Turnover moderat = Flag-Kandidat; Chart-Trigger bleibt nötig"
    },
    "Bear Flag": {
        "description": "Konsolidierung nach Abwärtstrend ( Vortag = 6d Tagesdurchschnitt)",
        "filters": {"Vortag %": (-30.0, -0.5), "Change %": (-3.0, 3.0), "Turnover Intensity": (0.1, 1.5)},
        "logic": "6d-Trend negativ, heute flach und Turnover moderat = Bear-Flag-Kandidat; noch kein Short-Trigger"
    },
    "Breakout Long": {
        "description": "Ausbruch nach oben — Close nahe Tageshoch",
        "filters": {"Change %": (4.0, 80.0), "Close Position": (0.65, 1.0)},
        "logic": "Close nahe High + starke Bewegung = bullischer Ausbruch"
    },
    # V2.7: "Breakdown Short" entfernt — redundant
    "Low Cap Rockets ": {
        "description": "Small/Micro Cap mit explosivem Volumen & Bewegung",
        "filters": {"MarketCap": (0, 500_000_000), "Turnover Intensity": (1.2, 50.0), "Change %": (5.0, 100.0)},
        "logic": "MCap < $500M + hohe 24h Turnover-Intensität + Change > 5%; Entry braucht Exchange-Trigger"
    },
    "Dip Buy": {
        "description": "Rücksetzer ohne Panik-Volumen",
        "filters": {"Change %": (-15.0, -3.0), "Turnover Intensity": (0.3, 1.5)},
        "logic": "Moderater Rücksetzer mit normalem Volumen — kein Panik-Dump"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach Abwärtstrend ( Vortag = 6d Tagesdurchschnitt)",
        "filters": {"Vortag %": (-50.0, -1.0), "Change %": (2.0, 50.0)},
        "logic": "6d-Trend negativ (avg <-1%/Tag ≈ -6%/Woche), heute Käufer (+2%) = mögliche Wende"
    },
    "Early Momentum": {
        "description": "Starke Bewegung mit erhoehtem Volumen",
        "filters": {"Change %": (3.0, 40.0), "Turnover Intensity": (1.0, 20.0)},
        "logic": "Positive Bewegung mit 24h Turnover-Aktivität; echte RVOL-Bestätigung erst aus Exchange-Kerzen"
    },
    "Whale Watch ": {
        "description": "Extremes Volumen MIT klarer Richtung - Big Player aktiv",
        "filters": {"Turnover Intensity": (2.5, 50.0), "Change %": (5.0, 100.0)},
        "logic": "Hohe 24h Turnover-Intensität + Richtung; ohne OI-Delta kein Beweis für Whale-Akkumulation"
    },
    "Accumulation ": {
        "description": "Leise Akkumulation bei stabilem Preis",
        "filters": {"Change %": (-2.0, 2.0), "Turnover Intensity": (1.2, 3.0)},
        "logic": "Seitwärts + erhöhte Turnover-Intensität = Akkumulations-Kandidat, kein bestätigter Entry"
    },
}


INTERNATIONAL_STRATEGIES = {
    " Alle zeigen": {
        "description": "Alle Aktien der Börse anzeigen — ohne Filter",
        "filters": {},
        "logic": "Kein Filter aktiv → zeige alle verfügbaren Aktien"
    },
    " Gewinner": {
        "description": "Aktien im Plus heute",
        "filters": {"Change %": (0.3, 100.0)},
        "logic": "Change > 0.3% = Aufwärtsbewegung"
    },
    " Verlierer": {
        "description": "Aktien im Minus heute",
        "filters": {"Change %": (-100.0, -0.3)},
        "logic": "Change < -0.3% = Abwärtsbewegung"
    },
    " Momentum": {
        "description": "Stärkste positive Bewegung",
        "filters": {"Change %": (1.0, 50.0)},
        "logic": "Change > 1% = echtes Momentum für europäische Blue-Chips"
    },
    " Breakout": {
        "description": "Starker Ausbruch nach oben — Close nahe Tageshoch",
        "filters": {"Change %": (1.5, 50.0), "Close Position": (0.65, 1.0)},
        "logic": "Change > 1.5% + Close nahe High = bullischer Ausbruch"
    },
    " Breakdown": {
        "description": "Starker Abverkauf — Close nahe Tagestief",
        "filters": {"Change %": (-50.0, -1.5), "Close Position": (0.0, 0.35)},
        "logic": "Change < -1.5% + Close nahe Low = Verkaufsdruck"
    },
    " Dip Buy": {
        "description": "Moderate Schwäche — potenzielle Kaufchance",
        "filters": {"Change %": (-5.0, -0.5)},
        "logic": "Change -0.5% bis -5% = Rücksetzer bei soliden Aktien"
    },
    " Volume Spike": {
        "description": "Deutlich überdurchschnittliches Volumen (normalisiert nach Tageszeit)",
        "filters": {"Change %": (0.5, 50.0), "RVOL": (0.4, 50.0)},
        "logic": "RVOL > 0.4 (normalisiert) + positive Bewegung = erhöhtes Interesse. Bei EU-Aktien selten >1.0 untertags."
    },
    " Reversal": {
        "description": "Trendumkehr: Vortag stark gefallen, heute Bounce",
        "filters": {"Vortag %": (-30.0, -1.5), "Change %": (0.5, 30.0)},
        "logic": "Gestern -1.5%+, heute Erholung +0.5%+ = mögliche Wende"
    },
    " Bull Flag": {
        "description": "Konsolidierung nach starkem Vortag — Momentum-Fortsetzung",
        "filters": {"Vortag %": (1.5, 20.0), "Change %": (-1.0, 1.0)},
        "logic": "Starker Vortag (+1.5%+), heute enge Range = Flagge bildet sich"
    },
    " Bear Flag": {
        "description": "Konsolidierung nach Abverkauf — Short-Setup",
        "filters": {"Vortag %": (-20.0, -1.5), "Change %": (-1.0, 1.0)},
        "logic": "Schwacher Vortag (-1.5%+), heute enge Range = Bear Flag"
    },
    " Big Movers": {
        "description": "Größte absolute Bewegungen des Tages",
        "filters": {"Change %": (2.0, 100.0)},
        "logic": "Change > 2% = signifikante Bewegung für europäische Verhältnisse"
    },
    " Whale Watch": {
        "description": "Extremes Volumen (normalisiert) — Big Player aktiv",
        "filters": {"RVOL": (0.5, 50.0)},
        "logic": "RVOL > 0.5 (normalisiert nach Tageszeit) = deutlich über Durchschnitt"
    },
}


def get_strategies_for_market(market_type, exchange="US"):
    """Gibt die passenden Strategien für den gewählten Markt zurück"""
    if market_type == "Krypto":
        return CRYPTO_STRATEGIES
    elif market_type == "Futures":
        return FUTURES_STRATEGIES
    elif market_type == "Forex":
        return FOREX_STRATEGIES
    else:  # Aktien
        if exchange and exchange != "US":
            return INTERNATIONAL_STRATEGIES
        return STRATEGIES


def apply_strategy(strategy_name, strategies_dict=None):
    """Wendet eine Strategie an. strategies_dict ist optional - wird automatisch ermittelt."""
    if st is None:
        raise RuntimeError("apply_strategy is only available in the legacy Streamlit UI")

    # Wenn kein Dictionary übergeben, suche in allen
    if strategies_dict is None:
        # Prüfe in welchem Dictionary die Strategie existiert
        if strategy_name in CRYPTO_STRATEGIES:
            strategies_dict = CRYPTO_STRATEGIES
        elif strategy_name in FUTURES_STRATEGIES:
            strategies_dict = FUTURES_STRATEGIES
        elif strategy_name in FOREX_STRATEGIES:
            strategies_dict = FOREX_STRATEGIES
        elif strategy_name in INTERNATIONAL_STRATEGIES:
            strategies_dict = INTERNATIONAL_STRATEGIES
        else:
            strategies_dict = STRATEGIES  # Aktien als Fallback

    if strategy_name in strategies_dict:
        strategy = strategies_dict[strategy_name]
        st.session_state.active_filters = strategy["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.filter_reset_counter = st.session_state.get("filter_reset_counter", 0) + 1
        st.session_state._strategy_just_applied = True  # Flag: Slider nicht rendern beim nächsten Rerun
        # FIX: min_price aus Strategy-Definition übernehmen (war immer 0 → $2 Stocks passierten)
        # Prüfe BACKTEST_STRATEGY_RULES für min_price, Fallback auf $5 für alle Strategien
        _bt_rule = BACKTEST_STRATEGY_RULES.get(strategy_name, {})
        _strategy_min_price = _bt_rule.get("min_price", 5.0)
        st.session_state.additional_filters = {
            "preis_min": _strategy_min_price, "preis_max": 100000.0,
            "nur_gewinner": False, "nur_verlierer": False,
        }

        # Auto-Switch für PM/AH Strategien (nur bei Aktien-Strategien)
        if strategy.get("session_hint") == "Pre-Market":
            st.session_state.active_trading_session = "Pre-Market"
            st.session_state.market_type = "Aktien"
        elif strategy.get("session_hint") == "After-Hours":
            st.session_state.active_trading_session = "After-Hours"
            st.session_state.market_type = "Aktien"

        # Auto-Switch auf Aktien für stocks_only Strategien
        if strategy.get("stocks_only"):
            st.session_state.market_type = "Aktien"
    else:
        # Strategie nicht gefunden - setze trotzdem auf den Namen
        st.session_state.current_strategy = strategy_name
        st.session_state.active_filters = {}
        st.warning(f" Strategie '{strategy_name}' nicht gefunden!")


def classify_pm_setup(pm_change, gap_pct, pm_position, rs_vs_spy, atr_pct=5.0, vol_ratio=1.0, float_cat="UNKNOWN"):
    """
    Klassifiziert das PM Setup basierend auf Preis-Aktion + Position + Volume.

    V2: Jetzt mit Volume Ratio und Float-Awareness:
    - vol_ratio < 0.3 → THIN suffix (unzuverlässig)
    - Low Float + >10% Move → SQUEEZE statt MOMENTUM

    Returns: (setup_type, setup_emoji, setup_description)
    """
    is_up = pm_change > 0
    abs_change = abs(pm_change)
    abs_gap = abs(gap_pct)
    is_thin = vol_ratio < 0.3  # Dünnes Volume = unzuverlässig
    is_low_float = float_cat in ("NANO", "MICRO")

    # === THIN VOLUME OVERRIDE — Bei sehr dünnem Volume Setup abstufen ===
    # Wird am Ende angewendet, hier nur Flag setzen

    # === SQUEEZE / EXTREME (>10% + starke RS) — VOR dem 5% Block! ===
    if abs_change >= 10 and abs(rs_vs_spy) >= 5:
        if is_up and pm_position >= 60:
            if is_low_float:
                label = ("SQUEEZE", "", "Low Float Squeeze! Extreme Move + RS — Parabolic Potential")
            else:
                label = ("SQUEEZE", "", "Extreme Move + Relative Strength = Possible Squeeze")
        elif is_up and pm_position < 40:
            label = ("FADING", "", "Extreme Gap but Fading Hard — Caution!")
        elif not is_up and pm_position <= 40:
            label = ("CAPITULATION", "", "Extreme Selling = Watch for Reversal")
        elif not is_up and pm_position >= 60:
            label = ("BOUNCE", "", "Extreme Drop but Bounced — Wait!")
        else:
            label = ("CONTESTED", "", "Extreme Move but Indecisive — Wait!")

        if is_thin:
            return (label[0] + " (THIN)", "", label[2] + " DÜNNES VOLUME — Vorsicht!")
        return label

    # === STARKE MOVES (>5%) ===
    if abs_change >= 5:
        if is_up and pm_position >= 70:
            if is_low_float and abs_change >= 7:
                label = ("SQUEEZE", "", "Low Float + Strong Hold = Squeeze Setup")
            elif abs_gap >= 5:
                label = ("GAP & GO", "", "Gap Up + Holding High = Momentum Long")
            else:
                label = ("MOMENTUM", "", "Strong Move + Holding = Long Momentum")
        elif is_up and pm_position < 40:
            label = ("FADING", "", "Gapped Up but Fading — Caution, kein Long!")
        elif not is_up and pm_position <= 30:
            if abs_gap >= 5:
                label = ("GAP & FADE", "", "Gap Down + Near Low = Short Momentum")
            else:
                label = ("WEAKNESS", "", "Strong Selling + Near Low = Short Setup")
        elif not is_up and pm_position >= 60:
            label = ("BOUNCE", "", "Gapped Down but Bounced — Wait for Rejection!")
        elif is_up:
            label = ("CONTESTED", "", "Strong Up but Mid-Range — Wait for Direction")
        else:
            label = ("CONTESTED", "", "Strong Down but Mid-Range — Watch for Break")

        if is_thin:
            return (label[0] + " (THIN)", "", label[2] + " DÜNNES VOLUME — Vorsicht!")
        return label

    # === MODERATE MOVES (3-5%) ===
    if 3 <= abs_change < 5:
        if is_up and pm_position >= 65:
            label = ("CONTINUATION", "", "Steady Uptrend — Wait for Pullback Entry")
        elif is_up and pm_position < 35:
            label = ("FADING", "", "Moderate Up but Fading — No Long Entry")
        elif not is_up and pm_position <= 35:
            label = ("CONTINUATION", "", "Steady Selling — Wait for Bounce or Break")
        elif not is_up and pm_position >= 65:
            label = ("RECOVERY", "", "Down but Recovering — Don't Short Here")
        elif is_up:
            label = ("BUILDING", "", "Moderate Up, Mid-Range — Watch for Breakout or Fade")
        else:
            label = ("CONTESTED", "", "Moderate Down, Mid-Range — Watch for Break or Bounce")

        if is_thin:
            return (label[0] + " (THIN)", "", label[2] + " DÜNNES VOLUME — Vorsicht!")
        return label

    # === KLEINE MOVES (2-3%) ===
    if 2 <= abs_change < 3:
        if 35 <= pm_position <= 65:
            label = ("RANGE", "↔", "Choppy — Wait for Direction")
        elif is_up and pm_position >= 65:
            label = ("MILD STRENGTH", "", "Slight Up Bias — Watch for Catalyst")
        elif not is_up and pm_position <= 35:
            label = ("MILD WEAKNESS", "", "Slight Down Bias — Watch for Catalyst")
        else:
            label = ("RANGE", "↔", "Small Move — Wait for Direction")

        if is_thin:
            return (label[0] + " (THIN)", "", label[2] + " DÜNNES VOLUME — Vorsicht!")
        return label

    # DEFAULT
    return ("WATCH", "", "Monitor for Setup Development")


BACKTEST_STRATEGY_RULES = {
    "Breakout Long": {
        "direction": "long",
        "description": "Momentum-Ausbruch: Change >3%, Close nahe High",
        "signal": {
            "change_pct_min": 3.0, "change_pct_max": 50.0,
            "close_pos_min": 0.60
        },
        "entry": "next_open",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    # V2.7: "Breakdown Short" + "Breakout Short" AutoTrader-Strategien entfernt — redundant
    "Gap Up Momentum": {
        "direction": "long",
        "description": "Gap Up >2% + Kurs hält sich oben (Close Pos >0.55)",
        "signal": {
            "gap_pct_min": 2.0, "gap_pct_max": 30.0,
            "close_pos_min": 0.55,
            "rvol_min": 1.0
        },
        "entry": "next_open",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Gap Down Short": {
        "direction": "short",
        "description": "Gap Down <-2% + Kurs bleibt unten (Close Pos <0.45)",
        "signal": {
            "gap_pct_min": -30.0, "gap_pct_max": -2.0,
            "close_pos_max": 0.45,
            "rvol_min": 1.0
        },
        "entry": "next_open",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Dip Buy": {
        "direction": "long",
        "description": "Rücksetzer: -3% bis -8%, normales Volumen",
        "signal": {
            "change_pct_min": -8.0, "change_pct_max": -3.0,
            "rvol_min": 0.6, "rvol_max": 2.5
        },
        "entry": "at_close",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 3.0,
        "max_hold_days": 5,
        "min_price": 5.0
    },
    "Reversal Hunter": {
        "direction": "long",
        "description": "Bounce nach Abverkauf: Vortag <-4%, heute >+2%",
        "signal": {
            "prev_change_pct_max": -4.0,
            "change_pct_min": 2.0
        },
        "entry": "at_close",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Bull Flag": {
        "direction": "long",
        "description": "Starker Vortag (+3%+), heute Konsolidierung (-2% bis +2%)",
        "signal": {
            "prev_change_pct_min": 3.0,
            "change_pct_min": -2.0,
            "change_pct_max": 2.0
        },
        "entry": "prev_high",
        "stop_pct": 0.04,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Volume Surge": {
        "direction": "long",
        "description": "Hohes Volumen (RVOL >2) + Aufwärtsbewegung >2%",
        "signal": {
            "change_pct_min": 2.0,
            "rvol_min": 2.0
        },
        "entry": "next_open",
        "stop_pct": 0.05,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    "Early Momentum": {
        "direction": "long",
        "description": "Starker Tag (+3%+), Close nahe High → Momentum hält",
        "signal": {
            "change_pct_min": 3.0, "change_pct_max": 30.0,
            "close_pos_min": 0.55
        },
        "entry": "at_close",
        "stop_pct": 0.04,
        "tp1_rr": 1.0,
        "tp2_rr": 2.0,
        "max_hold_days": 2,
        "min_price": 5.0
    },
    "Whale Watch": {
        "direction": "long",
        "description": "Extremes Volumen (RVOL >3) mit klarer Richtung (+2%+)",
        "signal": {
            "change_pct_min": 2.0,
            "rvol_min": 3.0,
            "close_pos_min": 0.55
        },
        "entry": "next_open",
        "stop_pct": 0.06,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "max_hold_days": 3,
        "min_price": 5.0
    },
    # ═══════════════════════════════════════════════════════════════
    # TURTLE TRADING (Richard Dennis, 1983)
    # Donchian Channel Breakout — Trend-Following System
    # Entry: Close > 20-Day High → Long. Stop: 2× ATR(20). Exit: Close < 10-Day Low
    # Nicht rule-based (braucht Donchian+ATR) → eigene Backtest-Logik in api.py
    # ═══════════════════════════════════════════════════════════════
}
