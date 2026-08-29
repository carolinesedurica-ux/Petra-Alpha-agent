"""Lightweight Black-Scholes utilities for the simulated options chain."""
import math
from statistics import NormalDist

_N = NormalDist().cdf
RISK_FREE = 0.045


def bs_price(S, K, T, sigma, is_call):
    if T <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * _N(d1) - K * math.exp(-RISK_FREE * T) * _N(d2)
    return K * math.exp(-RISK_FREE * T) * _N(-d2) - S * _N(-d1)


def bs_delta(S, K, T, sigma, is_call):
    if T <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _N(d1) if is_call else _N(d1) - 1.0
