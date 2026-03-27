"""
Chart Utilities — TradingView Lightweight Charts HTML Generation (V70.0)
"""
import json
from modules.indicators import calculate_ema_series


def create_lightweight_chart_html(ohlcv_data, ticker, sr_levels=None, patterns=None, fib_levels=None, 
                                   ema_periods=[20, 50, 100, 200], height=500, show_volume=True,
                                   vwap_data=None, volume_voids=None, trade_zones=None, harmonic_data=None, wyckoff_data=None):
    """
    Erstellt HTML für Lightweight Charts mit ALLEN Overlays:
    - Candlesticks + Volume
    - EMAs (20/50/100/200)
    - Support/Resistance Levels
    - Fibonacci Retracements
    - VWAP + Standard Deviation Bands
    - Volume Voids (orange highlights)
    - Harmonic Patterns (XABCD ZigZag)
    - Wyckoff Patterns (Range Box + Event Markers)
    - Trade Zones (Entry/Stop/Target)
    - Pattern Markers
    
    Returns:
        HTML string für streamlit.components.v1.html()
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return "<div>Keine Daten verfügbar</div>"
    
    # Prepare candlestick data
    candles_json = json.dumps(ohlcv_data)
    
    # Prepare volume data
    volume_data = [{"time": d["time"], "value": d.get("volume", 0), 
                    "color": "rgba(38, 166, 154, 0.5)" if d["close"] >= d["open"] else "rgba(239, 83, 80, 0.5)"} 
                   for d in ohlcv_data]
    volume_json = json.dumps(volume_data)
    
    # Prepare EMA data
    closes = [d["close"] for d in ohlcv_data]
    times = [d["time"] for d in ohlcv_data]
    
    ema_lines = []
    ema_colors = ["#2196F3", "#FF9800", "#E91E63", "#9C27B0"]  # Blue, Orange, Pink, Purple
    
    for i, period in enumerate(ema_periods):
        ema_values = calculate_ema_series(closes, period)
        ema_data = []
        for j, val in enumerate(ema_values):
            if val is not None:
                ema_data.append({"time": times[j], "value": round(val, 2)})
        if ema_data:
            ema_lines.append({
                "data": ema_data,
                "color": ema_colors[i % len(ema_colors)],
                "label": f"EMA {period}"
            })
    
    ema_json = json.dumps(ema_lines)
    
    # Prepare VWAP data
    vwap_lines = []
    if vwap_data:
        vwap_values = vwap_data.get("vwap_values", [])
        if vwap_values and len(vwap_values) == len(times):
            # VWAP Line
            vwap_line_data = [{"time": times[i], "value": round(vwap_values[i], 2)} for i in range(len(vwap_values))]
            vwap_lines.append({"data": vwap_line_data, "color": "#FFEB3B", "label": "VWAP", "lineWidth": 2})
            
            # Upper/Lower Bands
            std = vwap_data.get("std_dev", 0)
            if std > 0:
                upper_1 = [{"time": times[i], "value": round(vwap_values[i] + std, 2)} for i in range(len(vwap_values))]
                lower_1 = [{"time": times[i], "value": round(vwap_values[i] - std, 2)} for i in range(len(vwap_values))]
                upper_2 = [{"time": times[i], "value": round(vwap_values[i] + 2*std, 2)} for i in range(len(vwap_values))]
                lower_2 = [{"time": times[i], "value": round(vwap_values[i] - 2*std, 2)} for i in range(len(vwap_values))]
                
                vwap_lines.append({"data": upper_1, "color": "rgba(255, 235, 59, 0.5)", "label": "VWAP +1σ", "lineWidth": 1})
                vwap_lines.append({"data": lower_1, "color": "rgba(255, 235, 59, 0.5)", "label": "VWAP -1σ", "lineWidth": 1})
                vwap_lines.append({"data": upper_2, "color": "rgba(255, 235, 59, 0.3)", "label": "VWAP +2σ", "lineWidth": 1})
                vwap_lines.append({"data": lower_2, "color": "rgba(255, 235, 59, 0.3)", "label": "VWAP -2σ", "lineWidth": 1})
    
    vwap_json = json.dumps(vwap_lines)
    
    # Prepare S/R lines - VERBESSERT mit Type Labels
    sr_lines = []
    if sr_levels:
        for s in sr_levels.get("support_levels", [])[:3]:
            # Stärke skalieren: 50-100 → 1-3 für Liniendicke
            strength_raw = s.get("strength", 50)
            line_width = 1 if strength_raw < 70 else (2 if strength_raw < 90 else 3)
            
            # Type als Label nutzen wenn vorhanden
            level_type = s.get("type", "Support")
            label = f"S: ${s['price']:.2f}"
            if "PDL" in level_type:
                label = f"PDL: ${s['price']:.2f}"
            elif "Fib" in level_type:
                label = f"Fib: ${s['price']:.2f}"
            elif "Round" in level_type:
                label = f"${s['price']:.2f}"
            
            sr_lines.append({
                "price": s["price"],
                "color": "#4CAF50",
                "lineWidth": line_width,
                "label": label,
                "type": "support"
            })
        for r in sr_levels.get("resistance_levels", [])[:3]:
            strength_raw = r.get("strength", 50)
            line_width = 1 if strength_raw < 70 else (2 if strength_raw < 90 else 3)
            
            level_type = r.get("type", "Resistance")
            label = f"R: ${r['price']:.2f}"
            if "PDH" in level_type:
                label = f"PDH: ${r['price']:.2f}"
            elif "PDC" in level_type:
                label = f"PDC: ${r['price']:.2f}"
            elif "Fib" in level_type:
                label = f"Fib: ${r['price']:.2f}"
            elif "Round" in level_type:
                label = f"${r['price']:.2f}"
            
            sr_lines.append({
                "price": r["price"],
                "color": "#F44336",
                "lineWidth": line_width,
                "label": label,
                "type": "resistance"
            })
    sr_json = json.dumps(sr_lines)
    
    # Prepare Fibonacci lines
    fib_lines = []
    if fib_levels:
        fib_colors = {
            "0.0": "#787B86", "0.236": "#F44336", "0.382": "#FF9800",
            "0.5": "#FFEB3B", "0.618": "#4CAF50", "0.786": "#2196F3",
            "1.0": "#787B86", "1.272": "#9C27B0", "1.618": "#E91E63"
        }
        for level, price in fib_levels.get("levels", {}).items():
            fib_lines.append({
                "price": price,
                "color": fib_colors.get(level, "#787B86"),
                "label": f"Fib {level}"
            })
    fib_json = json.dumps(fib_lines)
    
    # Prepare Volume Voids (for horizontal highlighting)
    voids_json = json.dumps(volume_voids if volume_voids else [])
    
    # Prepare Harmonic Pattern data (XABCD zigzag lines)
    harmonic_json = json.dumps(harmonic_data if harmonic_data else [])
    
    # Prepare Wyckoff Pattern data (range box + event markers)
    wyckoff_json = json.dumps(wyckoff_data if wyckoff_data else [])
    
    # Prepare Trade Zones
    zones = []
    if trade_zones:
        if trade_zones.get("entry"):
            zones.append({"price": trade_zones["entry"], "color": "rgba(76, 175, 80, 0.3)", "label": "ENTRY", "type": "entry"})
        if trade_zones.get("stop"):
            zones.append({"price": trade_zones["stop"], "color": "rgba(244, 67, 54, 0.3)", "label": "STOP", "type": "stop"})
        if trade_zones.get("target"):
            zones.append({"price": trade_zones["target"], "color": "rgba(33, 150, 243, 0.3)", "label": "TARGET", "type": "target"})
        if trade_zones.get("target2"):
            zones.append({"price": trade_zones["target2"], "color": "rgba(33, 150, 243, 0.2)", "label": "TP2", "type": "target2"})
    zones_json = json.dumps(zones)
    
    # Pattern markers
    markers = []
    if patterns:
        for p in patterns[:5]:  # Max 5 patterns
            marker_color = "#4CAF50" if p.get("type") == "bullish" else "#F44336" if p.get("type") == "bearish" else "#FFEB3B"
            markers.append({
                "time": ohlcv_data[-1]["time"],
                "position": "aboveBar" if p.get("type") == "bearish" else "belowBar",
                "color": marker_color,
                "shape": "arrowDown" if p.get("type") == "bearish" else "arrowUp",
                "text": p.get("pattern", "")[:15]
            })
    markers_json = json.dumps(markers)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            body {{ margin: 0; padding: 0; background: #1a1a2e; font-family: Arial, sans-serif; }}
            #chart-container {{ width: 100%; height: {height}px; position: relative; }}
            .chart-legend {{
                position: absolute; top: 10px; left: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 10px; border-radius: 5px;
                color: #fff; font-size: 11px; max-width: 180px;
            }}
            .legend-item {{ display: flex; align-items: center; margin: 2px 0; }}
            .legend-color {{ width: 16px; height: 3px; margin-right: 6px; border-radius: 1px; }}
            .legend-section {{ margin-top: 6px; font-weight: bold; color: #888; font-size: 10px; }}
            .ticker-title {{
                position: absolute; top: 10px; right: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 10px 15px; border-radius: 5px;
                color: #fff; font-size: 16px; font-weight: bold;
            }}
            .pattern-box {{
                position: absolute; bottom: 10px; right: 10px; z-index: 100;
                background: rgba(26, 26, 46, 0.95); padding: 8px 12px; border-radius: 5px;
                color: #fff; font-size: 11px; max-width: 200px;
            }}
            .pattern-bullish {{ color: #4CAF50; }}
            .pattern-bearish {{ color: #F44336; }}
            .void-indicator {{
                position: absolute; top: 50px; right: 10px; z-index: 100;
                background: rgba(255, 152, 0, 0.2); padding: 5px 10px; border-radius: 3px;
                color: #FF9800; font-size: 10px; border: 1px solid rgba(255, 152, 0, 0.5);
            }}
        </style>
    </head>
    <body>
        <div id="chart-container">
            <div class="ticker-title">{ticker}</div>
            <div class="chart-legend" id="legend"></div>
            <div class="pattern-box" id="patterns"></div>
        </div>
        
        <script>
            const container = document.getElementById('chart-container');
            
            const chart = LightweightCharts.createChart(container, {{
                width: container.clientWidth,
                height: {height},
                layout: {{
                    background: {{ type: 'solid', color: '#1a1a2e' }},
                    textColor: '#d1d4dc',
                }},
                grid: {{
                    vertLines: {{ color: 'rgba(42, 46, 57, 0.5)' }},
                    horzLines: {{ color: 'rgba(42, 46, 57, 0.5)' }},
                }},
                crosshair: {{
                    mode: LightweightCharts.CrosshairMode.Normal,
                }},
                rightPriceScale: {{
                    borderColor: 'rgba(197, 203, 206, 0.4)',
                    scaleMargins: {{ top: 0.05, bottom: 0.2 }},
                }},
                timeScale: {{
                    borderColor: 'rgba(197, 203, 206, 0.4)',
                    timeVisible: true,
                    secondsVisible: false,
                }},
            }});
            
            // Candlestick Series
            const candleSeries = chart.addCandlestickSeries({{
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderUpColor: '#26a69a',
                borderDownColor: '#ef5350',
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
            }});
            
            const candleData = {candles_json};
            candleSeries.setData(candleData);
            
            // Volume Series
            {"" if not show_volume else f'''
            const volumeSeries = chart.addHistogramSeries({{
                priceFormat: {{ type: 'volume' }},
                priceScaleId: '',
            }});
            volumeSeries.priceScale().applyOptions({{
                scaleMargins: {{ top: 0.85, bottom: 0 }},
            }});
            const volumeData = {volume_json};
            volumeSeries.setData(volumeData);
            '''}
            
            // Build Legend
            let legendHtml = '<div class="legend-section">UP EMAs</div>';
            
            // EMA Lines
            const emaLines = {ema_json};
            emaLines.forEach((ema, index) => {{
                const lineSeries = chart.addLineSeries({{
                    color: ema.color,
                    lineWidth: 1,
                    priceLineVisible: false,
                    lastValueVisible: false,
                }});
                lineSeries.setData(ema.data);
                legendHtml += `<div class="legend-item"><div class="legend-color" style="background:${{ema.color}}"></div>${{ema.label}}</div>`;
            }});
            
            // VWAP Lines
            const vwapLines = {vwap_json};
            if (vwapLines.length > 0) {{
                legendHtml += '<div class="legend-section"> VWAP</div>';
                vwapLines.forEach((vwap, index) => {{
                    const lineSeries = chart.addLineSeries({{
                        color: vwap.color,
                        lineWidth: vwap.lineWidth || 1,
                        priceLineVisible: false,
                        lastValueVisible: index === 0,
                    }});
                    lineSeries.setData(vwap.data);
                    if (index === 0) {{
                        legendHtml += `<div class="legend-item"><div class="legend-color" style="background:${{vwap.color}}"></div>${{vwap.label}}</div>`;
                    }}
                }});
            }}
            
            // Support/Resistance Lines
            const srLines = {sr_json};
            if (srLines.length > 0) {{
                legendHtml += '<div class="legend-section"> S/R Levels</div>';
                srLines.forEach(sr => {{
                    candleSeries.createPriceLine({{
                        price: sr.price,
                        color: sr.color,
                        lineWidth: sr.lineWidth,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: sr.label,
                    }});
                    const icon = sr.type === 'support' ? '[+]' : '[-]';
                    legendHtml += `<div class="legend-item">${{icon}} ${{sr.price.toFixed(2)}}</div>`;
                }});
            }}
            
            // Fibonacci Lines
            const fibLines = {fib_json};
            if (fibLines.length > 0) {{
                fibLines.forEach(fib => {{
                    candleSeries.createPriceLine({{
                        price: fib.price,
                        color: fib.color,
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: true,
                        title: fib.label,
                    }});
                }});
            }}
            
            // Trade Zones (as price lines with different styles)
            const tradeZones = {zones_json};
            if (tradeZones.length > 0) {{
                legendHtml += '<div class="legend-section"> Trade Setup</div>';
                tradeZones.forEach(zone => {{
                    let lineColor, lineStyle, icon;
                    if (zone.type === 'entry') {{
                        lineColor = '#4CAF50';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        icon = '';
                    }} else if (zone.type === 'stop') {{
                        lineColor = '#F44336';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        icon = '';
                    }} else {{
                        lineColor = '#2196F3';
                        lineStyle = LightweightCharts.LineStyle.Dashed;
                        icon = '[OK]';
                    }}
                    
                    candleSeries.createPriceLine({{
                        price: zone.price,
                        color: lineColor,
                        lineWidth: 2,
                        lineStyle: lineStyle,
                        axisLabelVisible: true,
                        title: zone.label,
                    }});
                    legendHtml += `<div class="legend-item">${{icon}} ${{zone.price.toFixed(2)}} (${{zone.label}})</div>`;
                }});
            }}
            
            // Volume Voids - Add indicator if any exist
            const volumeVoids = {voids_json};
            if (volumeVoids.length > 0) {{
                volumeVoids.forEach(void_ => {{
                    // Add dotted lines at void boundaries
                    candleSeries.createPriceLine({{
                        price: void_.price_low,
                        color: 'rgba(255, 152, 0, 0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: false,
                    }});
                    candleSeries.createPriceLine({{
                        price: void_.price_high,
                        color: 'rgba(255, 152, 0, 0.6)',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: false,
                    }});
                }});
                
                // Add void indicator
                const voidDiv = document.createElement('div');
                voidDiv.className = 'void-indicator';
                voidDiv.innerHTML = ` ${{volumeVoids.length}} Volume Void${{volumeVoids.length > 1 ? 's' : ''}}`;
                container.appendChild(voidDiv);
            }}
            
            // Pattern Markers
            const markers = {markers_json};
            if (markers.length > 0) {{
                candleSeries.setMarkers(markers);
            }}
            
            // Harmonic Patterns - XABCD ZigZag Lines
            const harmonicPatterns = {harmonic_json};
            if (harmonicPatterns.length > 0) {{
                harmonicPatterns.forEach((pat, patIdx) => {{
                    const pts = pat.points;
                    if (pts && pts.length === 5) {{
                        const isLong = pat.direction === 'LONG';
                        const lineColor = isLong ? 'rgba(76, 175, 80, 0.9)' : 'rgba(244, 67, 54, 0.9)';
                        const fillColor = isLong ? 'rgba(76, 175, 80, 0.12)' : 'rgba(244, 67, 54, 0.12)';
                        
                        // Draw XABCD zigzag as line series segments
                        const harmonicLine = chart.addLineSeries({{
                            color: lineColor,
                            lineWidth: 2,
                            lineStyle: 0,
                            crosshairMarkerVisible: false,
                            lastValueVisible: false,
                            priceLineVisible: false,
                        }});
                        
                        const lineData = pts.map(p => ({{ time: p.time, value: p.price }}));
                        harmonicLine.setData(lineData);
                        
                        // Add XABCD markers on candleSeries
                        const labels = ['X', 'A', 'B', 'C', 'D'];
                        const harmonicMarkers = pts.map((p, i) => ({{
                            time: p.time,
                            position: (i === 0 || i === 2 || i === 4) ? (isLong ? 'belowBar' : 'aboveBar') : (isLong ? 'aboveBar' : 'belowBar'),
                            color: lineColor,
                            shape: 'circle',
                            text: labels[i] + ' $' + p.price.toFixed(1),
                        }}));
                        
                        // Merge with existing markers (sorted by time!)
                        const allMarkers = [...markers, ...harmonicMarkers].sort((a, b) => {{
                            if (typeof a.time === 'number' && typeof b.time === 'number') return a.time - b.time;
                            return String(a.time).localeCompare(String(b.time));
                        }});
                        candleSeries.setMarkers(allMarkers);
                        
                        // Trade levels from harmonic pattern (dashed lines)
                        if (pat.trade) {{
                            if (pat.trade.entry) {{
                                candleSeries.createPriceLine({{
                                    price: pat.trade.entry,
                                    color: 'rgba(76, 175, 80, 0.7)',
                                    lineWidth: 1,
                                    lineStyle: LightweightCharts.LineStyle.Dashed,
                                    axisLabelVisible: true,
                                    title: 'Entry',
                                }});
                            }}
                            if (pat.trade.stop_loss) {{
                                candleSeries.createPriceLine({{
                                    price: pat.trade.stop_loss,
                                    color: 'rgba(244, 67, 54, 0.7)',
                                    lineWidth: 1,
                                    lineStyle: LightweightCharts.LineStyle.Dashed,
                                    axisLabelVisible: true,
                                    title: 'Stop',
                                }});
                            }}
                            if (pat.trade.tp1) {{
                                candleSeries.createPriceLine({{
                                    price: pat.trade.tp1,
                                    color: 'rgba(33, 150, 243, 0.7)',
                                    lineWidth: 1,
                                    lineStyle: LightweightCharts.LineStyle.Dashed,
                                    axisLabelVisible: true,
                                    title: 'TP1',
                                }});
                            }}
                            if (pat.trade.tp2) {{
                                candleSeries.createPriceLine({{
                                    price: pat.trade.tp2,
                                    color: 'rgba(33, 150, 243, 0.5)',
                                    lineWidth: 1,
                                    lineStyle: LightweightCharts.LineStyle.Dotted,
                                    axisLabelVisible: true,
                                    title: 'TP2',
                                }});
                            }}
                        }}
                        
                        // Harmonic indicator badge
                        const hDiv = document.createElement('div');
                        hDiv.style.cssText = 'position:absolute;top:' + (8 + patIdx * 22) + 'px;right:10px;background:rgba(0,0,0,0.7);color:' + lineColor + ';padding:2px 8px;border-radius:4px;font-size:12px;z-index:5;';
                        hDiv.innerHTML = pat.emoji + ' ' + pat.pattern + ' (' + pat.direction + ') ' + pat.matches;
                        container.appendChild(hDiv);
                    }}
                }});
            }}
            
            // Wyckoff Patterns - Range Box + Event Markers
            const wyckoffPatterns = {wyckoff_json};
            if (wyckoffPatterns.length > 0) {{
                wyckoffPatterns.forEach((wp, wpIdx) => {{
                    const isLong = wp.direction === 'LONG';
                    const rangeColor = isLong ? 'rgba(76, 175, 80, 0.08)' : 'rgba(244, 67, 54, 0.08)';
                    const lineColor = isLong ? 'rgba(76, 175, 80, 0.7)' : 'rgba(244, 67, 54, 0.7)';
                    
                    // Range High line (dashed)
                    candleSeries.createPriceLine({{
                        price: wp.range_high,
                        color: lineColor,
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: 'Range High',
                    }});
                    
                    // Range Low line (dashed)
                    candleSeries.createPriceLine({{
                        price: wp.range_low,
                        color: lineColor,
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: 'Range Low',
                    }});
                    
                    // Range Mid line (dotted)
                    const rangeMid = (wp.range_high + wp.range_low) / 2;
                    candleSeries.createPriceLine({{
                        price: rangeMid,
                        color: lineColor.replace('0.7', '0.3'),
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: false,
                    }});
                    
                    // Event markers on chart
                    if (wp.events && wp.events.length > 0) {{
                        const wyckoffMarkers = wp.events.filter(e => e.time).map(e => ({{
                            time: e.time,
                            position: e.pos === 'above' ? 'aboveBar' : 'belowBar',
                            color: isLong ? '#4CAF50' : '#F44336',
                            shape: 'circle',
                            text: e.label || e.name,
                        }}));
                        
                        // Merge with existing markers
                        const existingMarkers = candleSeries.markers ? candleSeries.markers() : [];
                        const allMarkers = [...markers, ...wyckoffMarkers].sort((a, b) => {{
                            if (typeof a.time === 'number' && typeof b.time === 'number') return a.time - b.time;
                            return String(a.time).localeCompare(String(b.time));
                        }});
                        candleSeries.setMarkers(allMarkers);
                    }}
                    
                    // Trade levels
                    if (wp.trade) {{
                        if (wp.trade.entry) {{
                            candleSeries.createPriceLine({{
                                price: wp.trade.entry, color: 'rgba(76, 175, 80, 0.6)',
                                lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                                axisLabelVisible: true, title: 'Entry',
                            }});
                        }}
                        if (wp.trade.stop) {{
                            candleSeries.createPriceLine({{
                                price: wp.trade.stop, color: 'rgba(244, 67, 54, 0.6)',
                                lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                                axisLabelVisible: true, title: 'Stop',
                            }});
                        }}
                        if (wp.trade.tp1) {{
                            candleSeries.createPriceLine({{
                                price: wp.trade.tp1, color: 'rgba(33, 150, 243, 0.6)',
                                lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                                axisLabelVisible: true, title: 'TP1',
                            }});
                        }}
                    }}
                    
                    // Wyckoff badge
                    const wDiv = document.createElement('div');
                    const badgeTop = 8 + (harmonicPatterns.length * 22) + (wpIdx * 22);
                    wDiv.style.cssText = 'position:absolute;top:' + badgeTop + 'px;right:10px;background:rgba(0,0,0,0.7);color:' + lineColor + ';padding:2px 8px;border-radius:4px;font-size:12px;z-index:5;';
                    wDiv.innerHTML = wp.emoji + ' Wyckoff ' + wp.type + ' (' + wp.phase + ') Score=' + wp.score;
                    container.appendChild(wDiv);
                }});
            }}
            
            // Pattern Box
            const patterns = {json.dumps([{"pattern": p.get("pattern", ""), "type": p.get("type", "neutral"), "confidence": p.get("confidence", "Medium")} for p in (patterns or [])[:3]])};
            if (patterns.length > 0) {{
                let patternHtml = '<strong> Patterns:</strong><br>';
                patterns.forEach(p => {{
                    const cls = p.type === 'bullish' ? 'pattern-bullish' : p.type === 'bearish' ? 'pattern-bearish' : '';
                    patternHtml += `<div class="${{cls}}">${{p.pattern}} (${{p.confidence}})</div>`;
                }});
                document.getElementById('patterns').innerHTML = patternHtml;
            }} else {{
                document.getElementById('patterns').style.display = 'none';
            }}
            
            document.getElementById('legend').innerHTML = legendHtml;
            
            // Auto-fit
            chart.timeScale().fitContent();
            
            // Resize handler
            window.addEventListener('resize', () => {{
                chart.applyOptions({{ width: container.clientWidth }});
            }});
        </script>
    </body>
    </html>
    """
    
    return html


