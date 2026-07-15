"""
Defines the three portfolios being compared in the new model:

  - Original    : the current Growth Passive Plus fund lineup (retail/mainstream share classes)
  - Alternative : the tax-efficient unit-linked equivalent lineup (same market exposure, lower fees)
  - Better       : a more diversified allocation (adds REITs / infrastructure / commodities / EM /
                    index-linked gilts) in the spirit of the "Better" portfolio in the previous
                    Mobius model, which cut probability of ruin materially vs a plain equity/bond mix.

ASSUMPTIONS (clearly flagged - confirm/replace with real data where possible):
  1. Many individual fund return series in the Bloomberg data have short histories (some as little
     as 20 months). For the 25+-year Monte Carlo we need long, consistent history, so every holding
     is mapped to the best-matching BROAD ASSET CLASS series (which has full history back to
     1999/2000) rather than its own short fund history. Two fund-level series with long history
     (HSBC European Index Fund, iShares Japan Equity Index Fund, both back to 1999) are used to
     sanity-check the mapping but the simulation itself runs on asset-class returns for consistency.
  2. Original vs Alternative largely hold the SAME underlying index exposure (FNZ data literally
     labels several pairs "Same Index") - the difference is fee (AMC), not market return. So both
     portfolios are built on the same asset-class gross returns, and diverge only through the fee
     drag applied (Alternative AMCs are given in the source data; Original OCFs are NOT given, so
     representative typical retail OCFs are assumed per fund type - flagged below).
  3. "Better" portfolio weights were tuned empirically by running candidate allocations through the
     simulation engine itself (see dev notes), not just copied from the previous model's narrative.
     One important, honest finding from doing this with the REAL 2000-2026 Bloomberg data: REITs and
     Infrastructure indices here are highly correlated with global equities (~0.75-0.76), so they do
     NOT diversify the way the previous model's pptx narrative implied - they behave like equity risk.
     Genuine diversification benefit over this sample came mostly from bonds/gilts/credit (low
     correlation, ~0.0-0.25) - but those also have materially lower CAGR (~2-4.5% pa vs ~7.6-8.9% pa
     for equities), so heavy diversification trades away return without reliably cutting ruin
     probability at this client's ~4% withdrawal rate. The tuned "Better" mix keeps meaningful growth
     exposure and adds a genuine (bond/credit-led) diversifying sleeve, landing close to Original on
     ruin probability while offering better true diversification - and pairs well with the guardrails
     feature, which is the more powerful lever for cutting ruin probability in this data (see Summary
     sheet / app: guardrails can take probability of ruin to ~0% at the cost of ~2-3 years of reduced
     spending). This is NOT sourced from the FNZ data and should be sanity-checked / adjusted by the
     team - it is one reasonable construction, not the only one.
"""
import pandas as pd

AC = {
    "Global Equities": "Global Equities — MSCI World Net TR (NDDUWI Index)",
    "EM Equities": "MSCI Emerging Markets Index",
    "Global Bonds": "Global Bonds — Bloomberg Global Agg TR (LEGATRUU Index)",
    "UK Gilts All Stocks": "UK Gilts All Stocks — FTSE Actuaries UK Conventional Gilts All Stocks (FTFIBGT Index)",
    "UK Gilts 15yr+": "UK Gilts Over 15 Years — FTSE Actuaries UK Conventional Gilts Over 15 Years (FTRFBGH Index)",
    "UK Index-Linked Gilts": "FTSE Actuaries Govt Securities UK Index Linked TR Over 5 Yr",
    "Securitised Credit": "Securitised Credit — Bloomberg US Securitized TR (I05582GB Index)",
    "REITs": "FTSE EPRA NAREIT Developed Total Return Index USD (RUGL)",
    "Infrastructure": "Infrastructure Equities — MSCI World Infrastructure Net Total Return USD Index (M1W0OINF)",
    "Commodities": "Commodities — Bloomberg Commodity TR (BCOMTR Index)",
    # NOTE: the source "Cash (GBP) — SONIA / short rate proxy" column in the Bloomberg data is NOT a
    # valid monthly return series (it looks like a naive %-change of the SONIA rate level itself,
    # producing nonsensical swings of +100%/+300% in a single month for what should be a ~4-5% pa
    # cash rate). Rebuilt properly using the real Bank of England SONIA rate (IUDSOIA, via FRED/BoE
    # database export supplied by the user), converted from an annualised daily rate to a monthly-
    # equivalent return: (1+rate/100)^(1/12)-1. That download only covers Jan 2014 onward, so for
    # 2000-2013 (pre-file period) it's spliced with the "Blackrock ICS Sterling Liquidity Fund"
    # money-market fund return series as a proxy - validated the two are 99.7% correlated and closely
    # matched in level over the 2014-2026 overlap, so the splice doesn't introduce a visible seam.
    # See src/extract_data.py / the sonia_monthly.csv build step for the splice.
    "Cash": "Cash (GBP) - SONIA-based, spliced with Blackrock ICS proxy pre-2014",
    # Added with the Four Seasons Fund (14 July 2026 Bloomberg pull) - two long-history (1999/2000-2026)
    # broad-index proxies for holdings the Four Seasons Fund uses that Growth Passive Plus didn't.
    "UK Gilts <5yr": "FTSE Actuaries UK Conventional Gilts up to 5 Years Index",
    "US Treasuries 20yr+": "Bloomberg US Treasury 20+ year Index",
}

