# DEEP AUDIT REPORT: TradingBot Scanner Modules
**Date:** March 29, 2026
**Severity: CRITICAL - Multiple show-stopper bugs found**

---

## CRITICAL ISSUES (Will cause crashes/failures)

### 1. MISSING MODULE: modules/scanners.py
**File:** Scanner.py imports (lines 111-133)
**Status:** Only `scanners.py.bak` exists
**Severity:** CRITICAL

**Problem:**
The following imports will FAIL at runtime:
```python
from modules.scanners import (
    _biotech_config_load, _biotech_config_save, _biotech_cache_load,
    _biotech_cache_save, _biotech_progress_write,
    _biotech_progress_read, _biotech_background_scan,
    _biotech_quick_scan,
    _biotech_technical_score, _biotech_news_momentum, _biotech_risk_score,
    ... (and many more)
)
```

**Missing Functions (29+ functions):**
- `_biotech_background_scan` - Full biotech scan with all signals
- `_biotech_quick_scan` - Quick catalyst-only scan
- `_biotech_technical_score(poly_key, ticker)` - Technical analysis score
- `_biotech_risk_score(market_cap_m, shares_m, negative_flags, price)` - Risk/opportunity score
- `_biotech_news_momentum(news_items)` - News sentiment analysis
- `_scan_biotech_news(poly_key, ticker, limit)` - News scraping and catalyst detection
- `_fetch_biotech_universe(poly_key, min_price, min_mcap_m)` - Universe loading
- `_compute_biotech_technical_from_bars(bars)` - Technical metrics
- `_check_clinical_trials(ticker)` - CT.gov data fetching
- And 20+ configuration, cache, and progress functions

**Impact:**
- Biotech scanner tab will crash on startup
- Any attempt to run biotech scans (lines 15516, 15523) will fail with ImportError
- Users cannot access biotech functionality at all

**Fix:**
Restore `modules/scanners.py` from backup or reconstruct from `scanners.py.bak`

---

### 2. HARDCODED PIPELINE_SCORE = 0 (Biotech Scoring Broken)
**File:** modules/scanners.py.bak, lines 2182-2213
**Function:** `_biotech_background_scan()`
**Severity:** CRITICAL

**Problem:**
```python
# Line 2182-2183
trial_data = {"pipeline_score": 0, "readout_score": 0, "readout_label": "",
              "catalyst_readouts": [], "trials": [], "phase_summary": {}, "total_active": 0}

# Line 2213 - Used but never updated!
pipeline_score=trial_data["pipeline_score"],  # Always 0
```

The `pipeline_score` is initialized to 0 and NEVER updated anywhere in the function.

**What SHOULD happen:**
- Should analyze drug pipeline stage/phase (Phase 1, 2, 3, NDA filing, etc.)
- Should score based on: number of active trials, phase progression, time to key milestones
- Should contribute up to ~20 points to final score

**Current behavior:**
- Pipeline strength completely ignored
- Biotech scores missing 1/3 of their informational content
- Companies with strong pipelines (Phase 3 trials, multiple programs) scored identically to single-stage companies

**Impact (Example):**
- Company A: FDA approval expected in 3 months (strong pipeline) = Score without pipeline benefit
- Company B: Early-stage Phase 1 only = Same score as Company A
- Trading decisions based on incomplete information = Real money losses

**Fix:**
Must implement pipeline_score calculation. Options:
1. Use ClinicalTrials.gov API to count active trials by phase
2. Parse BPIQ drug pipeline data (already implemented for catalysts)
3. Minimum: Count active trials and weight by phase (Phase 3 = 3x Phase 1 weight)

---

### 3. POLYGON API ENDPOINT - STARTER PLAN INCOMPATIBILITY
**File:** scanner.py, lines 5638, 6181, 278, 818
**Severity:** CRITICAL
**Impact:** API calls will fail silently, breaking critical scanner functionality

**Problem - Wrong Endpoint (Multi-ticker snapshot WITHOUT ticker):**
```python
# Line 5638 (in _get_early_movers_pm)
url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
params = {"apiKey": poly_key}  # NO TICKER PARAM - LOADS ALL TICKERS!

# Line 6181 (in get_premarket_analysis)
url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
# Same issue

# Line 278, 818 (in _load_all_tickers_polygon)
url = "https://api.polygon.io/v3/reference/tickers"
# v3 endpoint also has tier restrictions
```

