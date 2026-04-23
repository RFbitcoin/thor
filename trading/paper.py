import json, os, time
from datetime import datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_state.json')

DEFAULT_STATE = {
    "balance": 10000.00,
    "btc_held": 0.0,
    "position": None,
    "trades": [],
    "starting_balance": 10000.00
}

TRAIL_STOP_PCT = 0.08   # 8% from peak — matches backtest engine

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return dict(DEFAULT_STATE)

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def get_portfolio(current_price):
    state = load_state()
    pos = state.get('position')
    btc_value = state['btc_held'] * current_price

    unrealized_pnl = 0.0
    liq_price = None
    trail_stop_price = None
    trail_pct_used = 0
    peak_price = None

    if pos:
        leverage  = pos.get('leverage', 1)
        direction = pos.get('direction', 'LONG')
        entry     = pos['entry_price']
        margin    = pos['margin']
        size      = pos['size']

        if direction == 'LONG':
            unrealized_pnl = (current_price - entry) / entry * size
            liq_price      = round(entry * (1 - 1/leverage * 0.9), 2)
        else:
            unrealized_pnl = (entry - current_price) / entry * size
            liq_price      = round(entry * (1 + 1/leverage * 0.9), 2)
        unrealized_pnl = round(unrealized_pnl, 2)

        # Trailing stop — update peak price in state on every poll
        saved_peak = pos.get('peak_price', entry)
        if direction == 'LONG':
            new_peak = max(saved_peak, current_price)
        else:
            new_peak = min(saved_peak, current_price)

        if new_peak != saved_peak:
            pos['peak_price'] = new_peak
            state['position'] = pos
            save_state(state)

        peak_price = new_peak

        if direction == 'LONG':
            trail_stop_price = round(peak_price * (1 - TRAIL_STOP_PCT), 2)
            trail_range      = peak_price - trail_stop_price
            trail_used       = peak_price - current_price
        else:
            trail_stop_price = round(peak_price * (1 + TRAIL_STOP_PCT), 2)
            trail_range      = trail_stop_price - peak_price
            trail_used       = current_price - peak_price

        trail_pct_used = round(max(0, min(100, trail_used / trail_range * 100)), 1) if trail_range > 0 else 0

    total_value  = round(state['balance'] + unrealized_pnl, 2)
    total_return = round(((total_value - state['starting_balance']) / state['starting_balance']) * 100, 2)

    return {
        "balance":          round(state['balance'], 2),
        "btc_held":         round(state['btc_held'], 8),
        "btc_value":        round(btc_value, 2),
        "total_value":      total_value,
        "total_return":     total_return,
        "unrealized_pnl":   unrealized_pnl,
        "liq_price":        liq_price,
        "trail_stop_price": trail_stop_price,
        "trail_pct_used":   trail_pct_used,
        "peak_price":       round(peak_price, 2) if peak_price else None,
        "position":         pos,
        "trades":           state['trades'][-10:],
        "trade_count":      len(state['trades'])
    }

def buy(current_price, pct=1.0, leverage=1, reason="Manual"):
    """Open a LONG position. pct = % of balance to use as margin."""
    state = load_state()
    if state.get('position'):
        return {"ok": False, "msg": "Already in a position — close it first"}
    margin = round(state['balance'] * pct, 2)
    if margin < 10:
        return {"ok": False, "msg": "Insufficient balance"}
    size = round(margin * leverage, 2)  # notional
    btc_amount = size / current_price
    liq_price = round(current_price * (1 - 1/leverage * 0.9), 2) if leverage > 1 else 0

    state['balance'] -= margin
    state['btc_held'] += btc_amount
    state['position'] = {
        "direction":   "LONG",
        "entry_price": current_price,
        "peak_price":  current_price,
        "btc_amount":  round(btc_amount, 8),
        "margin":      margin,
        "size":        size,
        "leverage":    leverage,
        "liq_price":   liq_price,
        "opened_at":   datetime.utcnow().isoformat(),
        "reason":      reason
    }
    save_state(state)
    liq_str = f" | Liq: ${liq_price:,.0f}" if leverage > 1 else ""
    return {"ok": True, "msg": f"LONG {btc_amount:.4f} BTC @ ${current_price:,.0f} | {leverage}x leverage | Margin: ${margin:,.0f}{liq_str}"}

def sell_short(current_price, pct=1.0, leverage=1, reason="Manual"):
    """Open a SHORT position."""
    state = load_state()
    if state.get('position'):
        return {"ok": False, "msg": "Already in a position — close it first"}
    margin = round(state['balance'] * pct, 2)
    if margin < 10:
        return {"ok": False, "msg": "Insufficient balance"}
    size = round(margin * leverage, 2)
    btc_amount = size / current_price
    liq_price = round(current_price * (1 + 1/leverage * 0.9), 2) if leverage > 1 else 0

    state['balance'] -= margin
    state['position'] = {
        "direction":   "SHORT",
        "entry_price": current_price,
        "peak_price":  current_price,
        "btc_amount":  round(btc_amount, 8),
        "margin":      margin,
        "size":        size,
        "leverage":    leverage,
        "liq_price":   liq_price,
        "opened_at":   datetime.utcnow().isoformat(),
        "reason":      reason
    }
    save_state(state)
    liq_str = f" | Liq: ${liq_price:,.0f}" if leverage > 1 else ""
    return {"ok": True, "msg": f"SHORT {btc_amount:.4f} BTC @ ${current_price:,.0f} | {leverage}x leverage | Margin: ${margin:,.0f}{liq_str}"}

def close(current_price, reason="Manual"):
    """Close any open position (long or short)."""
    state = load_state()
    pos = state.get('position')
    if not pos:
        return {"ok": False, "msg": "No open position"}

    direction = pos.get('direction', 'LONG')
    entry = pos['entry_price']
    margin = pos['margin']
    size = pos['size']
    leverage = pos.get('leverage', 1)

    if direction == 'LONG':
        pnl = (current_price - entry) / entry * size
        state['btc_held'] = 0.0
    else:  # SHORT
        pnl = (entry - current_price) / entry * size

    pnl = round(pnl, 2)
    pnl_pct = round((pnl / margin) * 100, 2)
    proceeds = round(margin + pnl, 2)
    state['balance'] += max(proceeds, 0)  # can't go below 0 (liquidated)

    trade = {
        "direction": direction,
        "entry_price": entry,
        "exit_price": current_price,
        "btc_amount": pos['btc_amount'],
        "margin": margin,
        "size": size,
        "leverage": leverage,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "opened_at": pos['opened_at'],
        "closed_at": datetime.utcnow().isoformat(),
        "reason": reason
    }
    state['position'] = None
    state['trades'].append(trade)
    save_state(state)
    return {"ok": True, "msg": f"Closed {direction} @ ${current_price:,.0f} | P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)", "trade": trade}

# Legacy compatibility
def sell(current_price, reason="Manual"):
    return close(current_price, reason)

def reset(starting_balance=10000.00):
    state = dict(DEFAULT_STATE)
    state['starting_balance'] = starting_balance
    state['balance'] = starting_balance
    save_state(state)
    return {"ok": True, "msg": f"Portfolio reset to ${starting_balance:,.2f}"}