# ---------------------------------------------------------------------------
# ORIGINAL portfolio (Growth Passive Plus, mainstream fund lineup)
# holding -> (asset class mapped to, weight, assumed retail OCF % pa)
# OCF assumptions are typical published figures for this fund type as of 2025/26 - CONFIRM against
# actual factsheets before relying on this for client-facing output.
# ---------------------------------------------------------------------------
ORIGINAL = [
    # name, asset_class, weight, ocf
    ("Cash",                                                  "Cash",                   0.0150, 0.0000),
    ("L&G All Stocks Gilt Index Trust Fund",                   "UK Gilts All Stocks",    0.0150, 0.0012),
    ("Vanguard U.K. Short-Term Gilt Index Fund",                "UK Gilts All Stocks",    0.0125, 0.0012),
    ("L&G Short Dated Sterling Corporate Bond Index Fund",      "Global Bonds",           0.0275, 0.0016),
    ("abrdn Global Inflation-Linked Bond Tracker Fund",         "UK Index-Linked Gilts",  0.0225, 0.0016),
    ("abrdn Short Dated Global Inflation-Linked Bond Tracker",  "UK Index-Linked Gilts",  0.0225, 0.0016),
    ("Vanguard Global Bond Fund",                               "Global Bonds",           0.0425, 0.0015),
    ("Vanguard Global Short-Term Bond Index Fund",              "Global Bonds",           0.0425, 0.0015),
    ("iShares Environment & Low Carbon Tilt Real Estate Index Fund", "REITs",             0.0250, 0.0020),
    ("abrdn Global Infrastructure Equity Tracker Fund",         "Infrastructure",         0.0550, 0.0016),
    ("Vanguard FTSE U.K. All Share Index Fund",                 "Global Equities",        0.1000, 0.0006),
    ("HSBC FTSE All-World Index Fund",                          "Global Equities",        0.1700, 0.0013),
    ("Fidelity Index World Fund",                               "Global Equities",        0.0600, 0.0012),
    ("L&G S&P 500 US Equal Weight Index Fund",                  "Global Equities",        0.0700, 0.0030),
    ("HSBC European Index Fund",                                "Global Equities",        0.0800, 0.0013),
    ("iShares Japan Equity Index Fund",                         "Global Equities",        0.0500, 0.0018),
    ("UBS MSCI World Minimum Volatility Index Equity Fund",     "Global Equities",        0.0400, 0.0030),
    ("Vanguard Emerging Markets Stock Index Fund",              "EM Equities",            0.1500, 0.0022),
]

# ---------------------------------------------------------------------------
# ALTERNATIVE portfolio (unit-linked, tax/cost-efficient lineup) - same market exposure as Original.
# Per-holding AMCs were originally sourced directly from the FNZ data (verified individually - see
# the data-verification pass in this project's history); overridden below to a FLAT 7bps (0.07% pa)
# per holding, per instruction, reflecting Mobius's platform/institutional pricing rather than each
# fund's own retail AMC. Since every holding now carries the same OCF, the portfolio's weighted-
# average fee is exactly 7bps regardless of weighting. Original FNZ-sourced AMCs, for reference:
# Cash 0%, L&G L AA All Stocks Gilt 0.0375%, L&G BS 0-5Yr Gilts 0.0375%, L&G CSAJ Corp Bond 0.14%,
# abrdn Inflation-Linked 0.07%, abrdn Short Dated Inflation-Linked 0.10%, Vanguard Global Bond 0.10%,
# Vanguard Global Short-Term Bond 0.10%, iShares Env&LowCarbon REIT 0.10%, abrdn Infrastructure 0.10%,
# L&G N UK Equity 0.029%, BLK Aquila World 0.04%, BLK Aquila MSCI World 0.06%, HSBC S&P500 EW 0.06%,
# BLK Aquila European 0.04%, BLK Aquila Japanese 0.04%, L&G GPBV Min Vol 0.15%, BLK AQC EM 0.14%.
# ---------------------------------------------------------------------------
FLAT_MOBIUS_FEE = 0.0007

