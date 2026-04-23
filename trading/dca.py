"""
THOR DCA Accumulation Engine
------------------------------
Quietly accumulates spot BTC using a separate budget when conditions are right.

Entry conditions (ALL must be true):
  1. Fear & Greed index ≤ 30 (Fear or Extreme Fear)
  2. Composite signal score ≤ -0.20 (oversold / bearish)
  3. No buy in the last 24 hours (avoid over-accumulating)
  4. Remaining budget > buy amount

Runs in paper mode — simulated spot buys at real market price.
Wire to live exchange (Kraken/Coinbase) when exchange integration is built.

State: trading/dca_state.json
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

log = logging.getLogger(__name__)

STATE_FILE    = BASE_DIR / "trading" / "dca_state.json"
POLL_INTERVAL = 15 * 60   # check every 15 minutes

# Entry thresholds
FNG_THRESHOLD       = 30    # Fear & Greed ≤ this value
SIGNAL_THRESHOLD    = -0.20 # Composite score ≤ this (oversold)
MIN_HOURS_BETWEEN   = 24    # minimum hours between buys

DEFAULT_STATE = {
    "enabled":        False,
    "mode":           "paper",       # paper | live (live requires exchange integration)
    "budget_usdc":    500.0,         # total USDC allocated for DCA
    "buy_amount_usdc": 25.0,         # USDC per buy
    "total_spent":    0.0,           # USDC spent so far
    "total_btc":      0.0,           # BTC accumulated
    "avg_buy_price":  0.0,           # weighted average buy price
    "buys":           [],            # list of individual buy records
    "last_buy_ts":    None,
    "last_check_ts":  None,
    "last_fng":       None,
    "last_signal":    None,
    "conditions_met": False,
    "log":            [],
}


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return dict(DEFAULT_STATE)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _log(state: dict, msg: str):
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    log.info(entry)
    state.setdefault("log", [])
    state["log"].append(entry)
    state["log"] = state["log"][-200:]


def _get_price() -> float | None:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=8,
        )
        return float(r.json()["price"])
    except Exception as e:
        log.warning(f"Price fetch failed: {e}")
        return None


def _get_fng() -> int | None:
    """Fetch Fear & Greed index value (0-100)."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        return int(r.json()["data"][0]["value"])
    except Exception as e:
        log.warning(f"F&G fetch failed: {e}")
        return None


def _get_signal_score() -> float | None:
    """Pull composite signal score from THOR aggregator."""
    try:
        from signals.aggregator import get_signal
        data = get_signal("BTC")
        return float(data.get("composite", 0))
    except Exception as e:
        log.warning(f"Signal fetch failed: {e}")
        return None


def _hours_since_last_buy(state: dict) -> float:
    last = state.get("last_buy_ts")
    if not last:
        return 9999
    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600


