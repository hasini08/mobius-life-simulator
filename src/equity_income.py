"""
Individual-share / basket decumulation testing framework (internship Weeks 5-8, tasks 13-16):
tests whether a single UK share - or a hand-built basket of shares - can meet a retirement
income objective (not running out of money), reusing the SAME Monte Carlo engine
(engine.run_simulation, engine.downside_stats) as the portfolio-level Mobius Wealth app rather
than a parallel implementation.

Mechanism: each share (or basket) is registered as an ordinary entry in the SAME PORTFOLIOS/AC
dicts portfolios.py already exposes - identical to how the app's live portfolio editor and the
Better v4 migration register new holdings. Every existing helper (asset_class_weights,
weighted_avg_fee, downside_stats, run_simulation, historical_single_path, asset_correlation_matrix)
then works on a single share or a basket completely unchanged.

Currently wired to PLACEHOLDER/SYNTHETIC share data - see generate_placeholder_equity_data.py.
Swap data/equities/uk_shares_returns.csv for a real Bloomberg export (task 12, Hasini's own next
step) and everything below keeps working unchanged, provided the new file has the same shape
(Date index, one column per ticker, monthly simple returns).
"""
from itertools import combinations
from pathlib import Path

import pandas as pd

from portfolios import PORTFOLIOS, AC
from engine import run_simulation, downside_stats, ClientProfile

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EQUITY_DIR = DATA_DIR / "equities"
EQUITY_RETURNS_CSV = EQUITY_DIR / "uk_shares_returns.csv"
SHARE_METADATA_CSV = EQUITY_DIR / "share_metadata.csv"

# Illustrative flat platform/dealing fee, pa - NOT sourced from anything, just a placeholder so
# individual shares aren't compared "fee-free" against the rest of the project's portfolios (which
# all carry a fee). Replace once a real fee basis (e.g. platform charge + dealing costs) is known.
DEFAULT_SHARE_FEE = 0.0010

SHARE_PREFIX = "Share: "


def load_equity_returns(path=EQUITY_RETURNS_CSV) -> pd.DataFrame:
    return pd.read_csv(path, index_col=0, parse_dates=True)


def load_share_metadata(path=SHARE_METADATA_CSV) -> pd.DataFrame:
    return pd.read_csv(path)


def register_shares(equity_df: pd.DataFrame, fee: float = DEFAULT_SHARE_FEE) -> list:
    """Registers every column of equity_df as its own single-holding 'portfolio' (100% weight, one
    share), so run_simulation/downside_stats/etc. work on it exactly like any other portfolio in
    this project. Returns the list of registered portfolio names."""
    names = []
    for ticker in equity_df.columns:
        AC[ticker] = ticker
        name = f"{SHARE_PREFIX}{ticker}"
        PORTFOLIOS[name] = [(ticker, ticker, 1.0, fee)]
        names.append(name)
    return names


def register_basket(name: str, weights: dict, fee: float = DEFAULT_SHARE_FEE) -> str:
    """Registers an arbitrary constant-mix (rebalanced-monthly) combination of shares as a
    portfolio. weights: {ticker: weight} - need not sum to 1.0 (carried through as-is, same
    convention as the app's holdings editor). This is the SAME rebalance-every-month convention
    every other portfolio in this project uses (weighted_monthly_returns applies fixed weights
    every month) - see register_buy_and_hold_basket for the no-rebalancing alternative."""
    PORTFOLIOS[name] = [(ticker, ticker, w, fee) for ticker, w in weights.items()]
    return name


def buy_and_hold_monthly_returns(weights: dict, equity_df: pd.DataFrame, label: str = "buyhold") -> pd.Series:
    """Models a basket held WITHOUT rebalancing - the alternative to the constant-mix convention
    every other portfolio here uses. Winners drift to a bigger share of the basket over time (for
    better or worse), exactly as an unmanaged real portfolio would - this is what task 16 ("explore
    different portfolio weighting and rebalancing approaches") is actually comparing against."""
    tickers = list(weights.keys())
    w0 = pd.Series(weights, dtype=float)
    w0 = w0 / w0.sum()
    sub = equity_df[tickers].dropna()
    growth = (1 + sub).cumprod()
    value = growth.mul(w0, axis=1).sum(axis=1)
    prior_value = value.shift(1)
    prior_value.iloc[0] = w0.sum()  # normalised start = 1.0
    monthly_ret = value / prior_value - 1
    monthly_ret.name = label
    return monthly_ret


