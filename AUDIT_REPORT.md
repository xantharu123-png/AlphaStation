# BI SCANNER AUDIT REPORT
## Practical Testing of `analyze_breakout_imminent` Function
**Date**: 2026-04-09  
**Test Scope**: 5 realistic stock scenarios (30 bars each)  
**Result**: 4/6 checks passed; **2 critical discrepancies found**

---

## TEST CASES & RESULTS

### Case A: Dead Stock (IHS/ADAMH-style)
- **Setup**: $8.00, barely moves ±0.2%/day, 50K avg vol, RVOL 0.7x
- **Expected**: Should be FILTERED (invalid=False)
- **Actual**: ✓ PASS - Score 34/188, invalid=False, Grade D
- **Notes**: Correctly rejected due to low RVOL

### Case B: Real Breakout Setup (NVDA-style)
- **Setup**: $120-125 consolidation, vol drying up last 5 bars, uptick on last bar
- **Expected**: Valid=True, Grade B+, High Smart Money score
- **Actual**: ✗ FAIL - Score 70/188, Valid=True but Grade D, SM fires=0
- **Key Signals Fired**:
  - ✓ ATR Squeeze: 0.23x (6 pts) - CUT signal
  - ✓ OBV Divergence: "bullisch" (+13 pts claimed, but SM=0 due to RVOL)
  - ✓ RSI Drift: 61 (+5 pts)
  - ✓ Higher Lows: 50% (+2 pts)
  - ✓ Resilience: 100% (+14 pts) - BOOSTED
  - ✓ Close Position: 62% (+5 pts)
  - ✓ Compression: StdDev 0.50% (+6 pts)

**DISCREPANCY**: 
- OBV signal prints "OBV-Divergenz bullisch [Smart Money!]" and increments sm_fires in code (line 996)
- BUT: sm_fires=0 in final output because sm_eligible=False (RVOL=0.75 < 0.8 threshold)
- At line 1765-1766, sm_fires is RESET to 0 if sm_eligible=False
- This disables Grade B (requires sm_hits >= 2)

### Case C: Already Pumped Stock (ZNTL +40%)
- **Setup**: $3.00 for 29 bars, pump to $4.20 on last bar with 10x volume
- **Expected**: Should be FILTERED (score < 65)
- **Actual**: ✓ PASS - Score 34/188, invalid=False, Grade D
- **Notes**: Correctly rejected; no consolidation detected

### Case D: Healthy Consolidation (AAPL-style)
- **Setup**: Slow climb $170→180 over 30 bars, normal volume, higher lows
- **Expected**: Valid=True (healthy uptrend consolidation)
- **Actual**: ✗ FAIL - Score 36/188, invalid=False, Grade D
- **Key Signals That DIDN'T Fire**:
  - ✗ ADX Turning: ADX=100 (already established, so no +14 BOOSTED)
  - ✗ Resilience: 0% (no down days, so no recovery bounces)
  - ✗ Inst. Accumulation: No high-volume up days detected
  - ✓ OBV: Only +7 (not divergence, price moved +5.6%)
  - ✓ Higher Lows: 100% but only +10 (not BOOSTED)

**DISCREPANCY**:
- AAPL is a textbook healthy consolidation (higher lows, price grinding higher)
- But ADX > 20 = "trend running" so Signal 7 fails
- Price moved +5.6%, so OBV can't be "divergence" (price_flat check fails)
- No down days = 0% resilience
- Result: Only CUT signals fire (~22 pts) but score needs 65

---

## ROOT CAUSE ANALYSIS

### Issue #1: RVOL Gate Disables Smart Money Signals
**Location**: Lines 835, 1761-1766  
**Severity**: HIGH - Breaks real breakout detection

```python
# Line 835: RVOL check
sm_eligible = (avg_volume >= 100_000 and _rvol_current >= 0.8) or crypto_mode

# Lines 1761-1766: Reset SM counters if not eligible
if sm_eligible:
    smart_money_fires = sm_fires
    smart_money_hits = sm_hits
else:
    smart_money_fires = 0
    smart_money_hits = 0
```

