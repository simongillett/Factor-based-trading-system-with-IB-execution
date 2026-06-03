# Lodon Trading System Architecture

```mermaid
flowchart TB
    subgraph DataSources["📊 Data Sources"]
        FMP["FMP API<br/>(Financial Modeling Prep)"]
    end

    subgraph DataPipeline["💾 Data Pipeline"]
        direction TB
        PricesDB[(company_prices_daily.db<br/>DuckDB)]
        MetricsDB[(company_metrics_1yr.db<br/>DuckDB)]
        DailyUpdate["daily_update.py<br/>⏰ 6am daily"]
        FetchMetrics["fetch_key_metrics_1yr.py<br/>+ add_reported_currency.py<br/>⏰ Sunday 8pm"]
    end

    subgraph FactorModel["🧠 Factor Model"]
        direction TB
        Backtest["normalization_weighting_Zipline.py<br/>⏰ Sunday 8pm"]
        FeatureSelect["Feature Selection<br/>• Variance threshold<br/>• Correlation filter<br/>• IC-based selection"]
        Scaler["StandardScaler<br/>Normalization"]
        FactorArtifacts["DynamicWeeklyFactors/<br/>• factors_latest.json<br/>• scaler_latest.joblib"]
    end

    subgraph Execution["⚡ Execution"]
        direction TB
        DailyTrade["daily_trade.py<br/>⏰ 9:30am Mon-Fri"]
        SignalFiles["daily_signals/<br/>signals_YYYYMMDD.json"]
        IBGateway["IB Gateway<br/>localhost:4001"]
        IBC["IBC Controller<br/>(login/2FA/restarts)"]
        TradeLogs["logs/<br/>• daily_trade_YYYYMMDD.log<br/>• trades.json"]
    end

    subgraph Broker["🏦 IBKR"]
        IBKRServers["IBKR Servers"]
    end

    subgraph Positions["📈 Position Management"]
        Portfolio["Portfolio<br/>• Max 15 positions<br/>• Score-weighted sizing<br/>• 1.5x leverage"]
    end

    %% Data Flow
    FMP -->|prices| DailyUpdate
    FMP -->|metrics| FetchMetrics
    DailyUpdate --> PricesDB
    FetchMetrics --> MetricsDB

    %% Model Flow
    PricesDB --> Backtest
    MetricsDB --> Backtest
    Backtest --> FeatureSelect
    FeatureSelect -->|selected factors| Scaler
    Scaler --> FactorArtifacts

    %% Execution Flow
    FactorArtifacts --> DailyTrade
    PricesDB --> DailyTrade
    MetricsDB --> DailyTrade
    DailyTrade --> SignalFiles
    DailyTrade -->|TWS API| IBGateway
    IBC -->|manages| IBGateway
    IBGateway --> IBKRServers
    DailyTrade --> TradeLogs

    %% Trade Flow
    IBKRServers --> Portfolio

    %% Styling
    classDef source fill:#e1f5fe,stroke:#01579b
    classDef storage fill:#fff3e0,stroke:#e65100
    classDef model fill:#f3e5f5,stroke:#7b1fa2
    classDef exec fill:#fff8e1,stroke:#f57f17
    classDef broker fill:#e0f2f1,stroke:#00695c

    class FMP source
    class PricesDB,MetricsDB storage
    class Backtest,FeatureSelect,Scaler,FactorArtifacts model
    class DailyTrade,SignalFiles,IBGateway,IBC,TradeLogs exec
    class IBKRServers,Portfolio broker
```

## Data Flow Summary

| Stage | Component | Schedule | Output |
|-------|-----------|----------|--------|
| **Source** | FMP API | - | Raw prices & metrics |
| **Data** | daily_update.py | 6am daily | company_prices_daily.db |
| **Data** | fetch_key_metrics_1yr.py + add_reported_currency.py | Sunday 8pm | company_metrics_1yr.db (with currency) |
| **Model** | normalization_weighting_Zipline.py | Sunday 8pm | factors_latest.json, scaler_latest.joblib |
| **Trade** | daily_trade.py | 9:30am Mon-Fri | Signals + orders via IB Gateway |
| **Position** | IBKR | Real-time | Portfolio updates |

## Key Files

```
/home/simon0099/Lodon/
├── FMP_Databases/
│   ├── company_prices_daily.db      # Daily OHLCV
│   ├── company_prices_daily_1yr.db  # 1-year prices
│   └── company_metrics_1yr.db       # Fundamental metrics
├── DynamicWeeklyFactors/
│   ├── factors_latest.json          # Selected factor names + directions
│   └── scaler_latest.joblib         # Fitted StandardScaler
├── daily_signals/
│   └── signals_YYYYMMDD.json        # Daily BUY/SELL signals
├── zipline-factor-backtest/
│   └── normalization_weighting_Zipline.py
├── daily_trade.py                   # Signal generation + IB execution
└── logs/
    ├── daily_trade_YYYYMMDD.log     # Execution logs
    └── trades.json                  # Trade history
```
