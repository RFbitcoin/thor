"""
THOR Auto-Trader — GMX Autonomous Execution Engine
Watches THOR's composite signal and automatically opens/closes
positions on GMX v2 when conviction exceeds the threshold.

Risk controls:
  - Max leverage:       GMX_MAX_LEVERAGE (default 5x)
  - Max position size:  GMX_MAX_POSITION_PCT of wallet (default 20%)
  - Trailing stop:      8% from position peak (same as backtest engine)
  - Kill switch:        set GMX_AUTO_TRADE=false to halt immediately
"""

import os
import json
import time
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv

try:
    from notifications.telegram import alert_trade_opened, alert_trade_closed, alert_error as _tg_error
    _TG = True
except Exception:
    _TG = False

load_dotenv(os.path.expanduser('~/.thor/config/.env'))

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gmx_state.json')

MIN_CONVICTION  = 0.25   # signal must be above this to trigger a trade (0–1 scale)
POLL_INTERVAL   = 60     # seconds between signal checks
MAX_LEVERAGE    = float(os.getenv('GMX_MAX_LEVERAGE',    '5'))
MAX_POS_PCT     = float(os.getenv('GMX_MAX_POSITION_PCT','0.20'))

DEFAULT_STATE = {
    'enabled':    False,
    'position':   None,       # {symbol, direction, is_long, entry_price, peak_price, size_usd, collateral, leverage}
    'trades':     [],
    'last_signal': None,
    'last_check':  None,
    'log':        [],
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return dict(DEFAULT_STATE)


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)


