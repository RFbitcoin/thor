"""
THOR Signal Performance Analytics
------------------------------------
Calculates per-pillar accuracy over rolling 30 and 90 day windows.

Returns a structured dict ready for the dashboard API.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from analytics.signal_logger import _get_conn, PILLAR_KEYS

log = logging.getLogger(__name__)

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


def _accuracy_for_window(rows: list, days: int) -> dict:
    """
    Given resolved signal rows, compute per-pillar accuracy for the last `days` days.
    Returns {pillar_key: {accuracy_pct, correct, total, signal_count}}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    window_rows = [
        r for r in rows
        if datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) >= cutoff
    ]

    results = {}
    for key in PILLAR_KEYS:
        correct_key = f"{key}_correct"
        correct = 0
        incorrect = 0
        neutral = 0

        for row in window_rows:
            pillars = json.loads(row["pillar_json"])
            val = pillars.get(correct_key)
            if val is True:
                correct += 1
            elif val is False:
                incorrect += 1
            else:
                neutral += 1

        total = correct + incorrect
        accuracy = round(correct / total * 100, 1) if total > 0 else None

        results[key] = {
            "accuracy_pct":  accuracy,
            "correct":       correct,
            "incorrect":     incorrect,
            "neutral":       neutral,
            "signal_count":  len(window_rows),
            "total_directional": total,
        }

    return results


def _trend_arrow(acc_30: float | None, acc_90: float | None) -> str:
    """Returns trend indicator based on 30d vs 90d accuracy."""
    if acc_30 is None or acc_90 is None:
        return "─"
    diff = acc_30 - acc_90
    if diff >= 5:
        return "▲"
    if diff <= -5:
        return "▼"
    return "─"


def _grade(accuracy: float | None) -> str:
    if accuracy is None:
        return "pending"
    if accuracy >= 65:
        return "strong"
    if accuracy >= 55:
        return "good"
    if accuracy >= 45:
        return "weak"
    return "poor"


def get_performance() -> dict:
    """
    Compute full performance analytics.
    Returns structured dict for the dashboard.
    """
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT ts, price, composite, signal_direction, pillar_json "
                "FROM signals WHERE outcome_resolved = 1 ORDER BY ts DESC LIMIT 5000"
            ).fetchall()
            rows = [dict(r) for r in rows]

            total_logged = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome_resolved = 0"
            ).fetchone()[0]

    except Exception as e:
        log.error(f"Performance query error: {e}")
        return {"error": str(e)}

    acc_30 = _accuracy_for_window(rows, 30)
    acc_90 = _accuracy_for_window(rows, 90)

    pillars = []
    for key in PILLAR_KEYS:
        a30 = acc_30[key]["accuracy_pct"]
        a90 = acc_90[key]["accuracy_pct"]
        degrading = (
            a30 is not None and a90 is not None and (a90 - a30) >= 10
        )
        pillars.append({
            "key":          key,
            "label":        PILLAR_LABELS[key],
            "acc_30d":      a30,
            "acc_90d":      a90,
            "trend":        _trend_arrow(a30, a90),
            "grade":        _grade(a30),
            "degrading":    degrading,
            "total_30d":    acc_30[key]["total_directional"],
            "total_90d":    acc_90[key]["total_directional"],
            "signal_count": acc_30[key]["signal_count"],
        })

    # Overall composite accuracy
    overall_30 = None
    overall_90 = None
    valid_30 = [p["acc_30d"] for p in pillars if p["acc_30d"] is not None]
    valid_90 = [p["acc_90d"] for p in pillars if p["acc_90d"] is not None]
    if valid_30:
        overall_30 = round(sum(valid_30) / len(valid_30), 1)
    if valid_90:
        overall_90 = round(sum(valid_90) / len(valid_90), 1)

    # Best and worst performing pillars
    ranked = [p for p in pillars if p["acc_30d"] is not None]
    ranked.sort(key=lambda p: p["acc_30d"], reverse=True)

    return {
        "pillars":        pillars,
        "overall_30d":    overall_30,
        "overall_90d":    overall_90,
        "total_logged":   total_logged,
        "pending":        pending,
        "resolved":       total_logged - pending,
        "best_30d":       ranked[0]["label"] if ranked else None,
        "worst_30d":      ranked[-1]["label"] if ranked else None,
        "computed_at":    datetime.now(timezone.utc).isoformat(),
    }
