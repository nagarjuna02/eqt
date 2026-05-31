from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import (
    METRICS_PATH,
    PRICES_PATH,
    FUNDAMENTALS_PATH,
    create_snapshot,
    load_fundamentals,
    load_master,
    load_metrics,
    load_prices,
    monthly_returns,
    normalized_series,
    trailing_rolling_return,
)
from collector import update_store


st.set_page_config(page_title="EQT V2", layout="wide")


def _mtime(path) -> float:
    return os.path.getmtime(path) if path.exists() else 0


@st.cache_data
def load_dashboard_data(price_mtime: float, metrics_mtime: float, fundamentals_mtime: float):
    master = load_master()
    prices = load_prices()
    metrics = load_metrics()
    fundamentals = load_fundamentals()
    if not metrics.empty and not fundamentals.empty:
        for col in fundamentals.columns:
            if col != "Ticker" and col in metrics.columns:
                metrics = metrics.drop(columns=[col])
        metrics = metrics.merge(fundamentals, on="Ticker", how="left")
    return master, prices, metrics


def pct_fmt(value):
    if value is None or pd.isna(value):
        return ""
    return f"{value:.1f}%"


def score_fmt(value):
    if value is None or pd.isna(value):
        return ""
    return f"{value:.0f}"


def style_negative(value):
    if isinstance(value, (int, float)) and value < 0:
        return "background-color: #fde8e8; color: #8a1f1f"
    return ""


master, prices, metrics = load_dashboard_data(_mtime(PRICES_PATH), _mtime(METRICS_PATH), _mtime(FUNDAMENTALS_PATH))

with st.sidebar:
    st.title("EQT V2")
    if st.button("Update Data", use_container_width=True):
        with st.spinner("Updating parquet data..."):
            update_store()
        st.cache_data.clear()
        st.rerun()

    asset_types = sorted(metrics["Asset_Type"].dropna().unique()) if not metrics.empty else []
    selected_asset_types = st.multiselect(
        "Asset type",
        asset_types,
        default=[value for value in asset_types if value in ["Mutual Fund", "ETF"]],
    )

    houses = sorted(metrics["House"].dropna().unique()) if not metrics.empty else []
    default_houses = [value for value in houses if value in ["SBI", "HDFC", "ICICI"]]
    selected_houses = st.multiselect("House", houses, default=default_houses)

    themes = sorted(metrics["Theme"].dropna().unique()) if not metrics.empty else []
    selected_themes = st.multiselect("Theme", themes, default=themes)

    include_benchmarks = st.toggle("Show benchmarks", value=False)


if prices.empty or metrics.empty:
    st.title("EQT V2")
    st.warning("No parquet data found.")
    if st.button("Create Data Store"):
        with st.spinner("Fetching data..."):
            update_store()
        st.cache_data.clear()
        st.rerun()
    st.stop()


filtered = metrics.copy()
if selected_asset_types:
    filtered = filtered[filtered["Asset_Type"].isin(selected_asset_types)]
if selected_houses:
    filtered = filtered[filtered["House"].isin(selected_houses)]
if selected_themes:
    filtered = filtered[filtered["Theme"].isin(selected_themes)]
if include_benchmarks:
    benchmark_rows = metrics[metrics["Asset_Type"] == "Index"]
    filtered = pd.concat([filtered, benchmark_rows], ignore_index=True).drop_duplicates("Ticker")

investable = filtered[filtered["Asset_Type"].isin(["Mutual Fund", "ETF"])].copy()
investable = investable.sort_values("Buy_Low_Score", ascending=False)
top_candidates = investable.head(8)

latest_date = metrics["Latest_Date"].max()
oldest_latest = metrics["Latest_Date"].min()

fundamentals = load_fundamentals()
nifty_pe = None
multiplier = 1.0
if not fundamentals.empty and "Nifty_PE" in fundamentals.columns:
    pe_vals = fundamentals["Nifty_PE"].dropna()
    if not pe_vals.empty:
        nifty_pe = float(pe_vals.iloc[0])
        if nifty_pe <= 18:
            multiplier = 1.2
        elif nifty_pe >= 26:
            multiplier = 0.8
        else:
            multiplier = 1.2 - 0.05 * (nifty_pe - 18)

st.title("EQT V2")

