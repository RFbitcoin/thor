"""
THOR Bitcoin Price Prediction Engine
--------------------------------------
Combines THOR's 10 signal pillars + RSS news sentiment to produce:
  - Bull/Neutral/Bear probability
  - 24-hour price target range
  - Per-pillar contribution breakdown

Updates every 30 minutes in a background thread.
State is cached in predictions/state.json for fast API reads.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

# ---------------------------------------------------------------------------
# Path setup — allow running standalone or imported from dashboard/
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from predictions.rss_client import fetch_headlines

log = logging.getLogger(__name__)

STATE_DIR     = BASE_DIR / "predictions"
STATE_FILE    = STATE_DIR / "state.json"   # kept for BTC backwards compat
POLL_INTERVAL = 30 * 60   # 30 minutes

def _state_file(symbol: str) -> Path:
    sym = symbol.upper()
    if sym == "BTC":
        return STATE_FILE
    return STATE_DIR / f"state_{sym}.json"

# ---------------------------------------------------------------------------
# Pillar weights (mirror live aggregator — must sum to 1.0)
# ---------------------------------------------------------------------------
PILLAR_WEIGHTS = {
    "technical":    0.12,
    "derivatives":  0.12,
    "macro":        0.10,
    "sentiment":    0.10,
    "vix":          0.08,
    "volume":       0.08,
    "btc_dom":      0.08,
    "rsi_div":      0.10,
    "ema200":       0.12,
    "vwap":         0.10,
}

PILLAR_LABELS = {
    "technical":   "Technical Analysis",
    "derivatives": "Derivatives / Funding",
    "macro":       "Macro / On-chain",
    "sentiment":   "Fear & Greed",
    "vix":         "VIX Volatility",
    "volume":      "Volume Profile",
    "btc_dom":     "BTC Dominance",
    "rsi_div":     "RSI Divergence",
    "ema200":      "200 EMA Trend",
    "vwap":        "VWAP Position",
}

# Sentiment pillar weight for news overlay
NEWS_SENTIMENT_WEIGHT = 0.15   # blended into final score on top of pillars


def _get_current_price(symbol: str = "BTC") -> float | None:
    """Fetch latest price from Binance (free, no key)."""
    try:
        pair = symbol.upper() + "USDT"
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": pair},
            timeout=8,
        )
        return float(r.json()["price"])
    except Exception as e:
        log.warning(f"Price fetch failed: {e}")
        return None


def _get_atr(period: int = 14, symbol: str = "BTC") -> float | None:
    """
    Fetch last `period+1` daily candles from Binance and compute ATR.
    Returns ATR in USD.
    """
    try:
        pair = symbol.upper() + "USDT"
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": "1d", "limit": period + 1},
            timeout=10,
        )
        candles = r.json()
        highs  = [float(c[2]) for c in candles]
        lows   = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]

        trs = []
        for i in range(1, len(candles)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i]  - closes[i - 1]),
            )
            trs.append(tr)
        return float(np.mean(trs[-period:]))
    except Exception as e:
        log.warning(f"ATR fetch failed: {e}")
        return None


def _pillar_scores_from_aggregator(symbol: str = "BTC") -> dict | None:
    """
    Pull current pillar scores from THOR's live aggregator.
    Returns dict of {pillar_name: score} where scores are in [-1, 1].
    """
    try:
        from signals.aggregator import get_signal
        data = get_signal(symbol)
        # aggregator returns 'pillars' key with normalised -1..+1 scores
        pillars_raw = data.get("pillars", {})
        pillars = {}
        for key in PILLAR_WEIGHTS:
            val = pillars_raw.get(key)
            if val is not None:
                pillars[key] = float(np.clip(val, -1.0, 1.0))
        return pillars if pillars else None
    except Exception as e:
        log.warning(f"Aggregator read failed: {e}")
        return None


def _mock_pillars() -> dict:
    """Fallback: generate neutral pillar scores (all zeros) when aggregator unavailable."""
    return {k: 0.0 for k in PILLAR_WEIGHTS}


def _compute_prediction(pillar_scores: dict, news_score: float) -> dict:
    """
    Core prediction logic.

    Args:
        pillar_scores: {pillar_name: float} in [-1, 1]
        news_score:    aggregate news sentiment in [-1, 1]

    Returns:
        Full prediction dict.
    """
    # Weighted pillar composite
    composite = sum(
        pillar_scores.get(k, 0.0) * w
        for k, w in PILLAR_WEIGHTS.items()
    )

    # Blend in news sentiment
    final_score = (composite * (1 - NEWS_SENTIMENT_WEIGHT)
                   + news_score * NEWS_SENTIMENT_WEIGHT)
    final_score = float(np.clip(final_score, -1.0, 1.0))

    # Convert score → probabilities using a simple logistic-style mapping
    # score=0 → 33/34/33, score=+1 → ~80% bull, score=-1 → ~80% bear
    bull_raw = 1 / (1 + np.exp(-final_score * 4))   # sigmoid scaled
    bear_raw = 1 - bull_raw
    # Squeeze extremes to leave room for neutral
    bull_p   = float(bull_raw * 0.85)
    bear_p   = float(bear_raw * 0.85)
    neut_p   = float(1.0 - bull_p - bear_p)

    # Dominant direction
    if bull_p >= bear_p and bull_p > 0.40:
        direction = "BULL"
        direction_pct = round(bull_p * 100, 1)
    elif bear_p > bull_p and bear_p > 0.40:
        direction = "BEAR"
        direction_pct = round(bear_p * 100, 1)
    else:
        direction = "NEUTRAL"
        direction_pct = round(neut_p * 100, 1)

    # Per-pillar contributions (signed)
    contributions = {}
    for k, w in PILLAR_WEIGHTS.items():
        score = pillar_scores.get(k, 0.0)
        contributions[k] = {
            "label":        PILLAR_LABELS[k],
            "score":        round(score, 3),
            "contribution": round(score * w, 4),
            "signal":       "bull" if score > 0.1 else ("bear" if score < -0.1 else "neutral"),
        }

    return {
        "direction":      direction,
        "direction_pct":  direction_pct,
        "bull_pct":       round(bull_p * 100, 1),
        "neutral_pct":    round(neut_p * 100, 1),
        "bear_pct":       round(bear_p * 100, 1),
        "composite_score": round(final_score, 4),
        "pillar_score":    round(composite, 4),
        "news_score":      round(news_score, 4),
        "contributions":   contributions,
    }


def _compute_price_range(price: float, atr: float, prediction: dict) -> dict:
    """
    Compute 24h price target range.
    Direction and conviction determine the midpoint offset; ATR sets width.
    """
    score     = prediction["composite_score"]
    direction = prediction["direction"]
    conv      = abs(score)                    # 0–1 conviction

    # Midpoint shift: up to 1.5× ATR in predicted direction
    shift = score * atr * 1.5

    mid_target = price + shift
    half_width = atr * (0.8 + conv * 0.4)    # wider range when less certain

    low  = round(mid_target - half_width, 0)
    high = round(mid_target + half_width, 0)
    mid  = round(mid_target, 0)

    return {
        "current_price": round(price, 2),
        "target_low":    low,
        "target_mid":    mid,
        "target_high":   high,
        "atr_24h":       round(atr, 2),
    }


def run_prediction(symbol: str = "BTC") -> dict:
    """
    Full prediction run for any symbol — fetch data, compute, return state dict.
    """
    sym = symbol.upper()
    now = datetime.now(timezone.utc).isoformat()

    # 1. Pillar scores
    pillar_scores = _pillar_scores_from_aggregator(sym) or _mock_pillars()

    # 2. News sentiment (headlines are crypto-general, apply to any token)
    news_data = fetch_headlines(max_per_feed=8)

    # 3. Price + ATR for this specific symbol
    price = _get_current_price(sym)
    atr   = _get_atr(14, sym) if price else None

    # 4. Prediction
    prediction = _compute_prediction(pillar_scores, news_data["aggregate_score"])

    # 5. Price range
    price_range = None
    if price and atr:
        price_range = _compute_price_range(price, atr, prediction)

    state = {
        "symbol":       sym,
        "prediction":   prediction,
        "price_range":  price_range,
        "news":         {
            "aggregate_score": news_data["aggregate_score"],
            "bull_pct":        news_data["bull_pct"],
            "bear_pct":        news_data["bear_pct"],
            "neutral_pct":     news_data["neutral_pct"],
            "article_count":   news_data["article_count"],
            "articles":        news_data["articles"][:30],
        },
        "pillar_scores": {k: round(v, 4) for k, v in pillar_scores.items()},
        "updated_at":   now,
        "next_update":  None,
    }

    # Save to per-symbol state file
    try:
        sf = _state_file(sym)
        sf.parent.mkdir(parents=True, exist_ok=True)
        with open(sf, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"Failed to save prediction state: {e}")

    return state


# ---------------------------------------------------------------------------
# Background watcher thread
# ---------------------------------------------------------------------------
_watcher_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_watcher():
    """Start background prediction update thread."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return

    _stop_event.clear()

    def _loop():
        log.info("Prediction watcher started")
        while not _stop_event.is_set():
            try:
                log.info("Running prediction update...")
                run_prediction()
                log.info("Prediction updated.")
            except Exception as e:
                log.error(f"Prediction update error: {e}")
            _stop_event.wait(POLL_INTERVAL)

    _watcher_thread = threading.Thread(target=_loop, name="prediction-watcher", daemon=True)
    _watcher_thread.start()


