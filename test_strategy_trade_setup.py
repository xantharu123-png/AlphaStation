from api import _build_structured_trade_setup


def test_momentum_sidebar_targets_do_not_use_tiny_one_r_targets():
    setup = _build_structured_trade_setup(
        direction="LONG",
        entry=9.27,
        atr=0.28,
        support_1=8.97,
        resistance_1=9.57,
        high_20d=9.60,
        low_20d=8.35,
        range_pos=73,
    )

    assert setup is not None
    assert setup["tp1"] >= 9.85
    assert setup["tp2"] >= 10.05
    assert setup["rr_tp1"] >= 1.5
    assert setup["rr_tp2"] >= 2.5
    assert setup["rr"] >= 2.0
    assert any("Resistance" in warning for warning in setup["warnings"])
    assert any("20D-Range" in note for note in setup["notes"])


def test_short_sidebar_targets_respect_minimum_r_and_support_barriers():
    setup = _build_structured_trade_setup(
        direction="SHORT",
        entry=50.0,
        atr=1.2,
        support_1=49.3,
        resistance_1=51.1,
        high_20d=55.0,
        low_20d=48.0,
        range_pos=25,
    )

    assert setup is not None
    assert setup["tp1"] <= 48.0
    assert setup["tp2"] <= 46.9
    assert setup["rr_tp1"] >= 1.5
    assert setup["rr_tp2"] >= 2.5
    assert any("Support" in warning for warning in setup["warnings"])