**The Problem**:
- Case B has avg_volume = 1.45M (healthy) but RVOL = 0.75 (slightly below 0.8 threshold)
- This is a REAL consolidation with volume drying up (classic breakout pattern)
- But RVOL < 0.8 causes sm_eligible=False → sm_fires=0 → Grade stuck at D

**Why This Is Wrong**:
- Volume drying up = energy building up (Wyckoff principle)
- Setting RVOL floor at 0.8 is too strict for real breakouts
- A stock with avg vol 1.5M should have BOOSTED signals, not silenced

**Recommendation**: Lower RVOL threshold to 0.7 or calculate differently (e.g., use last bar vol vs 20-bar avg, not 5-bar average)

---

### Issue #2: ADX "Turning" Requires ADX < 20 Initially, But Realistic Consolidations Have ADX > 20
**Location**: Lines 1154-1167  
**Severity**: MEDIUM - Misses healthy consolidations

```python
# Signal 7: ADX Turning
if adx < 20 and adx_prev and adx > adx_prev:
    score += 14; sm_fires += 1  # BOOSTED
elif adx < 25 and adx_prev and adx > adx_prev:
    score += 9  # Still strong
elif adx < 20:
    score += 4  # Low ADX but not turning
else:
    details.append(f" ADX already hoch: {adx:.0f} (Trend laeuft schon)")  # NO POINTS
```

**The Problem**:
- Case D: ADX = 100 (strong uptrend already established)
- ADX > 20 = trend running = 0 points for this signal
- But AAPL-style consolidation IS a valid setup; ADX high just means trend is healthy

**Why This Is Wrong**:
- ADX > 20 doesn't mean "no breakout coming"
- It means trend is already established
- Missing the difference between "breakout starting" (ADX <20 turning) vs "trend continuing from consolidation" (ADX already high)

**Recommendation**: Add separate logic for "ADX high AND flattening" as a consolidation signal

---

### Issue #3: "price_flat" Gate (< 5% move) Is Too Strict for OBV Divergence
**Location**: Lines 924-926, 994-1016  
**Severity**: MEDIUM - Degrades OBV signal to lower tier

```python
price_change_pct = ((closes[-1] - closes[0]) / closes[0]) * 100
price_flat = abs(price_change_pct) < 5  # Exactly 5% is the cutoff
```

**The Problem**:
- Case D: Price moves +5.6% over 30 bars (healthy, slow climb)
- price_flat = False
- OBV Divergence (signal 3): Only awards +7 points (not BOOSTED +13)
- Even though OBV is rising during this period (bullish sign)

**Why This Is Wrong**:
- +5.6% move doesn't change the fact that OBV is rising
- This should still count as hidden momentum (OBV divergence)
- Threshold should be higher (e.g., 8-10%) to avoid false negatives on good consolidations

**Recommendation**: Raise price_flat threshold to 8% or make it directional (e.g., price up 5% but OBV up 10%)

---

### Issue #4: Resilience Requires Down Days (Can't Score on Pure Uptrend)
**Location**: Lines 1311-1334  
**Severity**: LOW-MEDIUM - Penalizes strong uptrends

```python
negative_days = sum(1 for i in range(1, n) if closes[i] < closes[i-1])
recovery_days = 0
for i in range(2, n):
    if closes[i-1] < closes[i-2] and closes[i] > closes[i-1]:
        recovery_days += 1
resilience = min(1.0, recovery_days / max(1, negative_days))
```

**The Problem**:
- Case D: 0 negative days (pure uptrend)
- recovery_days / 0 = division issue → capped at 1.0 or returns 0%
- Actually returns 0% (if negative_days=0, then resilience = 0/1 = 0%)

**Why This Is Wrong**:
- A stock with NO down days is a sign of strength, not weakness
- Should score HIGH on resilience (100% hold rate!)
- Instead it scores 0% because the math treats "no downtrends" as "no recovery"

**Recommendation**: Handle negative_days=0 case separately (award 14pts immediately)

---

## SIGNAL FIRING ANALYSIS

