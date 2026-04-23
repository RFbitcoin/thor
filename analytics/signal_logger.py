"""
THOR Signal Logger
--------------------
Logs every signal snapshot to SQLite and resolves outcomes 24h later.

Schema:
  signals(id, ts, price, composite, signal_direction, pillar_json,
          outcome_price, outcome_ts, outcome_resolved)

Outcome resolution:
  - Run every hour via background thread
  - Finds entries older than 24h with no outcome yet
  - Fetches current price, marks as resolved
  - Stores outcome_correct per pillar in pillar_json
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DB_PATH         = Path(__file__).resolve().parent / "analytics.db"
OUTCOME_HOURS   = 24          # hours before we check if the signal was right
RESOLVE_INTERVAL = 60 * 60   # check for unresolved outcomes every hour

PILLAR_KEYS = [
    "technical", "derivatives", "macro", "sentiment",
    "vix", "volume", "btc_dom", "rsi_div", "ema200", "vwap",
]


# ── Database setup ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts               TEXT    NOT NULL,
                price            REAL    NOT NULL,
                composite        REAL    NOT NULL,
                signal_direction TEXT    NOT NULL,
                pillar_json      TEXT    NOT NULL,
                outcome_price    REAL,
                outcome_ts       TEXT,
                outcome_resolved INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON signals(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resolved ON signals(outcome_resolved)")
        conn.commit()


# ── Logging ────────────────────────────────────────────────────────────────────

def log_signal(signal_data: dict):
    """
    Called every time /api/signal/BTC returns data.
    Extracts pillar scores and logs to DB.
    """
    try:
        price     = signal_data.get("price") or 0
        composite = signal_data.get("composite", 0)
        direction = signal_data.get("signal", "NEUTRAL")
        pillars   = signal_data.get("pillars", {})

        if not price or not pillars:
            return

        # Only log if we don't have an entry in the last 10 minutes
        # (avoids flooding the DB on rapid dashboard refreshes)
        with _get_conn() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            existing = conn.execute(
                "SELECT id FROM signals WHERE ts > ? LIMIT 1", (cutoff,)
            ).fetchone()
            if existing:
                return

            pillar_snapshot = {k: round(float(pillars.get(k, 0)), 4) for k in PILLAR_KEYS}

            conn.execute(
                """INSERT INTO signals (ts, price, composite, signal_direction, pillar_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    float(price),
                    float(composite),
                    str(direction),
                    json.dumps(pillar_snapshot),
                ),
            )
            conn.commit()
    except Exception as e:
        log.warning(f"Signal log error: {e}")


# ── Outcome resolution ─────────────────────────────────────────────────────────

def _fetch_current_price() -> float | None:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=8,
        )
        return float(r.json()["price"])
    except Exception:
        return None


def resolve_outcomes():
    """
    Find all unresolved signal entries older than OUTCOME_HOURS and mark them.
    Per-pillar correctness is stored back into pillar_json.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=OUTCOME_HOURS)).isoformat()
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, price, pillar_json FROM signals "
                "WHERE outcome_resolved = 0 AND ts < ?",
                (cutoff,),
            ).fetchall()

        if not rows:
            return

        outcome_price = _fetch_current_price()
        if not outcome_price:
            log.warning("Outcome resolution skipped — price unavailable")
            return

        now_ts = datetime.now(timezone.utc).isoformat()

        with _get_conn() as conn:
            for row in rows:
                entry_price = row["price"]
                pillars     = json.loads(row["pillar_json"])
                price_change = (outcome_price - entry_price) / entry_price  # + = up, - = down

                # For each pillar, was its direction correct?
                for key in PILLAR_KEYS:
                    score = pillars.get(key, 0)
                    if abs(score) < 0.05:
                        # Pillar was neutral — mark as neutral (not counted in accuracy)
                        pillars[f"{key}_correct"] = None
                    else:
                        predicted_up   = score > 0
                        actually_up    = price_change > 0
                        pillars[f"{key}_correct"] = predicted_up == actually_up

                pillars["outcome_price_change_pct"] = round(price_change * 100, 3)

                conn.execute(
                    """UPDATE signals
                       SET outcome_price = ?, outcome_ts = ?, outcome_resolved = 1,
                           pillar_json = ?
                       WHERE id = ?""",
                    (outcome_price, now_ts, json.dumps(pillars), row["id"]),
                )
            conn.commit()
            log.info(f"Resolved {len(rows)} signal outcomes at price ${outcome_price:,.0f}")

    except Exception as e:
        log.error(f"Outcome resolution error: {e}")


# ── Background resolver thread ─────────────────────────────────────────────────

_resolver_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_resolver():
    global _resolver_thread
    if _resolver_thread and _resolver_thread.is_alive():
        return

    init_db()
    _stop_event.clear()

    def _loop():
        log.info("Signal outcome resolver started")
        while not _stop_event.is_set():
            try:
                resolve_outcomes()
            except Exception as e:
                log.error(f"Resolver loop error: {e}")
            _stop_event.wait(RESOLVE_INTERVAL)

    _resolver_thread = threading.Thread(target=_loop, name="signal-resolver", daemon=True)
    _resolver_thread.start()


def stop_resolver():
    _stop_event.set()
