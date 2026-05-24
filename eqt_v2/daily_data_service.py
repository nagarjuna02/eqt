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


def run_once_if_due() -> bool:
    load_dotenv(APP_DIR / ".env")
    now = datetime.now()
    state = _load_state()
    if not _should_update(now, state):
        return False

    logger.info("Daily parquet data update is due. Running incremental collector.")
    update_store()
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
