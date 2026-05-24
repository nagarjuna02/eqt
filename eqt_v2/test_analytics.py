from datetime import date

import pandas as pd

from analytics import _repair_price_history, _return_between, normalized_series, trailing_rolling_return


def test_return_between_rejects_negative_period_from_data_gap():
    data = pd.DataFrame(
        {
            "Date": [date(2020, 12, 31), date(2022, 1, 3)],
            "Close": [100.0, 125.0],
        }
    )

    result, days = _return_between(data, date(2021, 1, 1), date(2021, 12, 31))

    assert result is None
    assert days is None


def test_return_between_rejects_short_labeled_period():
    data = pd.DataFrame(
        {
            "Date": [date(2021, 7, 1), date(2022, 1, 1)],
            "Close": [100.0, 125.0],
        }
    )

    result, days = _return_between(
        data,
        date(2021, 1, 1),
        date(2022, 1, 1),
        min_days=355,
    )

    assert result is None
    assert days is None


def test_trailing_rolling_return_rejects_short_window_from_data_gap():
    prices = pd.DataFrame(
        {
            "Date": [date(2021, 1, 1), date(2022, 7, 1), date(2022, 8, 1)],
            "Ticker": ["GAP", "GAP", "GAP"],
            "Close": [100.0, 120.0, 130.0],
        }
    )

    rolling = trailing_rolling_return(prices, ["GAP"], window_days=365)

    assert rolling.empty


def test_trailing_rolling_return_accepts_full_enough_window():
    prices = pd.DataFrame(
        {
            "Date": [date(2021, 1, 1), date(2021, 12, 27), date(2022, 1, 1)],
            "Ticker": ["OK", "OK", "OK"],
            "Close": [100.0, 120.0, 130.0],
        }
    )

    rolling = trailing_rolling_return(prices, ["OK"], window_days=365)

    assert not rolling.empty
    assert round(float(rolling.iloc[-1]["Rolling_Return_Pct"]), 2) == 30.0


def test_price_repair_replaces_isolated_bad_tick():
    prices = pd.DataFrame(
        {
            "Date": [date(2023, 7, 10), date(2023, 7, 11), date(2023, 7, 12)],
            "Ticker": ["BAD", "BAD", "BAD"],
            "Open": [290.0, 29155.0, 292.0],
            "High": [290.0, 29155.0, 292.0],
            "Low": [290.0, 29155.0, 292.0],
            "Close": [290.0, 29155.0, 292.0],
            "Volume": [0, 0, 0],
        }
    )

    repaired = _repair_price_history(prices)

    assert repaired.loc[repaired["Date"].eq(date(2023, 7, 11)), "Close"].iloc[0] == 291.0


def test_price_repair_adjusts_persistent_split_step():
    prices = pd.DataFrame(
        {
            "Date": [date(2023, 10, 18), date(2023, 10, 19), date(2023, 10, 20), date(2023, 10, 23)],
            "Ticker": ["SPLIT", "SPLIT", "SPLIT", "SPLIT"],
            "Open": [200.0, 198.0, 19.7, 19.8],
            "High": [200.0, 198.0, 19.7, 19.8],
            "Low": [200.0, 198.0, 19.7, 19.8],
            "Close": [200.0, 198.0, 19.8, 19.6],
            "Volume": [100, 100, 100, 100],
        }
    )

    repaired = _repair_price_history(prices)
    trend = normalized_series(repaired, ["SPLIT"], date(2023, 10, 18))

    assert trend["Normalized"].max() < 105
    assert trend["Normalized"].min() > 95
