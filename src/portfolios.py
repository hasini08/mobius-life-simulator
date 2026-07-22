"""
Defines the portfolios being compared in the model:

  - Original    : the current Growth Passive Plus fund lineup (retail/mainstream share classes)
  - Alternative : the tax-efficient unit-linked equivalent lineup (same market exposure, lower fees)
  - Four Seasons: Aspen Advisers' real multi-asset decumulation strategy
  - Better       : a more diversified allocation (adds REITs / infrastructure / commodities / EM /
                    index-linked gilts) in the spirit of the "Better" portfolio in the previous
                    Mobius model, which cut probability of ruin materially vs a plain equity/bond mix.

DATA-DRIVEN: portfolio holdings/weights/fees, the asset-class name mapping, and each portfolio's
display name/owner/provider live in data/portfolio_holdings.csv, data/asset_class_map.csv and
data/portfolio_meta.csv respectively - not hardcoded here - so a brand new portfolio (a different
competitor's fund, say) can be added directly (by hand, or via the app's in-app editor) without
touching this file or engine.py. This module just loads those sheets and exposes the same
functions/shape as before.

ASSUMPTIONS (clearly flagged - confirm/replace with real data where possible):
  1. Many individual fund return series in the Bloomberg data have short histories (some as little
     as 20 months). For the 25+-year Monte Carlo we need long, consistent history, so every holding
     is mapped to the best-matching BROAD ASSET CLASS series (which has full history back to
     1999/2000) rather than its own short fund history.
  2. Original vs Alternative largely hold the SAME underlying index exposure (FNZ data literally
     labels several pairs "Same Index") - the difference is fee (AMC), not market return.
  3. "Better" portfolio weights were tuned empirically by running candidate allocations through the
     simulation engine itself - REITs/Infrastructure are highly correlated with global equities here
     (~0.75-0.76), so genuine diversification came mostly from bonds/gilts/credit instead. NOT sourced
     from FNZ data - one reasonable construction, not the only one.
  4. Four Seasons holdings/weights are from the FNZ data supplied 14 July 2026; several holdings are
     mapped to "Commodities" as the closest available proxy for gold/natural-resources exposure.

See data/portfolio_holdings.csv and data/asset_class_map.csv for the full per-holding detail and
per-portfolio provenance notes.
"""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HOLDINGS_CSV = DATA_DIR / "portfolio_holdings.csv"
ASSET_MAP_CSV = DATA_DIR / "asset_class_map.csv"
PORTFOLIO_META_CSV = DATA_DIR / "portfolio_meta.csv"


def load_asset_class_map(path=ASSET_MAP_CSV) -> dict:
    df = pd.read_csv(path)
    return dict(zip(df["Label"], df["BloombergColumn"]))


def load_portfolios(path=HOLDINGS_CSV) -> dict:
    """Reads the long-format holdings sheet (Portfolio, Holding, AssetClass, Weight, OCF) into the
    same {name: [(holding, asset_class, weight, ocf), ...]} shape the rest of the codebase expects."""
    df = pd.read_csv(path)
    portfolios = {}
    for name, group in df.groupby("Portfolio", sort=False):
        portfolios[name] = list(
            group[["Holding", "AssetClass", "Weight", "OCF"]].itertuples(index=False, name=None)
        )
    return portfolios


def load_portfolio_meta(path=PORTFOLIO_META_CSV) -> dict:
    """Reads per-portfolio presentation metadata (DisplayName, Owner - 'Mobius' or 'Competitor',
    Provider - the fund house's name) into {name: {DisplayName, Owner, Provider}}. This is what
    lets the app compare Mobius against ANY registered competitor's portfolio, not just Aspen's -
    a new portfolio just needs a row here (and in portfolio_holdings.csv) to get correctly
    labelled/coloured everywhere, with no code changes. Portfolios missing a row here fall back to
    sensible defaults in the app (display name = the portfolio's own key, Owner = 'Competitor')."""
    df = pd.read_csv(path)
    return {row.Portfolio: {"DisplayName": row.DisplayName, "Owner": row.Owner, "Provider": row.Provider}
            for row in df.itertuples(index=False)}


AC = load_asset_class_map()
PORTFOLIOS = load_portfolios()
PORTFOLIO_META = load_portfolio_meta()


def portfolio_summary(name):
    rows = PORTFOLIOS[name]
    df = pd.DataFrame(rows, columns=["Holding", "AssetClass", "Weight", "OCF"])
    df["FeeContribution"] = df["Weight"] * df["OCF"]
    return df


def asset_class_weights(name):
    """Aggregate a portfolio's holdings into net asset-class weights (sums to 1.0)."""
    df = portfolio_summary(name)
    return df.groupby("AssetClass")["Weight"].sum()


def weighted_avg_fee(name):
    df = portfolio_summary(name)
    return df["FeeContribution"].sum() / df["Weight"].sum()


EQUITY_CLASSES = {
    "Global Equities", "EM Equities",
    # Mobius Better labels its equity sleeve under its own strategy names rather than
    # the generic "Global Equities"/"EM Equities" used by the Aspen portfolios.
    "Eq Gbl DM Novum Mgd Vol", "Eq Gbl DM Quality Gross", "Eq EM Net",
}


def scale_to_equity_weight(name, target_equity_weight):
    """Rescale a named portfolio's asset-class weight vector to hit a target TOTAL equity weight
    (Global Equities + EM Equities combined), preserving the relative split within the equity
    sleeve and within the non-equity sleeve. This replicates the previous model's equity-allocation
    sweep methodology (its pptx scanned equity weight 20-100% for each portfolio 'shape' to find
    where probability of ruin bottoms out) - `name` supplies the shape (e.g. Better's relative
    tilt toward REITs/credit/ILGs within its non-equity sleeve), `target_equity_weight` overrides
    the overall growth/defensive split.

    Returns a pandas Series of asset-class weights summing to 1.0.
    """
    base = asset_class_weights(name)
    is_equity = base.index.isin(EQUITY_CLASSES)
    base_equity_total = base[is_equity].sum()
    base_non_equity_total = base[~is_equity].sum()
    target_equity_weight = min(max(target_equity_weight, 0.0), 1.0)
    target_non_equity = 1.0 - target_equity_weight

    scaled = base.copy().astype(float)
    if base_equity_total > 0:
        scaled[is_equity] = base[is_equity] * (target_equity_weight / base_equity_total)
    if base_non_equity_total > 0:
        scaled[~is_equity] = base[~is_equity] * (target_non_equity / base_non_equity_total)
    return scaled


if __name__ == "__main__":
    for name in PORTFOLIOS:
        df = portfolio_summary(name)
        w = df["Weight"].sum()
        fee = weighted_avg_fee(name)
        print(f"\n=== {name} === total weight={w:.4f}  weighted-avg OCF={fee*100:.3f}% pa")
        print(asset_class_weights(name).round(4))
