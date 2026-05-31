from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config" / "universe.json"
DATA_DIR = APP_DIR / "data"
MASTER_PATH = DATA_DIR / "master.parquet"
PRICES_PATH = DATA_DIR / "prices.parquet"
METRICS_PATH = DATA_DIR / "metrics.parquet"
FUNDAMENTALS_PATH = DATA_DIR / "fundamentals.parquet"
SNAPSHOT_DIR = APP_DIR / "snapshots"

PERIODS = {
    "1M": 30,
    "3M": 91,
    "6M": 182,
    "1Y": 365,
    "3Y": 365 * 3,
    "5Y": 365 * 5,
    "10Y": 365 * 10,
}

WINDOW_GAP_TOLERANCE_DAYS = 10
PRICE_STEP_THRESHOLD = 4.0
PRICE_CONTINUITY_BAND = (0.5, 1.5)


@dataclass(frozen=True)
class DataStatus:
    configured_tickers: int
    price_tickers: int
    latest_date: date | None
    oldest_latest_date: date | None
    price_rows: int


def load_universe(config_path: Path = CONFIG_PATH) -> pd.DataFrame:
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    rows = []
    for category, members in raw.items():
        for name, metadata in members.items():
            rows.append(
                {
                    "Name": name,
                    "Ticker": metadata["ticker"],
                    "Category": category,
                    "Asset_Type": metadata["asset_type"],
                    "Benchmark": metadata["benchmark"],
                    "House": metadata["house"],
                    "Theme": metadata["theme"],
                }
            )

    universe = pd.DataFrame(rows)
    return universe.sort_values(["Asset_Type", "Category", "House", "Name"]).reset_index(drop=True)


