# Trading System Steering Document

## Known Issues & Fixes Applied

### 1. Holiday/Weekend Price Data Poisoning
**Problem:** FMP inserts price data for instruments that trade 24/7 (e.g., USDE) on weekends and holidays. This causes `MAX(date)` to return a date with only 1 symbol, collapsing the universe to nothing and generating 0 signals.

**Fix:** The price query now selects the most recent date with >100 symbols:
```sql
SELECT MAX(date) FROM (SELECT date FROM prices GROUP BY date HAVING COUNT(*) > 100)
```

**Prevention:** The holiday guard in `daily_trade.py` skips execution entirely on NYSE holidays. The FMP daily update still runs (data is still useful for backfill), but signal generation won't act on thin data.

### 2. Market Holiday Detection
**Fix:** `is_market_holiday()` in `daily_trade.py` covers: New Year's, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas. Handles observed dates when holidays fall on weekends.

### 3. IB Symbol Mapping (FMP → IB)
**Problem:** FMP uses dashes for share classes (`BRK-B`), IB uses spaces (`BRK B`).

**Fix:** `to_ib_symbol()` in `resolve_contracts` converts `X-Y` → `X Y` when the suffix is 1-2 alpha characters. Reverse mapping applied when reading positions back.

### 4. Fractional Share Sells
**Problem:** IB API rejects fractional sell orders (error 10243). SCM had 6.7409 shares.

**Status:** NOT FIXED in code. Fractional positions must be sold manually via TWS/desktop. Consider adding `math.floor()` to sell quantities, accepting the residual dust.

### 5. Order Attributes (eTradeOnly)
**Problem:** IB API 9.81+ rejects orders unless `eTradeOnly = False` and `firmQuoteOnly = False` are explicitly set.

**Fix:** Both are set in `execute_signal()`. Any ad-hoc order scripts must include these.

### 6. Cron Timing vs Market Data
**Problem:** FMP daily update runs at 06:00 ET (before market open). Signal generation runs at 09:30 ET. On the first trading day after a holiday weekend, the 06:00 update won't have new data yet — but the `HAVING COUNT(*) > 100` fix ensures it falls back to the last real trading day.

---

## Execution Schedule (crontab)

| Time | Job | Python |
|------|-----|--------|
| 06:00 daily | FMP daily_update.py | trading_env/bin/python3 |
| 09:00 Mon-Fri | ibgateway_healthcheck.sh | bash |
| 09:30 Mon-Fri | daily_trade.py | /home/simon0099/bin/python3 |
| 20:00 Sunday | run_weekly_backtest.sh | bash |
| 02:00 1st of month | log_rotate.sh | bash |

**Note:** `daily_trade.py` shebang points to `trading_env/bin/python` but ibapi is installed at `/home/simon0099/lib/python3.12/site-packages/`. The cron correctly uses `/home/simon0099/bin/python3`.

---

## Pre-Trade Checklist (manual intervention needed if)

- [ ] IB Gateway is down (check port 4001)
- [ ] FMP data hasn't updated (check `FMP_Databases/daily_update.log` timestamp)
- [ ] Fractional positions exist that need manual liquidation
- [ ] New symbols with dashes appear that aren't share classes (add to exclusion if needed)
- [ ] `reqGlobalCancel()` needed to clear stale orders from prior failed runs

---

## Architecture Constraints

- **No external holiday calendar dependency** — holiday logic is self-contained
- **IB Gateway must be running** — no fallback execution path
- **Single account** — U22836495
- **Max leverage:** 1.5x
- **Universe:** ~8,800 US equities from FMP, filtered to >$1M daily dollar volume
- **Rebalance:** Daily at 09:30 ET, sells before buys
- **Algo:** Adaptive (Normal priority) for whole-share orders; no algo for fractional

---

## Ad-Hoc Order Template

When placing orders outside of `daily_trade.py`:
```python
o = Order()
o.action = 'BUY'  # or 'SELL'
o.totalQuantity = N
o.orderType = 'MKT'
o.tif = 'DAY'
o.eTradeOnly = False
o.firmQuoteOnly = False
o.algoStrategy = 'Adaptive'
o.algoParams = [TagValue('adaptivePriority', 'Normal')]
```

Always use `clientId=1` to share the order ID sequence with `daily_trade.py`.
