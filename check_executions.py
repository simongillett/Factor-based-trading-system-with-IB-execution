#!/usr/bin/env python3
"""Query executed trades within a date range from IBKR"""
import argparse
from datetime import datetime
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.execution import ExecutionFilter
import time

class ExecutionChecker(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.executions = []

    def execDetails(self, reqId, contract, execution):
        self.executions.append({
            'time': execution.time,
            'symbol': contract.symbol,
            'side': execution.side,
            'shares': execution.shares,
            'price': execution.price,
            'exec_id': execution.execId
        })

    def execDetailsEnd(self, reqId):
        print(f"\n{'='*60}")
        print(f"Found {len(self.executions)} executions")
        print(f"{'='*60}\n")
        for e in sorted(self.executions, key=lambda x: x['time']):
            print(f"{e['time']} | {e['side']:4} {e['shares']:>6} {e['symbol']:<6} @ ${e['price']:.2f}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in (2104, 2106, 2158):
            print(f"Error {errorCode}: {errorString}")

def main():
    p = argparse.ArgumentParser(description='Query IBKR executions by date range')
    p.add_argument('--start', required=True, help='Start date (YYYYMMDD)')
    p.add_argument('--end', help='End date (YYYYMMDD), defaults to today')
    p.add_argument('--symbol', help='Filter by symbol (optional)')
    args = p.parse_args()

    checker = ExecutionChecker()
    checker.connect("127.0.0.1", 4002, 999)
    time.sleep(2)

    ef = ExecutionFilter()
    ef.time = args.start + " 00:00:00"
    if args.symbol:
        ef.symbol = args.symbol

    print(f"Querying executions from {args.start} to {args.end or 'now'}...")
    checker.reqExecutions(1, ef)
    time.sleep(5)
    checker.disconnect()

if __name__ == '__main__':
    main()
