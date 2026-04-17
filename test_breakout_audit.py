#!/usr/bin/env python3
"""
Practical Audit: analyze_breakout_imminent with realistic stock data.
Tests Cases A-E to verify signal firing and scoring.
"""
import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)

from modules.patterns import analyze_breakout_imminent

def generate_bars(case_name, price_base, pattern_type, num_bars=30):
    """Generate realistic OHLCV bar data for test cases."""
    bars = []

    if case_name == "A_DEAD_STOCK":
        # Dead stock: IHS/ADAMH style, barely moves ±0.2%/day
        price = price_base
        avg_volume = 50_000
        for i in range(num_bars):
            # ±0.2% price move per day
            daily_move = (price_base * 0.002) if (i % 3 == 0) else -(price_base * 0.002)
            price += daily_move
            volatility = price * 0.001  # 0.1% body size
            open_p = price - volatility * 0.5
            close = price + volatility * 0.5
            high = price + volatility
            low = price - volatility
            volume = avg_volume * (0.7 + 0.3 * (i % 5) / 5)  # RVOL ~0.7x
            bars.append({
                "open": open_p, "close": close, "high": high, "low": low, "volume": int(volume)
            })
        return bars

    elif case_name == "B_REAL_BREAKOUT":
        # Real breakout setup: consolidating $120-125, vol drying up last 5, then uptick
        price = 120.0
        avg_volume = 2_000_000
        for i in range(num_bars - 5):
            # Consolidation: small bodies
            open_p = 122.0 + (i % 3) * 0.5
            close = open_p + 0.2
            high = 125.0 + (i % 2) * 0.1
            low = 120.0 - (i % 2) * 0.1
            volume = avg_volume * (1.0 - i * 0.02)  # Declining vol
            bars.append({"open": open_p, "close": close, "high": high, "low": low, "volume": int(volume)})

        # Last 5 bars: vol dries up then uptick
        for i in range(5):
            if i < 4:
                # Vol dry-up phase
                open_p = 122.5
                close = 122.8
                high = 123.2
                low = 122.3
                volume = int(avg_volume * 0.3)  # Dry-up
            else:
                # Last bar: uptick + volume return
                open_p = 122.8
                close = 124.5
                high = 124.8
                low = 122.5
                volume = int(avg_volume * 1.5)  # Volume spike
            bars.append({"open": open_p, "close": close, "high": high, "low": low, "volume": volume})
        return bars

    elif case_name == "C_ALREADY_PUMPED":
        # Pumped stock: flat $3 for 29 bars, then jumps to $4.20 on last bar (10x vol)
        price = 3.0
        avg_volume = 500_000
        for i in range(29):
            open_p = price
            close = price + 0.01
            high = price + 0.02
            low = price - 0.01
            volume = int(avg_volume * 0.8)  # Normal low volume
            bars.append({"open": open_p, "close": close, "high": high, "low": low, "volume": volume})

        # Last bar: massive pump
        bars.append({
            "open": 3.0, "close": 4.20, "high": 4.30, "low": 2.95,
            "volume": int(avg_volume * 10)
        })
        return bars

    elif case_name == "D_HEALTHY_CONSOLIDATION":
        # AAPL-style: slowly climbing $170-180 over 30 bars, volume normal, higher lows
        price = 170.0
        avg_volume = 50_000_000
        for i in range(num_bars):
            # Slow uptrend with consolidation
            price += 0.33  # ~$10 gain over 30 bars
            open_p = price - 0.5
            close = price + 0.3
            high = price + 1.0
            low = price - 1.2
            volume = int(avg_volume * (0.9 + 0.2 * (i % 3) / 3))  # Normal volume
            bars.append({"open": open_p, "close": close, "high": high, "low": low, "volume": volume})
        return bars

    elif case_name == "E_DOWNTREND":
        # Downtrend: $50 to $35, lower highs and lows consistently
        price = 50.0
        avg_volume = 3_000_000
        for i in range(num_bars):
            # Downtrend: -50/30 = -1.67/bar
            price -= 0.5
            open_p = price + 0.3
            close = price - 0.2
            high = price + 0.5
            low = price - 0.8
            volume = int(avg_volume * (1.0 - i * 0.01))  # Declining volume in downtrend
            bars.append({"open": open_p, "close": close, "high": high, "low": low, "volume": volume})
        return bars


