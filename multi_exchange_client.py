import logging
import requests
import ccxt
import time
from requests.adapters import HTTPAdapter, Retry
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from config import DERIBIT_API_KEY, DERIBIT_API_SECRET

logger = logging.getLogger(__name__)

class ExchangeError(Exception):
    pass

class MultiExchangeClient:
    """
    Multi-exchange client supporting Deribit, OKX, and Bybit for public data.
    Uses CCXT for OKX and Bybit, custom REST client for Deribit.
    """
    
    def __init__(self, max_retries=3, backoff=0.3):
        # Initialize CCXT exchanges (no API keys needed for public data)
        self.okx = ccxt.okx({
            'sandbox': False,
            'rateLimit': 1000,
            'enableRateLimit': True,
        })
        
        self.bybit = ccxt.bybit({
            'sandbox': False,
            'rateLimit': 1000,
            'enableRateLimit': True,
        })
        
        # Initialize Deribit REST client
        self.deribit_base_url = "https://www.deribit.com/api/v2/"
        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=backoff,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        
        # Exchange priority for price fetching
        self.exchange_priority = ['deribit', 'okx', 'bybit']
        
    def _deribit_get(self, endpoint: str, params: dict) -> dict:
        """Make authenticated or public request to Deribit API."""
        url = self.deribit_base_url + endpoint
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data.get("result"):
                raise ExchangeError(f"No result in Deribit response: {data}")
            return data["result"]
        except Exception as e:
            logger.error(f"Deribit GET {endpoint} error: {e}")
            raise ExchangeError(f"Deribit API error: {e}")
    
    def _normalize_symbol(self, asset: str, exchange: str, instrument_type: str = 'spot') -> str:
        """Normalize asset symbol for different exchanges."""
        asset = asset.upper()
        
        if exchange == 'deribit':
            if instrument_type == 'perpetual':
                return f"{asset}-PERPETUAL"
            elif instrument_type == 'spot':
                return f"{asset}-PERPETUAL"  # Deribit uses perpetual as spot proxy
            else:
                return asset
        elif exchange == 'okx':
            if instrument_type == 'perpetual':
                return f"{asset}-USDT-SWAP"
            elif instrument_type == 'spot':
                return f"{asset}/USDT"
            else:
                return f"{asset}/USDT"
        elif exchange == 'bybit':
            if instrument_type == 'perpetual':
                return f"{asset}USDT"
            elif instrument_type == 'spot':
                return f"{asset}/USDT"
            else:
                return f"{asset}/USDT"
        
        return asset
    
    def get_ticker_from_exchange(self, asset: str, exchange: str, instrument_type: str = 'spot') -> Optional[float]:
        """Get ticker price from specific exchange."""
        try:
            symbol = self._normalize_symbol(asset, exchange, instrument_type)
            
            if exchange == 'deribit':
                data = self._deribit_get("public/ticker", {"instrument_name": symbol})
                return float(data.get("last_price", 0))
            
            elif exchange == 'okx':
                if instrument_type == 'perpetual':
                    ticker = self.okx.fetch_ticker(symbol)
                else:
                    ticker = self.okx.fetch_ticker(symbol)
                return float(ticker['last'])
            
            elif exchange == 'bybit':
                if instrument_type == 'perpetual':
                    # For Bybit perpetuals, use linear contracts
                    markets = self.bybit.load_markets()
                    if symbol in markets:
                        ticker = self.bybit.fetch_ticker(symbol)
                        return float(ticker['last'])
                    else:
                        # Try alternative symbol format
                        alt_symbol = f"{asset}/USDT:USDT"
                        ticker = self.bybit.fetch_ticker(alt_symbol)
                        return float(ticker['last'])
                else:
                    ticker = self.bybit.fetch_ticker(symbol)
                    return float(ticker['last'])
                    
        except Exception as e:
            logger.warning(f"Failed to get {asset} {instrument_type} price from {exchange}: {e}")
            return None
    
    def get_best_price(self, asset: str, instrument_type: str = 'spot') -> Tuple[float, str]:
        """Get best available price from all exchanges with exchange info."""
        prices = {}
        
        for exchange in self.exchange_priority:
            price = self.get_ticker_from_exchange(asset, exchange, instrument_type)
            if price and price > 0:
                prices[exchange] = price
        
        if not prices:
            raise ExchangeError(f"No price data available for {asset} {instrument_type}")
        
        # Return first available price (based on priority)
        best_exchange = next(iter(prices.keys()))
        return prices[best_exchange], best_exchange
    
    def get_spot_price(self, asset: str) -> float:
        """Get spot price (best available across exchanges)."""
        price, _ = self.get_best_price(asset, 'spot')
        return price
    
    def get_perpetual_price(self, asset: str) -> float:
        """Get perpetual futures price (best available across exchanges)."""
        price, _ = self.get_best_price(asset, 'perpetual')
        return price
    
    def get_ticker(self, instrument_name: str) -> float:
        """Get ticker for specific instrument (primarily for Deribit options)."""
        # For Deribit-specific instruments
        if '-' in instrument_name and any(x in instrument_name for x in ['C', 'P']):
            try:
                data = self._deribit_get("public/ticker", {"instrument_name": instrument_name})
                return float(data.get("last_price", 0))
            except:
                return 0.0
        
        # For standard assets, try all exchanges
        asset = instrument_name.split('-')[0] if '-' in instrument_name else instrument_name
        try:
            return self.get_spot_price(asset)
        except:
            return 0.0
    
    def get_all_exchange_prices(self, asset: str, instrument_type: str = 'spot') -> Dict[str, float]:
        """Get prices from all exchanges for comparison."""
        prices = {}
        
        for exchange in ['deribit', 'okx', 'bybit']:
            price = self.get_ticker_from_exchange(asset, exchange, instrument_type)
            if price and price > 0:
                prices[exchange] = price
        
        return prices
    
    def get_historical_prices(self, asset: str, days: int = 30, exchange: str = 'deribit') -> List[float]:
        """Get historical prices for risk calculations."""
        end_ts = int(time.time() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        
        if exchange == 'deribit':
            try:
                symbol = self._normalize_symbol(asset, 'deribit', 'perpetual')
                data = self._deribit_get("public/get_tradingview_chart_data", {
                    "instrument_name": symbol,
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                    "resolution": "60"
                })
                return data.get("close", [])
            except Exception as e:
                logger.error(f"Failed to get historical data from Deribit: {e}")
        
        # Fallback to CCXT exchanges
        try:
            if exchange == 'okx':
                symbol = self._normalize_symbol(asset, 'okx', 'perpetual')
                ohlcv = self.okx.fetch_ohlcv(symbol, '1h', limit=24*days)
                return [candle[4] for candle in ohlcv]  # Close prices
            elif exchange == 'bybit':
                symbol = self._normalize_symbol(asset, 'bybit', 'perpetual')
                ohlcv = self.bybit.fetch_ohlcv(symbol, '1h', limit=24*days)
                return [candle[4] for candle in ohlcv]  # Close prices
        except Exception as e:
            logger.error(f"Failed to get historical data from {exchange}: {e}")
        
        return []
    
    def get_instruments(self, asset: str, kind: str = "option", expired: bool = False) -> List[dict]:
        """Get available instruments (primarily for Deribit options)."""
        try:
            expired_str = str(expired).lower()
            return self._deribit_get("public/get_instruments", {
                "currency": asset,
                "kind": kind,
                "expired": expired_str
            })
        except Exception as e:
            logger.error(f"Failed to get instruments: {e}")
            return []
    
    def find_option_instrument(self, asset: str, strike: float, days: int, option_type: str = "put") -> str:
        """Find option instrument (Deribit only)."""
        instruments = self.get_instruments(asset)
        target_ts = (datetime.now() + timedelta(days=days)).timestamp() * 1000
        
        # Filter by option type and strike
        candidates = [
            inst for inst in instruments
            if inst.get("option_type") == option_type and 
            abs(inst.get("strike", 0) - strike) < 1e-6
        ]
        
        if not candidates:
            raise ExchangeError(f"No {option_type} instruments at strike {strike}")
        
        # Find closest expiry
        best_instrument = min(
            candidates, 
            key=lambda x: abs(x.get("expiration_timestamp", 0) - target_ts)
        )
        
        return best_instrument.get("instrument_name", "")
    
    def get_orderbook(self, asset: str, exchange: str = 'deribit', instrument_type: str = 'spot') -> Dict:
        """Get orderbook data from specified exchange."""
        try:
            symbol = self._normalize_symbol(asset, exchange, instrument_type)
            
            if exchange == 'deribit':
                data = self._deribit_get("public/get_order_book", {
                    "instrument_name": symbol,
                    "depth": 20
                })
                return {
                    'bids': data.get('bids', []),
                    'asks': data.get('asks', []),
                    'timestamp': data.get('timestamp', 0)
                }
            elif exchange == 'okx':
                orderbook = self.okx.fetch_order_book(symbol, 20)
                return orderbook
            elif exchange == 'bybit':
                orderbook = self.bybit.fetch_order_book(symbol, 20)
                return orderbook
        except Exception as e:
            logger.error(f"Failed to get orderbook from {exchange}: {e}")
            return {'bids': [], 'asks': [], 'timestamp': 0}
    
    def get_market_summary(self, asset: str) -> Dict:
        """Get comprehensive market summary across all exchanges."""
        summary = {
            'asset': asset,
            'timestamp': datetime.now().isoformat(),
            'spot_prices': {},
            'perpetual_prices': {},
            'best_spot': None,
            'best_perpetual': None,
            'spread_analysis': {}
        }
        
        # Get spot prices
        summary['spot_prices'] = self.get_all_exchange_prices(asset, 'spot')
        summary['perpetual_prices'] = self.get_all_exchange_prices(asset, 'perpetual')
        
        # Determine best prices
        if summary['spot_prices']:
            best_spot_exchange = min(summary['spot_prices'].keys(), 
                                   key=lambda x: summary['spot_prices'][x])
            summary['best_spot'] = {
                'price': summary['spot_prices'][best_spot_exchange],
                'exchange': best_spot_exchange
            }
        
        if summary['perpetual_prices']:
            best_perp_exchange = min(summary['perpetual_prices'].keys(),
                                   key=lambda x: summary['perpetual_prices'][x])
            summary['best_perpetual'] = {
                'price': summary['perpetual_prices'][best_perp_exchange],
                'exchange': best_perp_exchange
            }
        
        # Calculate spreads
        if len(summary['spot_prices']) > 1:
            prices = list(summary['spot_prices'].values())
            summary['spread_analysis']['spot_spread'] = {
                'max_price': max(prices),
                'min_price': min(prices),
                'spread_abs': max(prices) - min(prices),
                'spread_pct': ((max(prices) - min(prices)) / min(prices)) * 100
            }
        
        return summary


# Legacy compatibility - single exchange clients
class DeribitClient(MultiExchangeClient):
    """Legacy compatibility wrapper for Deribit-specific functionality."""
    
    def __init__(self, max_retries=3, backoff=0.3):
        super().__init__(max_retries, backoff)
    
    def get_ticker(self, instrument_name: str) -> float:
        """Get ticker for specific Deribit instrument."""
        return super().get_ticker(instrument_name)
    
    def get_spot_price(self, asset: str) -> float:
        """Get spot price preferring Deribit."""
        price = self.get_ticker_from_exchange(asset, 'deribit', 'spot')
        if price:
            return price
        return super().get_spot_price(asset)
    
    def get_perpetual_price(self, asset: str) -> float:
        """Get perpetual price preferring Deribit."""
        price = self.get_ticker_from_exchange(asset, 'deribit', 'perpetual')
        if price:
            return price
        return super().get_perpetual_price(asset)


class OKXClient(MultiExchangeClient):
    """OKX-specific client wrapper."""
    
    def __init__(self):
        super().__init__()
    
    def get_spot_price(self, asset: str) -> float:
        price = self.get_ticker_from_exchange(asset, 'okx', 'spot')
        if price:
            return price
        raise ExchangeError(f"Could not get {asset} spot price from OKX")
    
    def get_perpetual_price(self, asset: str) -> float:
        price = self.get_ticker_from_exchange(asset, 'okx', 'perpetual')
        if price:
            return price
        raise ExchangeError(f"Could not get {asset} perpetual price from OKX")


class BybitClient(MultiExchangeClient):
    """Bybit-specific client wrapper."""
    
    def __init__(self):
        super().__init__()
    
    def get_spot_price(self, asset: str) -> float:
        price = self.get_ticker_from_exchange(asset, 'bybit', 'spot')
        if price:
            return price
        raise ExchangeError(f"Could not get {asset} spot price from Bybit")
    
    def get_perpetual_price(self, asset: str) -> float:
        price = self.get_ticker_from_exchange(asset, 'bybit', 'perpetual')
        if price:
            return price
        raise ExchangeError(f"Could not get {asset} perpetual price from Bybit")


# Backward compatibility
DeribitError = ExchangeError