**Why it fails:**
- `/v2/snapshot/locale/us/markets/stocks/tickers` (no ticker param) = **Enterprise plan only**
- `/v3/reference/tickers` = **Professional+ plan**
- Starter plan can ONLY use: `/v2/snapshot/locale/us/markets/stocks/tickers/{TICKER}` (single ticker)

**Correct endpoint for Starter:**
```python
url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
params = {"apiKey": poly_key}
```

**Impact:**
- Early movers scanner fails to load data
- Pre-market analyzer can't fetch tickers
- Silent failures (no error, just empty results) → misleading empty scan results

**Fix:**
Convert to single-ticker calls looped over watchlist or cached universe.
Warning: This will be much slower (100 tickers = 100 API calls).

---

## HIGH SEVERITY ISSUES (Scoring/Data Quality)

### 4. BREAKOUT ANALYSIS - SCORE EXCEEDS MAXIMUM WITHOUT CAP
**File:** modules/patterns.py, lines 800-1710
**Function:** `analyze_breakout_imminent()`
**Severity:** HIGH

**Problem:**
```python
# Line 1660
max_score = 200

# Then 20 signals accumulate points (no ceiling enforcement):
# Signal 1 (ATR Ratio): +6 max
# Signal 2 (Body Compression): +5 max
# Signal 3 (OBV Divergence - BOOSTED): +13 max
# Signal 4 (MACD): +10 max
# ...up to Signal 20...

# Line 1702-1710 - Score returned WITHOUT normalization
return is_valid, score, max_score, details, direction_confidence, grade, ...
```

**Issue:**
- Max_score is 200, but actual possible score can exceed 250+
- Grade thresholds assume max 200 (non-crypto: A at 105+, S at 120+)
- Scores above 200 break percentage-based reporting

**Example:**
If 15+ signals all fire maximally = 130+ points possible
- Grade "S" threshold = 120 for non-crypto
- But score could be 150+ → Grades don't reflect true signal strength
- Or score could be 180+ → Inflated grades

**Fix:**
Add before return statement (line 1710):
```python
score = min(max_score, score)  # Cap at 200
```

**Impact:** MEDIUM-HIGH
- Breaks score normalization for crypto/stocks with many boosted signals
- Grade inflation in edge cases (rarely happens but when it does, very wrong)
- Analytics and backtesting based on these scores could be skewed

---

### 5. POLYGON API HARDCODED/UNVERIFIED HANDLING
**File:** scanner.py, multiple locations
**Severity:** HIGH

**Problem 1 - No error handling for /v2/snapshot multi-ticker:**
```python
# Line 5638-5646
url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
_resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=30)
if _resp.status_code != 200:
    tickers = []  # SILENT FAILURE
else:
    resp = _resp.json()
    tickers = resp.get("tickers", [])

if len(tickers) == 0:
    return [], 0, 0, debug_stats  # EMPTY RESULT
```

If the endpoint fails (403 Forbidden on Starter plan), it silently returns empty.
User sees "No tickers found" not "API key insufficient for this endpoint".

**Problem 2 - No rate limit verification:**
The code calls `rate_limited_get()` but doesn't verify Polygon rate limits:
- Starter: 5 requests/minute
- Each multi-ticker request = 1 call
- But code makes calls in loops without proper throttling

---

## MEDIUM SEVERITY ISSUES (Logic/Design)

### 6. BIOTECH SCORE CAP PLACEMENT - ADDS BONUS AFTER CAP
**File:** modules/scanners.py.bak, lines 2220-2223
**Function:** `_biotech_background_scan()`
**Severity:** MEDIUM

**Problem:**
```python
# Line 2211-2218
total_score = _calculate_biotech_catalyst_score(
    catalyst_score=catalyst_score,
    pipeline_score=trial_data["pipeline_score"],  # Always 0
    technical_score=tech_data["technical_score"],
    risk_score=risk_data["risk_score"],
    news_momentum_score=momentum_score,
    rvol=_rvol_val
)
# Returns capped at 100 (line 797: min(100, max(0, total)))

# Line 2220-2223: THEN ADDS BONUS!
_readout_bonus = trial_data.get("readout_score", 0)
total_score = min(100, total_score + _readout_bonus)
```

