"""Forward-looking Capital Market Assumptions (CMA) blending.

The historical bootstrap (2000-2026) that drives the rest of this model is a real, data-honest
sample - but it is ONE 26-year window, and it happens to span an unusually strong run for global
equities (the post-2009 bull market dominates the sample). A plan that looks safe purely off that
window may be flattered by it. The standard way planning tools address this is to blend the
historical distribution with independent, forward-looking 10-year return forecasts published by
asset managers - not to replace history (which correctly captures volatility, drawdowns, and how
asset classes move together), just to optionally recentre its AVERAGE return towards what
forecasters currently expect going forward.

SOURCE: Monevator's "Investment return forecasts and assumptions" compilation (accessed 2026),
https://monevator.com/investment-return-forecasts/ - a median-across-provider summary of published
10-year nominal return forecasts (Vanguard, Schroders, JPMorgan, BlackRock, and others), a widely-
used independent secondary source for UK retail/adviser planning since primary providers publish
in incompatible formats (some GBP, some USD, some real, some nominal, some behind paywalls).
Figures below are nominal, GBP, 10-year forecasts as compiled there.

Three of this model's eleven asset classes have no direct match in the compiled sources (they are
narrower sub-categories than what forecasters typically publish) and are proxied from the closest
available category - flagged individually below. This is a simplification, not a real forecast for
those three classes specifically, and should be revisited if better data becomes available.

METHODOLOGY: blend_weight in [0, 1] (0 = pure historical bootstrap, exactly today's model; 1 = pure
forward-looking). For each asset class, convert its CMA annual figure to an implied monthly mean
(compounding), compare that to the SAME asset class's actual historical monthly mean over the
sample, and shift every monthly return in the historical series by
    blend_weight * (mu_cma_monthly - mu_hist_monthly)
This is an additive, constant shift applied uniformly across the whole history for that asset
class - it recentres the mean exactly to the target (at blend_weight=1) while leaving volatility,
skew, fat tails, and month-to-month/cross-asset correlation structure UNCHANGED (a shift changes
nothing about how returns disperse or co-move, only their average). It is the standard, simplest
way to blend a historical bootstrap with a forward-looking view without inventing a new synthetic
distribution.
"""
import pandas as pd

# Forward-looking 10-year nominal GBP annual return forecasts, mapped onto this model's asset
# classes (see src/portfolios.py's AC dict for the exact column-name mapping applied downstream).
# Source: Monevator compilation (median across Vanguard / Schroders / JPMorgan / BlackRock / other
# published CMAs), https://monevator.com/investment-return-forecasts/, accessed 2026.
CMA_ANNUAL = {
    "Global Equities": 0.054,        # "Global equities"
    "EM Equities": 0.083,            # "Emerging markets"
    "Global Bonds": 0.046,           # "Global government bonds, £ hedged"
    "UK Gilts All Stocks": 0.045,    # "UK government bonds"
    # No duration-specific (15yr+) forecast published anywhere in the compiled sources - proxied
    # with the same all-stocks UK gilts figure. In reality longer-duration gilts would be expected
    # to carry a somewhat higher (but also more volatile) forward yield; treat this as conservative.
    "UK Gilts 15yr+": 0.045,
    "UK Index-Linked Gilts": 0.056,  # "Inflation-linked bonds"
    # No direct "securitised credit" forecast published - proxied with the Global Bonds figure as
    # the closest published fixed-income category (both are diversified, investment-grade-led debt).
    "Securitised Credit": 0.046,
    "REITs": 0.064,                  # "Global REITs" / "Global property"
    # No direct "listed infrastructure" forecast published - proxied with the Global REITs figure,
    # the closest published "real assets" category (both are equity-like, income-generating,
    # inflation-linked real-asset exposures).
    "Infrastructure": 0.064,
    "Commodities": 0.056,            # "Commodities"
    "Cash": 0.031,                   # "Cash"
}

PROXIED_ASSET_CLASSES = {"UK Gilts 15yr+", "Securitised Credit", "Infrastructure"}


