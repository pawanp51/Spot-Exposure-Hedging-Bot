from typing import List, Dict
from datetime import datetime
from greeks import GreeksCalculator, OptionType
from multi_exchange_client import MultiExchangeClient

client = MultiExchangeClient()


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

class PortfolioAnalytics:
    def __init__(self):
        # each pos: {type, side, size, instrument, strike, time_to_expiry, volatility, option_type, entry_price, timestamp}
        self.positions: List[Dict] = []

    def add_spot(self, asset: str, size: float, entry_price: float):
        self.positions.append({
            "type":        "spot",
            "side":        1 if size >= 0 else -1,
            "size":        abs(size),
            "instrument":  f"{asset}-SPOT",
            "entry_price": entry_price,
            "timestamp":   _now()
        })

    def add_perp(self, asset: str, size: float, entry_price: float):
        self.positions.append({
            "type":        "perp",
            "side":        1 if size >= 0 else -1,
            "size":        abs(size),
            "instrument":  f"{asset}-PERPETUAL",
            "entry_price": entry_price,
            "timestamp":   _now()
        })

    def add_option(self,
                   asset: str,
                   option_type: OptionType,
                   strike: float,
                   days: int,
                   volatility: float,
                   size: float,
                   entry_price: float):
        """Add a long/short option leg."""
        T = days / 365
        inst = client.find_option_instrument(asset, strike, days, option_type=option_type)
        self.positions.append({
            "type":          "option",
            "side":          1 if size >= 0 else -1,
            "size":          abs(size),
            "instrument":    inst,
            "strike":        strike,
            "time_to_expiry": T,
            "volatility":    volatility,
            "option_type":   option_type,
            "entry_price":   entry_price,
            "timestamp":     _now()
        })

    def compute_portfolio_greeks(self) -> Dict[str, float]:
        totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        for pos in self.positions:
            side, size = pos["side"], pos["size"]
            if pos["type"] in ("spot", "perp"):
                # Spot & perp contribute pure delta = size * side
                totals["delta"] += side * size
            else:
                # Option leg: fetch live underlying
                asset = pos["instrument"].split("-")[0]
                S = client.get_spot_price(asset)
                K = pos["strike"]
                T = pos["time_to_expiry"]
                vol = pos["volatility"]
                otype = pos["option_type"]

                d = GreeksCalculator.delta(S, K, T, 0.0, vol, otype)
                g = GreeksCalculator.gamma(S, K, T, 0.0, vol)
                th = GreeksCalculator.theta(S, K, T, 0.0, vol, otype)
                v = GreeksCalculator.vega(S, K, T, 0.0, vol)

                totals["delta"] += side * size * d
                totals["gamma"] += side * size * g
                totals["theta"] += side * size * th
                totals["vega"]  += side * size * v

        return totals
    
    def compute_pnl_attribution(self) -> Dict:
        """
        Returns a breakdown of unrealized P&L for each position
        plus a total P&L.
        """
        breakdown = []
        total_pnl = 0.0

        for pos in self.positions:
            side, size = pos["side"], pos["size"]
            entry = pos["entry_price"]
            inst = pos["instrument"]

            if pos["type"] == "spot":
                current = client.get_spot_price(inst.split("-")[0])
            elif pos["type"] == "perp":
                current = client.get_perpetual_price(inst.split("-")[0])
            else:  # option
                current = client.get_ticker(inst)

            pnl = side * size * (current - entry)
            breakdown.append({
                "instrument": inst,
                "size": size * side,
                "entry": entry,
                "current": current,
                "pnl": pnl
            })
            total_pnl += pnl

        return {
            "total_pnl": total_pnl,
            "legs": breakdown,
            "timestamp": _now()
        }