def _execute_paper_buy(state: dict, price: float):
    """Simulate a spot BTC buy at current market price."""
    buy_amount = state["buy_amount_usdc"]
    remaining  = state["budget_usdc"] - state["total_spent"]

    if remaining < buy_amount:
        buy_amount = remaining  # use whatever is left

    if buy_amount < 1:
        _log(state, "Budget exhausted — no buy executed")
        return

    btc_bought = buy_amount / price

    # Update weighted average buy price
    prev_total_btc  = state["total_btc"]
    prev_avg        = state["avg_buy_price"]
    new_total_btc   = prev_total_btc + btc_bought
    if new_total_btc > 0:
        new_avg = ((prev_avg * prev_total_btc) + (price * btc_bought)) / new_total_btc
    else:
        new_avg = price

    state["total_spent"]   += buy_amount
    state["total_btc"]     += btc_bought
    state["avg_buy_price"]  = round(new_avg, 2)
    state["last_buy_ts"]    = datetime.now(timezone.utc).isoformat()

    buy_record = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "price":      round(price, 2),
        "usdc_spent": round(buy_amount, 2),
        "btc_bought": round(btc_bought, 8),
        "fng":        state.get("last_fng"),
        "signal":     round(state.get("last_signal") or 0, 4),
        "mode":       "paper",
    }
    state["buys"].append(buy_record)

    _log(state, (
        f"[PAPER BUY] ${buy_amount:.2f} USDC → {btc_bought:.6f} BTC @ ${price:,.2f} "
        f"| Avg cost: ${new_avg:,.2f} | Total BTC: {state['total_btc']:.6f}"
    ))

    # Telegram alert
    try:
        from notifications.telegram import _send
        _send(
            f"🟢 <b>THOR DCA — Paper Buy</b>\n"
            f"─────────────────────\n"
            f"Spent:    <b>${buy_amount:.2f} USDC</b>\n"
            f"Bought:   <b>{btc_bought:.6f} BTC</b>\n"
            f"Price:    <b>${price:,.2f}</b>\n"
            f"Avg cost: <b>${new_avg:,.2f}</b>\n"
            f"Total BTC: <b>{state['total_btc']:.6f}</b>\n"
            f"F&G: {state.get('last_fng')} | Score: {state.get('last_signal', 0):.3f}\n"
            f"─────────────────────\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
    except Exception:
        pass


def _tick(state: dict):
    """One DCA check cycle."""
    state["last_check_ts"] = datetime.now(timezone.utc).isoformat()

    fng    = _get_fng()
    signal = _get_signal_score()
    price  = _get_price()

    state["last_fng"]    = fng
    state["last_signal"] = signal

    if fng is None or signal is None or price is None:
        _log(state, f"Data unavailable — F&G:{fng} signal:{signal} price:{price}")
        state["conditions_met"] = False
        return

    hours_since = _hours_since_last_buy(state)
    remaining   = state["budget_usdc"] - state["total_spent"]

    fng_ok     = fng <= FNG_THRESHOLD
    signal_ok  = signal <= SIGNAL_THRESHOLD
    timing_ok  = hours_since >= MIN_HOURS_BETWEEN
    budget_ok  = remaining >= 1.0

    conditions_met = fng_ok and signal_ok and timing_ok and budget_ok
    state["conditions_met"] = conditions_met

    _log(state, (
        f"Check — F&G:{fng}({'✓' if fng_ok else '✗'}) "
        f"Signal:{signal:.3f}({'✓' if signal_ok else '✗'}) "
        f"Timing:{hours_since:.1f}h({'✓' if timing_ok else '✗'}) "
        f"Budget:${remaining:.0f}({'✓' if budget_ok else '✗'})"
    ))

    if conditions_met:
        _execute_paper_buy(state, price)


def get_summary(state: dict | None = None) -> dict:
    """Return current DCA summary for the dashboard."""
    if state is None:
        state = load_state()

    price     = _get_price() or 0
    total_btc = state.get("total_btc", 0)
    avg_price = state.get("avg_buy_price", 0)
    spent     = state.get("total_spent", 0)
    budget    = state.get("budget_usdc", 500)

    current_value = total_btc * price if price else 0
    unrealised_pnl = current_value - spent if spent > 0 else 0
    unrealised_pct = (unrealised_pnl / spent * 100) if spent > 0 else 0

    return {
        "enabled":          state.get("enabled", False),
        "mode":             state.get("mode", "paper"),
        "budget_usdc":      budget,
        "buy_amount_usdc":  state.get("buy_amount_usdc", 25),
        "total_spent":      round(spent, 2),
        "remaining_budget": round(budget - spent, 2),
        "total_btc":        round(total_btc, 8),
        "avg_buy_price":    round(avg_price, 2),
        "current_price":    round(price, 2),
        "current_value":    round(current_value, 2),
        "unrealised_pnl":   round(unrealised_pnl, 2),
        "unrealised_pct":   round(unrealised_pct, 2),
        "buy_count":        len(state.get("buys", [])),
        "last_buy_ts":      state.get("last_buy_ts"),
        "last_check_ts":    state.get("last_check_ts"),
        "last_fng":         state.get("last_fng"),
        "last_signal":      state.get("last_signal"),
        "conditions_met":   state.get("conditions_met", False),
        "log":              state.get("log", [])[-30:],
        "buys":             state.get("buys", [])[-50:],
    }


# ── Background watcher ─────────────────────────────────────────────────────────

_thread: threading.Thread | None = None
_stop   = threading.Event()


def start_watcher():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def _loop():
        log.info("DCA watcher started")
        while not _stop.is_set():
            try:
                state = load_state()
                if state.get("enabled"):
                    _tick(state)
                    save_state(state)
            except Exception as e:
                log.error(f"DCA loop error: {e}")
            _stop.wait(POLL_INTERVAL)

    _thread = threading.Thread(target=_loop, name="dca-watcher", daemon=True)
    _thread.start()


def stop_watcher():
    _stop.set()


def enable(budget: float | None = None, buy_amount: float | None = None) -> dict:
    state = load_state()
    state["enabled"] = True
    if budget is not None:
        state["budget_usdc"] = float(budget)
    if buy_amount is not None:
        state["buy_amount_usdc"] = float(buy_amount)
    _log(state, f"DCA enabled — budget ${state['budget_usdc']} / buy ${state['buy_amount_usdc']}")
    save_state(state)
    start_watcher()
    return {"ok": True, "msg": "DCA accumulation enabled"}


def disable() -> dict:
    state = load_state()
    state["enabled"] = False
    _log(state, "DCA disabled")
    save_state(state)
    _stop.set()
    return {"ok": True, "msg": "DCA accumulation disabled"}


def reset() -> dict:
    """Clear all DCA history and reset to defaults."""
    state = dict(DEFAULT_STATE)
    save_state(state)
    return {"ok": True, "msg": "DCA state reset"}