def stop_watcher():
    _stop_event.set()


def get_state(symbol: str = "BTC") -> dict:
    """Read latest cached state from disk for the given symbol."""
    try:
        with open(_state_file(symbol.upper())) as f:
            return json.load(f)
    except Exception:
        return {"error": f"No prediction available yet for {symbol.upper()}. Run a refresh."}


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running prediction...")
    state = run_prediction()
    p = state["prediction"]
    r = state["price_range"]
    print(f"\nDirection: {p['direction']} ({p['direction_pct']}%)")
    print(f"Bull {p['bull_pct']}% | Neutral {p['neutral_pct']}% | Bear {p['bear_pct']}%")
    if r:
        print(f"Price now:  ${r['current_price']:,.0f}")
        print(f"Target 24h: ${r['target_low']:,.0f} – ${r['target_high']:,.0f}  (mid ${r['target_mid']:,.0f})")
    print(f"\nNews sentiment: {state['news']['aggregate_score']:+.3f}  "
          f"({state['news']['bull_pct']}% bull / {state['news']['bear_pct']}% bear)")
    print(f"\nPillar contributions:")
    for k, c in p["contributions"].items():
        bar = "▲" if c["signal"] == "bull" else ("▼" if c["signal"] == "bear" else "─")
        print(f"  {bar} {c['label']:25s}  {c['score']:+.3f}  → {c['contribution']:+.4f}")
