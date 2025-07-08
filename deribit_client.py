import logging
import requests
from requests.adapters import HTTPAdapter, Retry
from datetime import datetime, timedelta

BASE_URL = "https://www.deribit.com/api/v2/"
logger = logging.getLogger(__name__)

class DeribitError(Exception): pass

class DeribitClient:
    """REST client with retry for Deribit, including options lookup."""
    def __init__(self, max_retries=3, backoff=0.3):
        self.session = requests.Session()
        retries = Retry(total=max_retries, backoff_factor=backoff,
                        status_forcelist=[429,500,502,503,504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)

    def _get(self, endpoint, params):
        url = BASE_URL + endpoint
        try:
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            d = r.json()
            if not d.get("result"):
                raise DeribitError(f"No result in response: {d}")
            return d["result"]
        except Exception as e:
            logger.error(f"Deribit GET {endpoint} error: {e}")
            raise DeribitError(e)

    def get_ticker(self, instrument_name):
        """Fetch last traded price for any instrument."""
        data = self._get("public/ticker", {"instrument_name": instrument_name})
        return float(data.get("last_price"))

    def get_spot_price(self, asset):
        """Use perpetual index as spot proxy."""
        return self.get_ticker(f"{asset}-PERPETUAL")
    
    def get_perpetual_price(self, asset):
        """Fetch perpetual contract price for an asset."""
        return self.get_ticker(f"{asset}-PERPETUAL")
    

    def get_historical_prices(self, instrument, start_ts, end_ts, resolution="60"):
        """Fetch historical close prices."""
        data = self._get("public/get_tradingview_chart_data", {
            "instrument_name": instrument,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "resolution": resolution
        })
        return data.get("close", [])

    def get_instruments(self, asset, kind="option", expired=False):
        """
        Fetch available Deribit instruments for an asset.
        """
        expired_str = str(expired).lower()
        return self._get("public/get_instruments", {
            "currency": asset,
            "kind": kind,
            "expired": expired_str
        })


    def find_option_instrument(self, asset, strike, days, option_type="put"):
        """
        Finds the closest matching option instrument by strike and expiry.
        :param asset: e.g. 'ETH'
        :param strike: numeric strike price
        :param days: days until expiry
        :param option_type: 'put' or 'call'
        :return: instrument_name or raises DeribitError
        """
        instruments = self.get_instruments(asset)
        target_ts = (datetime.now() + timedelta(days=days)).timestamp() * 1000
        # filter by type and nearest expiry
        candidates = [inst for inst in instruments
                      if inst.get("option_type") == option_type and abs(inst.get("strike") - strike) < 1e-6]
        if not candidates:
            raise DeribitError(f"No {option_type} instruments at strike {strike}")
        # find candidate with expiry closest to target_ts
        inst = min(candidates, key=lambda x: abs(x.get("expiration_timestamp") - target_ts))
        return inst.get("instrument_name")