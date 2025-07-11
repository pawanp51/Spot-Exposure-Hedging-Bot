from config import THRESHOLD_PERCENT
from scipy.stats import norm
import numpy as np

class RiskCalculator:
    """
    Calculates risk metrics for futures hedging and portfolio analysis.

    Core methods:
    - net_delta(): spot delta exposure
    - threshold_limit(): threshold check for hedging
    - needs_hedge(): boolean trigger
    - hedge_amount(): amount to hedge delta
    - var(): parametric VaR at given confidence level
    - max_drawdown(): max drawdown from P&L series
    - correlation_matrix(): correlation of asset returns
    """
    def __init__(self, spot_size: float, perp_size: float, threshold: float = THRESHOLD_PERCENT):
        self.spot = spot_size
        self.perp = perp_size
        self.threshold_percent = threshold

    def net_delta(self) -> float:
        return self.spot + self.perp

    def threshold_limit(self) -> float:
        """Allowed delta exposure based on threshold percent of spot."""
        return abs(self.spot) * (self.threshold_percent / 100)

    def needs_hedge(self) -> bool:
        """Returns True if net delta exceeds threshold limit."""
        return abs(self.net_delta()) > self.threshold_limit()

    def hedge_amount(self) -> float:
        """Amount needed to neutralize net delta."""
        return self.net_delta()

    def var(self, prices: list[float], confidence: float = 0.95) -> float:
        """
        Parametric Value at Risk (VaR) for a single-asset P&L, assuming normal returns.
        Falls back to empirical percentile if returns are nearly constant.

        :param prices: historical price series for the asset
        :param confidence: confidence level (e.g. 0.95)
        :return: VaR as positive number representing potential loss
        """
        # compute log returns
        returns = np.diff(np.log(prices))
        if len(returns) == 0:
            raise ValueError("Not enough price data for VaR calculation.")

        mu = np.mean(returns)
        sigma = np.std(returns, ddof=1)
        alpha = 1 - confidence

        # If volatility is effectively zero, use empirical percentile
        if sigma < 1e-8:
            # empirical VaR as the negative of the percentile return
            emp = -np.percentile(returns, alpha*100)
            return emp * abs(self.spot)

        # theoretical z-score for the (1 - confidence) quantile
        z = norm.ppf(alpha)
        # parametric VaR: -(mu + sigma * z)
        var_pct = -(mu + sigma * z)
        return max(var_pct * abs(self.spot), 0.0)

    def max_drawdown(self, pnl_series: list[float]) -> float:
        """
        Calculates maximum drawdown from a time series of portfolio P&L values.

        :param pnl_series: cumulative P&L over time
        :return: max drawdown as positive number
        """
        cumulative = np.maximum.accumulate(pnl_series)
        drawdowns = cumulative - pnl_series
        return float(np.max(drawdowns))

    def correlation_matrix(self, price_dict: dict[str, list[float]]) -> np.ndarray:
            """
            Compute correlation matrix for multiple assets.
            :param price_dict: dict of {symbol: price series}
            :return: numpy correlation matrix
            """
            aligned = [np.diff(np.log(prices)) for prices in price_dict.values() if len(prices) > 1]
            if len(aligned) < 2:
                raise ValueError("Need at least two assets with price history for correlation.")
            return np.corrcoef(aligned)

    def beta(self, prices_benchmark: list[float], prices_asset: list[float]) -> float:
        # log returns
        r_b = np.diff(np.log(prices_benchmark))
        r_a = np.diff(np.log(prices_asset))
        cov = np.cov(r_a, r_b, ddof=1)[0,1]
        var_b = np.var(r_b, ddof=1)
        return cov / var_b

    def perp_hedge_ratio(self, spot_series: list[float], perp_series: list[float]) -> float:
        β = self.beta(spot_series, perp_series)
        # hedge size = spot_qty * β  (so that perp exposure offsets spot)
        return self.spot * β
