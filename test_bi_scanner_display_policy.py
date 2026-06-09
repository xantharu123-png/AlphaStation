import api
from modules.scanners import _bi_interleave_candidates_by_symbol


def test_bi_scanners_show_candidates_not_signal_only_rows():
    assert "bi_long" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_short" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_long" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS
    assert "bi_short" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS


def test_bi_candidate_order_is_not_alphabet_prefix_biased():
    candidates = ["AAA", "AAB", "AAC", "BAA", "BBB", "CAA", "DDD", "EEE"]
    ordered = _bi_interleave_candidates_by_symbol(candidates)

    assert ordered[:5] == ["AAA", "BAA", "CAA", "DDD", "EEE"]
