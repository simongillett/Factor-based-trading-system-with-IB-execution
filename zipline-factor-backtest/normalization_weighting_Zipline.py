"""
Factor backtest with IC-derived weights and rank-normalized scoring.
Optimized parameters from autoresearch (Sharpe 7.707).

Config:
- 21 factors (18 fundamental + 3 momentum), IC-weighted
- forward_days=150, top_n=75
- Biweekly Wednesday rebalance
- IC_HALFLIFE=13, IC_THRESHOLD=0.01
- Rank normalization (no z-scores)
- Equal-weight portfolio (no leverage)
"""

import os
from pathlib import Path
os.environ['ZIPLINE_ROOT'] = str(Path(__file__).parent / '.zipline')

import json
import duckdb
import polars as pl
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from sklearn.feature_selection import VarianceThreshold
from zipline.api import attach_pipeline, pipeline_output, order_target_percent
from zipline.pipeline import Pipeline, CustomFactor
from zipline.pipeline.data import USEquityPricing
from zipline import run_algorithm
from zipline.utils.calendar_utils import get_calendar
import sqlite3

# ── Optimized parameters ──────────────────────────────────────────────────

TOP_N = 75
MIN_DOLLAR_VOLUME = 1_000_000
FORWARD_DAYS = 150
IC_HALFLIFE = 13
IC_THRESHOLD = 0.01
VARIANCE_THRESHOLD = 0.01
CORRELATION_THRESHOLD = 0.9
TRAIN_START = pd.Timestamp("2018-04-01")
TRAIN_CUTOFF = pd.Timestamp("2024-04-01")
REBALANCE_WEEKDAY = 2  # Wednesday

# ── Load data ─────────────────────────────────────────────────────────────

print("🔗 Loading data...")
script_dir = Path(__file__).parent
db_dir = script_dir.parent / "FMP_Databases"

con = duckdb.connect(str(db_dir / "company_metrics_10yr.db"))
key_metrics = con.execute("""
    SELECT * FROM key_metrics
    WHERE exchange NOT IN ('OTC', 'PNK', '')
      AND reported_currency = 'USD'
""").pl()
con.close()

con = duckdb.connect(str(db_dir / "company_prices_daily.db"))
prices = con.execute("SELECT * FROM prices").pl()
con.close()

print(f"✅ {len(key_metrics)} metrics, {len(prices)} prices")

# ── Candidate factor selection ────────────────────────────────────────────

id_cols = {"symbol", "cik", "date", "fiscal_year", "period", "reported_currency", "exchange"}
numeric_cols = [c for c in key_metrics.columns if c not in id_cols]

selector = VarianceThreshold(threshold=VARIANCE_THRESHOLD)
selector.fit(key_metrics.select(numeric_cols).fill_null(0).to_numpy())
retained = [c for c, k in zip(numeric_cols, selector.get_support()) if k]

corr = np.abs(np.corrcoef(key_metrics.select(retained).fill_null(0).to_numpy(), rowvar=False))
drop = set()
for i in range(len(retained)):
    for j in range(i + 1, len(retained)):
        if corr[i, j] > CORRELATION_THRESHOLD:
            drop.add(retained[j])
candidate_factors = [c for c in retained if c not in drop]

# ── Add momentum factors ─────────────────────────────────────────────────

prices = prices.with_columns(
    (pl.col("close") * pl.col("volume")).alias("dollar_volume")
)

metrics_pd = key_metrics.to_pandas()
metrics_pd["date"] = pd.to_datetime(metrics_pd["date"])
prices_pd = prices.to_pandas()
prices_pd["date"] = pd.to_datetime(prices_pd["date"])

price_pivot = prices_pd.pivot_table(index="date", columns="symbol", values="close")

for period, name in [(63, "mom_3m"), (126, "mom_6m"), (252, "mom_12m")]:
    ret = price_pivot.pct_change(period)
    ret_stacked = ret.stack().rename(name)
    ret_stacked.index.names = ["date", "symbol"]
    metrics_pd = metrics_pd.merge(ret_stacked.reset_index(), on=["symbol", "date"], how="left")

mom_factors = ["mom_3m", "mom_6m", "mom_12m"]
candidate_factors = candidate_factors + mom_factors
print(f"✅ {len(candidate_factors)} candidate factors (incl momentum)")

