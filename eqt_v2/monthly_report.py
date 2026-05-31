from __future__ import annotations

import argparse
import os
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from analytics import (
    APP_DIR,
    FUNDAMENTALS_PATH,
    METRICS_PATH,
    PRICES_PATH,
    SNAPSHOT_DIR,
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


REPORT_DIR = SNAPSHOT_DIR / "monthly"
CHART_DIR = REPORT_DIR / "charts"

REPORT_COLUMNS = [
    "Name",
    "Buy_Low_Score",
    "Review_Bucket",
    "Return_3M_Pct",
    "Return_6M_Pct",
    "Return_1Y_Pct",
    "CAGR_3Y_Pct",
    "Drawdown_1Y_Pct",
    "Range_Position_3Y_Pct",
    "Relative_1Y_To_Benchmark_Pct",
    "TER_Direct_Pct",
    "Portfolio_PE",
    "AUM",
    "Ticker",
    "Asset_Type",
    "House",
    "Theme",
    "Benchmark",
]


def _fmt_pct(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.1f}%"


def _fmt_score(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.0f}"


def _fmt_number(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.1f}"


def _fmt_aum(value: object) -> str:
    if pd.isna(value):
        return ""
    amount = float(value)
    if abs(amount) >= 10_000_000:
        return f"{amount / 10_000_000:.1f}Cr"
    if abs(amount) >= 100_000:
        return f"{amount / 100_000:.1f}L"
    return f"{amount:,.0f}"


def _load_report_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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


def _format_table(frame: pd.DataFrame) -> pd.DataFrame:
    table = frame.copy()
    pct_cols = [col for col in table.columns if col.endswith("_Pct")]
    for col in pct_cols:
        table[col] = table[col].map(_fmt_pct)
    if "Buy_Low_Score" in table.columns:
        table["Buy_Low_Score"] = table["Buy_Low_Score"].map(_fmt_score)
    if "Portfolio_PE" in table.columns:
        table["Portfolio_PE"] = table["Portfolio_PE"].map(_fmt_number)
    if "AUM" in table.columns:
        table["AUM"] = table["AUM"].map(_fmt_aum)
    return table


def _table_html(frame: pd.DataFrame, max_rows: int = 20) -> str:
    visible = frame.head(max_rows)
    if visible.empty:
        return "<p>No rows available.</p>"
    return _format_table(visible).to_html(index=False, border=0, classes="data-table")


def _write_image(fig, path: Path) -> Path | None:
    try:
        fig.write_image(str(path), width=1400, height=680, scale=2)
    except Exception:
        return None
    return path


def _build_charts(
    prices: pd.DataFrame,
    metrics: pd.DataFrame,
    display_names: dict[str, str],
    report_stamp: str,
) -> list[Path]:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    investable = metrics[metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])].copy()
    investable = investable.sort_values("Buy_Low_Score", ascending=False)
    selected = list(investable["Ticker"].head(5))
    benchmarks = list(
        metrics[(metrics["Asset_Type"] == "Index") & (metrics["Ticker"].isin(investable["Benchmark"]))]
        ["Ticker"]
        .drop_duplicates()
        .head(3)
    )
    selected = list(dict.fromkeys(selected + benchmarks))

    image_paths: list[Path] = []
    scatter = investable.dropna(subset=["Return_1Y_Pct", "Drawdown_1Y_Pct", "Buy_Low_Score"])
    if not scatter.empty:
        fig = px.scatter(
            scatter,
            x="Return_1Y_Pct",
            y="Drawdown_1Y_Pct",
            size="Buy_Low_Score",
            color="House",
            hover_name="Name",
            labels={
                "Return_1Y_Pct": "1Y return %",
                "Drawdown_1Y_Pct": "Drawdown from 1Y high %",
            },
            template="plotly_white",
            title="Review Queue: Return vs Drawdown",
        )
        fig.add_vline(x=0, line_dash="dot", line_color="#999")
        fig.add_hline(y=0, line_dash="dot", line_color="#999")
        path = _write_image(fig, CHART_DIR / f"{report_stamp}_review_scatter.png")
        if path:
            image_paths.append(path)

    if selected:
        trend = normalized_series(prices, selected, date.today() - timedelta(days=365 * 3))
        if not trend.empty:
            trend["Name"] = trend["Ticker"].map(display_names)
            fig = px.line(
                trend,
                x="Date",
                y="Normalized",
                color="Name",
                labels={"Normalized": "Growth of 100"},
                template="plotly_white",
                title="3Y Growth of 100",
            )
            fig.add_hline(y=100, line_dash="dot", line_color="#999")
            path = _write_image(fig, CHART_DIR / f"{report_stamp}_growth_100.png")
            if path:
                image_paths.append(path)

        rolling = trailing_rolling_return(prices, selected, 365)
        if not rolling.empty:
            rolling = rolling[pd.to_datetime(rolling["Date"]) >= pd.Timestamp.today() - pd.DateOffset(years=5)]
            rolling["Name"] = rolling["Ticker"].map(display_names)
            fig = px.line(
                rolling,
                x="Date",
                y="Rolling_Return_Pct",
                color="Name",
                labels={"Rolling_Return_Pct": "Trailing 1Y return %"},
                template="plotly_white",
                title="Trailing 1Y Returns",
            )
            fig.add_hline(y=0, line_dash="dot", line_color="#999")
            path = _write_image(fig, CHART_DIR / f"{report_stamp}_rolling_1y.png")
            if path:
                image_paths.append(path)

        monthly = monthly_returns(prices, selected)
        if not monthly.empty:
            monthly["Name"] = monthly["Ticker"].map(display_names)
            latest_month = pd.to_datetime(monthly["Date"]).max()
            monthly = monthly[pd.to_datetime(monthly["Date"]) >= latest_month - pd.DateOffset(months=24)]
            fig = px.bar(
                monthly.sort_values(["Date", "Name"]),
                x="Date",
                y="Monthly_Return_Pct",
                color="Name",
                barmode="group",
                labels={"Monthly_Return_Pct": "Monthly return %"},
                template="plotly_white",
                title="Monthly Returns",
            )
            fig.add_hline(y=0, line_dash="dot", line_color="#999")
            path = _write_image(fig, CHART_DIR / f"{report_stamp}_monthly_returns.png")
            if path:
                image_paths.append(path)

    return image_paths


