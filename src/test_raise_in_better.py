"""
Tests replacing Mobius Better's 5% Commodities holding with each RAISE variant in turn, to see
whether a real momentum/low-vol/plain factor-index sleeve is a better "real assets" substitute
than commodities for the decumulation objective. Reuses run_simulation's own custom_weights/
custom_fee override (the same mechanism the app's equity-allocation sweep uses) rather than a
parallel blending implementation - RAISE's return series is merged into a COPY of the main
asset_df so it can sit alongside Better's other 10 holdings in one weighted blend.

Run: `python test_raise_in_better.py`
"""
import pandas as pd

from engine import load_asset_returns, load_cpi, ClientProfile, run_simulation, downside_stats
from portfolios import AC, asset_class_weights, weighted_avg_fee

RAISE_RETURNS_CSV = "../data/equities/raise_index_returns.csv"
COMMOD_WEIGHT = 0.05  # Better's current "real assets" slot being tested for a replacement

pd.set_option("display.width", 120)


def main():
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    raise_df = pd.read_csv(RAISE_RETURNS_CSV, index_col=0, parse_dates=True)

    # Merge RAISE's columns into a COPY of the main asset_df so weighted_monthly_returns can
    # resolve them alongside Better's other 10 holdings in one blend (outer join on date).
    blended_df = asset_df.join(raise_df, how="outer")
    for col in raise_df.columns:
        AC[col] = col  # identity mapping, same pattern as the Better v4 migration

    better_fee = weighted_avg_fee("Better")
    base_weights = asset_class_weights("Better")
    assert abs(base_weights.get("Commod", 0) - COMMOD_WEIGHT) < 1e-9, "Commod weight assumption stale"

    profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=500_000.0,
                             initial_annual_spend=20_000.0)

    variants = {
        "Better (Commod, current)": None,
        "Better + RAISE": "RAISE",
        "Better + RAISE Mom Leaders": "RAISE + Mom Leaders",
        "Better + RAISE Low Vol Leaders": "RAISE + Low Vol Leaders",
    }

    rows = []
    for label, replacement in variants.items():
        weights = base_weights.copy()
        if replacement is not None:
            weights = weights.drop("Commod")
            weights[replacement] = COMMOD_WEIGHT
        res = run_simulation(label, blended_df, cpi, profile, n_sims=2000, seed=42,
                              custom_weights=weights, custom_fee=better_fee)
        s = res.summary()
        dd = downside_stats(label, blended_df, custom_weights=weights, custom_fee=better_fee)
        monthly = weights  # keep for window reporting below
        rows.append({
            "Variant": label,
            "Probability of ruin": s["Probability of ruin"],
            "Median legacy": s["Median legacy"],
            "Max DD": dd["maxdd"],
            "Average DD": dd["avgdd"],
        })

    df = pd.DataFrame(rows)
    print("=" * 100)
    print("Mobius Better: Commodities (5%) vs each RAISE variant (5%) - same scenario as the app")
    print("(age 65, £500,000 pot, £20,000/yr, 30-year horizon)")
    print("=" * 100)
    print(df.to_string(index=False, formatters={
        "Probability of ruin": "{:.1%}".format,
        "Median legacy": "£{:,.0f}".format,
        "Max DD": "{:.1%}".format,
        "Average DD": "{:.1%}".format,
    }))


if __name__ == "__main__":
    main()