summary_cols = st.columns(6)
summary_cols[0].metric("Latest data", str(latest_date))
summary_cols[1].metric("Tracked", f"{metrics['Ticker'].nunique()} tickers")
summary_cols[2].metric("Investable", f"{metrics[metrics['Asset_Type'].isin(['Mutual Fund', 'ETF'])]['Ticker'].nunique()}")
summary_cols[3].metric("Nifty 50 PE", f"{nifty_pe:.1f}" if nifty_pe is not None else "N/A", f"Mult: {multiplier:.2f}x" if nifty_pe is not None else "")
summary_cols[4].metric("Top score", score_fmt(top_candidates["Buy_Low_Score"].max()))
summary_cols[5].metric("Oldest update", str(oldest_latest))

tab_overview, tab_opportunities, tab_compare, tab_returns, tab_costs, tab_snapshot = st.tabs(
    ["Overview", "Buy Low", "Compare", "Returns", "Costs", "Snapshot"]
)

display_names = master.set_index("Ticker")["Name"].to_dict()

with tab_overview:
    left, right = st.columns([1.1, 1])

    with left:
        overview_cols = [
            "Name",
            "Buy_Low_Score",
            "Review_Bucket",
            "TER_Direct_Pct",
            "Portfolio_PE",
            "AUM",
            "House",
            "Theme",
            "Return_6M_Pct",
            "Return_1Y_Pct",
            "Drawdown_1Y_Pct",
            "Relative_1Y_To_Benchmark_Pct",
        ]
        overview_cols = [col for col in overview_cols if col in top_candidates.columns]
        st.subheader("Review Queue")
        st.dataframe(
            top_candidates[overview_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Name": st.column_config.TextColumn("Name", width="large"),
                "Buy_Low_Score": st.column_config.ProgressColumn(
                    "Score",
                    format="%.0f",
                    min_value=0,
                    max_value=100,
                ),
                "Return_6M_Pct": st.column_config.NumberColumn("6M", format="%.1f%%"),
                "Return_1Y_Pct": st.column_config.NumberColumn("1Y", format="%.1f%%"),
                "Drawdown_1Y_Pct": st.column_config.NumberColumn("1Y DD", format="%.1f%%"),
                "Relative_1Y_To_Benchmark_Pct": st.column_config.NumberColumn("Vs bench 1Y", format="%.1f%%"),
                "TER_Direct_Pct": st.column_config.NumberColumn("TER", format="%.2f%%"),
                "Portfolio_PE": st.column_config.NumberColumn("P/E", format="%.1f"),
                "AUM": st.column_config.NumberColumn("AUM", format="compact"),
            },
        )

    with right:
        scatter = investable.dropna(subset=["Return_1Y_Pct", "Drawdown_1Y_Pct", "Buy_Low_Score"])
        fig = px.scatter(
            scatter,
            x="Return_1Y_Pct",
            y="Drawdown_1Y_Pct",
            size="Buy_Low_Score",
            color="House",
            hover_name="Name",
            hover_data=["Theme", "Return_6M_Pct", "Relative_1Y_To_Benchmark_Pct"],
            labels={
                "Return_1Y_Pct": "1Y return %",
                "Drawdown_1Y_Pct": "Drawdown from 1Y high %",
            },
            template="plotly_white",
            height=430,
        )
        fig.add_vline(x=0, line_dash="dot", line_color="#999")
        fig.add_hline(y=0, line_dash="dot", line_color="#999")
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20))
        st.plotly_chart(fig, use_container_width=True)

