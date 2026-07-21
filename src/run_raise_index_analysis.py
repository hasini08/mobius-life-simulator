"""
Tests the REAL FTSE Russell RAISE index constructions (see load_raise_index.py) against the
decumulation objective (avoiding running out of retirement income), reusing the exact same
Monte Carlo engine/registration mechanism as equity_income.py - no parallel simulation code.

Run: `python run_raise_index_analysis.py`
"""
import pandas as pd

from engine import load_asset_returns, load_cpi, ClientProfile, run_simulation, downside_stats
from equity_income import register_shares, SHARE_PREFIX

RAISE_RETURNS_CSV = "../data/equities/raise_index_returns.csv"

pd.set_option("display.width", 120)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")


def main():
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    raise_df = pd.read_csv(RAISE_RETURNS_CSV, index_col=0, parse_dates=True)

    profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=500_000.0,
                             initial_annual_spend=20_000.0)

    names = register_shares(raise_df, fee=0.0010)

    print("=" * 90)
    print("RAISE index constructions vs the 'don't run out of retirement income' objective")
    print(f"(real FTSE Russell simulation data, {raise_df.index.min().date()} to "
          f"{raise_df.index.max().date()}, {len(raise_df)} months)")
    print("=" * 90)

    rows = []
    for name in names:
        res = run_simulation(name, raise_df, cpi, profile, n_sims=2000, seed=42)
        s = res.summary()
        dd = downside_stats(name, raise_df)
        rows.append({
            "Index": name.removeprefix(SHARE_PREFIX),
            "Probability of ruin": s["Probability of ruin"],
            "Median legacy": s["Median legacy"],
            "Max DD": dd["maxdd"],
            "Average DD": dd["avgdd"],
            "CVaR 95 Mthly": dd["cvar_m"],
        })
    ranked = pd.DataFrame(rows).sort_values("Probability of ruin").reset_index(drop=True)
    print(ranked.to_string(index=False))


if __name__ == "__main__":
    main()
