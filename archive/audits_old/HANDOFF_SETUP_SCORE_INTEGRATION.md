# HANDOFF: Setup Score Sortierung integrieren

## AKTUELLER STAND

### Was fertig ist
1. **`calculate_confluence_score()`** (Zeile 1425) — 10-Kategorien Engine, wird im Detail-View bei Ticker-Klick ausgeführt
2. **`calculate_setup_score()`** (Zeile 1876) — Quick Score 0-100 aus Snapshot-Daten, **FUNKTION EXISTIERT, IST ABER NOCH NICHT IN DER SCAN-PIPELINE INTEGRIERT**
3. **Detail-View Integration** (Zeile ~15896) — Confluence automatisch für alle Aktien-Strategien bei Klick, SPY gecacht, kein $5 Cutoff mehr
4. **Confluence Strategie gelöscht** — Kein "Confluence Long/Short 🔥" mehr im Dropdown, kein toter Code mehr

### Was als Nächstes gemacht werden muss
**`calculate_setup_score()` in die Scan-Pipeline integrieren, damit die besten Setups zuerst sortiert werden (statt nach Alpha = RVOL+Change).**

---

## GENAUE INTEGRATIONS-ANLEITUNG

### Schritt 1: Setup Score in `fetch_stock_data()` berechnen

**Datei:** scanner.py  
**Funktion:** `fetch_stock_data()` ab Zeile 10254  
**Einfügepunkt:** Zeile ~10640, direkt VOR `results.append({`

Dort sind alle nötigen Variablen bereits vorhanden:
```python
# Diese Variablen existieren bereits im Scope:
change        # Chg%
rvol          # RVOL
close_pos     # Close Position 0-1
upper_wick_pct  # Upper Wick %
lower_wick_pct  # Lower Wick %
vortag_chg    # Vortag %
atr_pct       # ATR %
dollar_volume # Dollar Volume
price         # Preis
```

**Was eingefügt werden muss (VOR results.append):**
```python
# Setup Score — Richtung aus Strategie ableiten
SHORT_KEYWORDS = ["Short", "Bear", "Breakdown", "Losers", "Down"]
setup_direction = "short" if any(kw in current_strategy for kw in SHORT_KEYWORDS) else "long"
setup_score = calculate_setup_score(
    change_pct=change, rvol=rvol, close_pos=close_pos,
    upper_wick_pct=upper_wick_pct, lower_wick_pct=lower_wick_pct,
    vortag_pct=vortag_chg, atr_pct=atr_pct,
    dollar_volume=dollar_volume, price=price,
    direction=setup_direction
)
```

**In results.append hinzufügen:**
```python
"SetupScore": setup_score,
```

Der aktuelle `results.append` Block (Zeile ~10640):
```python
results.append({
    "Ticker": ticker_raw, "Name": "",
    "Preis": round(price, 4), "Chg%": round(change, 2),
    "RVOL": rvol, "Vortag%": vortag_chg,
    "ClosePos": round(close_pos, 2) if close_pos is not None else 0.5, "Alpha": alpha,
    "Gap%": round(gap_pct, 2),
    "TrueGap%": round(true_gap_pct, 2),
    "UpperWick%": round(upper_wick_pct, 1),
    "LowerWick%": round(lower_wick_pct, 1),
    "FlagScore": flag_score,
    "FlagDetails": flag_details,
    "High": high,
    "Low": low,
    "PrevClose": prev_close,
    "ATR%": atr_pct,
    "VolRegime": volatility_regime,
    "DollarVol": dollar_volume,
    "IsLiquid": is_liquid,
    "BreakoutHealth": breakout_health,
})
```

### Schritt 2: Crypto auch (optional)

**Funktion:** Crypto results.append Zeile ~10226  
Gleiche Logik, aber `atr_pct` ist dort evtl. nicht vorhanden → None übergeben.

### Schritt 3: Sortierung umstellen

**ALLE diese Stellen von Alpha auf SetupScore ändern:**

| Zeile | Aktuell | Neu |
|-------|---------|-----|
| ~14594 | `sorted(filtered, key=lambda x: x.get("Alpha", 0), reverse=True)[:30]` | `sorted(filtered, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)[:30]` |
| ~14691 | `sorted(filtered, key=lambda x: x.get("Alpha", 0), reverse=True)[:50]` | `sorted(filtered, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)[:50]` |
| ~14775 | `sorted(filtered, key=lambda x: x.get("Alpha", 0), reverse=True)[:50]` | `sorted(filtered, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)[:50]` |
| ~15125 | `sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]` | `sorted(results, key=lambda x: x.get("SetupScore", x.get("Alpha", 0)), reverse=True)[:50]` |

Fallback auf Alpha wenn SetupScore nicht existiert (für Krypto/Futures/Insider die keinen SetupScore haben).

### Schritt 4: Setup Score in Tabelle anzeigen

Im Display-Bereich die Spalte hinzufügen. Aktuell zeigt die Tabelle "Alpha" — ersetzen oder ergänzen mit "Setup":

```python
"SetupScore": st.column_config.ProgressColumn("Setup", min_value=0, max_value=100, format="%d"),
```

Oder als NumberColumn:
```python
"SetupScore": st.column_config.NumberColumn("Setup", format="%d/100"),
```

---

## calculate_setup_score() SIGNATUR (Zeile 1876)

```python
def calculate_setup_score(change_pct, rvol, close_pos, upper_wick_pct, lower_wick_pct,
                           vortag_pct, atr_pct, dollar_volume, price, direction="long"):
```

**Kategorien:**
- Volume (0-20): RVOL ≥3.0=20, ≥2.0=16, ≥1.5=12, ≥1.0=6
- Kerze (0-20): Close Position (0-14) + Wick (0-6)
- Timing (0-20): ATR Extension Sweet Spot 0.5-2x=20, ≤3x=14, ≤3.5x=7
- Liquidität (0-15): DollarVol ≥$5M=15, ≥$1M=12, ≥$500k=8, ≥$100k=3
- Momentum (0-15): Change + RVOL zusammen positiv = 15
- Kontext (0-10): Vortag <1.5%=10 (Konsolidierung), <3%=6, <5%=3

---

## CONFLUENCE ENGINE BUGS (bereits gefixt)

Zur Referenz, diese wurden in dieser Session gefixt:
1. ✅ Markt: spy_change=None → FAIL (nicht Gratis-PASS)
2. ✅ Markt: SPY Trend Override nur wenn SPY HEUTE auch rot + unter EMA50
3. ✅ RS: Long muss change_pct > 0 (fallende Aktie ≠ Outperformer)
4. ✅ Pattern: Nur "bullish"/"bearish", nicht "neutral" (Doji)
5. ✅ Short-Kerze: Lower Wick ≥30% = FAIL (Hammer-Erkennung)
6. ✅ $5 Cutoff entfernt — Pennys bekommen auch Confluence

---

## DATEI

- **scanner.py** — 17.394 Zeilen
- Syntax: ✅ OK
- Pfad lokal: `C:\Users\miros\Desktop\TradingBot\scanner.py`

## GIT (noch ausstehend vom User)

```powershell
cd C:\Users\miros\Desktop\TradingBot
git add scanner.py
git commit -m "Confluence Detail-View für alle Strategien, Setup Score Funktion, Penny Limit entfernt, toter Code bereinigt"
git push
```
