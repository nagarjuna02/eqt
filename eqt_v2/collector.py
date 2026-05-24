from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from analytics import (
    DATA_DIR,
    FUNDAMENTALS_PATH,
    calculate_metrics,
    get_data_status,
    load_prices,
    load_universe,
    save_master,
    save_metrics,
    save_prices,
)
from fundamentals import update_fundamentals


LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_PATH = LOG_DIR / "collector.log"


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("eqt_v2.collector")


logger = setup_logging()


def _clean_yfinance_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"])

    data = raw.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(-1)

    data = data.reset_index()
    if "Date" not in data.columns:
        first_col = data.columns[0]
        data.rename(columns={first_col: "Date"}, inplace=True)

    wanted = ["Date", "Open", "High", "Low", "Close", "Volume"]
    for col in wanted:
        if col not in data.columns:
            data[col] = None

    data = data[wanted].copy()
    data["Date"] = pd.to_datetime(data["Date"]).dt.date
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data["Ticker"] = ticker
    return data[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def fetch_prices(ticker: str, start: date, end: date) -> pd.DataFrame:
    raw = yf.download(
        tickers=ticker,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        progress=False,
        auto_adjust=True,
        threads=False,
    )
    return _clean_yfinance_frame(raw, ticker)


def update_store(history_years: int = 16, overlap_days: int = 10, force_full: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    universe = load_universe()
    save_master(universe)

    existing = pd.DataFrame() if force_full else load_prices()
    all_parts = [] if force_full or existing.empty else [existing.copy()]
    today = date.today()
    default_start = today - timedelta(days=365 * history_years)

    logger.info("Starting EQT V2 parquet update for %s configured tickers.", universe["Ticker"].nunique())

    for _, row in universe.iterrows():
        ticker = row["Ticker"]
        name = row["Name"]
        ticker_existing = existing[existing["Ticker"] == ticker] if not existing.empty else pd.DataFrame()

        if ticker_existing.empty or force_full:
            fetch_start = default_start
            logger.info("%s (%s): full fetch from %s", name, ticker, fetch_start)
        else:
            latest = ticker_existing["Date"].max()
            fetch_start = latest - timedelta(days=overlap_days)
            all_parts[0] = all_parts[0][
                ~((all_parts[0]["Ticker"] == ticker) & (all_parts[0]["Date"] >= fetch_start))
            ]
            logger.info("%s (%s): incremental fetch from %s", name, ticker, fetch_start)

        try:
            fetched = fetch_prices(ticker, fetch_start, today)
        except Exception as exc:
            logger.exception("%s (%s): fetch failed: %s", name, ticker, exc)
            continue

        if fetched.empty:
            logger.warning("%s (%s): no rows returned", name, ticker)
            continue
        all_parts.append(fetched)
        logger.info("%s (%s): fetched %s rows through %s", name, ticker, len(fetched), fetched["Date"].max())

    prices = pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()
    save_prices(prices)

    saved_prices = load_prices()
    fundamentals = update_fundamentals()
    metrics = calculate_metrics(saved_prices, universe)
    if FUNDAMENTALS_PATH.exists() and not fundamentals.empty:
        metrics = metrics.merge(fundamentals, on="Ticker", how="left")
    save_metrics(metrics)

    status = get_data_status(saved_prices, universe)
    logger.info(
        "Update complete. rows=%s, tickers=%s/%s, latest=%s, oldest ticker latest=%s",
        status.price_rows,
        status.price_tickers,
        status.configured_tickers,
        status.latest_date,
        status.oldest_latest_date,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Update EQT V2 parquet datastore.")
    parser.add_argument("--history-years", type=int, default=16)
    parser.add_argument("--overlap-days", type=int, default=10)
    parser.add_argument("--force-full", action="store_true")
    args = parser.parse_args()

    update_store(
        history_years=args.history_years,
        overlap_days=args.overlap_days,
        force_full=args.force_full,
    )


if __name__ == "__main__":
    main()