### Signals That Fired as Expected:
1. **ATR Squeeze** (Signal 1): 6pts - CUT - Works correctly
2. **Close Position** (Signal 4): 5-10pts - Works correctly
3. **Range Duration** (Signal 5): 5pts - Works correctly
4. **Tight Compression** (Signal 12): 4-6pts - Works correctly
5. **Higher Lows** (Signal 10): 10pts - Works correctly
6. **Volume Void** (Signal 19): 10pts - Works correctly

### Signals That SHOULD Fire But Don't (False Negatives):

| Signal | Case | Expected | Actual | Why Failed |
|--------|------|----------|--------|-----------|
| OBV Divergence (3) | B | +13 BOOSTED | +13 but SM reset | RVOL gate |
| ADX Turning (7) | B | +14 BOOSTED | +4 | ADX not turning (static at 7) |
| ADX Turning (7) | D | +14 BOOSTED | +0 | ADX=100 (too high) |
| Resilience (11) | D | +14 BOOSTED | +0 | 0% (no down days) |

### Signal that Fires Weaker Than Expected:

| Signal | Case | Expected | Actual | Why |
|--------|------|----------|--------|-----|
| OBV (3) | D | +13 BOOSTED | +7 | price_flat=False (+5.6% move) |

---

## IMPACT SUMMARY

### Case B Impact:
- **Scored**: 70/188 (37.2%)
- **Valid**: Yes (+5 above 65 threshold)
- **Grade**: D (stuck, needs 85+ for B)
- **Missing**: 15 points to Grade B
- **Root Cause**: RVOL=0.75 disables sm_fires/sm_hits entirely

### Case D Impact:
- **Scored**: 36/188 (19.1%)
- **Valid**: No (-29 below 65 threshold)
- **Grade**: D
- **Missing**: 29 points to pass threshold
- **Root Causes**: 
  - ADX too high (no Signal 7 points)
  - Resilience=0% (no Signal 11 points)
  - OBV only 7pts (price not flat)

---

## CHECKLIST: EXPECTATIONS vs REALITY

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| Dead stock filtered | False | False | ✓ PASS |
| Real breakout scores highest | B > C | B > C | ✓ PASS |
| Real breakout gets Grade B+ | Grade B+ | Grade D | ✗ FAIL |
| Pumped stock filtered | False | False | ✓ PASS |
| Downtrend low long score | E < D | E < D | ✓ PASS |
| Healthy AAPL valid | True | False | ✗ FAIL |

**Result**: 4/6 (66.7%)

---

## RECOMMENDATIONS

### Priority 1 (Blocks Real Breakouts):
1. **Lower RVOL threshold from 0.8 to 0.65-0.70**
   - Volume dry-up is a feature, not a bug
   - Keep >= 100K avg volume check but relax RVOL gate
   
2. **Restructure price_flat logic for OBV**
   - Use "price magnitude < 8%" for BOOSTED classification
   - Or: price up <8% AND OBV up significantly = still divergence

### Priority 2 (Blocks Good Consolidations):
3. **Fix ADX signal for established trends**
   - Add "ADX > 20 AND flat for 10+ bars" as consolidation marker
   - Don't penalize high ADX; recognize it's healthy trend
   
4. **Handle resilience for pure uptrends**
   - If negative_days = 0, award max points (14) for "100% hold"
   - Currently awards 0% for lack of down days

### Priority 3 (Refinement):
5. **Test sm_fires reset logic**
   - Current design: OBV fires +13pts AND sm_fires += 1, but then sm_fires → 0
   - Either remove RVOL gate OR don't reset counters if any signals fired

---

## CONCLUSION

**The function is over-engineered for rare "extreme" setups** (flat price, OBV divergence, ADX turning from <20) and **underscores normal healthy consolidations** (slow grind with higher lows, established ADX trend, no down days).

The RVOL gate at 0.8 is the most harmful issue—it silences Smart Money signals on realistic volume dry-ups, which is exactly when you want to see such signals.

**Expected Impact of Fixes**: Case B would jump to Grade B+ (85+ score + sm_hits=2), Case D would jump to 65+ (valid).
