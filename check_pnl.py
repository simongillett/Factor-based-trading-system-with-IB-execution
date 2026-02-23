#!/usr/bin/env python3
"""Check IBKR Paper Trading account P&L"""

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from threading import Thread
import time

class PnLChecker(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.account_data = {}
        self.positions = []
        self.connected = False

    def nextValidId(self, orderId):
        self.connected = True

    def accountSummary(self, reqId, account, tag, value, currency):
        self.account_data[tag] = value

    def accountSummaryEnd(self, reqId):
        pass

    def position(self, account, contract, pos, avgCost):
        if pos != 0:
            self.positions.append({
                'symbol': contract.symbol,
                'position': pos,
                'avg_cost': avgCost
            })

    def positionEnd(self):
        pass

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in [2104, 2106, 2158, 2119]:
            print(f"Error: {errorCode} - {errorString}")

def main():
    app = PnLChecker()
    app.connect("127.0.0.1", 4001, clientId=98)
    
    thread = Thread(target=app.run)
    thread.start()
    
    # Wait for connection
    for _ in range(20):
        if app.connected:
            break
        time.sleep(0.5)
    
    if not app.connected:
        print("Failed to connect to IBGateway")
        app.disconnect()
        return

    app.reqAccountSummary(1, "All", "NetLiquidation,TotalCashValue,UnrealizedPnL,RealizedPnL,GrossPositionValue")
    app.reqPositions()
    time.sleep(3)

    print("\n=== Paper Trading Account Summary ===")
    for tag, value in app.account_data.items():
        try:
            print(f"{tag}: ${float(value):,.2f}")
        except:
            print(f"{tag}: {value}")

    if app.positions:
        print("\n=== Positions ===")
        for p in app.positions:
            print(f"{p['symbol']}: {p['position']} shares @ ${p['avg_cost']:.2f}")
    else:
        print("\nNo open positions")

    app.disconnect()

if __name__ == "__main__":
    main()
