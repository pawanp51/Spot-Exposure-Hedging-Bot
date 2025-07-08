import numpy as np
from datetime import datetime
from math import ceil
from typing import Dict

from deribit_client import DeribitClient, DeribitError
from options_hedger import OptionsHedger
from greeks import GreeksCalculator, OptionType
from risk import RiskCalculator

# single shared client
client = DeribitClient()

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def hedge_protective_put(asset: str,
                         spot_qty: float,
                         strike: float,
                         days: int,
                         vol: float) -> Dict:
    """Long-put protective hedge."""
    S = client.get_spot_price(asset)
    T = days / 365
    inst = client.find_option_instrument(asset, strike, days, option_type="put")
    hedger = OptionsHedger(S, strike, T, 0.0, vol, spot_qty)
    qty = hedger.hedge_qty()
    price = client.get_ticker(inst) or 0.0
    return {
        "strategy": "protective_put",
        "instrument": inst,
        "size": qty,
        "cost": qty * price,
        "delta": hedger.put_delta(),
        "gamma": GreeksCalculator.gamma(S, strike, T, 0.0, vol),
        "theta": GreeksCalculator.theta(S, strike, T, 0.0, vol, OptionType.PUT),
        "vega": GreeksCalculator.vega(S, strike, T, 0.0, vol),
        "timestamp": _now()
    }

def covered_call(asset: str,
                 spot_qty: float,
                 strike: float,
                 days: int,
                 vol: float) -> Dict:
    """Covered call: hold spot, sell a call per unit."""
    S = client.get_spot_price(asset)
    T = days / 365
    inst = client.find_option_instrument(asset, strike, days, option_type="call")
    qty = ceil(spot_qty)
    price = client.get_ticker(inst) or 0.0
    return {
        "strategy": "covered_call",
        "instrument": inst,
        "size": -qty,            # negative= sold
        "cost": -qty * price,    # premium collected
        "timestamp": _now()
    }

def collar(asset: str,
           spot_qty: float,
           put_strike: float,
           call_strike: float,
           days: int,
           vol: float) -> Dict:
    """Collar = long put + short call in equal quantities."""
    put = hedge_protective_put(asset, spot_qty, put_strike, days, vol)
    call = covered_call(asset, spot_qty, call_strike, days, vol)
    net_cost = put["cost"] + call["cost"]
    return {
        "strategy": "collar",
        "size": put["size"],
        "put": put,
        "call": call,
        "cost": net_cost,
        "timestamp": _now()
    }

def delta_neutral(asset: str,
                  spot_qty: float,
                  perp_qty: float,
                  threshold: float) -> Dict:
    """Perpetual futures hedge to neutralize net delta."""
    spot_price = client.get_spot_price(asset)
    perp_price = client.get_perpetual_price(asset)
    rc = RiskCalculator(spot_qty, perp_qty, threshold)
    hedge_qty = -rc.hedge_amount()   # trade this many perpetual
    return {
        "strategy": "delta_neutral",
        "size": hedge_qty,
        "cost": abs(hedge_qty * perp_price),
        "timestamp": _now()
    }