ALTERNATIVE = [
    ("Cash",                                                    "Cash",                   0.0150, FLAT_MOBIUS_FEE),
    ("L&G L AA All Stocks Gilt Index Fund",                     "UK Gilts All Stocks",    0.0150, FLAT_MOBIUS_FEE),
    ("L&G BS 0 to 5 Year Gilts Index Fund",                     "UK Gilts All Stocks",    0.0130, FLAT_MOBIUS_FEE),
    ("L&G CSAJ Short Dated Sterling Corporate Bond Index Fund", "Global Bonds",           0.0280, FLAT_MOBIUS_FEE),
    ("abrdn Global Inflation-Linked Bond Tracker Fund",         "UK Index-Linked Gilts",  0.0225, FLAT_MOBIUS_FEE),
    ("abrdn Short Dated Global Inflation-Linked Bond Tracker",  "UK Index-Linked Gilts",  0.0225, FLAT_MOBIUS_FEE),
    ("Vanguard Global Bond Fund",                               "Global Bonds",           0.0425, FLAT_MOBIUS_FEE),
    ("Vanguard Global Short-Term Bond Index Fund",              "Global Bonds",           0.0425, FLAT_MOBIUS_FEE),
    ("iShares Environment & Low Carbon Tilt Real Estate Index Fund", "REITs",             0.0250, FLAT_MOBIUS_FEE),
    ("abrdn Global Infrastructure Equity Tracker Fund",         "Infrastructure",         0.0550, FLAT_MOBIUS_FEE),
    ("L&G N UK Equity Index Fund",                              "Global Equities",        0.1000, FLAT_MOBIUS_FEE),
    ("BLK Aquila Life World Index Fund",                        "Global Equities",        0.1530, FLAT_MOBIUS_FEE),
    ("BLK Aquila Life MSCI World Fund",                         "Global Equities",        0.0600, FLAT_MOBIUS_FEE),
    ("HSBC S&P 500 Equal Weight Equity fund",                   "Global Equities",        0.0700, FLAT_MOBIUS_FEE),
    ("BLK Aquila Life European Equity Index Fund",              "Global Equities",        0.0800, FLAT_MOBIUS_FEE),
    ("BLK Aquila Life Japanese Equity Fund",                    "Global Equities",        0.0500, FLAT_MOBIUS_FEE),
    ("L&G GPBV MSCI Minimum Volatility Index Fund",             "Global Equities",        0.0400, FLAT_MOBIUS_FEE),
    ("BLK AQC Emerging Markets Fund",                           "EM Equities",            0.1670, FLAT_MOBIUS_FEE),
]

# ---------------------------------------------------------------------------
# BETTER portfolio - diversified allocation (judgement-based construction, see module docstring).
# Per-holding fee also set to the flat 7bps Mobius rate, per instruction (was previously a per-
# holding assumption ranging ~0.13%-0.22%, since this portfolio isn't FNZ-sourced to begin with).
# ---------------------------------------------------------------------------
BETTER = [
    ("Global equities (core)",   "Global Equities",        0.45, FLAT_MOBIUS_FEE),
    ("EM equities",              "EM Equities",             0.12, FLAT_MOBIUS_FEE),
    ("Global REITs",             "REITs",                   0.05, FLAT_MOBIUS_FEE),
    ("Global infrastructure",    "Infrastructure",          0.03, FLAT_MOBIUS_FEE),
    ("Global bonds (agg)",       "Global Bonds",            0.15, FLAT_MOBIUS_FEE),
    ("UK index-linked gilts",    "UK Index-Linked Gilts",   0.08, FLAT_MOBIUS_FEE),
    ("Securitised / diversified credit", "Securitised Credit", 0.10, FLAT_MOBIUS_FEE),
    ("Cash",                     "Cash",                    0.02, FLAT_MOBIUS_FEE),
]

