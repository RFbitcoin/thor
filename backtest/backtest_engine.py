"""
Backtest Engine with Real BGeometrics Data
"""
import pandas as pd
import numpy as np
import json
import os
import sys

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from bgeometrics_client import BGeometricsClient
from signal_engine import SignalEngine

class BacktestEngine:
    def __init__(self):
        self.signal_engine = SignalEngine()
    
    def run_backtest(self):
        """Run backtest with real BGeometrics data"""
        print("Fetching real BGeometrics data...")
        client = BGeometricsClient()
        df = client.backfill_all()
        
        print(f"✓ Loaded {len(df)} days of data")
        print(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
        
        # Add basic technicals
        df['rsi'] = self._calculate_rsi(df['close'])
        df['ma50'] = df['close'].rolling(50).mean()
        df['ma200'] = df['close'].rolling(200).mean()
        df['ma_slope'] = df['ma50'].diff()
        
        print(f"\nGenerating signals on {len(df)} days...")
        
        signals = []
        for idx, row in df.iterrows():
            signal = self.signal_engine.generate_signal(
                timestamp=row['date'],
                ohlc_data={
                    'rsi': row['rsi'] if pd.notna(row['rsi']) else 50,
                    'ma_slope': row['ma_slope'] if pd.notna(row['ma_slope']) else 0
                },
                on_chain_data={
                    'sopr': row['sopr'] if pd.notna(row['sopr']) else 1.0,
                    'mvrv_zscore': row['mvrv_zscore'] if pd.notna(row['mvrv_zscore']) else 0,
                    'exchange_netflow': 0,
                    'lth_supply_change': 0
                },
                derivatives_data={
                    'funding_rate': row['funding_rate'] if pd.notna(row['funding_rate']) else 0,
                    'oi_change': 0,
                    'basis': 0
                },
                macro_data={
                    'fed_rate': 5.0,
                    'm2_growth': 0,
                    'dxy': 100,
                    'vix': 20
                },
                sentiment_data={
                    'fear_greed': row['fear_greed'] if pd.notna(row['fear_greed']) else 50,
                    'social_volume': 0,
                    'whale_transactions': 0,
                    'volume_change': 0,
                    'volume_ma_ratio': 1.0
                }
            )
            
            signals.append({
                'date': row['date'],
                'close': row['close'],
                'signal_type': signal.signal_type,
                'score': signal.score,
                'conviction': signal.conviction,
                'regime': signal.regime
            })
        
        signals_df = pd.DataFrame(signals)
        
        # Backtest: buy on BUY signal, sell on SELL signal
        trades = []
        position = None
        entry_price = None
        entry_date = None
        
        for idx, row in signals_df.iterrows():
            if row['signal_type'] == 'BUY' and position is None:
                position = 'LONG'
                entry_price = row['close']
                entry_date = row['date']
            elif row['signal_type'] == 'SELL' and position == 'LONG':
                exit_price = row['close']
                pnl = (exit_price - entry_price) / entry_price
                days_held = (row['date'] - entry_date).days
                trades.append({
                    'entry_date': entry_date.strftime('%Y-%m-%d'),
                    'entry_price': float(entry_price),
                    'exit_date': row['date'].strftime('%Y-%m-%d'),
                    'exit_price': float(exit_price),
                    'pnl_pct': float(pnl * 100),
                    'days_held': int(days_held),
                    'win': pnl > 0
                })
                position = None
        
        # Calculate metrics
        if len(trades) > 0:
            trades_df = pd.DataFrame(trades)
            wins = trades_df[trades_df['win']]
            losses = trades_df[~trades_df['win']]
            
            results = {
                'num_trades': len(trades_df),
                'win_rate': float(len(wins) / len(trades_df)),
                'avg_win_pct': float(wins['pnl_pct'].mean()) if len(wins) > 0 else 0,
                'avg_loss_pct': float(losses['pnl_pct'].mean()) if len(losses) > 0 else 0,
                'total_pnl_pct': float(trades_df['pnl_pct'].sum()),
                'best_trade': float(trades_df['pnl_pct'].max()),
                'worst_trade': float(trades_df['pnl_pct'].min()),
            }
        else:
            results = {
                'num_trades': 0,
                'win_rate': 0,
                'avg_win_pct': 0,
                'avg_loss_pct': 0,
                'total_pnl_pct': 0,
                'best_trade': 0,
                'worst_trade': 0,
            }
            trades = []
        
        # Signal distribution
        signal_counts = signals_df['signal_type'].value_counts().to_dict()
        
        print("\n=== BACKTEST RESULTS ===")
        print(f"Period: {signals_df['date'].min().date()} to {signals_df['date'].max().date()}")
        print(f"\nSignal Distribution:")
        for signal_type in ['BUY', 'SELL', 'NEUTRAL']:
            count = signal_counts.get(signal_type, 0)
            print(f"  {signal_type}: {count} ({count/len(signals_df)*100:.1f}%)")
        
        print(f"\nTrades: {results['num_trades']}")
        if results['num_trades'] > 0:
            print(f"Win Rate: {results['win_rate']:.1%}")
            print(f"Total PnL: {results['total_pnl_pct']:.2f}%")
            print(f"Avg Win: {results['avg_win_pct']:.2f}%")
            print(f"Avg Loss: {results['avg_loss_pct']:.2f}%")
            print(f"Best Trade: {results['best_trade']:.2f}%")
            print(f"Worst Trade: {results['worst_trade']:.2f}%")
        
        # Save results
        output = {
            'backtest_date': pd.Timestamp.now().isoformat(),
            'data_range': {
                'start': signals_df['date'].min().isoformat(),
                'end': signals_df['date'].max().isoformat(),
                'days': len(signals_df)
            },
            'metrics': results,
            'signal_distribution': {str(k): int(v) for k, v in signal_counts.items()},
            'trades': trades
        }
        
        with open('reports/backtest_results.json', 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        print("\n✓ Results saved to reports/backtest_results.json")
        return results
    
    def _calculate_rsi(self, prices, period=14):
        """Calculate RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

if __name__ == "__main__":
    engine = BacktestEngine()
    engine.run_backtest()
