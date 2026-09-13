"""nqq.marketdata — thin alias over providers (kept for import stability).

All real-data logic lives in providers.py (Alpaca IEX / Binance / Stooq / FRED, each
with an honest source label and synthetic fallback).
"""
import providers as _p
import data as datamod  # noqa: F401  (re-exported for callers that used mdmod.datamod)

quote = _p.quote
quotes = _p.quotes
status = _p.status
