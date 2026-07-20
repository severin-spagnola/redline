"""Golden/death-cross strategy. The signal clause is redlined `never`: it must
stay causal. An agent that reintroduces look-ahead here is blocked at the gate."""
import pandas as pd


def moving_averages(price: pd.Series, fast: int = 50, slow: int = 200):
    return price.rolling(fast).mean(), price.rolling(slow).mean()


# arch:begin signal-clause never reason="signals must be causal — no look-ahead bias"
def compute_signal(fast_ma: pd.Series, slow_ma: pd.Series) -> pd.Series:
    # Decision at bar t uses only MAs known at bar t (both are trailing windows).
    # DO NOT .shift(-1) / use future bars / reindex forward — that is look-ahead
    # and silently inflates every backtest metric.
    return (fast_ma > slow_ma).astype(int)
# arch:end signal-clause


def backtest(price: pd.Series) -> pd.Series:
    fast_ma, slow_ma = moving_averages(price)
    signal = compute_signal(fast_ma, slow_ma)
    returns = price.pct_change().fillna(0.0)
    # position is yesterday's signal applied to today's return (also causal)
    return (signal.shift(1).fillna(0) * returns)
