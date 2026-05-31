from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv

from analytics import APP_DIR
from collector import update_store


LOG_DIR = APP_DIR / "logs"
RUNTIME_DIR = APP_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "daily_data_state.json"
LOG_PATH = LOG_DIR / "daily_data_service.log"


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
    return logging.getLogger("eqt_v2.daily_data_service")


logger = setup_logging()


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _scheduled_time_reached(now: datetime) -> bool:
    run_time = os.getenv("EQT_DATA_RUN_TIME", "19:00")
    hour, minute = [int(part) for part in run_time.split(":", maxsplit=1)]
    return (now.hour, now.minute) >= (hour, minute)


def _should_update(now: datetime, state: dict) -> bool:
    date_key = now.date().isoformat()
    already_updated = state.get("last_update_date") == date_key
    return _scheduled_time_reached(now) and not already_updated


def send_alert_email(subject: str, body: str) -> None:
    import smtplib
    from email.message import EmailMessage

    host = os.getenv("EQT_EMAIL_SMTP_HOST", "").strip()
    port = int(os.getenv("EQT_EMAIL_SMTP_PORT", "587"))
    username = os.getenv("EQT_EMAIL_USERNAME", "").strip()
    password = os.getenv("EQT_EMAIL_PASSWORD", "")
    sender = os.getenv("EQT_EMAIL_FROM", username).strip()
    recipients = [part.strip() for part in os.getenv("EQT_EMAIL_TO", "").replace(";", ",").split(",") if part.strip()]
    use_tls = os.getenv("EQT_EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes", "y"}

    if not host or not sender or not recipients:
        logger.warning("Email configuration missing. Cannot send alert.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=60) as smtp:
        if port != 465 and use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)


def run_sentinel_check(state: dict) -> None:
    import pandas as pd
    from analytics import load_metrics

    metrics = load_metrics()
    if metrics.empty:
        return

    investable = metrics[metrics["Asset_Type"].isin(["Mutual Fund", "ETF"])]
    if investable.empty:
        return

    sent_alerts = state.setdefault("sent_alerts", {})
    alert_messages = []

    for _, row in investable.iterrows():
        ticker = row["Ticker"]
        name = row["Name"]
        score = row.get("Buy_Low_Score")
        drawdown = row.get("Drawdown_1Y_Pct")

        if score is not None and pd.notna(score):
            if score >= 80.0:
                if ticker not in sent_alerts:
                    drawdown_str = f"{drawdown:.1f}%" if drawdown is not None and pd.notna(drawdown) else "N/A"
                    alert_messages.append(f"- {name} ({ticker}) has entered Deep Value Watch. Score: {score:.1f}. Drawdown: {drawdown_str}.")
                    sent_alerts[ticker] = datetime.now().date().isoformat()
            else:
                if ticker in sent_alerts:
                    sent_alerts.pop(ticker)

    if alert_messages:
        subject = f"[PRIORITY ALERT] EQT V2 - Deep Value Entry Detected"
        body = "The daily data refresh has detected assets entering the Deep Value Watch zone (Buy Low Score >= 80):\n\n" + "\n".join(alert_messages) + "\n\nThis is a priority notification based on your custom investment criteria."
        try:
            send_alert_email(subject, body)
            logger.info("Sent priority alert email for %d assets.", len(alert_messages))
        except Exception as exc:
            logger.exception("Failed to send priority alert email: %s", exc)


def run_once_if_due() -> bool:
    load_dotenv(APP_DIR / ".env")
    now = datetime.now()
    state = _load_state()
    if not _should_update(now, state):
        return False

    logger.info("Daily parquet data update is due. Running incremental collector.")
    update_store()

    try:
        run_sentinel_check(state)
    except Exception as exc:
        logger.exception("Sentinel check failed: %s", exc)

    state["last_update_date"] = now.date().isoformat()
    state["last_update_at"] = datetime.now().isoformat(timespec="seconds")
    state["mode"] = "incremental"
    _save_state(state)
    logger.info("Daily parquet data update complete.")
    return True


def main() -> None:
    load_dotenv(APP_DIR / ".env")
    interval_minutes = int(os.getenv("EQT_DATA_CHECK_INTERVAL_MINUTES", "60"))
    logger.info("Starting EQT V2 daily data service. check_interval_minutes=%s", interval_minutes)
    while True:
        try:
            updated = run_once_if_due()
            if not updated:
                logger.info("Daily parquet data update is not due yet.")
        except Exception:
            logger.exception("Daily data service cycle failed.")
        time.sleep(max(interval_minutes, 1) * 60)


if __name__ == "__main__":
    main()