def _annual_to_monthly(annual_rate: float) -> float:
    """Compounding conversion: (1+annual)^(1/12) - 1."""
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def cma_shifts(asset_df: pd.DataFrame, ac_map: dict) -> dict:
    """For each asset class in ac_map (label -> column name in asset_df), compute the FULL
    (blend_weight=1) monthly-mean shift: mu_cma_monthly - mu_hist_monthly. Returns {label: shift}."""
    shifts = {}
    for label, col in ac_map.items():
        if label not in CMA_ANNUAL or col not in asset_df.columns:
            continue
        hist_monthly_mean = asset_df[col].dropna().mean()
        cma_monthly_mean = _annual_to_monthly(CMA_ANNUAL[label])
        shifts[label] = cma_monthly_mean - hist_monthly_mean
    return shifts


def apply_cma_blend(asset_df: pd.DataFrame, ac_map: dict, blend_weight: float) -> pd.DataFrame:
    """Return a COPY of asset_df with each mapped asset-class column's monthly returns shifted by
    blend_weight * (cma_monthly_mean - hist_monthly_mean). blend_weight=0 returns asset_df
    unchanged (by value); blend_weight=1 recentres each asset class's mean exactly to its CMA
    figure; values in between interpolate linearly. Volatility/shape/correlation are untouched -
    only the mean shifts. Columns not in ac_map (e.g. CPI) are left untouched."""
    if blend_weight == 0:
        return asset_df.copy()
    shifted = asset_df.copy()
    shifts = cma_shifts(asset_df, ac_map)
    for label, col in ac_map.items():
        if label not in shifts:
            continue
        shifted[col] = asset_df[col] + blend_weight * shifts[label]
    return shifted


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from engine import load_asset_returns
    from portfolios import AC

    asset_df = load_asset_returns()

    # blend_weight=0 must leave asset_df numerically unchanged
    z = apply_cma_blend(asset_df, AC, 0.0)
    for label, col in AC.items():
        if col in asset_df.columns:
            assert (z[col].dropna() == asset_df[col].dropna()).all(), f"blend=0 changed {label}"
    print("blend_weight=0 leaves asset_df unchanged: OK")

    # blend_weight=1 must recentre each mapped class's monthly mean to exactly the CMA figure
    full = apply_cma_blend(asset_df, AC, 1.0)
    for label, col in AC.items():
        if label not in CMA_ANNUAL or col not in asset_df.columns:
            continue
        target = _annual_to_monthly(CMA_ANNUAL[label])
        actual = full[col].dropna().mean()
        assert abs(actual - target) < 1e-9, f"{label}: {actual} != {target}"
    print("blend_weight=1 recentres every mapped asset class exactly to its CMA monthly mean: OK")

    # intermediate weights interpolate linearly
    half = apply_cma_blend(asset_df, AC, 0.5)
    label, col = "Global Equities", AC["Global Equities"]
    hist_mean = asset_df[col].dropna().mean()
    cma_mean = _annual_to_monthly(CMA_ANNUAL[label])
    expected_half_mean = hist_mean + 0.5 * (cma_mean - hist_mean)
    actual_half_mean = half[col].dropna().mean()
    assert abs(actual_half_mean - expected_half_mean) < 1e-9
    print("blend_weight=0.5 interpolates linearly: OK")

    print("\nFull (blend_weight=1) annualised CMA figures vs historical sample CAGR:")
    for label, col in AC.items():
        if label not in CMA_ANNUAL or col not in asset_df.columns:
            continue
        hist_monthly_mean = asset_df[col].dropna().mean()
        hist_annual = (1 + hist_monthly_mean) ** 12 - 1
        flag = " (proxied)" if label in PROXIED_ASSET_CLASSES else ""
        print(f"  {label:24s} hist~{hist_annual*100:5.1f}% pa  ->  CMA {CMA_ANNUAL[label]*100:5.1f}% pa{flag}")

    print("\nAll cma.py self-tests passed.")
