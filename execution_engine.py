#!/usr/bin/env python3
"""
Execution Engine - Consumes signals from SQS and executes via IB Gateway
"""
import os
import sys
import json
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import boto3
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

load_dotenv('/opt/trading/.env')

ENV = os.getenv('ENV')
IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT'))
SQS_QUEUE_URL = os.getenv('SQS_QUEUE_URL')

# Fail-closed validation
if ENV == 'live' and IB_PORT != 4001:
    sys.exit("FATAL: Live env requires port 4001")
if ENV == 'paper' and IB_PORT != 4002:
    sys.exit("FATAL: Paper env requires port 4002")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'/opt/trading/logs/engine_{datetime.now():%Y%m%d}.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class TradingEngine(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.next_order_id = None
        self.connected = False
        self.account_value = None
        self.prices = {}
        self.sqs = boto3.client('sqs', region_name='us-east-1')
    
    def nextValidId(self, orderId):
        self.next_order_id = orderId
        self.connected = True
        log.info(f"Connected, next order ID: {orderId}")
    
    def updateAccountValue(self, key, val, currency, accountName):
        if key == 'NetLiquidation' and currency == 'USD':
            self.account_value = float(val)
            log.info(f"Account value: ${self.account_value:,.2f}")
    
    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4 and price > 0:  # LAST price
            self.prices[reqId] = price
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        log.error(f"Error {errorCode}: {errorString}")
    
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, *args):
        log.info(f"Order {orderId}: {status}, filled={filled}, avg={avgFillPrice}")
    
    def connect_ib(self):
        log.info(f"Connecting to IB Gateway at {IB_HOST}:{IB_PORT}")
        self.connect(IB_HOST, IB_PORT, clientId=1)
        for _ in range(60):
            if self.connected:
                self.reqAccountUpdates(True, "")
                time.sleep(2)  # wait for account value
                return True
            time.sleep(1)
        return False
    
    def execute_signal(self, signal):
        contract = Contract()
        contract.symbol = signal['symbol']
        contract.secType = 'STK'
        contract.exchange = 'SMART'
        contract.currency = 'USD'
        
        action = signal.get('action', 'BUY')
        target_weight = signal.get('target_weight', 0)
        
        if not self.account_value:
            log.error("No account value available")
            return
        
        # Request delayed market data
        self.reqMarketDataType(3)  # 3 = delayed data
        req_id = self.next_order_id
        self.reqMktData(req_id, contract, "", True, False, [])
        
        # Wait for price
        for _ in range(10):
            time.sleep(0.5)
            price = self.prices.get(req_id)
            if price and price > 0:
                break
        
        if not price or price <= 0:
            log.error(f"No price for {signal['symbol']}")
            return
        
        quantity = int(self.account_value * target_weight / price)
        if quantity <= 0 and action == 'BUY':
            log.warning(f"Skipping {signal['symbol']}: quantity={quantity}")
            return
        
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'MKT'
        order.tif = 'DAY'
        
        log.info(f"Executing: {order.action} {order.totalQuantity} {contract.symbol}")
        self.placeOrder(self.next_order_id, contract, order)
        self.next_order_id += 1
    
    def poll_signals(self):
        while True:
            try:
                resp = self.sqs.receive_message(
                    QueueUrl=SQS_QUEUE_URL,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20
                )
                for msg in resp.get('Messages', []):
                    signal = json.loads(msg['Body'])
                    if signal.get('environment') != ENV:
                        log.warning(f"Ignoring signal for wrong env: {signal.get('environment')}")
                        continue
                    self.execute_signal(signal)
                    self.sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=msg['ReceiptHandle'])
            except Exception as e:
                log.error(f"Poll error: {e}")
                time.sleep(5)

def main():
    log.info(f"Starting {ENV} execution engine")
    engine = TradingEngine()
    
    log.info(f"Connecting to IB Gateway at {IB_HOST}:{IB_PORT}")
    engine.connect(IB_HOST, IB_PORT, clientId=1)
    
    from threading import Thread
    Thread(target=engine.run, daemon=True).start()
    
    # Wait for connection
    for _ in range(30):
        if engine.connected:
            break
        time.sleep(1)
    
    if not engine.connected:
        log.error("Failed to connect to IB Gateway")
        sys.exit(1)
    
    log.info("Connected to IB Gateway")
    engine.reqAccountUpdates(True, "")
    time.sleep(2)
    
    engine.poll_signals()

if __name__ == '__main__':
    main()
