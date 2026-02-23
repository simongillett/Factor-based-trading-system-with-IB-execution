#!/usr/bin/env python3
"""
Signal Publisher - Sends trading signals from DGX analytics to EC2 engines via SQS FIFO
"""
import json
import hashlib
import boto3
from datetime import datetime
from pathlib import Path

QUEUES = {
    'paper': 'https://sqs.us-east-1.amazonaws.com/842319560950/trading-paper-signals.fifo',
    'live': 'https://sqs.us-east-1.amazonaws.com/842319560950/trading-live-signals.fifo'
}

class SignalPublisher:
    def __init__(self, profile='Lodon'):
        self.sqs = boto3.Session(profile_name=profile).client('sqs', region_name='us-east-1')
    
    def publish(self, signals, env='paper'):
        queue = QUEUES[env]
        sent = []
        for s in signals:
            s['timestamp'] = datetime.utcnow().isoformat()
            s['environment'] = env
            body = json.dumps(s, default=str)
            dedup = hashlib.sha256(f"{s['symbol']}{s['timestamp']}".encode()).hexdigest()[:128]
            r = self.sqs.send_message(QueueUrl=queue, MessageBody=body, 
                                       MessageGroupId='signals', MessageDeduplicationId=dedup)
            sent.append(r['MessageId'])
        return sent
    
    def publish_from_backtest(self, env='paper', top_n=15, min_score=0.5, prev_symbols=None):
        results_dir = Path('zipline-factor-backtest/results')
        files = list(results_dir.glob('backtest_results_*.json'))
        if not files:
            print('No backtest results')
            return []
        
        with open(max(files, key=lambda p: p.stat().st_mtime)) as f:
            data = json.load(f)
        
        latest = data[max(data.keys())]
        candidates = [(k, v.get('composite_score', v.get('score', 0))) 
                      for k, v in latest.items() if v.get('composite_score', v.get('score', 0)) >= min_score]
        candidates.sort(key=lambda x: x[1], reverse=True)
        top = candidates[:top_n]
        
        # Factor-based weights
        scores = [s for _, s in top]
        min_s = min(scores) if scores else 0
        shifted = [s - min_s + 1e-6 for s in scores]
        total = sum(shifted)
        
        signals = [{'symbol': sym, 'action': 'BUY', 'score': score, 'target_weight': shifted[i] / total}
                   for i, (sym, score) in enumerate(top)]
        
        # Sell signals for exited positions
        if prev_symbols:
            new_symbols = {s['symbol'] for s in signals}
            for sym in prev_symbols - new_symbols:
                signals.append({'symbol': sym, 'action': 'SELL', 'score': 0, 'target_weight': 0})
        
        return self.publish(signals, env)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--env', choices=['paper', 'live'], default='paper')
    p.add_argument('--top-n', type=int, default=15)
    args = p.parse_args()
    
    pub = SignalPublisher()
    ids = pub.publish_from_backtest(env=args.env, top_n=args.top_n)
    print(f"Published {len(ids)} signals to {args.env}")
