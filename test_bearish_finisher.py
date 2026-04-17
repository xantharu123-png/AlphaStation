#!/usr/bin/env python3
"""
Reproduziert das User-Symptom: "letzte 2 Kerzen bearish, aber kommt als Grade B/C durch".
Baut synthetische Setups mit gutem historischem Setup + bearish Ende.
"""
import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)

from modules.patterns import analyze_breakout_imminent


def mk_bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def case_consol_then_bearish():
    """
    25 Bars schöne Range $120-125 mit closes nahe High (bullish),
    dann 2 Bars bearish: Close fällt von 124 auf 119 (unter Range-Low).
    Das ist KEIN imminenter Breakout — das ist ein Fakeout.
    """
    bars = []
    # 25 Bars schöne Konsolidierung, Volume langsam sinkend, Closes nahe High
    for i in range(25):
        # Schmale Range, bullisch-biased closes
        low = 120.5 + (i % 3) * 0.1
        high = 124.8 + (i % 2) * 0.1
        open_p = 121.0
        close = 124.2 + (i % 2) * 0.2  # Nahe High
        vol = int(2_000_000 * (1.0 - i * 0.015))  # Dry-Up
        bars.append(mk_bar(open_p, high, low, close, vol))

    # 2 Bars bearish — close stürzt
    bars.append(mk_bar(124.0, 124.2, 121.0, 121.2, 2_500_000))  # Rot: open 124 → close 121
    bars.append(mk_bar(121.0, 121.5, 118.5, 119.0, 3_000_000))  # Stärker rot: close 119 UNTER Range-Low

    return bars


def case_consol_then_one_weak_red():
    """
    Nur EIN roter Tag am Ende — weniger extrem, aber klar nicht bullisch.
    """
    bars = []
    for i in range(28):
        low = 120.0
        high = 124.5
        open_p = 121.5
        close = 123.8
        vol = int(1_800_000 * (1.0 - i * 0.015))
        bars.append(mk_bar(open_p, high, low, close, vol))
    # Letzte Kerze rot, close unter open
    bars.append(mk_bar(123.5, 123.8, 121.8, 122.0, 2_200_000))
    return bars


def case_failed_breakout_recovery():
    """
    Stock bricht aus, fällt zurück in Range, letzte Bar bearish.
    """
    bars = []
    # 20 Bars Konsolidierung 120-125
    for i in range(20):
        bars.append(mk_bar(122.0, 124.5, 120.5, 123.5, int(1_500_000)))
    # 5 Bars Failed Breakout (hochgelaufen, dann zurück)
    bars.append(mk_bar(124.0, 127.0, 123.8, 126.5, 3_500_000))
    bars.append(mk_bar(126.5, 128.0, 125.0, 127.5, 3_000_000))
    bars.append(mk_bar(127.0, 127.5, 124.0, 124.5, 2_800_000))  # Reject
    bars.append(mk_bar(124.0, 124.5, 121.5, 122.0, 2_500_000))  # Back in range
    bars.append(mk_bar(122.0, 122.5, 119.0, 120.0, 3_500_000))  # Bearish close
    return bars


def run(name, bars):
    is_valid, score, max_s, details, conf, grade, sm_f, sm_h = analyze_breakout_imminent(
        bars, direction="long", crypto_mode=False
    )
    last_3_closes = [b["close"] for b in bars[-3:]]
    last_3_opens = [b["open"] for b in bars[-3:]]
    reds = sum(1 for i in range(-3, 0) if bars[i]["close"] < bars[i]["open"])
    print(f"\n{'='*80}")
    print(f"{name}")
    print(f"{'='*80}")
    print(f"  Letzte 3 Opens : {last_3_opens}")
    print(f"  Letzte 3 Closes: {last_3_closes}")
    print(f"  Rote Kerzen in letzten 3: {reds}")
    print(f"  is_valid={is_valid}  score={score}/{max_s}  grade={grade}  sm_fires={sm_f}  sm_hits={sm_h}")
    if is_valid:
        print(f"  WARNUNG: Setup kommt durch trotz bearish Finisher")
    return is_valid, grade


def main():
    print("TEST: Bearish Finisher — reproduziert das User-Symptom")
    print("Erwartung: is_valid sollte FALSE sein bei bearish letzten 2-3 Kerzen")

    results = []
    for name, fn in [
        ("Case 1: 25 Bar Konsolidierung + 2 Bar stark bearish",
         case_consol_then_bearish),
        ("Case 2: 28 Bar Konsolidierung + 1 Bar schwach bearish",
         case_consol_then_one_weak_red),
        ("Case 3: Failed Breakout Recovery (zurück unter Range-Low)",
         case_failed_breakout_recovery),
    ]:
        is_valid, grade = run(name, fn())
        results.append((name, is_valid, grade))

    print("\n\n" + "="*80)
    print("ZUSAMMENFASSUNG")
    print("="*80)
    for name, is_valid, grade in results:
        marker = "FALSE POSITIVE" if is_valid else "OK"
        print(f"  [{marker}] {name} → is_valid={is_valid} grade={grade}")


if __name__ == "__main__":
    main()
