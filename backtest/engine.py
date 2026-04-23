import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import json
import os
from bgeometrics_client import BGeometricsClient

class SignalGenerator:
    """Generates signals from pillars and learns weights via logistic regression."""

    def __init__(self):
        self.weights = {
            'sopr':          0.09,
            'mvrv_zscore':   0.09,
            'funding_rate':  0.12,
            'fear_greed':    0.12,
            'price_momentum':0.12,
            'rsi':           0.08,
            'rsi_div':       0.09,
            'ema200':        0.09,
            'vix':           0.08,
            'volume':        0.06,
            'vwap':          0.06,
        }
        self.scaler = StandardScaler()
        self.lr_model = None

    def compute_pillars(self, df):
        """Compute all signal pillars from raw data."""
        df = df.copy()

        # Pillar 1: SOPR — >1 profit-taking (bearish), <1 accumulation (bullish)
        df['sopr_signal'] = np.where(df['sopr'] > 1.0, 1, -1)
        df['sopr_signal'] = df['sopr_signal'] * (np.abs(df['sopr'] - 1.0) / 0.1).clip(0, 1)

        # Pillar 2: MVRV Z-Score — trigger at ±1.5 (not ±2.5) for more sensitivity
        df['mvrv_signal'] = np.where(
            df['mvrv_zscore'] > 1.5, -1,
            np.where(df['mvrv_zscore'] < -1.5, 1, 0)
        )
        df['mvrv_signal'] = df['mvrv_signal'] * (np.abs(df['mvrv_zscore']) / 3.0).clip(0, 1)

        # Pillar 3: Funding Rate — high positive = crowded long (bearish)
        df['funding_signal'] = -np.sign(df['funding_rate']) * np.abs(df['funding_rate']).clip(0, 0.001) * 1000

        # Pillar 4: Fear & Greed — contrarian: extreme fear → buy, extreme greed → sell
        df['fear_greed_signal'] = -(df['fear_greed'] - 50) / 50  # inverted: fear is bullish

        # Pillar 5: Price Momentum — 10-day (works within 30-day test windows)
        df['price_momentum'] = df['close'].pct_change(10)
        df['momentum_signal'] = np.sign(df['price_momentum']) * np.abs(df['price_momentum']).clip(0, 0.2) / 0.2

        # Pillar 6: RSI — overbought/oversold
        delta    = df['close'].diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=5).mean()
        avg_loss = loss.rolling(14, min_periods=5).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))
        df['rsi_signal'] = np.where(df['rsi'] > 70, -1, np.where(df['rsi'] < 30, 1, 0))
        df['rsi_signal'] = df['rsi_signal'] * (np.abs(df['rsi'] - 50) / 50).clip(0, 1)

        # Pillar 7: RSI Divergence
        # Bullish: price lower low + RSI higher low → reversal up
        # Bearish: price higher high + RSI lower high → reversal down
        price_chg_10 = df['close'].pct_change(10)
        rsi_chg_10   = df['rsi'].diff(10)
        df['rsi_div_signal'] = np.where(
            (price_chg_10 < -0.02) & (rsi_chg_10 >  3),  1.0,   # bullish divergence
            np.where(
            (price_chg_10 >  0.02) & (rsi_chg_10 < -3), -1.0,   # bearish divergence
            np.where(
            (price_chg_10 >  0.01) & (rsi_chg_10 < -5),  0.5,   # hidden bullish
            np.where(
            (price_chg_10 < -0.01) & (rsi_chg_10 >  5), -0.5,   # hidden bearish
            0.0))))

        # Pillar 8: 200 EMA + Price Action
        # Uses pre-computed full-history EMA values if available, otherwise computes locally
        if 'ema200_full' in df.columns:
            e200 = df['ema200_full']
            e50  = df['ema50_full']
        else:
            e200 = df['close'].ewm(span=200, min_periods=20, adjust=False).mean()
            e50  = df['close'].ewm(span=50,  min_periods=15, adjust=False).mean()

        above_200    = (df['close'] > e200).astype(float) * 2 - 1   # +1 or -1
        ema200_slope = e200.pct_change(10).fillna(0)
        ma50_vs_200  = (e50 > e200).astype(float) * 2 - 1           # golden/death cross
        df['ema200_signal'] = (
            above_200    * 0.5 +
            np.sign(ema200_slope) * 0.3 +
            ma50_vs_200  * 0.2
        ).clip(-1, 1)

        # Pillar 9: VIX — cross-asset fear gauge
        # VIX 20 = neutral; above 20 = fear = bearish for BTC; below 20 = calm = bullish
        if 'vix' in df.columns:
            df['vix_signal'] = -((df['vix'] - 20.0) / 20.0).clip(-1, 1)
        else:
            df['vix_signal'] = 0.0

        # Pillar 10: Volume — conviction signal (high volume × price direction)
        if 'volume' in df.columns:
            vol_avg = df['volume'].rolling(30, min_periods=10).mean()
            vol_ratio = (df['volume'] / (vol_avg + 1e-9)).clip(0, 4)
            price_dir = np.sign(df['close'].pct_change(3).fillna(0))
            df['volume_signal'] = ((vol_ratio - 1.0) / 1.5).clip(-1, 1) * price_dir
        else:
            df['volume_signal'] = 0.0

        # Pillar 11: Rolling VWAP (5-day) — price vs institutional flow
        # Uses Binance OHLCV high/low/close/volume; falls back to close-only if needed
        if 'volume' in df.columns:
            if 'high' in df.columns and 'low' in df.columns:
                typical_price = (df['high'] + df['low'] + df['close']) / 3.0
            else:
                typical_price = df['close']
            tp_vol = typical_price * df['volume']
            vwap_5d = (tp_vol.rolling(5, min_periods=3).sum() /
                       df['volume'].rolling(5, min_periods=3).sum().clip(lower=1e-9))
            vwap_dist = (df['close'] - vwap_5d) / (vwap_5d + 1e-9)
            df['vwap_signal'] = vwap_dist.clip(-0.05, 0.05) / 0.05
        else:
            df['vwap_signal'] = 0.0

        return df

    def compute_composite_signal(self, df, weights=None):
        """Compute weighted composite signal with dynamic per-window threshold."""
        w = weights or self.weights
        df = df.copy()

        pillar_cols = [
            'sopr_signal', 'mvrv_signal', 'funding_signal',
            'fear_greed_signal', 'momentum_signal', 'rsi_signal',
            'rsi_div_signal', 'ema200_signal',
            'vix_signal', 'volume_signal', 'vwap_signal',
        ]
        for col in pillar_cols:
            if col not in df.columns:
                df[col] = 0.0

        df['signal'] = (
            w.get('sopr',          0.09) * df['sopr_signal'] +
            w.get('mvrv_zscore',   0.09) * df['mvrv_signal'] +
            w.get('funding_rate',  0.12) * df['funding_signal'] +
            w.get('fear_greed',    0.12) * df['fear_greed_signal'] +
            w.get('price_momentum',0.12) * df['momentum_signal'] +
            w.get('rsi',           0.08) * df['rsi_signal'] +
            w.get('rsi_div',       0.09) * df['rsi_div_signal'] +
            w.get('ema200',        0.09) * df['ema200_signal'] +
            w.get('vix',           0.08) * df['vix_signal'] +
            w.get('volume',        0.06) * df['volume_signal'] +
            w.get('vwap',          0.06) * df['vwap_signal']
        )
        df['signal'] = df['signal'].clip(-1, 1)
        df['conviction'] = np.abs(df['signal']) * 100

        # Dynamic threshold: calibrate to this window's signal distribution
        # Use the 40th percentile of |signal| — ~60% of days will be active
        threshold = float(np.clip(df['signal'].abs().quantile(0.40), 0.04, 0.30))

        df['action'] = np.where(
            df['signal'] >  threshold, 'BUY',
            np.where(df['signal'] < -threshold, 'SELL', 'NEUTRAL')
        )
        return df

    def learn_weights(self, df_train):
        """Learn optimal weights via logistic regression on signal → next-day return."""
        df = df_train.copy()
        df['next_return'] = df['close'].pct_change(1).shift(-1)
        df['label'] = (df['next_return'] > 0).astype(int)

        feature_cols = [
            'sopr_signal', 'mvrv_signal', 'funding_signal',
            'fear_greed_signal', 'momentum_signal', 'rsi_signal',
            'rsi_div_signal', 'ema200_signal',
            'vix_signal', 'volume_signal', 'vwap_signal',
        ]
        X = df[feature_cols].fillna(0).values
        y = df['label'].dropna().values
        X = X[:len(y)]

        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)
        self.lr_model = LogisticRegression(max_iter=1000)
        self.lr_model.fit(X_scaled, y)

        coefs = np.abs(self.lr_model.coef_[0])
        coefs = coefs / coefs.sum()

        self.weights = {
            'sopr':          float(coefs[0]),
            'mvrv_zscore':   float(coefs[1]),
            'funding_rate':  float(coefs[2]),
            'fear_greed':    float(coefs[3]),
            'price_momentum':float(coefs[4]),
            'rsi':           float(coefs[5]),
            'rsi_div':       float(coefs[6]),
            'ema200':        float(coefs[7]),
            'vix':           float(coefs[8]),
            'volume':        float(coefs[9]),
            'vwap':          float(coefs[10]),
        }
        return self.weights


