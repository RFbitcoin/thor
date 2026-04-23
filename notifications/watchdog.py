"""
THOR Watchdog Daemon
----------------------
Runs as a separate systemd service. Every 60 seconds it:
  1. Pings the THOR Flask server (/api/health)
  2. If THOR is unreachable for >5 minutes with open positions → emergency close
  3. Sends Telegram alerts for health issues and failsafe triggers

Run standalone: /usr/bin/python3 /home/rfranklin/thor/notifications/watchdog.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Path setup
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from notifications.telegram import (
    alert_watchdog_warning,
    alert_watchdog_failsafe,
    alert_error,
    send_test,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "logs" / "watchdog.log"),
    ],
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
THOR_URL         = "http://127.0.0.1:5000"
HEALTH_ENDPOINT  = f"{THOR_URL}/api/health"
GMX_POS_ENDPOINT = f"{THOR_URL}/api/gmx/positions"
GMX_CLOSE_EP     = f"{THOR_URL}/api/gmx/close"
PAPER_STATE_FILE = BASE_DIR / "trading" / "paper_state.json"
GMX_STATE_FILE   = BASE_DIR / "trading" / "gmx_state.json"

POLL_INTERVAL    = 60          # seconds between health checks
OUTAGE_THRESHOLD = 5 * 60      # 5 minutes of consecutive failures → failsafe
WARN_AFTER       = 2 * 60      # 2 minutes unreachable → send warning


def _ping() -> bool:
    """Returns True if THOR Flask is responding."""
    try:
        r = requests.get(HEALTH_ENDPOINT, timeout=5)
        return r.ok
    except Exception:
        return False


def _get_open_paper_positions() -> list:
    """Read open paper positions from state file."""
    try:
        with open(PAPER_STATE_FILE) as f:
            state = json.load(f)
        positions = state.get("positions", [])
        return [p for p in positions if p.get("status") == "open"]
    except Exception:
        return []


def _get_open_gmx_positions() -> list:
    """Read open GMX positions from state file."""
    try:
        with open(GMX_STATE_FILE) as f:
            state = json.load(f)
        pos = state.get("position")
        return [pos] if pos else []
    except Exception:
        return []


def _emergency_close_gmx(positions: list) -> list:
    """
    Attempt to close all open GMX positions via the API.
    Returns list of position descriptions for the alert.
    """
    closed = []
    for pos in positions:
        symbol    = pos.get("symbol", "BTC")
        direction = pos.get("direction", "long")
        try:
            r = requests.post(
                GMX_CLOSE_EP,
                json={"symbol": symbol, "direction": direction},
                timeout=15,
            )
            if r.ok:
                closed.append(f"{symbol} {direction.upper()}")
                log.info(f"Emergency closed {symbol} {direction}")
            else:
                log.error(f"Close failed for {symbol}: {r.text}")
                closed.append(f"{symbol} {direction.upper()} (CLOSE FAILED)")
        except Exception as e:
            log.error(f"Close request error for {symbol}: {e}")
            closed.append(f"{symbol} {direction.upper()} (ERROR: {e})")
    return closed


def run():
    log.info("THOR Watchdog started")
    send_test()  # confirm Telegram is working on startup

    first_fail_time: float | None = None
    warning_sent = False
    failsafe_triggered = False

    while True:
        alive = _ping()

        if alive:
            if first_fail_time is not None:
                outage_secs = time.time() - first_fail_time
                log.info(f"THOR back online after {outage_secs:.0f}s outage")
                if warning_sent:
                    alert_watchdog_warning("✅ THOR is back online.")
            # Reset state
            first_fail_time   = None
            warning_sent      = False
            failsafe_triggered = False

        else:
            now = time.time()
            if first_fail_time is None:
                first_fail_time = now
                log.warning("THOR unreachable — starting outage timer")

            outage_secs = now - first_fail_time
            log.warning(f"THOR unreachable for {outage_secs:.0f}s")

            # 2-minute warning
            if outage_secs >= WARN_AFTER and not warning_sent:
                paper_pos = _get_open_paper_positions()
                gmx_pos   = _get_open_gmx_positions()
                pos_count = len(paper_pos) + len(gmx_pos)
                msg = (
                    f"THOR has been unreachable for {outage_secs/60:.0f} minutes.\n"
                    f"Open positions: {pos_count}\n"
                    f"Failsafe will trigger at 5 minutes."
                )
                alert_watchdog_warning(msg)
                warning_sent = True
                log.warning("Warning alert sent")

            # 5-minute failsafe
            if outage_secs >= OUTAGE_THRESHOLD and not failsafe_triggered:
                log.error("OUTAGE THRESHOLD REACHED — triggering failsafe")
                gmx_pos = _get_open_gmx_positions()
                closed  = []
                if gmx_pos:
                    closed = _emergency_close_gmx(gmx_pos)
                else:
                    log.info("No GMX positions to close")

                paper_pos = _get_open_paper_positions()
                if paper_pos:
                    closed += [f"Paper: {p.get('symbol','BTC')} (monitor manually)" for p in paper_pos]

                alert_watchdog_failsafe(closed)
                failsafe_triggered = True
                log.error(f"Failsafe complete. Positions actioned: {closed}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
