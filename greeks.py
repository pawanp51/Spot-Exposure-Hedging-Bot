import math
from scipy.stats import norm

class OptionType:
    CALL = 'call'
    PUT = 'put'

class GreeksCalculator:
    @staticmethod
    def _d1(S, K, T, r, sigma):
        return (math.log(S/K) + (r + 0.5*sigma*sigma)*T) / (sigma*math.sqrt(T))

    @staticmethod
    def _d2(d1, sigma, T):
        return d1 - sigma*math.sqrt(T)

    @classmethod
    def delta(cls, S, K, T, r, sigma, otype):
        d1 = cls._d1(S,K,T,r,sigma)
        return norm.cdf(d1) if otype==OptionType.CALL else norm.cdf(d1)-1
    
    @classmethod
    def gamma(cls, S, K, T, r, sigma):
        d1 = cls._d1(S, K, T, r, sigma)
        return norm.pdf(d1) / (S * sigma * math.sqrt(T))

    @classmethod
    def theta(cls, S, K, T, r, sigma, otype):
        d1 = cls._d1(S, K, T, r, sigma)
        d2 = cls._d2(d1, sigma, T)
        if otype == OptionType.CALL:
            return (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
        else:
            return (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365

    @classmethod
    def vega(cls, S, K, T, r, sigma):
        d1 = cls._d1(S, K, T, r, sigma)
        return S * norm.pdf(d1) * math.sqrt(T) / 100