class BacktestEngine:
    """Walk-forward backtest engine with regime filter and multi-day hold."""

    TAKER_FEE            = 0.001   # GMX V1: 0.1% per side open/close (use 0.00065 for V2)
    BORROW_RATE_PER_DAY  = 0.0012  # 0.005%/hr × 24h at ~100% pool utilisation basis
    ASSUMED_LEVERAGE     = 3.0     # representative avg (conviction maps 1x–5x, ~3x mean)

    def __init__(self, df, train_days=120, test_days=30):
        self.df         = df.copy()  # preserve DatetimeIndex
        self.train_days = train_days
        self.test_days  = test_days
        self.results    = []

    def run(self):
        sig_gen = SignalGenerator()
        n = len(self.df)

        # --- Full-history indicators (computed before window splits for accuracy) ---
        self.df['ma20']   = self.df['close'].rolling(20, min_periods=10).mean()
        self.df['regime'] = np.where(self.df['close'] > self.df['ma20'], 'BULL', 'BEAR')

        # 200 EMA + 50 EMA on full price series so test slices inherit accurate values
        k200 = 2.0 / 201;  k50 = 2.0 / 51
        e200 = [float(self.df['close'].iloc[0])]
        e50  = [float(self.df['close'].iloc[0])]
        for px in self.df['close'].iloc[1:]:
            e200.append(float(px) * k200 + e200[-1] * (1 - k200))
            e50.append( float(px) * k50  + e50[-1]  * (1 - k50))
        self.df['ema200_full'] = e200
        self.df['ema50_full']  = e50

        window_idx = 0
        while True:
            train_start = window_idx * self.test_days
            train_end   = train_start + self.train_days
            test_end    = train_end   + self.test_days

            if test_end > n:
                break

            df_train = self.df.iloc[train_start:train_end].copy()
            df_test  = self.df.iloc[train_end:test_end].copy()

            df_train = sig_gen.compute_pillars(df_train)
            df_test  = sig_gen.compute_pillars(df_test)

            learned_weights = sig_gen.learn_weights(df_train)
            df_test = sig_gen.compute_composite_signal(df_test, weights=learned_weights)

            metrics = self._evaluate(df_test, window_idx)
            self.results.append(metrics)
            window_idx += 1

        return self._summarize()

    def _evaluate(self, df_test, window_idx):
        df = df_test.copy()
        df['next_return'] = df['close'].pct_change(1).shift(-1)

        # --- Signal smoothing: 3-day EMA reduces single-day noise spikes ---
        df['signal_smooth'] = df['signal'].ewm(span=3, adjust=False).mean()

        # Dynamic entry threshold — 35th pctile of |smoothed signal| (was 40th, slightly more active)
        threshold = float(np.clip(df['signal_smooth'].abs().quantile(0.35), 0.04, 0.30))
        MIN_CONVICTION = 0.07   # hard floor: reject very weak signals even if above threshold
        LOSS_STOP_DAYS = 5      # cut a losing trade after this many days underwater
        MAX_HOLD_DAYS  = 7      # GMX borrow fees make 35-day holds uneconomic at leverage

        # ATR-based trailing stop: 2× daily ATR as % of price (floor 2%, cap 6%)
        # Uses |close diff| as close-only ATR proxy when high/low are unavailable
        if 'high' in df.columns and 'low' in df.columns:
            atr_abs = (df['high'] - df['low']).rolling(14, min_periods=5).mean()
        else:
            atr_abs = df['close'].diff().abs().rolling(14, min_periods=5).mean()
        atr_pct_series = (atr_abs / df['close']).fillna(0.025)
        atr_pct_vals   = atr_pct_series.values

        signals = df['signal_smooth'].values
        regimes = df['regime'].values if 'regime' in df.columns else np.array(['BULL'] * len(df))
        closes  = df['close'].values

        # Signal persistence: 1-day confirmation (was 2) — enter faster, catch more moves
        sig_series  = pd.Series(df['signal_smooth'].values)
        long_ready  = (sig_series > threshold).fillna(False).values
        short_ready = (sig_series < -threshold).fillna(False).values

        # --- State machine ---
        positions     = np.zeros(len(df))
        current_pos   = 0
        last_exit_dir = 0
        hold_days     = 0
        entry_price   = 0.0
        peak_price    = 0.0   # highest price seen since entry (for trailing stop)

        for i in range(len(df)):
            sig = float(signals[i]) if not np.isnan(signals[i]) else 0.0
            reg = regimes[i] if isinstance(regimes[i], str) else 'RANGING'
            px  = float(closes[i])

            if current_pos == 0:
                # Entry: regime confirmed + signal above threshold + min conviction
                if (long_ready[i] and sig > MIN_CONVICTION
                        and reg == 'BULL' and last_exit_dir != 1):
                    current_pos   = 1
                    entry_price   = px
                    peak_price    = px
                    hold_days     = 0
                    last_exit_dir = 0
                elif (short_ready[i] and sig < -MIN_CONVICTION
                        and reg == 'BEAR' and last_exit_dir != -1):
                    current_pos   = -1
                    entry_price   = px
                    peak_price    = px
                    hold_days     = 0
                    last_exit_dir = 0
            else:
                hold_days += 1
                price_pnl = (px - entry_price) / (entry_price + 1e-9) * current_pos

                # Update peak: highest favourable price seen since entry
                if current_pos == 1:
                    peak_price = max(peak_price, px)
                else:
                    peak_price = min(peak_price, px)

                # ATR-based trailing stop (2× ATR, floor 2%, cap 6%)
                trail_stop_pct = float(np.clip(2.0 * atr_pct_vals[i], 0.02, 0.06))
                if current_pos == 1:
                    trail_drawdown = (peak_price - px) / (peak_price + 1e-9)
                else:
                    trail_drawdown = (px - peak_price) / (peak_price + 1e-9)

                # Exit conditions (any one triggers):
                exit_signal   = (current_pos == 1 and sig < 0) or (current_pos == -1 and sig > 0)
                exit_timeloss = hold_days >= LOSS_STOP_DAYS and price_pnl < 0
                exit_maxhold  = hold_days >= MAX_HOLD_DAYS
                exit_trail    = trail_drawdown >= trail_stop_pct

                if exit_signal or exit_timeloss or exit_maxhold or exit_trail:
                    last_exit_dir = current_pos
                    current_pos   = 0
                    hold_days     = 0
                    peak_price    = 0.0
                else:
                    last_exit_dir = 0

            positions[i] = current_pos

        df['position'] = positions

        # Conviction-scaled size: |smoothed signal| while in trade
        df['sized_pos'] = df['position'] * df['signal_smooth'].abs()

        # Transaction costs on position changes (entry + exit)
        pos_changed       = (df['position'] != df['position'].shift(1).fillna(0)).astype(float)
        df['cost']        = pos_changed * self.TAKER_FEE
        # Daily borrow fee: accrues each day position is open, scaled by leverage
        df['borrow_cost'] = ((df['position'] != 0).astype(float)
                             * self.BORROW_RATE_PER_DAY
                             * self.ASSUMED_LEVERAGE
                             * df['signal_smooth'].abs())
        df['strat_ret']   = df['sized_pos'] * df['next_return'] - df['cost'] - df['borrow_cost']

        # --- Per-trade tracking (long vs short separated) ---
        long_returns  = []
        short_returns = []
        running       = 0.0
        prev_pos      = 0.0
        open_dir      = 0   # direction of the currently open trade

        for i in range(len(df)):
            cur = float(df['position'].iloc[i])
            ret = df['strat_ret'].iloc[i]
            if not np.isnan(ret):
                running += ret
            if prev_pos != 0 and cur == 0:
                (long_returns if open_dir == 1 else short_returns).append(running)
                running  = 0.0
                open_dir = 0
            if prev_pos == 0 and cur != 0:
                running  = 0.0
                open_dir = int(cur)
            prev_pos = cur
        if prev_pos != 0:
            (long_returns if open_dir == 1 else short_returns).append(running)

        all_returns = long_returns + short_returns
        trades      = len(all_returns)

        def _wr(returns):
            return float(sum(1 for r in returns if r > 0) / len(returns)) if returns else 0.0

        win_rate       = _wr(all_returns)
        long_win_rate  = _wr(long_returns)
        short_win_rate = _wr(short_returns)

        # Total return and Sharpe over full period (flat days included as 0)
        daily        = df['strat_ret'].dropna()
        total_return = float(daily.sum()) * 100

        if len(daily) >= 10 and daily.std() > 1e-9:
            sharpe = float(daily.mean() / daily.std() * np.sqrt(252))
            sharpe = float(np.clip(sharpe, -5.0, 5.0))
        else:
            sharpe = 0.0

        return {
            'window':            window_idx,
            'start_date':        df.index[0]  if len(df) > 0 else None,
            'end_date':          df.index[-1] if len(df) > 0 else None,
            'total_return':      total_return,
            'win_rate':          win_rate,
            'sharpe':            sharpe,
            'trades':            trades,
            'long_trades':       len(long_returns),
            'short_trades':      len(short_returns),
            'long_win_rate':     long_win_rate,
            'short_win_rate':    short_win_rate,
            'long_total_ret':    round(sum(long_returns)  * 100, 2),
            'short_total_ret':   round(sum(short_returns) * 100, 2),
        }

    def _summarize(self):
        results_df = pd.DataFrame(self.results)

        total_long   = int(results_df['long_trades'].sum())
        total_short  = int(results_df['short_trades'].sum())
        total_trades = total_long + total_short

        avg_lwr = results_df['long_win_rate'].mean()  * 100
        avg_swr = results_df['short_win_rate'].mean() * 100
        sum_lret = results_df['long_total_ret'].sum()
        sum_sret = results_df['short_total_ret'].sum()

        print("\n" + "="*80)
        print("WALK-FORWARD BACKTEST RESULTS (120-day train, 30-day test windows)")
        print("="*80)
        print(f"\nTotal windows: {len(results_df)}")

        print(f"\nAggregate Metrics:")
        print(f"  Total Return:  {results_df['total_return'].sum():.2f}%")
        print(f"  Win Rate:      {results_df['win_rate'].mean()*100:.1f}%")
        print(f"  Sharpe Ratio:  {results_df['sharpe'].mean():.2f}")
        print(f"  Total Trades:  {total_trades}")

        print(f"\nLong vs Short Breakdown:")
        print(f"  {'':20s}  {'LONG':>10}  {'SHORT':>10}")
        print(f"  {'Trades':20s}  {total_long:>10}  {total_short:>10}")
        print(f"  {'Win Rate':20s}  {avg_lwr:>9.1f}%  {avg_swr:>9.1f}%")
        print(f"  {'Total Return':20s}  {sum_lret:>9.2f}%  {sum_sret:>9.2f}%")
        print(f"  {'Avg Return/Trade':20s}  {sum_lret/max(total_long,1):>9.3f}%  {sum_sret/max(total_short,1):>9.3f}%")

        print(f"\nPer-Window Breakdown:")
        print(results_df[['window', 'start_date', 'end_date', 'total_return',
                           'long_trades', 'long_win_rate', 'short_trades',
                           'short_win_rate', 'sharpe']].to_string(index=False))

        return results_df


def main():
    print("Fetching BGeometrics data (last 6 months)...")
    client = BGeometricsClient()

    end_date   = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')

    sopr       = client.get_sopr(start_date=start_date, end_date=end_date)
    mvrv       = client.get_mvrv_zscore(start_date=start_date, end_date=end_date)
    price      = client.get_btc_price(start_date=start_date, end_date=end_date)
    funding    = client.get_funding_rate(start_date=start_date, end_date=end_date)
    fear_greed = client.get_fear_greed(start_date=start_date, end_date=end_date)

    df = pd.DataFrame(index=price.index)
    df['close']        = price['btc_price']
    df['sopr']         = sopr['sopr']
    df['mvrv_zscore']  = mvrv['mvrv_zscore']
    df['funding_rate'] = funding['funding_rate']
    df['fear_greed']   = fear_greed['fear_greed']
    df = df.dropna()

    print(f"Data: {len(df)} days from {df.index[0].date()} to {df.index[-1].date()}")

    engine  = BacktestEngine(df, train_days=120, test_days=30)
    results = engine.run()

    os.makedirs('backtest', exist_ok=True)
    results.to_csv('backtest/results.csv', index=False)
    print(f"\nResults saved to backtest/results.csv")

if __name__ == '__main__':
    main()