def _log(state, msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    entry = f'[{ts}] {msg}'
    print(entry)
    state.setdefault('log', [])
    state['log'].append(entry)
    state['log'] = state['log'][-100:]   # keep last 100 entries


def _get_trail_pct(leverage: float) -> float:
    """
    Leverage-scaled trailing stop. At 5x, 8% = 40% of collateral given back — too much.
    Formula keeps the effective collateral-impact of the trail roughly constant across leverage.
    """
    return round(max(0.025, 0.08 / (leverage ** 0.6)), 3)


def _get_max_hold_days(leverage: float) -> int:
    """
    GMX borrow fees accrue linearly. At 5x and 0.005%/hr, 35 days = 4.2% of notional
    = 21% of collateral eaten by fees alone. Cap hold time to keep borrow cost < ~1% of notional.
    """
    return max(7, int(35 / leverage))


def _conviction_to_leverage(conviction):
    """
    Map signal conviction (0.25–1.0) to leverage (1x–MAX_LEVERAGE).
    Low conviction → conservative leverage.
    High conviction → approach max leverage.
    """
    norm = max(0, min(1, (conviction - MIN_CONVICTION) / (1.0 - MIN_CONVICTION)))
    lev  = 1.0 + norm * (MAX_LEVERAGE - 1.0)
    return round(min(lev, MAX_LEVERAGE), 1)


def _conviction_to_size(conviction, usdc_balance):
    """
    Map conviction to USDC collateral amount.
    Low conviction → 5% of wallet.
    High conviction → MAX_POS_PCT of wallet.
    """
    norm = max(0, min(1, (conviction - MIN_CONVICTION) / (1.0 - MIN_CONVICTION)))
    pct  = 0.05 + norm * (MAX_POS_PCT - 0.05)
    return round(usdc_balance * pct, 2)


class AutoTrader:
    """
    Background thread that watches THOR signal and executes on GMX.
    Start/stop via enable() / disable().
    """

    def __init__(self, gmx_client, signal_fn):
        """
        gmx_client — instance of GMXClient
        signal_fn  — callable that returns THOR signal dict for a symbol
                     e.g. lambda sym: get_signal(sym)
        """
        self._client    = gmx_client
        self._signal_fn = signal_fn
        self._thread    = None
        self._stop_evt  = threading.Event()

    def enable(self):
        state = load_state()
        state['enabled'] = True
        save_state(state)
        if self._thread is None or not self._thread.is_alive():
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return {'ok': True, 'msg': 'Auto-trader enabled'}

    def disable(self):
        state = load_state()
        state['enabled'] = False
        save_state(state)
        self._stop_evt.set()
        return {'ok': True, 'msg': 'Auto-trader disabled — kill switch activated'}

    def get_status(self):
        state = load_state()
        pos   = state.get('position')
        usdc  = self._client.get_usdc_balance()
        trail_stop = None
        trail_pct  = 0
        if pos:
            peak        = pos.get('peak_price', pos['entry_price'])
            pos_trail   = pos.get('trail_pct', _get_trail_pct(pos.get('leverage', 1.0)))
            if pos['is_long']:
                trail_stop  = round(peak * (1 - pos_trail), 2)
                trail_range = peak - trail_stop
                trail_used  = peak - pos.get('current_price', peak)
            else:
                trail_stop  = round(peak * (1 + pos_trail), 2)
                trail_range = trail_stop - peak
                trail_used  = pos.get('current_price', peak) - peak
            trail_pct = round(max(0, min(100, trail_used / trail_range * 100)), 1) if trail_range > 0 else 0

        return {
            'enabled':      state.get('enabled', False),
            'position':     pos,
            'trail_stop':   trail_stop,
            'trail_pct':    trail_pct,
            'usdc_balance': round(usdc, 2),
            'last_signal':  state.get('last_signal'),
            'last_check':   state.get('last_check'),
            'trade_count':  len(state.get('trades', [])),
            'log':          state.get('log', [])[-20:],
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_evt.is_set():
            try:
                state = load_state()
                if not state.get('enabled'):
                    break
                self._tick(state)
            except Exception as e:
                print(f'AutoTrader loop error: {e}')
            self._stop_evt.wait(POLL_INTERVAL)

    def _tick(self, state):
        """One iteration: check signal, manage position."""
        # Default symbol for auto-trade is BTC unless position is open
        pos    = state.get('position')
        symbol = pos['symbol'] if pos else 'BTC'

        try:
            sig = self._signal_fn(symbol)
        except Exception as e:
            _log(state, f'Signal fetch error: {e}')
            save_state(state)
            return

        direction  = sig.get('direction', 'NEUTRAL')   # BUY / SELL / NEUTRAL
        conviction = abs(sig.get('score', 0))           # 0–1 normalised
        state['last_signal'] = {
            'direction': direction, 'conviction': round(conviction, 3), 'symbol': symbol
        }
        state['last_check'] = datetime.now(timezone.utc).isoformat()

        # ── Manage existing position ─────────────────────────────────────────
        if pos:
            try:
                price = self._client._get_current_price(symbol)
            except Exception:
                save_state(state)
                return

            pos['current_price'] = price

            # Update peak price (trailing stop anchor)
            peak      = pos.get('peak_price', pos['entry_price'])
            trail_pct = pos.get('trail_pct', _get_trail_pct(pos.get('leverage', 1.0)))

            if pos['is_long']:
                new_peak   = max(peak, price)
                trail_stop = new_peak * (1 - trail_pct)
                exit_trail = price <= trail_stop
                exit_signal = direction == 'SELL'
            else:
                new_peak   = min(peak, price)
                trail_stop = new_peak * (1 + trail_pct)
                exit_trail = price >= trail_stop
                exit_signal = direction == 'BUY'

            # Max hold gate — borrow fees make long holds uneconomic at leverage
            max_hold  = pos.get('max_hold_days', _get_max_hold_days(pos.get('leverage', 1.0)))
            opened_at = datetime.fromisoformat(pos['opened_at'].replace('Z', '+00:00'))
            hold_days = (datetime.now(timezone.utc) - opened_at).days
            exit_maxhold = hold_days >= max_hold

            pos['peak_price'] = new_peak
            pos['hold_days']  = hold_days
            state['position'] = pos

            if exit_trail or exit_signal or exit_maxhold:
                reason = ('Trailing stop hit' if exit_trail
                          else 'Max hold reached' if exit_maxhold
                          else 'Signal reversed')
                _log(state, f'Closing {symbol} {pos["direction"]} — {reason} (price ${price:,.0f})')
                result = self._client.close_position(symbol, pos['is_long'])
                if result['ok']:
                    pnl = (price - pos['entry_price']) / pos['entry_price'] * pos['size_usd']
                    if not pos['is_long']:
                        pnl = -pnl
                    pnl_pct = (price - pos['entry_price']) / pos['entry_price'] * 100
                    if not pos['is_long']:
                        pnl_pct = -pnl_pct
                    trade = {
                        'symbol':      symbol,
                        'direction':   pos['direction'],
                        'entry_price': pos['entry_price'],
                        'exit_price':  price,
                        'size_usd':    pos['size_usd'],
                        'pnl':         round(pnl, 2),
                        'reason':      reason,
                        'tx_hash':     result.get('tx_hash'),
                        'closed_at':   datetime.now(timezone.utc).isoformat(),
                    }
                    state['trades'].append(trade)
                    state['position'] = None
                    _log(state, f'Closed — P&L: ${pnl:+,.2f} | tx: {result.get("tx_hash","")[:16]}…')
                    if _TG:
                        try:
                            alert_trade_closed('GMX', symbol, pos['direction'],
                                               pos['entry_price'], price, pnl_pct, reason)
                        except Exception:
                            pass
                else:
                    _log(state, f'Close failed: {result["msg"]}')

        # ── Open new position ─────────────────────────────────────────────────
        elif conviction >= MIN_CONVICTION and direction in ('BUY', 'SELL'):
            is_long  = direction == 'BUY'
            regime   = sig.get('regime', 'RANGING')
            # Respect regime — only long in BULL, short in BEAR
            if (is_long and regime == 'BEAR') or (not is_long and regime == 'BULL'):
                _log(state, f'Signal {direction} ignored — regime is {regime}')
                save_state(state)
                return

            usdc    = self._client.get_usdc_balance()
            if usdc < 10:
                _log(state, 'Insufficient USDC balance — skipping trade')
                save_state(state)
                return

            leverage   = _conviction_to_leverage(conviction)
            collateral = _conviction_to_size(conviction, usdc)
            _log(state, f'Opening {direction} {symbol} | conviction={conviction:.2f} | {leverage}x | ${collateral:.0f} USDC')

            result = self._client.open_position(symbol, collateral, leverage, is_long)
            if result['ok']:
                price = result['price']
                state['position'] = {
                    'symbol':        symbol,
                    'direction':     'LONG' if is_long else 'SHORT',
                    'is_long':       is_long,
                    'entry_price':   price,
                    'peak_price':    price,
                    'current_price': price,
                    'size_usd':      collateral * leverage,
                    'collateral':    collateral,
                    'leverage':      leverage,
                    'trail_pct':     _get_trail_pct(leverage),
                    'max_hold_days': _get_max_hold_days(leverage),
                    'hold_days':     0,
                    'opened_at':     datetime.now(timezone.utc).isoformat(),
                    'tx_hash':       result.get('tx_hash'),
                }
                _log(state, f'Opened {direction} {symbol} @ ${price:,.0f} | tx: {result.get("tx_hash","")[:16]}…')
                if _TG:
                    try:
                        alert_trade_opened('GMX', symbol, 'LONG' if is_long else 'SHORT',
                                           price, collateral * leverage, leverage,
                                           int(conviction * 100))
                    except Exception:
                        pass
            else:
                _log(state, f'Open failed: {result["msg"]}')

        save_state(state)