def _generate_rebalance_html(metrics: pd.DataFrame) -> str:
    investable = metrics[metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])].copy()
    candidates = investable[investable["Buy_Low_Score"] >= 60.0].copy()

    lump_sum = float(os.getenv("EQT_MONTHLY_LUMP_SUM", "20000"))
    currency = os.getenv("EQT_CURRENCY", "INR")

    if candidates.empty:
        return f"""
        <div style="background-color: #f3f4f6; border-left: 4px solid #9ca3af; padding: 12px; margin-bottom: 20px;">
          <h3 style="margin-top: 0; color: #374151;">Market Allocation Strategy ({lump_sum:,.0f} {currency})</h3>
          <p style="margin-bottom: 0;">Market is currently expensive. No funds meet the value criteria (Buy Low Score &ge; 60). Recommended action: <b>Hold cash</b> or accumulate in liquid funds.</p>
        </div>
        """

    candidates["Excess_Score"] = candidates["Buy_Low_Score"] - 50.0
    candidates["Excess_Score"] = candidates["Excess_Score"].clip(lower=1.0)

    total_excess = candidates["Excess_Score"].sum()
    candidates["Weight"] = candidates["Excess_Score"] / total_excess
    candidates["Allocation"] = candidates["Weight"] * lump_sum

    rows_html = []
    for _, row in candidates.iterrows():
        allocation_val = row["Allocation"]
        name = row["Name"]
        ticker = row["Ticker"]
        score = row["Buy_Low_Score"]
        rows_html.append(f"<li><b>{name}</b> ({ticker}) - Allocate <b>{allocation_val:,.0f} {currency}</b> (Score: {score:.1f})</li>")

    return f"""
    <div style="background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 12px; margin-bottom: 20px;">
      <h3 style="margin-top: 0; color: #065f46;">Recommended Lump Sum Allocation ({lump_sum:,.0f} {currency})</h3>
      <p>Based on excess Buy Low Score above a baseline of 50:</p>
      <ul style="margin-bottom: 0; padding-left: 20px;">
        {"".join(rows_html)}
      </ul>
    </div>
    """