# ── IC computation ────────────────────────────────────────────────────────

dv_pivot = prices_pd.pivot_table(index="date", columns="symbol", values=prices_pd.columns[prices_pd.columns.str.contains("dollar_volume")][0] if "dollar_volume" in prices_pd.columns else "volume")
# Recompute dv from prices_pd
prices_pd["dv"] = prices_pd["close"] * prices_pd["volume"]
dv_pivot = prices_pd.pivot_table(index="date", columns="symbol", values="dv")
dv_rolling = dv_pivot.rolling(20, min_periods=5).mean()
trading_dates = sorted(price_pivot.index)
date_idx = {d: i for i, d in enumerate(trading_dates)}

all_wednesdays = [d for d in trading_dates if d.weekday() == REBALANCE_WEEKDAY]
train_dates = [d for d in all_wednesdays if TRAIN_START <= d < TRAIN_CUTOFF]

print(f"📊 Computing IC over {len(train_dates)} training weeks...")

ic_history = {f: [] for f in candidate_factors}
n_valid = 0

for date in train_dates:
    idx = date_idx.get(date)
    if idx is None or idx + FORWARD_DAYS >= len(trading_dates):
        continue
    fwd_date = trading_dates[idx + FORWARD_DAYS]

    available = metrics_pd[metrics_pd["date"] <= date]
    latest = available.groupby("symbol").last()

    dv_today = dv_rolling.loc[date] if date in dv_rolling.index else pd.Series(dtype=float)
    valid_syms = [s for s in latest.index
                  if s in price_pivot.columns
                  and pd.notna(price_pivot.at[date, s])
                  and pd.notna(price_pivot.at[fwd_date, s])
                  and price_pivot.at[date, s] > 1
                  and dv_today.get(s, 0) >= MIN_DOLLAR_VOLUME]
    if len(valid_syms) < 30:
        continue

    fwd_ret = (price_pivot.loc[fwd_date, valid_syms] / price_pivot.loc[date, valid_syms] - 1).dropna()
    valid_syms = list(fwd_ret.index)
    if len(valid_syms) < 30:
        continue

    n_valid += 1
    for f in candidate_factors:
        vals = latest.loc[valid_syms, f]
        mask = vals.notna() & fwd_ret.notna()
        if mask.sum() < 20:
            ic_history[f].append(np.nan)
            continue
        ic, _ = spearmanr(vals[mask], fwd_ret[mask])
        ic_history[f].append(ic)

print(f"✅ IC computed over {n_valid} valid training weeks")

# Select factors
decay = np.array([2 ** (-(n_valid - 1 - i) / IC_HALFLIFE) for i in range(n_valid)])
selected_factors = []
ic_weights = []
for f in candidate_factors:
    ics = np.array(ic_history[f])
    valid = ~np.isnan(ics)
    if valid.sum() < 10:
        continue
    w = decay[valid]
    w = w / w.sum()
    mean_ic = np.average(ics[valid], weights=w)
    if abs(mean_ic) >= IC_THRESHOLD:
        selected_factors.append(f)
        ic_weights.append(mean_ic)
        print(f"  ✓ {f:40s}  IC={mean_ic:+.4f}")
    else:
        print(f"    {f:40s}  IC={mean_ic:+.4f}  (dropped)")

ic_weights = np.array(ic_weights)
ic_weights = ic_weights / np.abs(ic_weights).sum()
print(f"\n✅ Selected {len(selected_factors)} factors")

# ── Walk-forward scoring with rank normalization ──────────────────────────

# Convert metrics back to polars with momentum columns
key_metrics = pl.from_pandas(metrics_pd).sort("date")
unique_dates = key_metrics["date"].unique().sort().to_list()

scores_list = []
for current_date in unique_dates:
    current_data = key_metrics.filter(pl.col("date") == current_date)
    if len(current_data) == 0:
        continue

    # Rank-normalize each factor, apply IC weights
    score = np.zeros(len(current_data))
    for i, f in enumerate(selected_factors):
        vals = current_data[f].to_pandas()
        vals = vals.fillna(vals.median())
        ranked = vals.rank(pct=True).values
        score += ranked * ic_weights[i]

    scores_list.append(current_data.select(["symbol", "date"]).with_columns(
        pl.Series("composite_score", score)
    ))

