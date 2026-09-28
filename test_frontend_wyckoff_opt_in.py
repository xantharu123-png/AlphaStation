"""Wyckoff is an independent overlay, not a side effect of Patterns."""
from test_frontend_scanner_lifecycle import SOURCE, node_run


def run(code):
    pure = SOURCE[SOURCE.index("function isWyckoffPattern("):SOURCE.index("function usePublicPlans(")]
    node_run("const assert=require('node:assert/strict');\n" + pure + "\n" + code)


def test_generic_patterns_cannot_enable_wyckoff_or_show_failed_history_on_chart():
    run("""
assert.equal(typeof visibleChartPatterns,'function','Separate overlay selection is missing');
const patterns=[{pattern:'Bullish OB',type:'bullish'},
 {model:'causal_wyckoff_v3',structure_state:'confirmed',direction:'LONG',type:'bullish'},
 {model:'causal_wyckoff_v3',structure_state:'failed',direction:'LONG',type:'neutral'}];
assert.equal(visibleChartPatterns(patterns,{patterns:true,wyckoff:false},'LONG').length,1);
const wy=visibleChartPatterns(patterns,{patterns:false,wyckoff:true},'LONG');
assert.equal(wy.length,1);assert.equal(wy[0].structure_state,'confirmed');
assert.equal(visibleChartPatterns(patterns,{patterns:false,wyckoff:false},'LONG').length,0);
""")


def test_entering_and_leaving_wyckoff_changes_only_its_own_overlay():
    run("""
const previous={patterns:true,wyckoff:false,wyckoffSwings:false};
const wy={scanner:'Wyckoff Accumulation'},normal={scanner:'Momentum Breakout Long'};
const entered=patternOverlaysAfterSelection(previous,normal,wy);
assert.equal(entered.wyckoff,true);assert.equal(entered.patterns,true);
const left=patternOverlaysAfterSelection(entered,wy,normal);
assert.equal(left.wyckoff,false);assert.equal(left.patterns,true);
""")
