import api


def test_bi_scanners_show_candidates_not_signal_only_rows():
    assert "bi_long" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_short" not in api._SIGNAL_ONLY_SCANNERS
    assert "bi_long" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS
    assert "bi_short" in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS
