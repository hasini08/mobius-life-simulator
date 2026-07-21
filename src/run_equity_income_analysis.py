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
    equal_weight_basket, share_correlation_matrix,
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

    safest = ranked.iloc[0]["Share"]
    # pick the safest share from a DIFFERENT sector than the single safest share, so the demo
    # basket actually diversifies rather than doubling up on the same factor exposure
    safest_sector = ranked.iloc[0]["Sector"]
    other_sector_rows = ranked[ranked["Sector"] != safest_sector]
    partner = other_sector_rows.iloc[0]["Share"]
    basket_tickers = [safest, partner]
    weights = equal_weight_basket(basket_tickers)

    print()
    print("=" * 78)
    print(f"TASK 14/15 - equal-weight basket of the 2 safest, differently-sectored shares: {basket_tickers}")
    print("=" * 78)
    res, dd = evaluate_basket("Demo Basket (constant-mix)", weights, equity_df, cpi, profile)
    s = res.summary()
    print(f"Probability of ruin: {s['Probability of ruin']:.2%}   Median legacy: £{s['Median legacy']:,.0f}")
    print(f"Max DD: {dd['maxdd']:.2%}   Average DD: {dd['avgdd']:.2%}   CVaR 95 Mthly: {dd['cvar_m']:.2%}")

    print()
    print("=" * 78)
    print("TASK 16 - same basket, buy-and-hold (no rebalancing) instead of constant-mix")
    print("=" * 78)
    res_bh, dd_bh = evaluate_basket("Demo Basket (buy-and-hold)", weights, equity_df, cpi, profile,
                                     buy_and_hold=True)
    s_bh = res_bh.summary()
    print(f"Probability of ruin: {s_bh['Probability of ruin']:.2%}   Median legacy: £{s_bh['Median legacy']:,.0f}")
    print(f"Max DD: {dd_bh['maxdd']:.2%}   Average DD: {dd_bh['avgdd']:.2%}   CVaR 95 Mthly: {dd_bh['cvar_m']:.2%}")


if __name__ == "__main__":
    main()