scored_metrics = pl.concat(scores_list)
# Ensure date type matches prices
scored_metrics = scored_metrics.with_columns(pl.col("date").cast(pl.Date))
print(f"✅ Walk-forward scoring complete")

# ── Save factors for live trading ─────────────────────────────────────────

from datetime import datetime
factors_dir = script_dir.parent / "DynamicWeeklyFactors"
factors_dir.mkdir(exist_ok=True)
date_str = datetime.now().strftime("%Y%m%d")

with open(factors_dir / f"factors_{date_str}.json", "w") as f:
    json.dump({
        "factors": selected_factors,
        "ic_weights": ic_weights.tolist(),
        "date": date_str,
        "method": "IC-weighted rank-normalized",
        "forward_days": FORWARD_DAYS,
        "top_n": TOP_N,
        "rebalance": "biweekly Wednesday",
    }, f)
for name in ["factors_latest.json"]:
    (factors_dir / name).unlink(missing_ok=True)
    (factors_dir / name).symlink_to(f"factors_{date_str}.json")
print(f"💾 Saved factors to {factors_dir}")

# ── Merge with liquidity filter ───────────────────────────────────────────

merged = prices.sort(["symbol", "date"]).join_asof(
    scored_metrics.sort(["symbol", "date"]),
    on="date", by="symbol", strategy="backward"
).drop_nulls("composite_score").filter(
    pl.col("dollar_volume") >= MIN_DOLLAR_VOLUME
)
print(f"✅ {len(merged)} merged rows")

# Asset mappings
bundle_path = Path(__file__).parent / ".zipline/data/duckdb-bundle"
latest_bundle = sorted(bundle_path.glob("*"))[-1]
conn = sqlite3.connect(str(latest_bundle / "assets-7.sqlite"))
sid_to_symbol = dict(conn.execute("SELECT sid, symbol FROM equity_symbol_mappings").fetchall())
conn.close()

scores_by_date = {}
for key, df in merged.group_by("date"):
    date = pd.Timestamp(key[0]).normalize()
    scores_by_date[date] = dict(zip(df["symbol"].to_list(), df["composite_score"].to_list()))
print(f"✅ {len(scores_by_date)} dates")

# ── Zipline pipeline + execution ──────────────────────────────────────────

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
    context.last_rebalance = None

def before_trading_start(context, data):
    output = pipeline_output("pipeline").dropna()
    context.top_assets = output.sort_values("score", ascending=False).head(TOP_N).index.tolist()

def handle_data(context, data):
    today = context.get_datetime()
    # Biweekly Wednesday
    if today.weekday() != REBALANCE_WEEKDAY:
        return
    if context.last_rebalance is not None:
        days_since = (today - context.last_rebalance).days
        if days_since < 10:  # skip if less than ~2 weeks
            return
    context.last_rebalance = today

    targets = [a for a in context.top_assets if data.can_trade(a)][:TOP_N]
    target_set = set(targets)

    # Close ALL non-target positions
    for asset in list(context.portfolio.positions.keys()):
        if asset not in target_set:
            pos = context.portfolio.positions[asset]
            if pos.amount != 0:
                order_target_percent(asset, 0)

    # Equal weight, no leverage
    if targets:
        w = 1.0 / len(targets)
        for asset in targets:
            order_target_percent(asset, w)

# ── Run ───────────────────────────────────────────────────────────────────

if scores_by_date:
    start = max(min(scores_by_date.keys()), TRAIN_CUTOFF)
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

    fn = f"results/backtest_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    result.to_json(fn, orient='index', date_format='iso')

    # ── Generate signals ──────────────────────────────────────────────────

    latest = scores_by_date[max(scores_by_date.keys())]
    price_map = dict(zip(
        prices.filter(pl.col("date") == prices["date"].max())["symbol"].to_list(),
        prices.filter(pl.col("date") == prices["date"].max())["close"].to_list()
    ))

    blocklist = set()
    bl_path = script_dir.parent / "ibkr_blocklist.txt"
    if bl_path.exists():
        blocklist = {l.strip() for l in bl_path.read_text().splitlines() if l.strip() and not l.startswith("#")}

    valid = [(s, sc) for s, sc in latest.items()
             if not np.isnan(sc) and s not in blocklist
             and 1 <= price_map.get(s, 0) <= 10000]
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