**Issue:** Order matters!
- Readout bonus (up to 15 points) added AFTER score already capped at 100
- Result: Score can be 85 + 15 bonus = 100 cap applies again
- But if readout_bonus is added first, then entire capped at 100, it's different math

**Comment says (line 2221):** "V68: Cap bei 100 NACH Readout-Addition"
But code caps BEFORE addition, negating some bonus impact.

**Fix:**
Decide: Should bonus happen before or after cap?
- If before: `total_score = min(100, catalyst_score*2 + technical + risk + momentum + readout_bonus)`
- If after: Keep as is, but document clearly

---

### 7. BPIQ CATALYST CACHE - LOADED BUT NEVER USED IN UI
**File:** scanner.py, lines 2131-2250, 15516-15523
**Severity:** MEDIUM

**Problem:**
```python
# Function exists and is fully implemented
def _load_bpiq_catalyst_cache():  # Lines 2134-2250
    """Lädt ALLE Drugs mit Catalyst-Dates von BPIQ"""
    global _BPIQ_CATALYST_CACHE, _BPIQ_CACHE_TIMESTAMP
    # ... 120 lines of implementation ...
    return cache

# But it's NEVER CALLED in the UI
# Line 15516: _biotech_background_scan() is called
# But _biotech_background_scan() doesn't exist (module missing!)
```

**What happens:**
1. User clicks "Biotech Scanner" → imports fail → crash
2. If imports somehow work, _load_bpiq_catalyst_cache() is never invoked
3. BPIQ data stays empty despite 120 lines of code to load it

**Root cause:**
The functions that SHOULD call `_load_bpiq_catalyst_cache()` are in missing scanners.py module.
Lines in modules/scanners.py.bak (2192) show it SHOULD be called from `_biotech_background_scan()`.

---

### 8. BIOTECH CACHE FORMAT INCONSISTENCY
**File:** modules/scanners.py.bak, lines 2182-2196
**Severity:** MEDIUM

**Problem:**
```python
# Line 2182
trial_data = {"pipeline_score": 0, "readout_score": 0, "readout_label": "",
              "catalyst_readouts": [], "trials": [], "phase_summary": {}, "total_active": 0}

# Line 2194
trial_data["readout_score"] = bpiq_data["readout_score"]
trial_data["readout_label"] = bpiq_data["readout_label"]
trial_data["catalyst_readouts"] = bpiq_data["catalyst_readouts"]

# But other fields never populated:
# - trials: stays []
# - phase_summary: stays {}
# - total_active: stays 0
```

These fields are initialized but never used. Unclear if they're:
- Dead code from old CT.gov implementation
- Placeholder for future use
- Genuinely unused

**Impact:** Low but suggests incomplete refactoring.

---

## LOW SEVERITY ISSUES (Edge cases, minor bugs)

### 9. DIRECTIONAL SIGNALS COUNT - ALWAYS ZERO
**File:** modules/patterns.py, line 1664
**Severity:** LOW

**Problem:**
```python
# Line 1664
directional_signals = sum(1 for d in details if "" in d or "" in d)
#                                                  ^^             ^^
# Empty strings! Pattern match will match ANY string!
```

Both substrings are empty. This will match every detail, making:
```python
# Line 1665
direction_confidence = round((directional_signals / 20) * 100)
```

Always give direction_confidence = 100% if details exist.

**Should be:**
```python
directional_signals = sum(1 for d in details if "long" in d or "short" in d)
```

**Impact:** LOW
- direction_confidence always 100% or 0% (depending on if details exist)
- Field might not be used in critical decisions
- But analytics/reporting could be skewed

---

### 10. PATTERN VALIDATION - FLAGS WITHOUT THRESHOLD CHECK
**File:** modules/patterns.py, lines 117-135 (validate_flag_pattern)
**Severity:** LOW

**Problem:**
```python
# Line 135
is_valid = score >= 40  # Hardcoded threshold

# But max_score calculation:
if scenario == "SHORT":
    max_score = 30 + 25 + 25 + 20 + 25 + 25 + 8 = 158
```

