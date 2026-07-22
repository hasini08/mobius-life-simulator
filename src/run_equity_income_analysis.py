"""
Demo/sanity-check for the Week 5-8 individual-share decumulation framework (equity_income.py).
Uses PLACEHOLDER/SYNTHETIC share data (see generate_placeholder_equity_data.py) - replace with a
real Bloomberg export and re-run unchanged once task 12 is done.

Run: `python run_equity_income_analysis.py`
"""
import pandas as pd

from engine import load_asset_returns, load_cpi, ClientProfile
from equity_income import (
    load_equity_returns, load_share_metadata, rank_shares, evaluate_basket,
    share_correlation_matrix, find_best_baskets,
)

pd.set_option("display.width", 120)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")


def main():
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    equity_df = load_equity_returns()
    meta = load_share_metadata()

    profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=500_000.0,
                             initial_annual_spend=20_000.0)

    print("=" * 78)
    print("TASK 13 - individual shares vs the 'don't run out of money' objective")
    print("=" * 78)
    ranked = rank_shares(equity_df, cpi, profile)
    ranked = ranked.merge(meta[["Ticker", "Company", "Sector"]], left_on="Share", right_on="Ticker")
    ranked = ranked[["Share", "Company", "Sector", "Probability of ruin", "Median legacy",
                      "Max DD", "Average DD", "CVaR 95 Mthly"]]
    print(ranked.to_string(index=False))

    print()
    print("=" * 78)
    print("Correlation matrix (spot low-correlation pairs worth combining - task 14)")
    print("=" * 78)
    print(share_correlation_matrix(equity_df).round(2).to_string())

    print()
    print("=" * 78)
    print("TASK 14 - systematic basket search (every 3-share equal-weight combination, ranked by")
    print("actual probability of ruin - not just a hand-picked pair)")
    print("=" * 78)
    top_baskets = find_best_baskets(equity_df, cpi, profile, basket_size=3, top_n=5)
    print(top_baskets.to_string(index=False))

    best_basket = top_baskets.iloc[0]["Basket"]
    best_tickers = best_basket.split(" + ")
    weights = {t: 1.0 / len(best_tickers) for t in best_tickers}

    print()
    print("=" * 78)
    print(f"TASK 16 - best basket found ({best_basket}), compared across 3 rebalancing approaches")
    print("=" * 78)
    for label, mode in [("Constant-mix (rebalanced monthly)", "monthly"),
                        ("Annual rebalance", "annual"),
                        ("Buy-and-hold (never rebalanced)", "buy_and_hold")]:
        res, dd = evaluate_basket(f"Best basket ({mode})", weights, equity_df, cpi, profile, rebalance=mode)
        s = res.summary()
        print(f"{label:36s} Prob. of ruin: {s['Probability of ruin']:6.2%}   "
              f"Median legacy: £{s['Median legacy']:>10,.0f}   Max DD: {dd['maxdd']:7.2%}")


if __name__ == "__main__":
    main()
