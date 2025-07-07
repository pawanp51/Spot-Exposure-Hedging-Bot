from greeks import GreeksCalculator, OptionType
from math import ceil

class OptionsHedger:
    def __init__(self, S, strike, T, r, sigma, spot_qty):
        self.S, self.K, self.T, self.r, self.sigma, self.spot_qty = S, strike, T, r, sigma, spot_qty

    def put_delta(self):
        return abs(GreeksCalculator.delta(self.S, self.K, self.T, self.r, self.sigma, OptionType.PUT))

    def hedge_qty(self) -> int:
        d_put = self.put_delta()
        return ceil(self.spot_qty / d_put)
