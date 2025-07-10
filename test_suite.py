import unittest
from unittest.mock import MagicMock
import numpy as np

from risk import RiskCalculator
import strategies

class TestRiskCalculator(unittest.TestCase):
    def setUp(self):
        self.rc = RiskCalculator(spot_size=100, perp_size=80, threshold=10)

    def test_net_delta(self):
        self.assertEqual(self.rc.net_delta(), 20)

    def test_threshold_limit(self):
        self.assertEqual(self.rc.threshold_limit(), 10.0)

    def test_needs_hedge_true(self):
        self.assertTrue(self.rc.needs_hedge())

    def test_needs_hedge_false(self):
        rc = RiskCalculator(spot_size=100, perp_size=91, threshold=10)
        self.assertFalse(rc.needs_hedge())

    def test_hedge_amount(self):
        self.assertEqual(self.rc.hedge_amount(), 20)

    def test_var_parametric(self):
        prices = np.linspace(100, 110, 50).tolist()
        var = self.rc.var(prices)
        self.assertGreaterEqual(var, 0)

    def test_max_drawdown(self):
        pnl = [0, 10, 5, 15, 7, 20, 12]
        self.assertEqual(self.rc.max_drawdown(pnl), 8)

    def test_correlation_matrix(self):
        prices = {
            "BTC": np.linspace(100, 110, 50).tolist(),
            "ETH": np.linspace(50, 60, 50).tolist()
        }
        corr = self.rc.correlation_matrix(prices)
        self.assertEqual(corr.shape, (2, 2))

    def test_beta_and_hedge_ratio(self):
        benchmark = np.linspace(100, 110, 50).tolist()
        asset = np.linspace(50, 55, 50).tolist()
        beta = self.rc.beta(benchmark, asset)
        ratio = self.rc.perp_hedge_ratio(asset, benchmark)
        self.assertAlmostEqual(ratio, self.rc.spot * beta)


class TestStrategies(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.get_spot_price.return_value = 100
        self.client.find_option_instrument.return_value = "BTC-30AUG24-10000-P"
        self.client.get_ticker.return_value = 5
        self.client.get_perpetual_price.return_value = 102

    def test_hedge_protective_put(self):
        result = strategies.hedge_protective_put("BTC", 1, 9500, 30, 0.6, self.client)
        self.assertEqual(result["strategy"], "protective_put")

    def test_covered_call(self):
        result = strategies.covered_call("BTC", 1.3, 11000, 30, 0.6, self.client)
        self.assertEqual(result["strategy"], "covered_call")
        self.assertEqual(result["size"], -2)

    def test_collar(self):
        result = strategies.collar("BTC", 1, 9500, 11000, 30, 0.6, self.client)
        self.assertEqual(result["strategy"], "collar")

    def test_delta_neutral(self):
        result = strategies.delta_neutral("BTC", 100, 90, 5, self.client)
        self.assertEqual(result["strategy"], "delta_neutral")

if __name__ == "__main__":
    unittest.main()
