#!/usr/bin/env python3

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import time

class OrderChecker(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.orders = []
        self.positions = []
        self.errors = []
        
    def accountSummary(self, reqId, account, tag, value, currency):
        print(f"Account {account}: {tag} = {value} {currency}")
    
    def execDetails(self, reqId, contract, execution):
        print(f"Execution: {execution.execId} - {execution.side} {execution.shares} {contract.symbol} @ ${execution.price}")
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"Error {errorCode}: {errorString}")
        self.errors.append((errorCode, errorString))
    
    def openOrder(self, orderId, contract, order, orderState):
        print(f"Open Order {orderId}: {order.action} {order.totalQuantity} {contract.symbol} - Status: {orderState.status}")
        self.orders.append((orderId, contract.symbol, order.action, order.totalQuantity, orderState.status))
    
    def position(self, account, contract, position, avgCost):
        if position != 0:
            print(f"Position: {contract.symbol} - Qty: {position}, Avg Cost: ${avgCost:.2f}")
            self.positions.append((contract.symbol, position, avgCost))
    
    def positionEnd(self):
        print("Position updates complete")
    
    def openOrderEnd(self):
        print("Open order updates complete")

def main():
    checker = OrderChecker()
    
    print("Connecting to check order status...")
    checker.connect("127.0.0.1", 4001, 621)  # Live trading
    
    time.sleep(2)
    
    print("\nRequesting account summary...")
    checker.reqAccountSummary(1, "All", "TotalCashValue,NetLiquidation")
    
    print("\nRequesting all orders...")
    checker.reqAllOpenOrders()
    
    print("\nRequesting executions...")
    from ibapi.execution import ExecutionFilter
    exec_filter = ExecutionFilter()
    checker.reqExecutions(2, exec_filter)
    
    time.sleep(5)  # Wait for responses
    
    print(f"\nSummary:")
    print(f"Open Orders: {len(checker.orders)}")
    print(f"Positions: {len(checker.positions)}")
    print(f"Errors: {len(checker.errors)}")
    
    if checker.errors:
        print("\nErrors encountered:")
        for code, msg in checker.errors:
            print(f"  {code}: {msg}")
    
    checker.disconnect()

if __name__ == "__main__":
    main()
