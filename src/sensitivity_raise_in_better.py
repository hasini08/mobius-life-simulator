"""
Sensitivity check for the Better + RAISE swap (see test_raise_in_better.py): is RAISE + Mom
Leaders still the best "real assets" replacement across a range of withdrawal rates, and does it
still hold up on a shorter, more recent slice of history rather than the full backtest window?
Reuses the same custom_weights mechanism - no parallel code.

Run: `python sensitivity_raise_in_better.py`
"""
import pandas as pd

from engine import load_asset_returns, load_cpi, ClientProfile, run_simulation
from portfolios import AC, asset_class_weights, weighted_avg_fee

RAISE_RETURNS_CSV = "../data/equities/raise_index_returns.csv"
COMMOD_WEIGHT = 0.05
POT = 500_000.0

pd.set_option("display.width", 120)


def build_variants(base_weights):
    variants = {"Better (Commod, current)": None}
    for label, replacement in [("Better + RAISE", "RAISE"),
                                ("Better + RAISE Mom Leaders", "RAISE + Mom Leaders"),
                                ("Better + RAISE Low Vol Leaders", "RAISE + Low Vol Leaders")]:
        variants[label] = replacement
    weights_by_variant = {}
    for label, replacement in variants.items():
        w = base_weights.copy()
        if replacement is not None:
            w = w.drop("Commod")
            w[replacement] = COMMOD_WEIGHT
        weights_by_variant[label] = w
    return weights_by_variant


def main():
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    raise_df = pd.read_csv(RAISE_RETURNS_CSV, index_col=0, parse_dates=True)
    blended_df = asset_df.join(raise_df, how="outer")
    for col in raise_df.columns:
        AC[col] = col

    better_fee = weighted_avg_fee("Better")
    base_weights = asset_class_weights("Better")
    weights_by_variant = build_variants(base_weights)

    print("=" * 100)
    print("SENSITIVITY 1 - withdrawal rate sweep (full window, age 65, 30-year horizon)")
    print("=" * 100)
    wr_rows = []
    for wr_pct in [3.0, 3.5, 4.0, 4.5, 5.0]:
        row = {"Withdrawal rate": f"{wr_pct:.1f}%"}
        for label, weights in weights_by_variant.items():
            profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=POT,
                                     initial_annual_spend=POT * wr_pct / 100)
            res = run_simulation(label, blended_df, cpi, profile, n_sims=2000, seed=42,
                                  custom_weights=weights, custom_fee=better_fee)
            row[label] = res.summary()["Probability of ruin"]
        wr_rows.append(row)
    wr_df = pd.DataFrame(wr_rows).set_index("Withdrawal rate")
    print(wr_df.to_string(formatters={c: "{:.1%}".format for c in wr_df.columns}))

    print()
    print("=" * 100)
    print("SENSITIVITY 2 - recent-window check (last ~10 years of the common overlap, 4% withdrawal)")
    print("=" * 100)
    full_min = blended_df.dropna(subset=list(weights_by_variant["Better + RAISE"].index)).index.min()
    full_max = blended_df.dropna(subset=list(weights_by_variant["Better + RAISE"].index)).index.max()
    print(f"Full common window: {full_min.date()} to {full_max.date()}")
    recent_start = full_max - pd.DateOffset(years=10)
    recent_df = blended_df[blended_df.index >= recent_start]
    print(f"Recent window used: {recent_start.date()} to {full_max.date()}")

    recent_rows = []
    for label, weights in weights_by_variant.items():
        profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=POT, initial_annual_spend=20_000.0)
        res_full = run_simulation(label, blended_df, cpi, profile, n_sims=2000, seed=42,
                                   custom_weights=weights, custom_fee=better_fee)
        res_recent = run_simulation(label, recent_df, cpi, profile, n_sims=2000, seed=42,
                                     custom_weights=weights, custom_fee=better_fee)
        recent_rows.append({
            "Variant": label,
            "Prob. of ruin (full window)": res_full.summary()["Probability of ruin"],
            "Prob. of ruin (last 10yr)": res_recent.summary()["Probability of ruin"],
        })
    recent_df_out = pd.DataFrame(recent_rows)
    print(recent_df_out.to_string(index=False, formatters={
        "Prob. of ruin (full window)": "{:.1%}".format,
        "Prob. of ruin (last 10yr)": "{:.1%}".format,
    }))


if __name__ == "__main__":
    main()