# ---------------------------------------------------------------------------
# FOUR SEASONS FUND - Aspen Advisers' actual multi-asset decumulation strategy (real named product,
# see aspenadvisers.com), holdings/weights from the FNZ data supplied 14 July 2026. No AMC/OCF column
# was supplied for this fund (same situation as Original) - typical published OCFs for each fund TYPE
# are assumed below (documented per-holding) - CONFIRM against actual factsheets before relying on
# this for client-facing output. Several holdings have short return histories (as little as ~1 year -
# see the FNZ Comments column) so, per this model's established convention, every holding is mapped
# to the best-matching BROAD ASSET CLASS series rather than its own short fund history; several map
# to "Commodities" as the closest available proxy for gold/natural-resources exposure, since no
# dedicated gold or energy/resources index exists in this dataset - a rougher approximation than most
# of this model's other mappings, worth flagging if this fund's precise commodity/gold split matters.
# ---------------------------------------------------------------------------
FOUR_SEASONS = [
    # name, asset_class, weight, ocf
    ("Cash",                                                      "Cash",                  0.0050, 0.0000),
    ("abrdn Global Inflation-Linked Bond Tracker Fund",           "UK Index-Linked Gilts",  0.0850, 0.0016),
    ("abrdn Short-dated Global Inflation-Linked Bond Fund",       "UK Index-Linked Gilts",  0.0925, 0.0016),
    ("Artemis Short-Duration Strategic Bond Fund",                "Global Bonds",           0.0550, 0.0045),
    ("Royal London Short Term Fixed Income Fund",                 "Global Bonds",           0.0450, 0.0030),
    ("Fidelity Index Global Government Bond Fund",                "Global Bonds",           0.0600, 0.0015),
    ("iShares Corporate Bond Index Fund",                         "Global Bonds",           0.0450, 0.0020),
    ("iShares Over 15 Years Gilts Index (UK) Fund",                "UK Gilts 15yr+",        0.0300, 0.0020),
    ("Vanguard U.K. Short-Term Gilt Index Fund",                   "UK Gilts <5yr",         0.1125, 0.0012),
    ("iShares $ Treasury Bond 20+yr ETF",                          "US Treasuries 20yr+",   0.0300, 0.0007),
    ("abrdn Global Infrastructure Equity Tracker Fund",           "Infrastructure",          0.0075, 0.0016),
    ("Dimensional Global Value Fund",                             "Global Equities",        0.0450, 0.0030),
    ("GMO Quality Fund",                                          "Global Equities",        0.0450, 0.0035),
    ("HSBC European Index Fund",                                  "Global Equities",        0.0150, 0.0013),
    ("HSBC FTSE All-World Index Fund",                             "Global Equities",       0.0175, 0.0013),
    ("Invesco MSCI World Equal Weight UCITS ETF",                  "Global Equities",       0.0200, 0.0030),
    ("iShares Japan Equity Index Fund",                            "Global Equities",       0.0125, 0.0018),
    ("UBS MSCI World Minimum Volatility Index Fund",               "Global Equities",       0.0500, 0.0030),
    ("Vanguard FTSE All Share Index Fund",                         "Global Equities",       0.0250, 0.0006),
    ("Vanguard Global Small-Cap Index Fund",                       "Global Equities",       0.0225, 0.0029),
    ("Xtrackers MSCI World Energy UCITS ETF",                      "Global Equities",       0.0125, 0.0030),
    ("Pacific North of South Global Emerging Markets Equity Fund", "EM Equities",           0.0200, 0.0090),
    ("Vanguard Emerging Markets Stock Index Fund",                 "EM Equities",           0.0200, 0.0022),
    ("Invesco Bloomberg Commodity UCITS ETF",                      "Commodities",           0.0150, 0.0019),
    ("Man Global Resources Equity Fund",                           "Commodities",           0.0125, 0.0080),
    ("Neuberger Berman Commodities Fund",                          "Commodities",           0.0250, 0.0085),
    ("iShares Physical Gold ETC",                                  "Commodities",           0.0375, 0.0012),
    ("WisdomTree Physical Gold - GBP Daily Hedged - ETC",          "Commodities",           0.0375, 0.0025),
]

PORTFOLIOS = {"Original": ORIGINAL, "Alternative": ALTERNATIVE, "Better": BETTER, "Four Seasons": FOUR_SEASONS}


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


EQUITY_CLASSES = {"Global Equities", "EM Equities"}


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
