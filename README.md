# Multi-Exchange Spot Hedging Bot

## 🤖 Overview

A Telegram-integrated multi-exchange crypto hedging bot that performs real-time risk monitoring and automatically applies options and perpetual-based hedging strategies. Supports Deribit, OKX, and Bybit.

---

## 🏠 System Architecture & Risk Framework

### Architecture Components

* **Telegram Bot**: User interface for command-based interaction.
* **MultiExchangeClient**: Unified client interface to Deribit, OKX, and Bybit APIs.
* **RiskCalculator**: Computes delta exposure, VaR, drawdowns, and correlation.
* **PortfolioAnalytics**: Tracks Greek exposures, P\&L, and position-level metrics.
* **Strategies Module**: Houses strategy logic like delta-neutral, protective put, covered call, and collar.
* **Job Queue**: Schedules periodic monitoring jobs.

### Real-Time Risk Management

* Monitors Net Delta value of Spot and Perp positions.
* Applies threshold logic: If Net Delta > threshold, alerts and auto-hedges.
* Slippage and trading fee estimates are incorporated in all trade calculations.

---

# Hedging Strategies & Math Foundations

## Overview

Four core hedging strategies for options and derivatives trading with automated risk management.

## Strategies

### `hedge_protective_put(asset, spot_qty, strike, days, vol, client)`
- **Purpose**: Downside protection for long positions
- **Math**: Delta hedging with put options
- **Returns**: Greeks (delta, gamma, theta, vega) + cost analysis

### `covered_call(asset, spot_qty, strike, days, vol, client)`
- **Purpose**: Income generation from long positions
- **Math**: 1:1 hedge ratio, premium collection
- **Returns**: Negative size (short position), negative cost (premium received)

### `collar(asset, spot_qty, put_strike, call_strike, days, vol, client)`
- **Purpose**: Risk-defined position with limited upside/downside
- **Math**: Combined put + call strategy
- **Returns**: Net cost, detailed breakdown of both legs

### `delta_neutral(asset, spot_qty, perp_qty, threshold, client)`
- **Purpose**: Eliminate directional risk
- **Math**: Target net delta = 0 using perpetual futures
- **Returns**: Required futures position size

## Return Format

All strategies return:
```python
{
    "strategy": str,      # Strategy name
    "instrument": str,    # Option/futures instrument
    "size": float,        # Position size (+ = long, - = short)
    "cost": float,        # Total cost (+ = paid, - = received)
    "timestamp": str      # Execution timestamp
}
```

Options strategies include additional Greeks: `delta`, `gamma`, `theta`, `vega`

## Key Math

- **Time to expiration**: `T = days / 365`
- **Hedge ratios**: Calculated via `OptionsHedger` class
- **Greeks**: Real-time sensitivity analysis
- **Delta neutral**: `hedge_qty = -risk_calculator.hedge_amount()`

---

# Risk Calculation Formulas

## Overview

Risk metrics and portfolio analysis for futures hedging with parametric and empirical calculations.

## Core Methods

### `net_delta()`
- **Formula**: `spot_size + perp_size`
- **Purpose**: Calculate net delta exposure

### `threshold_limit()`
- **Formula**: `abs(spot_size) * (threshold_percent / 100)`
- **Purpose**: Maximum allowed delta exposure

### `needs_hedge()`
- **Formula**: `abs(net_delta()) > threshold_limit()`
- **Purpose**: Boolean trigger for hedging

### `hedge_amount()`
- **Formula**: `net_delta()`
- **Purpose**: Amount needed to neutralize delta

## Risk Metrics

### `var(prices, confidence=0.95)`
**Parametric Value at Risk**
- **Returns**: `μ + σ * z` where `z = norm.ppf(1-confidence)`
- **Log returns**: `np.diff(np.log(prices))`
- **Fallback**: Empirical percentile if σ < 1e-8

### `max_drawdown(pnl_series)`
**Maximum Drawdown**
- **Formula**: `max(cumulative_max - current_value)`
- **Returns**: Peak-to-trough decline as positive number

### `correlation_matrix(price_dict)`
**Asset Correlation**
- **Input**: `{symbol: price_series}`
- **Formula**: `np.corrcoef(log_returns)`
- **Returns**: Correlation matrix

### `beta(prices_benchmark, prices_asset)`
**Beta Coefficient**
- **Formula**: `cov(asset, benchmark) / var(benchmark)`
- **Returns**: Asset sensitivity to benchmark

### `perp_hedge_ratio(spot_series, perp_series)`
**Optimal Hedge Ratio**
- **Formula**: `spot_qty * β`
- **Purpose**: Perpetual futures hedge sizing

## Key Formulas

```python
# Delta calculations
net_delta = spot + perp
threshold = abs(spot) * (threshold_percent / 100)

# VaR calculation
returns = np.diff(np.log(prices))
z_score = norm.ppf(1 - confidence)
var = -(μ + σ * z_score) * position_size

# Beta calculation
β = cov(asset, benchmark) / var(benchmark)
hedge_ratio = spot_qty * β
```

---

## 🔹 Telegram Bot Commands

### Monitoring & Reporting

* `/monitor_risk <asset> <spot> <perp> <thr%>` — Begin risk monitoring.
* `/start_monitoring` — Enable automated checks every 60s.
* `/stop_monitoring` — Stop scheduled checks.
* `/risk_report <asset> <spot> <perp> <days> <conf>` — Historical VaR + MDD.
* `/return_dist` — View return histogram.
* `/stress_test` — Plot stress scenarios.

### Hedging

* `/hedge_now <asset> <size>` — Manually hedge using perp.
* `/auto_hedge <strategy> <args>` — Run a hedge strategy.
* `/hedge_status <asset>` — Last hedge info.
* `/hedge_history <asset> <n>` — Last n hedges.

### Portfolio/Market

* `/portfolio_analytics` — Greeks & PnL breakdown.
* `/exchange_prices <asset>` — Spot & perp across exchanges.
* `/market_summary <asset>` — Best bids + spreads.
* `/set_exchange <name>` — Preferred exchange.
* `/exchange_status` — Connection status.
* `/configure threshold=<%> freq=<s>` — Risk trigger config.

---

## ⚖️ Setup & Deployment

### 1. Clone the Repo

```bash
git clone https://github.com/yourname/hedging-bot.git
cd hedging-bot
```

### 2. Create `.env`

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
```

### 3. Install Requirements

```bash
pip install -r requirements.txt
```

### 4. Run the Bot

```bash
python bot.py
```

---

## 🔒 Risk Management Best Practices

* Always monitor position exposure using `/monitor_risk`.
* Avoid using leverage unless hedged properly.
* Use protective strategies (e.g., collar) during high volatility.
* Monitor slippage and transaction cost estimates.
* Simulate outcomes via `/risk_report`, `/stress_test`, `/return_dist`.

---

## ⚠️ Limitations

* No persistent state storage (data loss on restart).
* Assumes liquid markets and stable API connectivity.
* Execution simulated; no real trading integrated.
* Analytics do not cover portfolio margin impact.

---

## 🚀 Future Improvements

* Add database persistence (PostgreSQL/Redis).
* Plug in real trading APIs (dry run/live mode).
* Add Greeks-based dynamic rebalancing.
* Visual dashboard with real-time metrics.
* Train machine learning model for volatility forcasting.

---