with tab_opportunities:
    st.subheader("Buy Low Ranking")
    rank_cols = [
        "Name",
        "Buy_Low_Score",
        "Review_Bucket",
        "Return_3M_Pct",
        "Return_6M_Pct",
        "Return_YTD_Pct",
        "Return_1Y_Pct",
        "CAGR_3Y_Pct",
        "CAGR_5Y_Pct",
        "Drawdown_1Y_Pct",
        "Range_Position_3Y_Pct",
        "Gap_To_200D_SMA_Pct",
        "Relative_1Y_To_Benchmark_Pct",
        "TER_Direct_Pct",
        "TER_Regular_Pct",
        "Portfolio_PE",
        "AUM",
        "Ticker",
        "Asset_Type",
        "House",
        "Theme",
        "Benchmark",
    ]
    table = investable[[col for col in rank_cols if col in investable.columns]].copy()
    st.dataframe(
        table.style.map(style_negative, subset=[col for col in table.columns if col.endswith("_Pct")]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Name": st.column_config.TextColumn("Name", width="large"),
            "Buy_Low_Score": st.column_config.ProgressColumn(
                "Score",
                format="%.0f",
                min_value=0,
                max_value=100,
            ),
            "Return_3M_Pct": st.column_config.NumberColumn("3M", format="%.1f%%"),
            "Return_6M_Pct": st.column_config.NumberColumn("6M", format="%.1f%%"),
            "Return_YTD_Pct": st.column_config.NumberColumn("YTD", format="%.1f%%"),
            "Return_1Y_Pct": st.column_config.NumberColumn("1Y", format="%.1f%%"),
            "CAGR_3Y_Pct": st.column_config.NumberColumn("3Y CAGR", format="%.1f%%"),
            "CAGR_5Y_Pct": st.column_config.NumberColumn("5Y CAGR", format="%.1f%%"),
            "Drawdown_1Y_Pct": st.column_config.NumberColumn("1Y DD", format="%.1f%%"),
            "Range_Position_3Y_Pct": st.column_config.NumberColumn("3Y range", format="%.1f%%"),
            "Gap_To_200D_SMA_Pct": st.column_config.NumberColumn("200D gap", format="%.1f%%"),
            "Relative_1Y_To_Benchmark_Pct": st.column_config.NumberColumn("Vs bench 1Y", format="%.1f%%"),
            "TER_Direct_Pct": st.column_config.NumberColumn("TER Direct", format="%.2f%%"),
            "TER_Regular_Pct": st.column_config.NumberColumn("TER Regular", format="%.2f%%"),
            "Portfolio_PE": st.column_config.NumberColumn("P/E", format="%.1f"),
            "AUM": st.column_config.NumberColumn("AUM", format="compact"),
        },
    )