def print_result(case_name, is_valid, score, max_score, details, conf, grade, sm_fires, sm_hits):
    """Print test results in readable format."""
    print(f"\n{'='*80}")
    print(f"TEST CASE: {case_name}")
    print(f"{'='*80}")
    print(f"  Is Valid: {is_valid}")
    print(f"  Score: {score}/{max_score} ({score/max_score*100:.1f}%)")
    print(f"  Grade: {grade}")
    print(f"  Confidence: {conf}%")
    print(f"  Smart Money: {sm_fires} fires, {sm_hits} hits")
    print(f"\nSignals Fired:")
    for detail in details:
        if detail.strip():
            print(f"  -{detail.strip()}")


def main():
    print("BI SCANNER PRACTICAL AUDIT")
    print("Testing analyze_breakout_imminent with realistic bar data")
    print()

    test_cases = [
        ("A_DEAD_STOCK", 8.0),
        ("B_REAL_BREAKOUT", 120.0),
        ("C_ALREADY_PUMPED", 3.0),
        ("D_HEALTHY_CONSOLIDATION", 170.0),
        ("E_DOWNTREND", 50.0),
    ]

    results = {}

    for case_name, price_base in test_cases:
        bars = generate_bars(case_name, price_base, None)
        is_valid, score, max_score, details, conf, grade, sm_fires, sm_hits = \
            analyze_breakout_imminent(bars, direction="long", crypto_mode=False)

        results[case_name] = {
            "is_valid": is_valid,
            "score": score,
            "max_score": max_score,
            "details": details,
            "conf": conf,
            "grade": grade,
            "sm_fires": sm_fires,
            "sm_hits": sm_hits,
            "bars_count": len(bars),
            "price_range": f"${min(b['low'] for b in bars):.2f}-${max(b['high'] for b in bars):.2f}",
            "volume_range": f"{int(min(b['volume'] for b in bars)/1000)}K-{int(max(b['volume'] for b in bars)/1000)}K"
        }

        print_result(case_name, is_valid, score, max_score, details, conf, grade, sm_fires, sm_hits)
        print(f"  Bar Range: {results[case_name]['price_range']}")
        print(f"  Volume Range: {results[case_name]['volume_range']}")

    # ===== AUDIT CHECKLIST =====
    print("\n\n" + "="*80)
    print("AUDIT CHECKLIST: Does Reality Match Expectations?")
    print("="*80)

    checks = [
        ("A_DEAD_STOCK is_valid", results["A_DEAD_STOCK"]["is_valid"], False,
         "Dead stock with RVOL 0.7x should be FILTERED (not valid)"),

        ("B_REAL_BREAKOUT score > C score",
         results["B_REAL_BREAKOUT"]["score"] > results["C_ALREADY_PUMPED"]["score"], True,
         "Real breakout should score higher than already-pumped stock"),

        ("B_REAL_BREAKOUT grade", results["B_REAL_BREAKOUT"]["grade"], "B",
         "Real breakout with vol drying up + uptick should be Grade B+"),

        ("C_ALREADY_PUMPED has high score BUT low validity",
         results["C_ALREADY_PUMPED"]["is_valid"] == False and results["C_ALREADY_PUMPED"]["score"] < 65, True,
         "Already-pumped stock should get filtered (score < threshold)"),

        ("E_DOWNTREND has low long score",
         results["E_DOWNTREND"]["score"] < results["D_HEALTHY_CONSOLIDATION"]["score"], True,
         "Downtrend stock should score lower than consolidating stock for long"),

        ("D_HEALTHY_CONSOLIDATION is_valid", results["D_HEALTHY_CONSOLIDATION"]["is_valid"], True,
         "Healthy consolidation (AAPL-style) should be valid long"),
    ]

    for check_name, actual, expected, description in checks:
        status = "PASS" if actual == expected else "FAIL"
        print(f"\n[{status}] {check_name}")
        print(f"      Expected: {expected} | Actual: {actual}")
        print(f"      {description}")

    # Summary
    passed = sum(1 for _, a, e, _ in checks if a == e)
    print(f"\n\nSummary: {passed}/{len(checks)} checks passed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
