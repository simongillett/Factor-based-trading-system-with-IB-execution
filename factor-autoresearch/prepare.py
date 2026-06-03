"""
Fixed data loading and evaluation for factor autoresearch.
DO NOT MODIFY.
"""

import time
import numpy as np
import pandas as pd
import duckdb
from pathlib import Path
from scipy.stats import spearmanr

ID_COLS = {"symbol", "cik", "date", "fiscal_year", "period", "reported_currency", "exchange"}

# Fixed test period
TEST_START = pd.Timestamp("2024-04-01")
TEST_END = pd.Timestamp("2026-04-17")


class DataCache:
    """Singleton — loads all data once, reused across calls."""
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        t0 = time.time()
        db_dir = Path(__file__).parent.parent / "FMP_Databases"

        con = duckdb.connect(str(db_dir / "company_metrics_10yr.db"), read_only=True)
        self.metrics = con.execute("""
            SELECT * FROM key_metrics
            WHERE exchange NOT IN ('OTC','PNK','')
              AND reported_currency = 'USD'
        """).fetchdf()
        con.close()

        con = duckdb.connect(str(db_dir / "company_prices_daily.db"), read_only=True)
        prices = con.execute("SELECT symbol, date, close, volume FROM prices").fetchdf()
        con.close()

        self.metrics["date"] = pd.to_datetime(self.metrics["date"])
        prices["date"] = pd.to_datetime(prices["date"])
        prices["dv"] = prices["close"] * prices["volume"]

        self.price_pivot = prices.pivot_table(index="date", columns="symbol", values="close")
        dv_pivot = prices.pivot_table(index="date", columns="symbol", values="dv")
        self.dv_rolling = dv_pivot.rolling(20, min_periods=5).mean()
        self.trading_dates = sorted(self.price_pivot.index)
        self.date_idx = {d: i for i, d in enumerate(self.trading_dates)}

        # Precompute rebalance date lists
        self.mondays = [d for d in self.trading_dates if d.weekday() == 0]
        self.all_weekdays = {
            0: self.mondays,
            1: [d for d in self.trading_dates if d.weekday() == 1],
            2: [d for d in self.trading_dates if d.weekday() == 2],
            3: [d for d in self.trading_dates if d.weekday() == 3],
            4: [d for d in self.trading_dates if d.weekday() == 4],
        }

        # Numeric factor columns available
        self.factor_columns = [c for c in self.metrics.columns if c not in ID_COLS]

        elapsed = time.time() - t0
        print(f"Data loaded: {len(self.metrics)} metrics, {len(prices)} prices, "
              f"{len(self.factor_columns)} factors in {elapsed:.1f}s")


def get_tradeable_symbols(data, date, fwd_date, min_price=1, max_price=10000, min_dv=1_000_000):
    """Return list of symbols tradeable on date with valid forward price."""
    dv_today = data.dv_rolling.loc[date] if date in data.dv_rolling.index else pd.Series(dtype=float)
    syms = []
    for s in data.price_pivot.columns:
        if s not in data.price_pivot.columns:
            continue
        p = data.price_pivot.at[date, s]
        pf = data.price_pivot.at[fwd_date, s] if fwd_date in data.price_pivot.index else np.nan
        dv = dv_today.get(s, 0)
        if pd.notna(p) and pd.notna(pf) and min_price <= p <= max_price and dv >= min_dv:
            syms.append(s)
    return syms


def evaluate(score_fn, top_n, rebal_dates, forward_days, data=None,
             min_price=1, max_price=10000, min_dv=1_000_000):
    """
    Pure-math backtest. No leverage, equal weight.

    score_fn(latest_metrics_df, data, date) -> pd.Series indexed by symbol with scores.
        Higher score = more desirable stock.

    Returns weekly_returns array.
    """
    if data is None:
        data = DataCache.get()

    weekly_rets = []
    for date in rebal_dates:
        if date < TEST_START or date > TEST_END:
            continue
        idx = data.date_idx.get(date)
        if idx is None or idx + forward_days >= len(data.trading_dates):
            continue
        fwd_date = data.trading_dates[idx + forward_days]

        # Latest metrics per symbol as of this date
        available = data.metrics[data.metrics["date"] <= date]
        latest = available.groupby("symbol").last()

        # Tradeable universe
        tradeable = get_tradeable_symbols(data, date, fwd_date, min_price, max_price, min_dv)
        candidates = latest.loc[latest.index.isin(tradeable)]
        if len(candidates) < top_n:
            continue

        # Score
        scores = score_fn(candidates, data, date)
        scores = scores.dropna()
        if len(scores) < top_n:
            continue

        # Top N equal weight
        top_syms = scores.nlargest(top_n).index.tolist()
        ret = (data.price_pivot.loc[fwd_date, top_syms] / data.price_pivot.loc[date, top_syms] - 1).mean()
        weekly_rets.append(ret)

    return np.array(weekly_rets)


def compute_stats(rets):
    """Compute backtest statistics from array of periodic returns."""
    if len(rets) == 0:
        return {"sharpe": 0, "cagr_pct": 0, "max_drawdown_pct": 0,
                "total_return_pct": 0, "volatility_pct": 0, "n_weeks": 0}

    total = float(np.prod(1 + rets) - 1)
    years = len(rets) * 5 / 252  # assumes 5 trading days per period
    cagr = ((1 + total) ** (1 / years) - 1) * 100 if years > 0 and total > -1 else 0
    cum = np.cumprod(1 + rets)
    dd = float(((cum / np.maximum.accumulate(cum)) - 1).min() * 100)
    sharpe = float(np.sqrt(52) * rets.mean() / rets.std()) if rets.std() > 0 else 0
    vol = float(rets.std() * np.sqrt(52) * 100)

    return {
        "sharpe": round(sharpe, 3),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(dd, 2),
        "total_return_pct": round(total * 100, 2),
        "volatility_pct": round(vol, 1),
        "n_weeks": len(rets),
    }