with tab_compare:
    default_tickers = list(top_candidates["Ticker"].head(5))
    benchmark_defaults = list(
        metrics[(metrics["Asset_Type"] == "Index") & (metrics["Ticker"].isin(top_candidates["Benchmark"]))]
        ["Ticker"]
        .drop_duplicates()
        .head(3)
    )
    ticker_options = metrics.sort_values(["Asset_Type", "Name"])["Ticker"].tolist()
    selected_tickers = st.multiselect(
        "Tickers",
        ticker_options,
        default=list(dict.fromkeys(default_tickers + benchmark_defaults)),
        format_func=lambda ticker: display_names.get(ticker, ticker),
    )
    period = st.segmented_control("Window", ["1Y", "3Y", "5Y", "10Y"], default="3Y")
    start_days = {"1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5, "10Y": 365 * 10}[period]
    trend = normalized_series(prices, selected_tickers, date.today() - timedelta(days=start_days))
    if not trend.empty:
        trend["Name"] = trend["Ticker"].map(display_names)
        fig = px.line(
            trend,
            x="Date",
            y="Normalized",
            color="Name",
            labels={"Normalized": "Growth of 100"},
            template="plotly_white",
            height=520,
        )
        fig.add_hline(y=100, line_dash="dot", line_color="#999")
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

    rolling = trailing_rolling_return(prices, selected_tickers, 365)
    if not rolling.empty:
        rolling["Name"] = rolling["Ticker"].map(display_names)
        fig = px.line(
            rolling,
            x="Date",
            y="Rolling_Return_Pct",
            color="Name",
            labels={"Rolling_Return_Pct": "Trailing 1Y return %"},
            template="plotly_white",
            height=420,
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#999")
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

with tab_returns:
    returns_cols = [
        "Name",
        "Asset_Type",
        "House",
        "Theme",
        "Latest_Date",
        "Return_1M_Pct",
        "Return_3M_Pct",
        "Return_6M_Pct",
        "Return_YTD_Pct",
        "Return_1Y_Pct",
        "CAGR_3Y_Pct",
        "CAGR_5Y_Pct",
        "CAGR_10Y_Pct",
        "Drawdown_1Y_Pct",
        "Drawdown_3Y_Pct",
        "TER_Direct_Pct",
        "Portfolio_PE",
        "AUM",
    ]
    returns_table = filtered[[col for col in returns_cols if col in filtered.columns]].copy()
    st.dataframe(
        returns_table.style.map(style_negative, subset=[col for col in returns_table.columns if col.endswith("_Pct")]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Name": st.column_config.TextColumn("Name", width="large"),
            "Return_1M_Pct": st.column_config.NumberColumn("1M", format="%.1f%%"),
            "Return_3M_Pct": st.column_config.NumberColumn("3M", format="%.1f%%"),
            "Return_6M_Pct": st.column_config.NumberColumn("6M", format="%.1f%%"),
            "Return_YTD_Pct": st.column_config.NumberColumn("YTD", format="%.1f%%"),
            "Return_1Y_Pct": st.column_config.NumberColumn("1Y", format="%.1f%%"),
            "CAGR_3Y_Pct": st.column_config.NumberColumn("3Y CAGR", format="%.1f%%"),
            "CAGR_5Y_Pct": st.column_config.NumberColumn("5Y CAGR", format="%.1f%%"),
            "CAGR_10Y_Pct": st.column_config.NumberColumn("10Y CAGR", format="%.1f%%"),
            "Drawdown_1Y_Pct": st.column_config.NumberColumn("1Y DD", format="%.1f%%"),
            "Drawdown_3Y_Pct": st.column_config.NumberColumn("3Y DD", format="%.1f%%"),
            "TER_Direct_Pct": st.column_config.NumberColumn("TER", format="%.2f%%"),
            "Portfolio_PE": st.column_config.NumberColumn("P/E", format="%.1f"),
            "AUM": st.column_config.NumberColumn("AUM", format="compact"),
        },
    )

    month_tickers = st.multiselect(
        "Monthly return tickers",
        ticker_options,
        default=list(dict.fromkeys(default_tickers[:4] + benchmark_defaults[:1])),
        format_func=lambda ticker: display_names.get(ticker, ticker),
    )
    monthly = monthly_returns(prices, month_tickers)
    if not monthly.empty:
        monthly["Name"] = monthly["Ticker"].map(display_names)
        latest_month = pd.to_datetime(monthly["Date"]).max()
        monthly_window = monthly[pd.to_datetime(monthly["Date"]) >= latest_month - pd.DateOffset(months=24)]
        monthly_window = monthly_window.sort_values(["Date", "Name"])
        fig = px.bar(
            monthly_window,
            x="Date",
            y="Monthly_Return_Pct",
            color="Name",
            barmode="group",
            labels={"Monthly_Return_Pct": "Monthly return %"},
            template="plotly_white",
            height=430,
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#999")
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

with tab_costs:
    st.subheader("Cost & Valuation")
    cost_cols = [
        "Name",
        "Ticker",
        "Asset_Type",
        "House",
        "Theme",
        "TER_Direct_Pct",
        "TER_Regular_Pct",
        "Portfolio_PE",
        "AUM",
        "TER_Date",
        "TER_Match_Score",
        "TER_Scheme_Name",
    ]
    cost_table = filtered[[col for col in cost_cols if col in filtered.columns]].copy()
    st.dataframe(
        cost_table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Name": st.column_config.TextColumn("Name", width="large"),
            "TER_Direct_Pct": st.column_config.NumberColumn("TER Direct", format="%.2f%%"),
            "TER_Regular_Pct": st.column_config.NumberColumn("TER Regular", format="%.2f%%"),
            "Portfolio_PE": st.column_config.NumberColumn("P/E", format="%.1f"),
            "AUM": st.column_config.NumberColumn("AUM", format="compact"),
            "TER_Match_Score": st.column_config.NumberColumn("TER match", format="%.2f"),
            "TER_Scheme_Name": st.column_config.TextColumn("AMFI matched scheme", width="large"),
        },
    )

with tab_snapshot:
    st.subheader("Snapshot")
    snapshot_cols = [
        "Name",
        "House",
        "Theme",
        "Buy_Low_Score",
        "Review_Bucket",
        "Return_6M_Pct",
        "Return_1Y_Pct",
        "Drawdown_1Y_Pct",
        "Relative_1Y_To_Benchmark_Pct",
        "TER_Direct_Pct",
        "Portfolio_PE",
        "AUM",
    ]
    snapshot_cols = [col for col in snapshot_cols if col in investable.columns]
    snapshot_table = investable.head(12)[snapshot_cols]
    st.dataframe(snapshot_table, hide_index=True, use_container_width=True)
    if st.button("Export Snapshot CSV"):
        snapshot_path = create_snapshot(investable)
        st.success(f"Saved {snapshot_path}")
