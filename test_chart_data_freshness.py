from modules import data_fetchers as df


def test_polygon_intraday_chart_fetches_newest_bars_first_then_returns_chronological(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {"t": 2000, "o": 9.5, "h": 10.5, "l": 9.0, "c": 10.0, "v": 200},
                    {"t": 1000, "o": 4.5, "h": 5.5, "l": 4.0, "c": 5.0, "v": 100},
                ]
            }

    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["sort"] = params.get("sort")
        return FakeResponse()

    monkeypatch.setattr(df, "rate_limited_get", fake_get)

    bars = df._fetch_ohlcv_polygon("AQST", "test-key", "1H")

    assert seen["sort"] == "desc"
    assert [bar["close"] for bar in bars] == [5.0, 10.0]
