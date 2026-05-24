# EQT V2

Standalone parquet-based fund, ETF, and index review app.

## Universe

The configured universe is in `config/universe.json`.

V2 intentionally focuses on:

- Index benchmarks
- ETFs from SBI/HDFC where available in the existing list
- Mutual funds from SBI, HDFC, and ICICI only

## Data Store

Parquet files are written under `data/`:

- `master.parquet`: configured fund/index metadata
- `prices.parquet`: historical adjusted close/NAV proxy data from Yahoo Finance
- `fundamentals.parquet`: TER from AMFI plus P/E and AUM metadata from Yahoo Finance
- `metrics.parquet`: calculated review metrics

## Update Data

From the project root:

```powershell
.\.venv\Scripts\python.exe eqt_v2\collector.py
```

Force a clean historical reload:

```powershell
.\.venv\Scripts\python.exe eqt_v2\collector.py --force-full
```

## Run App

```powershell
.\.venv\Scripts\python.exe -m streamlit run eqt_v2\app.py --server.port 8502
```

## Daily Data Refresh

Run the daily data refresh as its own NSSM service. It wakes up hourly by default, checks whether the
configured daily run time has passed, and runs the incremental parquet collector once per date.

```powershell
nssm install EQT-V2-Data "C:\path\to\EQT\.venv\Scripts\python.exe" "C:\path\to\EQT\eqt_v2\daily_data_service.py"
nssm set EQT-V2-Data AppDirectory "C:\path\to\EQT\eqt_v2"
nssm set EQT-V2-Data AppStdout "C:\path\to\EQT\eqt_v2\logs\daily_data_service.out.log"
nssm set EQT-V2-Data AppStderr "C:\path\to\EQT\eqt_v2\logs\daily_data_service.err.log"
nssm start EQT-V2-Data
```

By default the data service checks hourly and runs once daily at 19:00 server time.
Adjust with `EQT_DATA_RUN_TIME` and `EQT_DATA_CHECK_INTERVAL_MINUTES`.
The service writes `runtime/daily_data_state.json` after a successful update and checks
`last_update_date` before running again, so restarts or hourly checks do not duplicate the same day's pull.

## Monthly Email Report

Copy `eqt_v2\.env.example` to `eqt_v2\.env` on the server and set the SMTP values.
The report job reads only environment variables or `.env`; do not put email passwords in code.

Generate a report without sending:

```powershell
.\.venv\Scripts\python.exe eqt_v2\monthly_report.py --no-update
```

Generate, update data, and send email:

```powershell
.\.venv\Scripts\python.exe eqt_v2\monthly_report.py --send
```

For a server that already runs the app with NSSM, run the monthly sender as a second NSSM service:

```powershell
nssm install EQT-V2-Email "C:\path\to\EQT\.venv\Scripts\python.exe" "C:\path\to\EQT\eqt_v2\monthly_email_service.py"
nssm set EQT-V2-Email AppDirectory "C:\path\to\EQT\eqt_v2"
nssm set EQT-V2-Email AppStdout "C:\path\to\EQT\eqt_v2\logs\monthly_email_service.out.log"
nssm set EQT-V2-Email AppStderr "C:\path\to\EQT\eqt_v2\logs\monthly_email_service.err.log"
nssm start EQT-V2-Email
```

By default the email service checks hourly and sends once on the 1st of each month at 08:00 server time.
Adjust with `EQT_EMAIL_DAY_OF_MONTH`, `EQT_EMAIL_SEND_TIME`, and `EQT_EMAIL_CHECK_INTERVAL_MINUTES`.
The service writes `runtime/monthly_email_state.json` after a successful send and checks `last_sent_date`
before sending again, so service restarts or hourly checks do not duplicate the same day's email.

## Calculation Notes

- Returns use actual adjusted close/NAV proxy values, not smoothed rolling averages.
- Long-period returns are shown as CAGR where appropriate.
- Rolling return charts use trailing windows only.
- Benchmarks are mapped by theme where possible.
- `Buy_Low_Score` is a ranking signal based on drawdown, weak recent return, underperformance versus benchmark, and 3-year range position.
- TER is sourced from AMFI's TER disclosure API and matched to configured mutual funds by scheme name.
- P/E and AUM are sourced from Yahoo Finance metadata where available.
- Yahoo Finance data is treated as a practical proxy, not an official total-return or TRI data source.
