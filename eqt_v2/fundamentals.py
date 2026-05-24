from __future__ import annotations

import logging
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from analytics import DATA_DIR, FUNDAMENTALS_PATH, load_universe


AMFI_BASE_URL = "https://www.amfiindia.com"
AMFI_MF_IDS = {
    "HDFC": "9",
    "ICICI": "20",
    "SBI": "22",
}

logger = logging.getLogger("eqt_v2.fundamentals")


def _clean_name(value: str) -> str:
    cleaned = value.upper()
    replacements = {
        "&": " AND ",
        "-": " ",
        "DIRECT PLAN": "DIRECT",
        "DIRECT GROWTH": "DIRECT",
        "DIR GR": "DIRECT",
        "REGULAR PLAN": "REGULAR",
        "GROWTH OPTION": "GROWTH",
        " FUND ": " ",
        " OPPORTUNITIES ": " ",
        " PRUDENTIAL ": " ",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    words = [word for word in cleaned.split() if word not in {"THE", "PLAN", "OPTION"}]
    return " ".join(words)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _clean_name(left), _clean_name(right)).ratio()


def _number(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_yahoo_fundamentals(universe: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, asset in universe.iterrows():
        ticker = asset["Ticker"]
        info: dict[str, Any] = {}
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            logger.warning("%s: Yahoo fundamentals failed: %s", ticker, exc)

        rows.append(
            {
                "Ticker": ticker,
                "Yahoo_Long_Name": info.get("longName") or info.get("shortName"),
                "Portfolio_PE": _number(
                    info.get("trailingPE")
                    or info.get("forwardPE")
                    or info.get("priceToEarnings")
                    or info.get("portfolioPE")
                ),
                "AUM": _number(
                    info.get("totalAssets")
                    or info.get("netAssets")
                    or info.get("marketCap")
                ),
                "AUM_Source": "Yahoo Finance",
            }
        )
    return pd.DataFrame(rows)


def _latest_amfi_month() -> tuple[str, str]:
    current_year = datetime.now().year
    financial_years = [
        f"{current_year}-{current_year + 1}",
        f"{current_year - 1}-{current_year}",
    ]
    for year in financial_years:
        url = f"{AMFI_BASE_URL}/api/populate-ter-month?year={year}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        months = response.json()
        if months:
            return year, months[0]["MonthNumber"]
    raise RuntimeError("AMFI TER month list is empty")


def fetch_amfi_ter_for_house(mf_id: str, month: str) -> pd.DataFrame:
    rows = []
    page = 1
    page_size = 500
    while True:
        url = (
            f"{AMFI_BASE_URL}/api/populate-te-rdata-revised"
            f"?MF_ID={mf_id}&Month={month}&strCat=-1&strType=-1&page={page}&pageSize={page_size}"
        )
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        meta = payload.get("meta", {})
        rows.extend(data)
        if page >= int(meta.get("pageCount", page)):
            break
        page += 1

    if not rows:
        return pd.DataFrame()

    ter = pd.DataFrame(rows)
    ter["TER_Date"] = pd.to_datetime(ter["TER_Date"], errors="coerce")
    for col in ["D_TER", "R_TER"]:
        ter[col] = pd.to_numeric(ter[col], errors="coerce")
    return ter.sort_values("TER_Date").drop_duplicates("Scheme_Name", keep="last")


def fetch_amfi_ter(universe: pd.DataFrame) -> pd.DataFrame:
    _, month = _latest_amfi_month()
    frames = []
    for house, mf_id in AMFI_MF_IDS.items():
        if house not in set(universe["House"]):
            continue
        try:
            frame = fetch_amfi_ter_for_house(mf_id, month)
        except Exception as exc:
            logger.warning("%s: AMFI TER fetch failed: %s", house, exc)
            continue
        if frame.empty:
            continue
        frame["House"] = house
        frame["TER_Month"] = month
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def match_ter_to_universe(universe: pd.DataFrame, amfi_ter: pd.DataFrame) -> pd.DataFrame:
    rows = []
    mutual_funds = universe[universe["Asset_Type"] == "Mutual Fund"]
    for _, asset in mutual_funds.iterrows():
        candidates = amfi_ter[amfi_ter["House"] == asset["House"]]
        best = None
        best_score = 0.0
        for _, candidate in candidates.iterrows():
            score = _similarity(asset["Name"], candidate["Scheme_Name"])
            if score > best_score:
                best = candidate
                best_score = score
        if best is None or best_score < 0.58:
            rows.append(
                {
                    "Ticker": asset["Ticker"],
                    "TER_Direct_Pct": None,
                    "TER_Regular_Pct": None,
                    "TER_Date": None,
                    "TER_Scheme_Name": None,
                    "TER_Match_Score": None,
                    "TER_Source": "AMFI",
                }
            )
            continue

        rows.append(
            {
                "Ticker": asset["Ticker"],
                "TER_Direct_Pct": _number(best.get("D_TER")),
                "TER_Regular_Pct": _number(best.get("R_TER")),
                "TER_Date": best.get("TER_Date"),
                "TER_Scheme_Name": best.get("Scheme_Name"),
                "TER_Match_Score": round(best_score, 3),
                "TER_Source": "AMFI",
            }
        )
    return pd.DataFrame(rows)


def update_fundamentals() -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    universe = load_universe()
    yahoo = fetch_yahoo_fundamentals(universe)
    amfi = fetch_amfi_ter(universe)
    ter = match_ter_to_universe(universe, amfi) if not amfi.empty else pd.DataFrame()

    fundamentals = yahoo.merge(ter, on="Ticker", how="left")
    fundamentals["Fundamentals_Updated_At"] = datetime.now().isoformat(timespec="seconds")
    fundamentals.to_parquet(FUNDAMENTALS_PATH, index=False)
    return fundamentals


def main() -> None:
    fundamentals = update_fundamentals()
    print(f"Saved {len(fundamentals)} rows to {FUNDAMENTALS_PATH}")


if __name__ == "__main__":
    main()
