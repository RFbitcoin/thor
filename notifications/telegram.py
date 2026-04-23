"""
THOR Telegram Alert Client
----------------------------
Sends push notifications to your Telegram for:
  - STRONG BUY / SELL signals
  - Auto-trader entries and exits
  - Watchdog health alerts
  - Daily morning prediction summary
  - Error conditions
"""

import logging
import os
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import dotenv_values

log = logging.getLogger(__name__)

# Load credentials from .env
_ENV_PATH = Path.home() / ".thor" / "config" / ".env"
_env = dotenv_values(_ENV_PATH)

BOT_TOKEN = _env.get("TELEGRAM_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID   = _env.get("TELEGRAM_CHAT_ID",   os.environ.get("TELEGRAM_CHAT_ID",   ""))

_BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Emoji shortcuts ────────────────────────────────────────────────────────────
_BULL  = "🟢"
_BEAR  = "🔴"
_WARN  = "⚠️"
_INFO  = "ℹ️"
_HEART = "💚"
_DEAD  = "💀"
_CHART = "📊"
_CLOCK = "🕐"
_BOLT  = "⚡"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message. Returns True on success."""
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return False
    try:
        r = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=8,
        )
        if not r.ok:
            log.warning(f"Telegram send failed: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        log.warning(f"Telegram send error: {e}")
        return False


def _now() -> str:
    return datetime.now().strftime("%H:%M")


# ── Public alert functions ─────────────────────────────────────────────────────

def alert_strong_signal(direction: str, conviction: int, composite: float,
                        price: float, regime: str):
    """Fire when composite signal crosses strong buy/sell threshold."""
    if direction.upper() == "BUY":
        icon = _BULL
        label = "STRONG BUY"
    else:
        icon = _BEAR
        label = "STRONG SELL"

    text = (
        f"{icon} <b>THOR — {label}</b>\n"
        f"─────────────────────\n"
        f"Price:      <b>${price:,.0f}</b>\n"
        f"Conviction: <b>{conviction}%</b>\n"
        f"Score:      <b>{composite:+.3f}</b>\n"
        f"Regime:     <b>{regime}</b>\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_trade_opened(mode: str, symbol: str, direction: str, price: float,
                       size_usd: float, leverage: float, conviction: int):
    """Fire when auto-trader opens a position."""
    icon = _BULL if direction.upper() == "LONG" else _BEAR
    text = (
        f"{icon} <b>THOR — Position Opened ({mode})</b>\n"
        f"─────────────────────\n"
        f"Symbol:     <b>{symbol}</b>\n"
        f"Direction:  <b>{direction.upper()}</b>\n"
        f"Entry:      <b>${price:,.2f}</b>\n"
        f"Size:       <b>${size_usd:,.0f}</b>\n"
        f"Leverage:   <b>{leverage:.1f}×</b>\n"
        f"Conviction: <b>{conviction}%</b>\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_trade_closed(mode: str, symbol: str, direction: str, entry: float,
                       exit_price: float, pnl_pct: float, reason: str):
    """Fire when auto-trader closes a position."""
    icon = _BULL if pnl_pct >= 0 else _BEAR
    pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"
    text = (
        f"{icon} <b>THOR — Position Closed ({mode})</b>\n"
        f"─────────────────────\n"
        f"Symbol:  <b>{symbol}</b>\n"
        f"Entry:   <b>${entry:,.2f}</b>\n"
        f"Exit:    <b>${exit_price:,.2f}</b>\n"
        f"P&L:     <b>{pnl_str}</b>\n"
        f"Reason:  <b>{reason}</b>\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_prediction_summary(direction: str, direction_pct: float,
                              price: float, target_low: float, target_high: float,
                              news_score: float, top_headlines: list):
    """Morning prediction summary."""
    icon = _BULL if direction == "BULL" else (_BEAR if direction == "BEAR" else _INFO)
    headlines_txt = ""
    for h in top_headlines[:3]:
        dot = "🟢" if h["label"] == "bullish" else ("🔴" if h["label"] == "bearish" else "⚪")
        headlines_txt += f"\n{dot} {h['title'][:60]}"

    text = (
        f"{_CHART} <b>THOR — Daily Prediction</b>\n"
        f"─────────────────────\n"
        f"{icon} <b>{direction}</b>  ({direction_pct:.0f}% confidence)\n"
        f"Price now:  <b>${price:,.0f}</b>\n"
        f"24h range:  <b>${target_low:,.0f} – ${target_high:,.0f}</b>\n"
        f"News score: <b>{news_score:+.2f}</b>\n"
        f"─────────────────────\n"
        f"<b>Top headlines:</b>{headlines_txt}\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_watchdog_warning(message: str):
    """Fire when watchdog detects a health issue."""
    text = (
        f"{_WARN} <b>THOR — Health Warning</b>\n"
        f"─────────────────────\n"
        f"{message}\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_watchdog_failsafe(positions_closed: list):
    """Fire when watchdog closes positions due to outage."""
    pos_txt = "\n".join(f"• {p}" for p in positions_closed) if positions_closed else "None"
    text = (
        f"{_DEAD} <b>THOR — FAILSAFE TRIGGERED</b>\n"
        f"─────────────────────\n"
        f"THOR was unreachable. Emergency close executed.\n"
        f"\nPositions closed:\n{pos_txt}\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def alert_error(context: str, error: str):
    """Fire on significant errors."""
    text = (
        f"{_WARN} <b>THOR — Error</b>\n"
        f"─────────────────────\n"
        f"Context: {context}\n"
        f"Error:   <code>{error[:200]}</code>\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


def send_test():
    """Send a test message to verify configuration."""
    text = (
        f"{_BOLT} <b>THOR is online</b>\n"
        f"─────────────────────\n"
        f"Telegram alerts are configured and working.\n"
        f"You will receive notifications for:\n"
        f"• Strong buy/sell signals\n"
        f"• Auto-trader entries & exits\n"
        f"• Daily prediction summary\n"
        f"• Watchdog health alerts\n"
        f"─────────────────────\n"
        f"{_CLOCK} {_now()}"
    )
    return _send(text)


if __name__ == "__main__":
    print("Sending test message...")
    ok = send_test()
    print("Sent!" if ok else "Failed — check token and chat ID in .env")