def register_buy_and_hold_basket(name: str, weights: dict, equity_df: pd.DataFrame,
                                  fee: float = DEFAULT_SHARE_FEE) -> str:
    """Registers a buy-and-hold basket as its own derived return column (mutates equity_df in
    place, adding one column) plus a single-holding portfolio pointing at it - needs its own return
    series rather than just a weights vector, since a buy-and-hold basket's effective blend drifts
    month to month (see buy_and_hold_monthly_returns)."""
    col = f"{name}__buyhold"
    equity_df[col] = buy_and_hold_monthly_returns(weights, equity_df, label=col)
    AC[col] = col
    PORTFOLIOS[name] = [(col, col, 1.0, fee)]
    return name


def annual_rebalance_monthly_returns(weights: dict, equity_df: pd.DataFrame,
                                      label: str = "annual_rebal") -> pd.Series:
    """The middle ground between the two other rebalancing conventions: resets to target weights
    once every 12 months (like a real portfolio reviewed annually) rather than every month
    (register_basket's constant-mix convention) or never (buy_and_hold_monthly_returns) - task 16
    asks for MULTIPLE weighting/rebalancing approaches, and annual review is the most common
    real-world cadence, sitting between the other two."""
    tickers = list(weights.keys())
    w0 = pd.Series(weights, dtype=float)
    w0 = w0 / w0.sum()
    sub = equity_df[tickers].dropna()
    n = len(sub)
    value = pd.Series(index=sub.index, dtype=float)
    portfolio_value = 1.0
    block_start = 0
    while block_start < n:
        block_end = min(block_start + 12, n)
        block = sub.iloc[block_start:block_end]
        growth = (1 + block).cumprod()
        block_value = growth.mul(w0, axis=1).sum(axis=1) * portfolio_value
        value.iloc[block_start:block_end] = block_value.to_numpy()
        portfolio_value = block_value.iloc[-1]
        block_start = block_end
    prior_value = value.shift(1)
    prior_value.iloc[0] = 1.0
    monthly_ret = value / prior_value - 1
    monthly_ret.name = label
    return monthly_ret


def register_annual_rebalance_basket(name: str, weights: dict, equity_df: pd.DataFrame,
                                      fee: float = DEFAULT_SHARE_FEE) -> str:
    """Registers an annually-rebalanced basket the same way register_buy_and_hold_basket does."""
    col = f"{name}__annual"
    equity_df[col] = annual_rebalance_monthly_returns(weights, equity_df, label=col)
    AC[col] = col
    PORTFOLIOS[name] = [(col, col, 1.0, fee)]
    return name


def equal_weight_basket(tickers: list) -> dict:
    n = len(tickers)
    return {t: 1.0 / n for t in tickers}