def save_master(universe: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(MASTER_PATH, index=False)


def load_master() -> pd.DataFrame:
    if MASTER_PATH.exists():
        return pd.read_parquet(MASTER_PATH)
    return load_universe()


def load_prices() -> pd.DataFrame:
    if not PRICES_PATH.exists():
        return pd.DataFrame(columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"])
    prices = pd.read_parquet(PRICES_PATH)
    prices["Date"] = pd.to_datetime(prices["Date"]).dt.date
    return _repair_price_history(prices)


def _is_continuous(ratio: float) -> bool:
    return PRICE_CONTINUITY_BAND[0] <= ratio <= PRICE_CONTINUITY_BAND[1]


def _repair_ticker_prices(data: pd.DataFrame) -> pd.DataFrame:
    data = data.sort_values("Date").copy()
    if len(data) < 3:
        return data

    price_cols = [col for col in ["Open", "High", "Low", "Close"] if col in data.columns]
    for col in price_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    closes = list(data["Close"])
    positions = list(data.index)
    inverse_threshold = 1 / PRICE_STEP_THRESHOLD

    for pos in range(1, len(closes) - 1):
        previous = closes[pos - 1]
        current = closes[pos]
        following = closes[pos + 1]
        if not previous or not current or not following:
            continue
        if pd.isna(previous) or pd.isna(current) or pd.isna(following):
            continue

        step_ratio = current / previous
        next_ratio = following / current
        surrounding_ratio = following / previous
        is_large_step = step_ratio >= PRICE_STEP_THRESHOLD or step_ratio <= inverse_threshold
        reverses_next_day = next_ratio >= PRICE_STEP_THRESHOLD or next_ratio <= inverse_threshold

        if is_large_step and reverses_next_day and _is_continuous(surrounding_ratio):
            replacement = (previous + following) / 2
            row_factor = replacement / current
            data.loc[positions[pos], price_cols] = data.loc[positions[pos], price_cols] * row_factor
            closes[pos] = replacement
            continue

        if is_large_step and _is_continuous(next_ratio):
            prior_positions = positions[:pos]
            data.loc[prior_positions, price_cols] = data.loc[prior_positions, price_cols] * step_ratio
            closes[:pos] = [close * step_ratio if close and pd.notna(close) else close for close in closes[:pos]]

    return data


def _repair_price_history(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty or "Ticker" not in prices.columns:
        return prices
    frames = [_repair_ticker_prices(group) for _, group in prices.groupby("Ticker", sort=False)]
    return pd.concat(frames, ignore_index=True).sort_values(["Ticker", "Date"]).reset_index(drop=True)


def save_prices(prices: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if prices.empty:
        prices = pd.DataFrame(columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"])
    prices = prices.copy()
    prices["Date"] = pd.to_datetime(prices["Date"]).dt.date
    prices = prices.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last")
    prices.to_parquet(PRICES_PATH, index=False)


def get_data_status(prices: pd.DataFrame, universe: pd.DataFrame) -> DataStatus:
    if prices.empty:
        return DataStatus(len(universe["Ticker"].unique()), 0, None, None, 0)

    latest_by_ticker = prices.groupby("Ticker")["Date"].max()
    return DataStatus(
        configured_tickers=len(universe["Ticker"].unique()),
        price_tickers=latest_by_ticker.shape[0],
        latest_date=latest_by_ticker.max(),
        oldest_latest_date=latest_by_ticker.min(),
        price_rows=len(prices),
    )


def _value_on_or_after(data: pd.DataFrame, target: date) -> tuple[date, float] | None:
    window = data[data["Date"] >= target]
    if window.empty:
        return None
    row = window.iloc[0]
    return row["Date"], float(row["Close"])


def _return_between(
    data: pd.DataFrame,
    start: date,
    end: date,
    min_days: int | None = None,
) -> tuple[float | None, int | None]:
    if data.empty:
        return None, None

    end_window = data[data["Date"] <= end]
    if end_window.empty:
        return None, None

    start_value = _value_on_or_after(data, start)
    if start_value is None:
        return None, None

    start_date, first = start_value
    end_row = end_window.iloc[-1]
    if start_date > end_row["Date"]:
        return None, None

    last = float(end_row["Close"])
    if first == 0:
        return None, None

    days = max((end_row["Date"] - start_date).days, 1)
    if min_days is not None and days < min_days:
        return None, None
    return ((last / first) - 1) * 100, days


def _annualize(total_return_pct: float | None, days: int | None) -> float | None:
    if total_return_pct is None or days is None or days < 365:
        return None
    gross = 1 + total_return_pct / 100
    if gross <= 0:
        return None
    return (gross ** (365 / days) - 1) * 100


def _drawdown_from_high(data: pd.DataFrame, lookback_days: int | None = None) -> float | None:
    if data.empty:
        return None
    recent = data
    if lookback_days is not None:
        cutoff = data["Date"].max() - timedelta(days=lookback_days)
        recent = data[data["Date"] >= cutoff]
    if recent.empty:
        return None
    high = recent["Close"].max()
    latest = recent.iloc[-1]["Close"]
    if high == 0:
        return None
    return ((latest / high) - 1) * 100


def _range_position(data: pd.DataFrame, lookback_days: int = 365 * 3) -> float | None:
    if data.empty:
        return None
    cutoff = data["Date"].max() - timedelta(days=lookback_days)
    recent = data[data["Date"] >= cutoff]
    if recent.empty:
        return None
    low = recent["Close"].min()
    high = recent["Close"].max()
    latest = recent.iloc[-1]["Close"]
    if high == low:
        return None
    return ((latest - low) / (high - low)) * 100


def _trailing_sma_gap(data: pd.DataFrame, window: int = 200) -> float | None:
    if len(data) < max(30, window // 2):
        return None
    sma = data["Close"].tail(window).mean()
    latest = data.iloc[-1]["Close"]
    if sma == 0:
        return None
    return ((latest / sma) - 1) * 100


def _calculate_metric_rows(prices: pd.DataFrame, universe: pd.DataFrame) -> list[dict]:
    rows = []

    for _, asset in universe.iterrows():
        ticker = asset["Ticker"]
        data = prices[prices["Ticker"] == ticker].sort_values("Date").reset_index(drop=True)
        if data.empty:
            continue

        latest = data.iloc[-1]
        row = asset.to_dict()
        row.update(
            {
                "Latest_Date": latest["Date"],
                "Latest_Close": float(latest["Close"]),
                "Drawdown_1Y_Pct": _drawdown_from_high(data, 365),
                "Drawdown_3Y_Pct": _drawdown_from_high(data, 365 * 3),
                "Drawdown_All_Pct": _drawdown_from_high(data, None),
                "Range_Position_3Y_Pct": _range_position(data, 365 * 3),
                "Gap_To_200D_SMA_Pct": _trailing_sma_gap(data, 200),
            }
        )

        for label, days in PERIODS.items():
            period_start = latest["Date"] - timedelta(days=days)
            return_pct, actual_days = _return_between(
                data,
                period_start,
                latest["Date"],
                min_days=days - WINDOW_GAP_TOLERANCE_DAYS,
            )
            row[f"Return_{label}_Pct"] = return_pct
            row[f"CAGR_{label}_Pct"] = _annualize(return_pct, actual_days)

        ytd_return, ytd_days = _return_between(data, date(latest["Date"].year, 1, 1), latest["Date"])
        row["Return_YTD_Pct"] = ytd_return
        row["CAGR_YTD_Pct"] = _annualize(ytd_return, ytd_days)
        rows.append(row)

    return rows


def calculate_metrics(prices: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()

    prices = prices.copy()
    prices["Date"] = pd.to_datetime(prices["Date"]).dt.date
    prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
    prices = prices.dropna(subset=["Close"])

    metrics = pd.DataFrame(_calculate_metric_rows(prices, universe))
    if metrics.empty:
        return metrics

    benchmark_returns = metrics.set_index("Ticker")["Return_1Y_Pct"].to_dict()
    metrics["Relative_1Y_To_Benchmark_Pct"] = metrics.apply(
        lambda row: (
            row["Return_1Y_Pct"] - benchmark_returns.get(row["Benchmark"])
            if pd.notna(row.get("Return_1Y_Pct")) and benchmark_returns.get(row["Benchmark"]) is not None
            else None
        ),
        axis=1,
    )

    pct_cols = [col for col in metrics.columns if col.endswith("_Pct")]
    for col in pct_cols:
        metrics[col] = pd.to_numeric(metrics[col], errors="coerce")

    investable = metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])
    metrics["Buy_Low_Score"] = None
    scoring = metrics.loc[investable].copy()
    if not scoring.empty:
        drawdown_score = (-scoring["Drawdown_1Y_Pct"]).rank(pct=True, na_option="keep") * 100
        weak_return_score = (-scoring["Return_6M_Pct"]).rank(pct=True, na_option="keep") * 100
        underperformance_score = (-scoring["Relative_1Y_To_Benchmark_Pct"]).rank(pct=True, na_option="keep") * 100
        range_score = (100 - scoring["Range_Position_3Y_Pct"]).clip(lower=0, upper=100)

        score_components = pd.DataFrame(
            {
                "drawdown": drawdown_score,
                "weak_return": weak_return_score,
                "underperformance": underperformance_score,
                "range": range_score,
            }
        )
        weights = pd.Series(
            {
                "drawdown": 0.35,
                "weak_return": 0.25,
                "underperformance": 0.25,
                "range": 0.15,
            }
        )
        weighted_sum = score_components.mul(weights).sum(axis=1, skipna=True)
        available_weight = score_components.notna().mul(weights).sum(axis=1)
        score = weighted_sum / available_weight.where(available_weight > 0)

        # Apply Macro-Valuation Multiplier if nifty_pe is available in fundamentals.parquet
        fundamentals = load_fundamentals()
        if not fundamentals.empty and "Nifty_PE" in fundamentals.columns:
            pe_vals = fundamentals["Nifty_PE"].dropna()
            if not pe_vals.empty:
                nifty_pe = float(pe_vals.iloc[0])
                if nifty_pe <= 18:
                    mult = 1.2
                elif nifty_pe >= 26:
                    mult = 0.8
                else:
                    mult = 1.2 - 0.05 * (nifty_pe - 18)
                score = (score * mult).clip(lower=0.0, upper=100.0)

        metrics.loc[scoring.index, "Buy_Low_Score"] = score.round(1)

    metrics["Review_Bucket"] = metrics["Buy_Low_Score"].apply(classify_review_bucket)
    return metrics.sort_values(["Asset_Type", "Buy_Low_Score"], ascending=[True, False]).reset_index(drop=True)


def classify_review_bucket(score: float | None) -> str:
    if score is None or pd.isna(score):
        return "Benchmark / Observe"
    if score >= 75:
        return "Deep Value Watch"
    if score >= 60:
        return "Worth Reviewing"
    if score >= 40:
        return "Neutral"
    if score < 15:
        return "Expensive - Pause SIP"
    return "Expensive / Strong"


def save_metrics(metrics: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    metrics.to_parquet(METRICS_PATH, index=False)


def load_metrics() -> pd.DataFrame:
    if METRICS_PATH.exists():
        return pd.read_parquet(METRICS_PATH)
    prices = load_prices()
    universe = load_master()
    return calculate_metrics(prices, universe)


def load_fundamentals() -> pd.DataFrame:
    if FUNDAMENTALS_PATH.exists():
        return pd.read_parquet(FUNDAMENTALS_PATH)
    return pd.DataFrame()


def normalized_series(prices: pd.DataFrame, tickers: Iterable[str], start: date) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        data = prices[(prices["Ticker"] == ticker) & (prices["Date"] >= start)].sort_values("Date")
        if data.empty:
            continue
        base = data.iloc[0]["Close"]
        if not base:
            continue
        output = data[["Date", "Ticker", "Close"]].copy()
        output["Normalized"] = output["Close"] / base * 100
        rows.append(output)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def monthly_returns(prices: pd.DataFrame, tickers: Iterable[str]) -> pd.DataFrame:
    frames = []
    for ticker in tickers:
        data = prices[prices["Ticker"] == ticker].sort_values("Date").copy()
        if data.empty:
            continue
        data["Date"] = pd.to_datetime(data["Date"])
        monthly = data.set_index("Date")["Close"].resample("ME").last().pct_change() * 100
        frame = monthly.dropna().reset_index()
        frame["Ticker"] = ticker
        frame.rename(columns={"Close": "Monthly_Return_Pct"}, inplace=True)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def trailing_rolling_return(prices: pd.DataFrame, tickers: Iterable[str], window_days: int = 365) -> pd.DataFrame:
    frames = []
    for ticker in tickers:
        data = prices[prices["Ticker"] == ticker].sort_values("Date").copy()
        if data.empty:
            continue
        dates = list(data["Date"])
        closes = list(data["Close"])
        returns = []
        for current_date, current_close in zip(dates, closes):
            target = current_date - timedelta(days=window_days)
            base_index = bisect_left(dates, target)
            if base_index >= len(dates) or dates[base_index] >= current_date:
                returns.append(None)
                continue
            if (current_date - dates[base_index]).days < window_days - WINDOW_GAP_TOLERANCE_DAYS:
                returns.append(None)
                continue
            base_close = closes[base_index]
            returns.append(((current_close / base_close) - 1) * 100 if base_close else None)
        data["Rolling_Return_Pct"] = returns
        frames.append(data[["Date", "Ticker", "Rolling_Return_Pct"]].dropna())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def create_snapshot(metrics: pd.DataFrame) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = SNAPSHOT_DIR / f"eqt_v2_snapshot_{timestamp}.csv"
    snapshot_cols = [
        "Name",
        "Ticker",
        "Asset_Type",
        "House",
        "Theme",
        "Latest_Date",
        "Buy_Low_Score",
        "Review_Bucket",
        "Return_3M_Pct",
        "Return_6M_Pct",
        "Return_1Y_Pct",
        "CAGR_3Y_Pct",
        "CAGR_5Y_Pct",
        "Drawdown_1Y_Pct",
        "Range_Position_3Y_Pct",
        "Relative_1Y_To_Benchmark_Pct",
        "TER_Direct_Pct",
        "TER_Regular_Pct",
        "Portfolio_PE",
        "AUM",
    ]
    cols = [col for col in snapshot_cols if col in metrics.columns]
    metrics[cols].to_csv(path, index=False)
    return path
