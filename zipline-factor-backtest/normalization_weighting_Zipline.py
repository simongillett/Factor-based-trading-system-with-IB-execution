"""
Factor backtest with proper position management.
"""

import os
from pathlib import Path
os.environ['ZIPLINE_ROOT'] = str(Path(__file__).parent / '.zipline')

import json
import duckdb
import polars as pl
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.feature_selection import VarianceThreshold
from zipline.api import attach_pipeline, pipeline_output, order_target_percent
from zipline.pipeline import Pipeline, CustomFactor
from zipline.pipeline.data import USEquityPricing
from zipline import run_algorithm
from zipline.utils.calendar_utils import get_calendar
import sqlite3

# Config
TOP_N = 10
REBALANCE_DAYS = 5

# Load data
print("🔗 Loading data...")
script_dir = Path(__file__).parent
db_dir = script_dir.parent / "FMP_Databases"

con = duckdb.connect(str(db_dir / "company_metrics__1yr.db"))
key_metrics = con.execute("SELECT * FROM key_metrics").pl()
con.close()

con = duckdb.connect(str(db_dir / "company_prices_daily_1yr.db"))
prices = con.execute("SELECT * FROM prices").pl()
con.close()

print(f"✅ {len(key_metrics)} metrics, {len(prices)} prices")

# Feature selection
id_cols = ["symbol", "cik", "date", "fiscal_year", "period", "reported_currency"]
numeric_cols = [c for c in key_metrics.columns if c not in id_cols]
metrics_np = key_metrics.select(numeric_cols).fill_null(0).to_numpy()

selector = VarianceThreshold(threshold=0.01)
selector.fit(metrics_np)
retained = [c for c, k in zip(numeric_cols, selector.get_support()) if k]

# Correlation filter
corr = np.abs(np.corrcoef(key_metrics.select(retained).fill_null(0).to_numpy(), rowvar=False))
drop = set()
for i in range(len(retained)):
    for j in range(i+1, len(retained)):
        if corr[i,j] > 0.9:
            drop.add(retained[j])
final_cols = [c for c in retained if c not in drop]
print(f"✅ {len(final_cols)} features")

# Score
scaler = StandardScaler()
scaled = scaler.fit_transform(key_metrics.select(final_cols).fill_null(0).to_numpy())
composite = scaled.mean(axis=1)
key_metrics = key_metrics.with_columns(pl.Series("composite_score", composite))

# Merge
merged = prices.sort(["symbol", "date"]).join_asof(
    key_metrics.select(["symbol", "date", "composite_score"]).sort(["symbol", "date"]),
    on="date", by="symbol", strategy="backward"
).drop_nulls("composite_score")
print(f"✅ {len(merged)} merged rows")

# Asset mappings
bundle_path = Path(__file__).parent / ".zipline/data/duckdb-bundle"
latest_bundle = sorted(bundle_path.glob("*"))[-1]
conn = sqlite3.connect(str(latest_bundle / "assets-7.sqlite"))
sid_to_symbol = dict(conn.execute("SELECT sid, symbol FROM equity_symbol_mappings").fetchall())
conn.close()

# Score lookup
scores_by_date = {}
for key, df in merged.group_by("date"):
    date = pd.Timestamp(key[0]).normalize()
    scores_by_date[date] = dict(zip(df["symbol"].to_list(), df["composite_score"].to_list()))
print(f"✅ {len(scores_by_date)} dates")

# Pipeline
class ScoreFactor(CustomFactor):
    window_length = 1
    inputs = [USEquityPricing.close]
    def compute(self, today, assets, out, close):
        day_scores = scores_by_date.get(today, {})
        for i, sid in enumerate(assets):
            sym = sid_to_symbol.get(sid)
            if sym and sym in day_scores:
                s = day_scores[sym]
                out[i] = 0.0 if np.isnan(s) else s
            else:
                out[i] = np.nan

def make_pipeline():
    return Pipeline(columns={"score": ScoreFactor()})

def initialize(context):
    attach_pipeline(make_pipeline(), "pipeline")
    context.day_count = 0

def before_trading_start(context, data):
    output = pipeline_output("pipeline").dropna()
    context.top_assets = output.sort_values("score", ascending=False).head(TOP_N).index.tolist()

def handle_data(context, data):
    context.day_count += 1
    if context.day_count % REBALANCE_DAYS != 1:
        return
    
    # Target positions
    targets = [a for a in context.top_assets if data.can_trade(a)][:TOP_N]
    target_set = set(targets)
    
    # Close positions not in targets
    for asset in list(context.portfolio.positions.keys()):
        if asset not in target_set and data.can_trade(asset):
            order_target_percent(asset, 0)
    
    # Equal weight new positions (total = 100%)
    if targets:
        weight = 1.0 / len(targets)
        for asset in targets:
            order_target_percent(asset, weight)

# Run
if scores_by_date:
    start = min(scores_by_date.keys())
    end = max(scores_by_date.keys())
    print(f"🚀 Backtest {start.date()} to {end.date()}...")
    
    result = run_algorithm(
        start=start, end=end,
        initialize=initialize,
        before_trading_start=before_trading_start,
        handle_data=handle_data,
        capital_base=1_000_000,
        data_frequency="daily",
        bundle="duckdb-bundle",
        trading_calendar=get_calendar('NYSE'),
    )
    print("✅ Done!")
    
    # Save
    fn = f"results/backtest_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    result.to_json(fn, orient='index', date_format='iso')
    
    # Signals
    latest = scores_by_date[max(scores_by_date.keys())]
    price_map = dict(zip(
        prices.filter(pl.col("date") == prices["date"].max())["symbol"].to_list(),
        prices.filter(pl.col("date") == prices["date"].max())["close"].to_list()
    ))
    
    valid = [(s, sc) for s, sc in latest.items() 
             if not np.isnan(sc) and not s.endswith(('F','Y','W')) and '-' not in s
             and 1 <= price_map.get(s, 0) <= 500]
    valid.sort(key=lambda x: x[1], reverse=True)
    
    top = []
    seen = set()
    for sym, sc in valid:
        if sym[:4] in seen: continue
        seen.add(sym[:4])
        top.append((sym, sc))
        if len(top) >= TOP_N: break
    
    if top:
        mn = min(s for _, s in top)
        shifted = [s - mn + 1e-6 for _, s in top]
        total = sum(shifted)
        signals = {sym: {'symbol': sym, 'action': 'BUY', 'score': float(sc), 'target_weight': sh/total}
                   for (sym, sc), sh in zip(top, shifted)}
        with open('results/latest_signals.json', 'w') as f:
            json.dump({'date': str(pd.Timestamp.now().date()), 'signals': signals}, f, indent=2)
        print(f"📊 {len(signals)} signals saved")
    
    # Summary
    print(f"\n📈 Results:")
    print(f"Return: {(result['portfolio_value'].iloc[-1]/result['portfolio_value'].iloc[0]-1)*100:.2f}%")
    print(f"Final: ${result['portfolio_value'].iloc[-1]:,.2f}")
    print(f"Positions: {result['longs_count'].iloc[-1]:.0f}")
    print(f"Leverage: {result['gross_leverage'].iloc[-1]:.2f}x")
    print(f"Drawdown: {result['max_drawdown'].iloc[-1]*100:.2f}%")