def rank_shares(equity_df: pd.DataFrame, cpi_series: pd.Series, profile: ClientProfile,
                fee: float = DEFAULT_SHARE_FEE, method: str = "stationary_block",
                n_sims: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Task 13: 'test individual shares against the objective of avoiding running out of
    retirement income' - runs the full Monte Carlo decumulation simulation for every share in
    equity_df on its own (100% weight), ranked by probability of ruin (ascending - safest first),
    alongside the same downside stats used throughout the rest of this project."""
    names = register_shares(equity_df, fee=fee)
    rows = []
    for name in names:
        res = run_simulation(name, equity_df, cpi_series, profile, method=method, n_sims=n_sims, seed=seed)
        s = res.summary()
        dd = downside_stats(name, equity_df)
        rows.append({
            "Share": name.removeprefix(SHARE_PREFIX),
            "Probability of ruin": s["Probability of ruin"],
            "Median legacy": s["Median legacy"],
            "Max DD": dd["maxdd"],
            "Average DD": dd["avgdd"],
            "CVaR 95 Mthly": dd["cvar_m"],
        })
    return pd.DataFrame(rows).sort_values("Probability of ruin").reset_index(drop=True)


def evaluate_basket(name: str, weights: dict, equity_df: pd.DataFrame, cpi_series: pd.Series,
                     profile: ClientProfile, fee: float = DEFAULT_SHARE_FEE,
                     method: str = "stationary_block", n_sims: int = 2000, seed: int = 42,
                     rebalance: str = "monthly", buy_and_hold: bool = False):
    """Task 14/15: evaluate an arbitrary hand-built basket the same way a single share is
    evaluated, so baskets and individual shares are directly comparable on one scale.
    rebalance: 'monthly' (constant-mix, the default used elsewhere in this project), 'annual'
    (task 16's periodic-review alternative), or 'buy_and_hold' (never rebalances). The legacy
    buy_and_hold=True flag is still honoured (equivalent to rebalance='buy_and_hold')."""
    if buy_and_hold:
        rebalance = "buy_and_hold"
    if rebalance == "buy_and_hold":
        register_buy_and_hold_basket(name, weights, equity_df, fee=fee)
    elif rebalance == "annual":
        register_annual_rebalance_basket(name, weights, equity_df, fee=fee)
    elif rebalance == "monthly":
        register_basket(name, weights, fee=fee)
    else:
        raise ValueError(f"unknown rebalance mode: {rebalance!r}")
    res = run_simulation(name, equity_df, cpi_series, profile, method=method, n_sims=n_sims, seed=seed)
    dd = downside_stats(name, equity_df)
    return res, dd


def find_best_baskets(equity_df: pd.DataFrame, cpi_series: pd.Series, profile: ClientProfile,
                       basket_size: int = 3, fee: float = DEFAULT_SHARE_FEE,
                       method: str = "stationary_block", n_sims: int = 2000, seed: int = 42,
                       top_n: int = 5) -> pd.DataFrame:
    """Task 14: systematically search every equal-weight combination of `basket_size` shares (not
    just a hand-picked pair) and rank them by actual Monte Carlo probability of ruin - the true
    objective - rather than by a correlation proxy alone. Correlation is still a useful lens
    (see share_correlation_matrix) for understanding WHY a basket works, but this answers the
    actual question task 14 asks: which combinations perform best.

    Brute-force over all C(n, basket_size) combinations - fine for the ~10-share universe this
    project currently has; would need a smarter search (e.g. greedy forward selection) if the
    real Bloomberg universe (task 12) turns out to have hundreds of candidates."""
    tickers = list(equity_df.columns)
    for t in tickers:
        AC[t] = t
    rows = []
    for combo in combinations(tickers, basket_size):
        weights = equal_weight_basket(list(combo))
        w = pd.Series(weights)
        label = " + ".join(combo)
        res = run_simulation(label, equity_df, cpi_series, profile, method=method, n_sims=n_sims,
                              seed=seed, custom_weights=w, custom_fee=fee)
        s = res.summary()
        dd = downside_stats(label, equity_df, custom_weights=w, custom_fee=fee)
        rows.append({
            "Basket": label,
            "Probability of ruin": s["Probability of ruin"],
            "Median legacy": s["Median legacy"],
            "Max DD": dd["maxdd"],
            "Average DD": dd["avgdd"],
        })
    return pd.DataFrame(rows).sort_values("Probability of ruin").head(top_n).reset_index(drop=True)


def share_correlation_matrix(equity_df: pd.DataFrame) -> pd.DataFrame:
    """Pairwise correlation of the raw share return series - use alongside rank_shares() to spot
    which low-probability-of-ruin shares are ALSO poorly correlated with each other (i.e. worth
    combining into a basket for task 14), rather than picking the top-N safest shares blind, which
    could still leave a basket concentrated in one sector/factor. Unlike engine.asset_correlation_
    matrix (which relabels raw Bloomberg column codes to friendly names via AC), equity_df's
    columns already ARE the friendly ticker labels, so a plain .corr() is all that's needed."""
    return equity_df.corr()
