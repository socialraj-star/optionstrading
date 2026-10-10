# Watchlists
PUT_UNIVERSE = [
    "IREN", "PLTR", "AXP", "WDC", "AMZN", "NVDA",
    "AAPL", "CLS", "GOOG", "ORCL", "SNDK", "STX", "TSLA"
]

MAGNIFICENT_10 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOG",
    "META", "TSLA", "AVGO", "ORCL", "NFLX"
]

SPY_QQQ = ["SPY", "QQQ"]

# Put screener params
PUT_DELTA_RANGE = (0.25, 0.35)
PUT_MIN_DTE = 25
PUT_MAX_DTE = 35
PUT_MIN_PREMIUM_PCT = 3.0  # % of strike price

# Technical filters for puts
ABOVE_MA_PERIOD = 50
PULLBACK_MIN_PCT = 3.0   # min % below 20d high
PULLBACK_MAX_PCT = 15.0  # max % below 20d high

# LEAPS screener params — target exactly 0.70 delta
LEAPS_TARGET_DELTA = 0.70
LEAPS_DELTA_RANGE = (0.65, 0.78)   # tighter band around 0.70
LEAPS_MIN_DTE = 300
LEAPS_MAX_DTE = 730
LEAPS_RSI_OVERSOLD = 40  # RSI must be at or below this

# SPY/QQQ $1 debit call spread params
SPREAD_WIDTH = 1.0
SPREAD_MAX_DEBIT = 0.70
SPREAD_MIN_DTE = 1
SPREAD_MAX_DTE = 14
SPREAD_ITM_DELTA_MIN = 0.50  # long leg must be at least slightly ITM

# Indicator params
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD = 12, 26, 9
BB_PERIOD, BB_STD = 20, 2.0

# Black-Scholes risk-free rate
RISK_FREE_RATE = 0.053
