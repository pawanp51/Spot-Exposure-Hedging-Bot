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

## 📈 Hedging Strategies & Math Foundations

### ✅ Delta Neutral (Perpetual Hedge)

* Objective: Make Net Delta ≈ 0.
* Formula: $\text{Perp Size} = -\frac{\Delta_{spot}}{\Delta_{perp}}$

### ⛔ Protective Put

* Long spot + Long put (option).
* Limits downside risk below strike price.

### 📉 Covered Call

* Long spot + Short call option.
* Generates premium income, capped upside.

### 🏛 Collar

* Long spot + Long put + Short call.
* Low-cost downside hedge with capped upside.

Each strategy returns trade execution info + Greeks (Delta, Gamma, Theta, Vega).

---

## 📊 Risk Calculation Formulas
---

### ✳ Net Delta Exposure
```math
\text{Net Delta} = \text{Spot Size} - |\text{Perp Size}|
```
Represents the directional exposure. Positive net delta indicates under-hedged, negative indicates over-hedged.

### ✳ Threshold Limit
```math
\text{Threshold Limit} = |\text{Spot Size}| \times \left(\frac{\text{Threshold Percent}}{100}\right)
```
Defines the tolerance band beyond which re-hedging is triggered.

### ✳ Hedge Requirement Trigger
```math
\text{Needs Hedge} \iff |\text{Net Delta}| > \text{Threshold Limit}
```
Boolean condition that determines whether rebalancing action is necessary.

### ✳ Hedge Amount (Delta Neutral)
```math
\text{Hedge Amount} = \text{Net Delta}
```
Quantity of futures needed to neutralize net delta.

### ✳ Parametric Value-at-Risk (VaR)
```math
\text{Returns} = \log(\text{Price}_{t}) - \log(\text{Price}_{t-1})

\mu = \text{Mean}(\text{Returns})
\sigma = \text{StdDev}(\text{Returns})
\alpha = 1 - \text{Confidence Level}\\
Z_{\alpha} = \text{norm.ppf}(\alpha)

\text{VaR%} = - (\mu + \sigma \times Z_{\alpha})
\text{VaR (USD)} = \text{VaR%} \times |\text{Spot Size}|
```
Falls back to empirical percentile if volatility is negligible.

### ✳ Maximum Drawdown
```math
\text{Cumulative Max} = \max_{i \leq t}(\text{PnL}_i)
\text{Drawdown}_t = \text{Cumulative Max}_t - \text{PnL}_t
\text{Max Drawdown} = \max_t(\text{Drawdown}_t)
```
Captures the worst peak-to-trough decline in portfolio value.

### ✳ Correlation Matrix
```math
\text{Returns}_i = \log(\text{Price}_{i,t}) - \log(\text{Price}_{i,t-1})
\text{Correlation Matrix} = \text{corrcoef}(\text{Returns}_1, \text{Returns}_2, \ldots)
```
Determines asset-to-asset relationships based on return comovements.

### ✳ Beta (Market Sensitivity)
```math
\beta = \frac{\text{Cov}(r_\text{asset}, r_\text{benchmark})}{\text{Var}(r_\text{benchmark})}
```
Measures the sensitivity of asset returns relative to a benchmark index.

### ✳ Perpetual Futures Hedge Ratio
```math
\text{Hedge Size} = \text{Spot Size} \times \beta
```
Used to scale hedge positions dynamically based on beta-adjusted correlation.

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
DERIBIT_API_KEY=your_key
DERIBIT_API_SECRET=your_secret
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

