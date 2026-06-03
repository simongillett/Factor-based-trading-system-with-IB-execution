"""
Position sizing module.

Given a portfolio value and the factor model's top-N picks,
determines how many positions can be held with acceptable
rounding loss from whole-share constraints.
"""

import numpy as np
import pandas as pd
from prepare import DataCache, TEST_START, TEST_END, get_tradeable_symbols, ID_COLS
from train import (
    get_candidate_factors, add_momentum_factors, compute_ic_weights,
    make_score_fn, TOP_N, FORWARD_DAYS, REBALANCE_WEEKDAY, TRAIN_START,
    IC_THRESHOLD, IC_HALFLIFE, MIN_PRICE, MAX_PRICE, MIN_DV
)


def compute_effective_positions(portfolio_value, top_n, score_fn, rebal_dates,
                                forward_days, data, min_price, max_price, min_dv,
                                max_rounding_loss_pct=5.0):
    """
    Simulate position sizing with whole-share rounding.

    Returns dict with:
      - mean_positions: avg positions actually held per rebalance
      - mean_rounding_loss_pct: avg % of capital lost to rounding
      - mean_cash_drag_pct: avg % sitting in cash
      - positions_per_date: list of (date, n_positions, rounding_loss_pct)
      - optimal_n: largest top_n where mean rounding loss < max_rounding_loss_pct
    """
    results = []

    for date in rebal_dates:
        if date < TEST_START or date > TEST_END:
            continue
        idx = data.date_idx.get(date)
        if idx is None or idx + forward_days >= len(data.trading_dates):
            continue
        fwd_date = data.trading_dates[idx + forward_days]

        available = data.metrics[data.metrics["date"] <= date]
        latest = available.groupby("symbol").last()
        tradeable = get_tradeable_symbols(data, date, fwd_date, min_price, max_price, min_dv)
        candidates = latest.loc[latest.index.isin(tradeable)]
        if len(candidates) < top_n:
            continue

        scores = score_fn(candidates, data, date).dropna()
        if len(scores) < top_n:
            continue

        top_syms = scores.nlargest(top_n).index.tolist()
        prices = data.price_pivot.loc[date, top_syms].dropna()
        n_actual = len(prices)
        if n_actual == 0:
            continue

        # Equal-weight target allocation
        target_per_position = portfolio_value / n_actual
        shares = np.floor(target_per_position / prices.values).astype(int)
        invested = (shares * prices.values).sum()
        cash_left = portfolio_value - invested

        # Positions with 0 shares
        held = int((shares > 0).sum())
        rounding_loss = (cash_left / portfolio_value) * 100

        results.append({
            "date": date,
            "n_target": n_actual,
            "n_held": held,
            "rounding_loss_pct": round(rounding_loss, 2),
            "median_price": round(float(prices.median()), 2),
        })

    if not results:
        return {"error": "no valid rebalance dates"}

    df = pd.DataFrame(results)
    return {
        "portfolio_value": portfolio_value,
        "target_top_n": top_n,
        "mean_positions_held": round(df["n_held"].mean(), 1),
        "mean_rounding_loss_pct": round(df["rounding_loss_pct"].mean(), 2),
        "min_positions_held": int(df["n_held"].min()),
        "max_positions_held": int(df["n_held"].max()),
        "median_stock_price": round(df["median_price"].median(), 2),
        "n_rebalances": len(df),
        "details": df,
    }


def find_optimal_top_n(portfolio_value, score_fn, rebal_dates, forward_days, data,
                       min_price, max_price, min_dv,
                       max_rounding_loss_pct=5.0, search_range=range(20, 120, 5)):
    """
    Find the largest top_n where mean rounding loss stays below threshold.
    """
    best_n = search_range.start
    for n in search_range:
        result = compute_effective_positions(
            portfolio_value, n, score_fn, rebal_dates, forward_days, data,
            min_price, max_price, min_dv, max_rounding_loss_pct
        )
        if "error" in result:
            continue
        if result["mean_rounding_loss_pct"] <= max_rounding_loss_pct:
            best_n = n
        else:
            break
    return best_n


def main():
    data = DataCache.get()

    # Replicate factor selection from train.py
    train_start = pd.Timestamp(TRAIN_START)
    rebal_dates = data.all_weekdays[REBALANCE_WEEKDAY][::2]  # biweekly
    train_dates = [d for d in rebal_dates if train_start <= d < TEST_START]

    train_metrics = data.metrics[data.metrics["date"] < TEST_START]
    candidates = get_candidate_factors(train_metrics)
    mom_factors = add_momentum_factors(data)
    candidates = candidates + mom_factors

    sel_factors, ic_weights = compute_ic_weights(data, candidates, train_dates)
    if not sel_factors:
        print("ERROR: no factors selected")
        return

    score_fn = make_score_fn(sel_factors, ic_weights)
    test_dates = [d for d in rebal_dates if TEST_START <= d <= TEST_END]

    # Analyze multiple portfolio sizes
    print("\n" + "=" * 70)
    print("POSITION SIZING ANALYSIS")
    print("=" * 70)

    for capital in [25_000, 50_000, 75_000, 100_000]:
        print(f"\n{'─' * 70}")
        print(f"Portfolio: ${capital:,.0f}  |  Target positions: {TOP_N}")
        print(f"{'─' * 70}")

        result = compute_effective_positions(
            capital, TOP_N, score_fn, test_dates, FORWARD_DAYS, data,
            MIN_PRICE, MAX_PRICE, MIN_DV
        )
        if "error" in result:
            print(f"  Error: {result['error']}")
            continue

        print(f"  Positions held:      {result['mean_positions_held']:.0f} avg "
              f"(min {result['min_positions_held']}, max {result['max_positions_held']})")
        print(f"  Rounding loss:       {result['mean_rounding_loss_pct']:.1f}% avg cash drag")
        print(f"  Median stock price:  ${result['median_stock_price']:.2f}")

        # Find optimal N for this capital
        optimal = find_optimal_top_n(
            capital, score_fn, test_dates, FORWARD_DAYS, data,
            MIN_PRICE, MAX_PRICE, MIN_DV,
            max_rounding_loss_pct=5.0
        )
        print(f"  Optimal top_n (≤5% loss): {optimal}")

    # Detailed comparison: $50k at various top_n
    print(f"\n{'=' * 70}")
    print("DETAILED: $50,000 portfolio at various top_n")
    print(f"{'=' * 70}")
    print(f"{'top_n':>6} {'held':>6} {'loss%':>7} {'$/pos':>8}")
    print(f"{'─' * 35}")

    for n in [20, 30, 40, 50, 60, 75, 100]:
        result = compute_effective_positions(
            50_000, n, score_fn, test_dates, FORWARD_DAYS, data,
            MIN_PRICE, MAX_PRICE, MIN_DV
        )
        if "error" in result:
            continue
        per_pos = 50_000 / n
        print(f"{n:>6} {result['mean_positions_held']:>6.0f} "
              f"{result['mean_rounding_loss_pct']:>6.1f}% ${per_pos:>7,.0f}")


if __name__ == "__main__":
    main()