Threshold (40) is reasonable, but max possible is much higher.
If all signals fire perfectly, unrealistic score inflation.

**Better approach:**
```python
is_valid = score >= 40 and score > (0.25 * max_score)  # 25% of max
```

---

### 11. VOLUME ANALYSIS - SPARSE DATA HANDLING
**File:** modules/patterns.py, lines 333-335
**Severity:** LOW

**Problem:**
```python
# Line 333-334
recent_vol = sum(volumes[-5:]) / 5
prior_vol = sum(volumes[-10:-5]) / 5 if sum(volumes[-10:-5]) > 0 else 1

# If volumes list has < 5 elements, sum() is 0, division returns 0
# Then vol_ratio = 0 / 1 = 0 (valid, but might not represent true ratio)
```

**Edge case:** If only 3 days of volume data:
```python
volumes = [100, 200, 150]
recent_vol = sum([100, 200, 150]) / 5 = 450 / 5 = 90  # Treats missing as 0!
```

Not a crash, but incorrect calculation if data is sparse.

**Fix:** Check data length:
```python
if len(volumes) < 10:
    return 0  # Insufficient data
```

---

## ARCHITECTURE ISSUES

### 12. INCOMPLETE REFACTOR - SCANNERS.PY NEVER COMPLETED
**Severity:** CRITICAL

**Status:** Refactor was partially done:
- Scanner.py has import statements for 29+ functions from `modules.scanners`
- `modules.scanners.py.bak` contains full implementations
- But `modules.scanners.py` (active file) DOES NOT EXIST

**Comments in code show intent:**
```python
# Line 2068: _biotech_config_load — Moved to modules/scanners.py
# Line 2071: _biotech_config_save — Moved to modules/scanners.py
# ...etc (20+ comments)
```

But the actual `modules/scanners.py` file was never created!

**Suspect timeline:**
1. Developer planned refactor → created scanners.py.bak copy
2. Started moving functions → created comments "Moved to..."
3. Never finished → never created actual modules/scanners.py
4. Code left in broken state

---

## SUMMARY TABLE

| Issue | File | Lines | Severity | Type | Status |
|-------|------|-------|----------|------|--------|
| Missing modules/scanners.py | scanner.py | 111-133 | CRITICAL | Module Missing | Blocking |
| Pipeline score hardcoded 0 | scanners.py.bak | 2182-2213 | CRITICAL | Logic Error | Blocking Biotech |
| Wrong Polygon API endpoint | scanner.py | 5638, 6181, 278, 818 | CRITICAL | API Error | Silent Failure |
| Score exceeds max (200) | patterns.py | 1660-1710 | HIGH | Scoring Bug | Edge Case |
| Hardcoded empty string pattern | patterns.py | 1664 | LOW | Logic Error | Minor |
| Directional confidence always 100% | patterns.py | 1665 | LOW | Calculation | Minor |
| Readout bonus cap order | scanners.py.bak | 2220-2223 | MEDIUM | Design | Clarification Needed |
| BPIQ cache never called | scanner.py | 15516+ | MEDIUM | Dead Code | Cascading from #1 |

---

## RECOMMENDATIONS (Priority Order)

### IMMEDIATE (Next Hour)
1. **Restore modules/scanners.py** from scanners.py.bak
   - Command: `cp modules/scanners.py.bak modules/scanners.py`
   - Verify imports work

2. **Fix pipeline_score hardcoding** in modules/scanners.py (line ~2182)
   - Must calculate from trial data
   - Temporary: Use BPIQ catalyst count as proxy

3. **Fix Polygon API endpoints** in scanner.py (lines 5638, 6181, 278, 818)
   - Use single-ticker endpoints for Starter plan

### SHORT TERM (Today)
4. **Cap breakout scores** in patterns.py (line 1660)
   - Add: `score = min(200, score)` before return

5. **Fix empty string pattern match** in patterns.py (line 1664)
   - Change to meaningful pattern

### MEDIUM TERM (This Week)
6. Audit all API calls for tier restrictions
7. Implement proper pipeline_score from clinical trial data
8. Add comprehensive error logging instead of silent failures

