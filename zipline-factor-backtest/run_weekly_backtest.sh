#!/bin/bash
# Weekly backtest runner with fresh data

cd "$(dirname "$0")"
mkdir -p logs
PYTHON=/home/simon0099/bin/python3

echo "$(date): Updating 1yr metrics..." >> logs/weekly_backtest.log
cd /home/simon0099/Lodon
$PYTHON fetch_key_metrics_1yr.py >> /home/simon0099/Lodon/zipline-factor-backtest/logs/weekly_backtest.log 2>&1

echo "$(date): Backfilling reported_currency..." >> /home/simon0099/Lodon/zipline-factor-backtest/logs/weekly_backtest.log
$PYTHON add_reported_currency.py >> /home/simon0099/Lodon/zipline-factor-backtest/logs/weekly_backtest.log 2>&1

echo "$(date): Starting weekly backtest..." >> /home/simon0099/Lodon/zipline-factor-backtest/logs/weekly_backtest.log
cd /home/simon0099/Lodon/zipline-factor-backtest
$PYTHON normalization_weighting_Zipline.py >> logs/weekly_backtest.log 2>&1

echo "$(date): Backtest complete." >> logs/weekly_backtest.log
