#!/home/simon0099/Lodon/trading_env/bin/python
"""
Daily trade: generate signals from factor model, execute directly via IB Gateway.
Replaces: daily_signal_generator.py → SQS → execution_engine.py
"""
import json
import time
import logging
import threading
import duckdb
import numpy as np
from pathlib import Path
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.tag_value import TagValue

BASE = Path(__file__).parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f"daily_trade_{datetime.now():%Y%m%d}.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TOP_N = 20
MAX_LEVERAGE = 1.5
MIN_PRICE, MAX_PRICE = 1, 10000
MIN_DOLLAR_VOLUME = 1_000_000
IB_PORT = 4001


def load_blocklist():
    bl = BASE / "ibkr_blocklist.txt"
    if bl.exists():
        return {l.strip() for l in bl.read_text().splitlines() if l.strip() and not l.startswith("#")}
    return set()


def resolve_contracts(symbols):
    """Connect to IB Gateway briefly to resolve conId and primaryExchange for each symbol."""
    class Resolver(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.contracts = {}
            self.connected = False

        def nextValidId(self, orderId):
            self.connected = True

        def contractDetails(self, reqId, contractDetails):
            c = contractDetails.contract
            self.contracts[c.symbol] = {"conId": c.conId, "primaryExchange": c.primaryExchange}

        def contractDetailsEnd(self, reqId):
            pass

        def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
            if errorCode not in [2104, 2106, 2158, 2119]:
                log.error(f"Resolver error {errorCode}: {errorString}")

    resolver = Resolver()
    resolver.connect("127.0.0.1", IB_PORT, clientId=2)
    threading.Thread(target=resolver.run, daemon=True).start()

    for _ in range(20):
        if resolver.connected:
            break
        time.sleep(0.5)

    if not resolver.connected:
        log.warning("Could not connect to IB for contract resolution, proceeding without")
        resolver.disconnect()
        return {}

    # IB uses spaces where FMP uses dashes for share classes (BRK-B -> BRK B)
    def to_ib_symbol(sym):
        parts = sym.rsplit('-', 1)
        if len(parts) == 2 and len(parts[1]) <= 2 and parts[1].isalpha():
            return f"{parts[0]} {parts[1]}"
        return sym

    for i, sym in enumerate(symbols):
        c = Contract()
        c.symbol = to_ib_symbol(sym)
        c.secType = 'STK'
        c.exchange = 'SMART'
        c.currency = 'USD'
        resolver.reqContractDetails(i, c)

    time.sleep(5)
    resolver.disconnect()
    # Map resolved IB symbols back to FMP symbols
    ib_to_fmp = {to_ib_symbol(s): s for s in symbols if to_ib_symbol(s) != s}
    resolved = {}
    for sym, data in resolver.contracts.items():
        resolved[ib_to_fmp.get(sym, sym)] = data
    log.info(f"Resolved {len(resolved)}/{len(symbols)} contracts")
    return resolved


def generate_signals():
    """Generate BUY signals from weekly factor model + fresh prices."""
    factors_dir = BASE / "DynamicWeeklyFactors"
    with open(factors_dir / "factors_latest.json") as f:
        data = json.load(f)
        factors = data["factors"]
        directions = np.array(data.get("directions", [1] * len(factors)))

    db_dir = BASE / "FMP_Databases"
    con = duckdb.connect(str(db_dir / "company_metrics__1yr.db"))
    metrics = con.execute("SELECT * FROM key_metrics").fetchdf()
    con.close()

    con = duckdb.connect(str(db_dir / "company_prices_daily_1yr.db"))
    prices = con.execute("""
        SELECT symbol, close, close * volume AS dollar_volume FROM prices
        WHERE date = (SELECT MAX(date) FROM (SELECT date FROM prices GROUP BY date HAVING COUNT(*) > 100))
    """).fetchdf()
    all_prices = con.execute("SELECT symbol, date, close FROM prices").fetchdf()
    con.close()

    metrics = metrics[~metrics["exchange"].isin(["OTC", "PNK"])]
    if metrics["reported_currency"].isna().all():
        cache_path = db_dir / "reported_currency_cache.json"
        if cache_path.exists():
            with open(cache_path) as f:
                currency_map = json.load(f)
            metrics = metrics[metrics["symbol"].map(lambda s: currency_map.get(s)) == "USD"]
        else:
            log.warning("No reported_currency data and no cache - skipping currency filter")
    else:
        metrics = metrics[metrics["reported_currency"] == "USD"]
    metrics = metrics.sort_values("date").groupby("symbol").last().reset_index()

    # Compute momentum factors from price history (matches backtest logic)
    mom_factors = [f for f in factors if f.startswith("mom_")]
    if mom_factors:
        import pandas as pd
        all_prices["date"] = pd.to_datetime(all_prices["date"])
        price_pivot = all_prices.pivot_table(index="date", columns="symbol", values="close")
        mom_map = {"mom_3m": 63, "mom_6m": 126, "mom_12m": 252}
        for name in mom_factors:
            ret = price_pivot.pct_change(mom_map[name]).iloc[-1]
            metrics[name] = metrics["symbol"].map(ret).values

    X = metrics[factors].fillna(0).values
    lo, hi = np.percentile(X, [2, 98], axis=0)
    X = np.clip(X, lo, hi)
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaled = scaler.fit_transform(X)
    metrics["score"] = (scaled * directions).mean(axis=1)

    blocklist = load_blocklist()
    df = metrics.merge(prices, on="symbol")
    df = df[(df["close"] >= MIN_PRICE) & (df["close"] <= MAX_PRICE)
            & (~df["symbol"].isin(blocklist)) & (df["dollar_volume"] >= MIN_DOLLAR_VOLUME)]
    df = df.nlargest(TOP_N, "score")

    total = df["score"].sum()
    contract_map = resolve_contracts(df["symbol"].tolist())
    unresolved = [s for s in df["symbol"] if s not in contract_map]
    if unresolved:
        log.warning(f"Excluding {len(unresolved)} unresolved symbols: {unresolved}")
        df = df[df["symbol"].isin(contract_map.keys())]
        total = df["score"].sum()

    signals = [
        {"symbol": r["symbol"], "action": "BUY", "score": r["score"],
         "target_weight": MAX_LEVERAGE * r["score"] / total if total else 0,
         "conId": contract_map[r["symbol"]]["conId"],
         "primaryExchange": contract_map[r["symbol"]]["primaryExchange"]}
        for _, r in df.iterrows()
    ]
    total_weight = sum(s["target_weight"] for s in signals)
    if round(total_weight, 4) > MAX_LEVERAGE:
        raise ValueError(f"Total weight {total_weight:.2%} exceeds {MAX_LEVERAGE:.0%} - aborting")

    out = BASE / "daily_signals" / f"signals_{datetime.now():%Y%m%d}.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump({"date": datetime.now().isoformat(), "signals": signals}, f, indent=2)
    log.info(f"Generated {len(signals)} signals (leverage: {total_weight:.2f}x)")
    fallback_prices = dict(zip(df["symbol"], df["close"]))
    return signals, fallback_prices


class Executor(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.next_order_id = None
        self.connected = False
        self.account_value = None
        self.prices = {}
        self.positions = {}
        self.pending_orders = {}
        self.next_mkt_data_id = 900000
        self.trades_file = LOG_DIR / "trades.json"

    def nextValidId(self, orderId):
        self.next_order_id = orderId
        self.connected = True
        log.info(f"Connected, next order ID: {orderId}")

    def updateAccountValue(self, key, val, currency, accountName):
        if key == 'NetLiquidation' and currency == 'USD':
            self.account_value = float(val)
            log.info(f"Account value: ${self.account_value:,.2f}")

    def updatePortfolio(self, contract, position, marketPrice, marketValue,
                        averageCost, unrealizedPNL, realizedPNL, accountName):
        # Normalize IB symbol (e.g. "BRK B") back to FMP format ("BRK-B")
        sym = contract.symbol.replace(' ', '-') if ' ' in contract.symbol else contract.symbol
        self.positions[sym] = position  # keep float for fractional detection

    def tickPrice(self, reqId, tickType, price, attrib):
        if price <= 0:
            return
        if tickType in (1, 66):
            self.prices.setdefault(reqId, {})['bid'] = price
        elif tickType in (2, 67):
            self.prices.setdefault(reqId, {})['ask'] = price
        elif tickType in (4, 68):
            self.prices.setdefault(reqId, {})['last'] = price

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, *args):
        log.info(f"Order {orderId}: {status}, filled={filled}, avg={avgFillPrice}")
        if status in ('Filled', 'Cancelled', 'ApiCancelled'):
            if orderId in self.pending_orders:
                info = self.pending_orders.pop(orderId)
                self.log_trade({
                    'timestamp': datetime.now().isoformat(),
                    'order_id': orderId, 'symbol': info['symbol'],
                    'action': info['action'], 'quantity': int(filled),
                    'price': avgFillPrice, 'status': status.lower()
                })

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        log.error(f"Error {errorCode}: {errorString}")

    def log_trade(self, trade):
        trades = []
        if self.trades_file.exists():
            with open(self.trades_file) as f:
                trades = json.load(f)
        trades.append(trade)
        with open(self.trades_file, 'w') as f:
            json.dump(trades, f, indent=2)

    def connect_and_wait(self):
        log.info(f"Connecting to IB Gateway at 127.0.0.1:{IB_PORT}")
        self.connect("127.0.0.1", IB_PORT, clientId=1)
        threading.Thread(target=self.run, daemon=True).start()
        for _ in range(60):
            if self.connected:
                self.reqAccountUpdates(True, "")
                time.sleep(3)
                return True
            time.sleep(1)
        return False

    def _get_live_price(self, contract):
        """Try snapshot market data; return price or None."""
        self.reqMarketDataType(3)
        mkt_req_id = self.next_mkt_data_id
        self.next_mkt_data_id += 1
        self.reqMktData(mkt_req_id, contract, "", True, False, [])

        for _ in range(30):
            time.sleep(0.5)
            data = self.prices.get(mkt_req_id, {})
            if data.get('bid') or data.get('ask') or data.get('last'):
                break

        data = self.prices.get(mkt_req_id, {})
        bid, ask = data.get('bid'), data.get('ask')
        return (bid + ask) / 2 if bid and ask else (bid or ask or data.get('last'))

    @staticmethod
    def _to_ib_symbol(sym):
        parts = sym.rsplit('-', 1)
        if len(parts) == 2 and len(parts[1]) <= 2 and parts[1].isalpha():
            return f"{parts[0]} {parts[1]}"
        return sym

    def execute_signal(self, signal, fallback_prices=None):
        contract = Contract()
        contract.symbol = self._to_ib_symbol(signal['symbol'])
        contract.secType = 'STK'
        contract.exchange = 'SMART'
        contract.currency = 'USD'
        if signal.get('conId'):
            contract.conId = signal['conId']
        if signal.get('primaryExchange'):
            contract.primaryExchange = signal['primaryExchange']

        action = signal.get('action', 'BUY')
        target_weight = signal.get('target_weight', 0)
        current_pos = self.positions.get(signal['symbol'], 0)

        # Full liquidation SELLs don't need a price
        if action == 'SELL':
            if current_pos <= 0:
                log.warning(f"No position to sell for {signal['symbol']}")
                return
            target_shares = 0
            diff = -current_pos
        else:
            if not self.account_value:
                log.error("No account value available")
                return

            price = self._get_live_price(contract)
            if not price or price <= 0:
                # Fall back to FMP close price from signal generation
                price = (fallback_prices or {}).get(signal['symbol'])
                if price and price > 0:
                    log.warning(f"Using FMP fallback price for {signal['symbol']}: {price}")
                else:
                    log.error(f"No price for {signal['symbol']}")
                    return

            target_shares = int(self.account_value * target_weight / price)
            diff = target_shares - int(current_pos)

        if diff == 0:
            log.info(f"Already at target for {signal['symbol']}: {current_pos} shares")
            return

        order = Order()
        order.action = 'BUY' if diff > 0 else 'SELL'
        order.totalQuantity = abs(diff)
        order.orderType = 'MKT'
        order.tif = 'DAY'
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        if order.totalQuantity == int(order.totalQuantity):
            order.algoStrategy = 'Adaptive'
            order.algoParams = [TagValue('adaptivePriority', 'Normal')]

        log.info(f"Executing: {order.action} {order.totalQuantity} {contract.symbol} "
                 f"(current={current_pos}, target={target_shares})")
        self.pending_orders[self.next_order_id] = {'symbol': contract.symbol, 'action': order.action}
        self.placeOrder(self.next_order_id, contract, order)
        self.next_order_id += 1
        time.sleep(0.5)

    def wait_for_fills(self, timeout=300):
        """Wait until all pending orders reach a terminal state, or timeout."""
        start = time.time()
        while self.pending_orders and (time.time() - start) < timeout:
            time.sleep(1)
        if self.pending_orders:
            syms = [v['symbol'] for v in self.pending_orders.values()]
            log.warning(f"Timed out with {len(self.pending_orders)} orders still pending: {syms}")
        else:
            log.info(f"All orders filled in {time.time() - start:.0f}s")


def _market_open():
    """Return True if current time is before 3:30pm ET (leave 30min buffer before close)."""
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.time() < dtime(15, 30)


def execute(signals, fallback_prices=None):
    """Connect to IB Gateway and execute all signals, retrying unfilled SELLs hourly."""
    executor = Executor()
    if not executor.connect_and_wait():
        log.error("Failed to connect to IB Gateway")
        return False

    # SELL positions no longer in signal set (including fractional remnants)
    new_symbols = {s['symbol'] for s in signals}
    for sym, pos in executor.positions.items():
        if pos > 0 and sym not in new_symbols:
            signals.append({"symbol": sym, "action": "SELL", "score": 0, "target_weight": 0})
            log.info(f"Added SELL for exited position: {sym}")

    log.info(f"Portfolio: {len(executor.positions)} IB positions, "
             f"{sum(1 for p in executor.positions.values() if p > 0)} long, "
             f"target {len(new_symbols)} positions")

    # Label signals based on current positions
    for s in signals:
        if s['action'] == 'SELL':
            continue
        s['action'] = 'BUY' if executor.positions.get(s['symbol'], 0) == 0 else 'REBAL'

    sells = [s for s in signals if s['action'] == 'SELL']
    new_buys = sorted([s for s in signals if s['action'] == 'BUY'], key=lambda s: s['score'])
    rebals = [s for s in signals if s['action'] == 'REBAL']
    log.info(f"Executing {len(sells)} SELLs, {len(new_buys)} new BUYs, {len(rebals)} rebalances")

    # Execute SELLs first (no price needed for full liquidations)
    for signal in sells:
        executor.execute_signal(signal, fallback_prices)
    if sells:
        log.info("Waiting for SELL fills before placing BUYs...")
        executor.wait_for_fills(timeout=120)
        executor.reqAccountUpdates(True, "")
        time.sleep(3)

    # Count unfilled SELLs — hold back that many lowest-scored BUYs
    unfilled_sells = [v['symbol'] for v in executor.pending_orders.values() if v['action'] == 'SELL']
    held_back = new_buys[:len(unfilled_sells)]  # lowest-scored (list is sorted ascending)
    to_execute = new_buys[len(unfilled_sells):]
    if held_back:
        log.info(f"Holding back {len(held_back)} BUYs due to unfilled SELLs: "
                 f"{[s['symbol'] for s in held_back]}")

    for signal in to_execute + rebals:
        executor.execute_signal(signal, fallback_prices)
    executor.wait_for_fills(timeout=300)

    # Hourly retry loop for unfilled SELLs + held-back BUYs
    while held_back and _market_open():
        log.info(f"Sleeping 1 hour — {len(held_back)} BUYs held back, "
                 f"unfilled SELLs: {unfilled_sells}")
        time.sleep(3600)

        # Check which SELLs filled during the sleep (orderStatus pops them)
        still_unfilled = [s for s in unfilled_sells
                          if any(v['symbol'] == s for v in executor.pending_orders.values())]
        newly_filled = len(unfilled_sells) - len(still_unfilled)

        if newly_filled > 0:
            log.info(f"{newly_filled} SELLs filled during wait, placing held-back BUYs")
            executor.reqAccountUpdates(True, "")
            time.sleep(3)
            release = held_back[-newly_filled:]  # release highest-scored first
            held_back = held_back[:-newly_filled]
            for signal in release:
                executor.execute_signal(signal, fallback_prices)
            executor.wait_for_fills(timeout=300)

        unfilled_sells = still_unfilled
        if not unfilled_sells:
            break

    if held_back:
        log.warning(f"Market closing — {len(held_back)} BUYs never placed: "
                    f"{[s['symbol'] for s in held_back]}")

    executor.disconnect()
    log.info("Done")
    return True


def is_market_holiday(dt=None):
    """Check if today is a US market holiday (NYSE schedule)."""
    from datetime import date
    if dt is None:
        dt = date.today()
    year = dt.year
    holidays = []
    # New Year's Day
    d = date(year, 1, 1)
    holidays.append(d if d.weekday() < 5 else date(year, 1, 2) if d.weekday() == 6 else date(year - 1, 12, 31))
    # MLK Day: 3rd Monday in January
    holidays.append(date(year, 1, 15 + (7 - date(year, 1, 15).weekday()) % 7))
    # Presidents Day: 3rd Monday in February
    holidays.append(date(year, 2, 15 + (7 - date(year, 2, 15).weekday()) % 7))
    # Good Friday: Easter - 2 days
    a, b, c = year % 19, year % 4, year % 7
    d_val = (19 * a + 24) % 30
    e = (2 * b + 4 * c + 6 * d_val + 5) % 7
    day = 22 + d_val + e
    easter = date(year, 3, day) if day <= 31 else date(year, 4, day - 31)
    from datetime import timedelta
    holidays.append(easter - timedelta(days=2))
    # Memorial Day: last Monday in May
    d = date(year, 5, 31)
    holidays.append(d - timedelta(days=(d.weekday()) % 7))
    # Juneteenth
    d = date(year, 6, 19)
    holidays.append(d if d.weekday() < 5 else d + timedelta(days=(7 - d.weekday())) if d.weekday() == 6 else d - timedelta(days=1))
    # Independence Day
    d = date(year, 7, 4)
    holidays.append(d if d.weekday() < 5 else d + timedelta(days=1) if d.weekday() == 6 else d - timedelta(days=1))
    # Labor Day: 1st Monday in September
    holidays.append(date(year, 9, 1 + (7 - date(year, 9, 1).weekday()) % 7))
    # Thanksgiving: 4th Thursday in November
    first_thu = date(year, 11, 1 + (3 - date(year, 11, 1).weekday()) % 7)
    holidays.append(first_thu + timedelta(weeks=3))
    # Christmas
    d = date(year, 12, 25)
    holidays.append(d if d.weekday() < 5 else d + timedelta(days=1) if d.weekday() == 6 else d - timedelta(days=1))
    return dt in holidays


if __name__ == "__main__":
    log.info("=== Daily Trade ===")
    if is_market_holiday():
        log.info("Market holiday — skipping.")
    else:
        signals, fallback_prices = generate_signals()
        execute(signals, fallback_prices)