def _build_html_report(
    metrics: pd.DataFrame,
    chart_paths: list[Path],
    csv_path: Path,
    report_stamp: str,
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{report_stamp}_eqt_v2_monthly_report.html"
    investable = metrics[metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])].copy()
    investable = investable.sort_values("Buy_Low_Score", ascending=False)
    top = investable[[col for col in REPORT_COLUMNS if col in investable.columns]]
    deep_value = top[top["Review_Bucket"].isin(["Deep Value Watch", "Worth Reviewing"])]
    costs = investable[
        [
            col
            for col in [
                "Name",
                "TER_Direct_Pct",
                "TER_Regular_Pct",
                "Portfolio_PE",
                "AUM",
                "House",
                "Theme",
            ]
            if col in investable.columns
        ]
    ].sort_values(["TER_Direct_Pct", "Portfolio_PE"], na_position="last")

    latest_date = metrics["Latest_Date"].max()
    app_url = os.getenv("EQT_APP_URL", "").strip()
    app_link = f'<p><a href="{app_url}">Open EQT V2 dashboard</a></p>' if app_url else ""
    chart_html = "\n".join(
        f'<h2>{path.stem.replace("_", " ").title()}</h2><img src="{path.name}" alt="{path.stem}">'
        for path in chart_paths
    )

    rebalance_html = _generate_rebalance_html(metrics)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Silver-Bullet Monthly Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; color: #1f2937; margin: 28px; }}
    h1 {{ margin-bottom: 4px; }}
    h2 {{ margin-top: 28px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
    .muted {{ color: #6b7280; }}
    .data-table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    .data-table th, .data-table td {{ border-bottom: 1px solid #e5e7eb; padding: 7px 8px; text-align: left; }}
    .data-table th {{ background: #f9fafb; }}
    img {{ max-width: 100%; border: 1px solid #e5e7eb; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>Silver-Bullet Monthly Report</h1>
  <p class="muted">Generated {datetime.now():%Y-%m-%d %H:%M}. Latest market data: {latest_date}.</p>
  {app_link}
  <p>CSV snapshot: {csv_path.name}</p>

  {rebalance_html}

  <h2>Action Queue</h2>
  {_table_html(deep_value, max_rows=15)}

  <h2>Buy Low Ranking</h2>
  {_table_html(top, max_rows=25)}

  {chart_html}

  <h2>Cost View</h2>
  {_table_html(costs, max_rows=25)}
</body>
</html>
"""
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _cid_for(path: Path) -> str:
    return path.stem.replace("_", "-")


def _email_body(metrics: pd.DataFrame, chart_paths: list[Path]) -> str:
    investable = metrics[metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])].copy()
    investable = investable.sort_values("Buy_Low_Score", ascending=False)
    top = investable[[col for col in REPORT_COLUMNS if col in investable.columns]].head(12)
    latest_date = metrics["Latest_Date"].max()
    app_url = os.getenv("EQT_APP_URL", "").strip()
    app_link = f'<p><a href="{app_url}">Open the live EQT V2 dashboard</a></p>' if app_url else ""
    chart_html = "\n".join(f'<p><img src="cid:{_cid_for(path)}" style="max-width:100%;"></p>' for path in chart_paths)
    
    rebalance_html = _generate_rebalance_html(metrics)
    
    return f"""<html>
<body style="font-family:Arial,sans-serif;color:#1f2937;">
  <h2>Silver-Bullet Monthly Report</h2>
  <p>Latest market data: <b>{latest_date}</b></p>
  {app_link}
  
  {rebalance_html}
  
  <h3>Top Buy Low candidates</h3>
  {_table_html(top, max_rows=12)}
  {chart_html}
  <p>The full HTML report and CSV snapshot are attached.</p>
</body>
</html>"""


def _split_recipients(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def send_email(report_path: Path, csv_path: Path, chart_paths: list[Path], metrics: pd.DataFrame) -> None:
    load_dotenv(APP_DIR / ".env")
    host = os.getenv("EQT_EMAIL_SMTP_HOST", "").strip()
    port = int(os.getenv("EQT_EMAIL_SMTP_PORT", "587"))
    username = os.getenv("EQT_EMAIL_USERNAME", "").strip()
    password = os.getenv("EQT_EMAIL_PASSWORD", "")
    sender = os.getenv("EQT_EMAIL_FROM", username).strip()
    recipients = _split_recipients(os.getenv("EQT_EMAIL_TO", ""))
    cc_val = os.getenv("EQT_EMAIL_CC", "").strip()
    cc_recipients = _split_recipients(cc_val) if cc_val else []
    use_tls = os.getenv("EQT_EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes", "y"}

    missing = [
        name
        for name, value in {
            "EQT_EMAIL_SMTP_HOST": host,
            "EQT_EMAIL_FROM": sender,
            "EQT_EMAIL_TO": recipients,
        }.items()
        if not value
    ]
    if username and not password:
        missing.append("EQT_EMAIL_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing email configuration: {', '.join(missing)}")

    subject_prefix = os.getenv("EQT_EMAIL_SUBJECT_PREFIX", "Silver-Bullet").strip()
    subject = f"{subject_prefix} Monthly Report - {date.today():%B %Y}"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if cc_recipients:
        message["Cc"] = ", ".join(cc_recipients)
    message.set_content("Silver-Bullet monthly report is attached.")
    message.add_alternative(_email_body(metrics, chart_paths), subtype="html")
    html_part = message.get_payload()[1]

    for path in chart_paths:
        html_part.add_related(
            path.read_bytes(),
            maintype="image",
            subtype="png",
            cid=f"<{_cid_for(path)}>",
            filename=path.name,
        )

    for attachment in [report_path, csv_path]:
        message.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=attachment.name,
        )

    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=60) as smtp:
        if port != 465 and use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def generate_report(update_data: bool = True) -> tuple[Path, Path, list[Path], pd.DataFrame]:
    if update_data:
        update_store()

    master, prices, metrics = _load_report_data()
    display_names = master.set_index("Ticker")["Name"].to_dict()
    report_stamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = create_snapshot(metrics)
    chart_paths = _build_charts(prices, metrics, display_names, report_stamp)
    report_path = _build_html_report(metrics, chart_paths, csv_path, report_stamp)

    for path in chart_paths:
        target = report_path.parent / path.name
        if path.resolve() != target.resolve():
            target.write_bytes(path.read_bytes())

    return report_path, csv_path, chart_paths, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and optionally email the EQT V2 monthly report.")
    parser.add_argument("--send", action="store_true", help="Send the generated report by email.")
    parser.add_argument("--no-update", action="store_true", help="Use existing parquet data without fetching updates.")
    args = parser.parse_args()

    report_path, csv_path, chart_paths, metrics = generate_report(update_data=not args.no_update)
    print(f"Report: {report_path}")
    print(f"CSV: {csv_path}")
    print(f"Charts: {len(chart_paths)}")
    if args.send:
        send_email(report_path, csv_path, chart_paths, metrics)
        print("Email sent.")


if __name__ == "__main__":
    main()
