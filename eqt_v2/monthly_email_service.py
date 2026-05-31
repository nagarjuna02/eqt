from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from analytics import APP_DIR
from monthly_report import generate_report, send_email


LOG_DIR = APP_DIR / "logs"
RUNTIME_DIR = APP_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "monthly_email_state.json"
LOG_PATH = LOG_DIR / "monthly_email_service.log"


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
    return logging.getLogger("eqt_v2.monthly_email_service")


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


def _should_send(now: datetime, state: dict) -> bool:
    day = int(os.getenv("EQT_EMAIL_DAY_OF_MONTH", "1"))
    send_time = os.getenv("EQT_EMAIL_SEND_TIME", "08:00")
    hour, minute = [int(part) for part in send_time.split(":", maxsplit=1)]
    month_key = now.strftime("%Y-%m")
    date_key = now.date().isoformat()
    already_sent = state.get("last_sent_date") == date_key or state.get("last_sent_month") == month_key
    scheduled_time_reached = (now.hour, now.minute) >= (hour, minute)
    return now.day == day and scheduled_time_reached and not already_sent


def run_once_if_due() -> bool:
    load_dotenv(APP_DIR / ".env")
    now = datetime.now()
    state = _load_state()
    if not _should_send(now, state):
        return False

    logger.info("Monthly report is due. Generating and sending email.")
    report_path, csv_path, chart_paths, metrics = generate_report(update_data=True)
    send_email(report_path, csv_path, chart_paths, metrics)
    state["last_sent_date"] = now.date().isoformat()
    state["last_sent_month"] = now.strftime("%Y-%m")
    state["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_report"] = str(report_path)
    _save_state(state)
    logger.info("Monthly report email sent. report=%s", report_path)
    return True


def main() -> None:
    load_dotenv(APP_DIR / ".env")
    interval_minutes = int(os.getenv("EQT_EMAIL_CHECK_INTERVAL_MINUTES", "60"))
    logger.info("Starting Silver-Bullet monthly email service. check_interval_minutes=%s", interval_minutes)
    while True:
        try:
            sent = run_once_if_due()
            if not sent:
                logger.info("Monthly email is not due yet.")
        except Exception:
            logger.exception("Monthly email service cycle failed.")
        time.sleep(max(interval_minutes, 1) * 60)


if __name__ == "__main__":
    main()
