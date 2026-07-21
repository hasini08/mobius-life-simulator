"""
One-off: generates PLACEHOLDER/SYNTHETIC UK share return data so the Week 5-8 individual-share
decumulation framework (equity_income.py) can be built and tested before real Bloomberg
company-level data is available (per the internship plan, task 12 - "Download UK equity/
company-level data from Bloomberg" - is Hasini's own follow-up step, not something this script
does).

NOT real securities. Ticker codes are prefixed "PH-" (placeholder) and company names are
fictional, precisely so nobody mistakes this for real market data. Returns are simulated from a
simple one-factor-plus-sector model (market factor + sector tilt + idiosyncratic noise) so that
baskets of same-sector shares are meaningfully correlated and baskets across sectors diversify -
enough structure to exercise the basket-building/rebalancing logic, not a forecast of anything.

Run once: `python generate_placeholder_equity_data.py`. Deterministic (fixed seed) - safe to
re-run, output is identical each time.

Delete data/equities/ and re-run this script to regenerate; there is nothing hand-edited to lose.
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EQUITY_DIR = DATA_DIR / "equities"
SEED = 20260701

START, END = "1999-01-31", "2026-06-30"

# (ticker, company, sector, market_beta, sector_vol_monthly, idio_vol_monthly, monthly_alpha)
# Two shares per sector so within-sector correlation and cross-sector diversification both show up.
# Alpha/vol chosen to give plausible-looking annualised return/vol spreads (not calibrated to any
# real data) - defensive sectors lower return & vol, cyclical/growth sectors higher of both.
SHARES = [
    ("PH-NBF", "Northbridge Foods plc",        "Consumer Staples", 0.55, 0.012, 0.018, 0.0020),
    ("PH-ARG", "Aldgate Retail Group plc",      "Consumer Staples", 0.60, 0.012, 0.020, 0.0018),
    ("PH-TFC", "Thameside Financial plc",       "Financials",        1.15, 0.020, 0.028, 0.0022),
    ("PH-CTB", "Cambrian Trust Bank plc",       "Financials",        1.25, 0.020, 0.030, 0.0018),
    ("PH-SPW", "Solent Power & Water plc",      "Utilities",         0.45, 0.008, 0.014, 0.0016),
    ("PH-PEU", "Pennine Utilities plc",         "Utilities",         0.40, 0.008, 0.013, 0.0017),
    ("PH-CMC", "Caledonia Mining Corp plc",     "Mining",            1.10, 0.030, 0.045, 0.0026),
    ("PH-STR", "Sterling Resources plc",        "Mining",            1.20, 0.030, 0.048, 0.0022),
    ("PH-MSW", "Meridian Software plc",         "Technology",        1.05, 0.022, 0.038, 0.0030),
    ("PH-ORB", "Orbital Technologies plc",      "Technology",        1.15, 0.022, 0.040, 0.0028),
]

MARKET_MEAN = 0.0050   # ~6.2% pa
MARKET_VOL = 0.043      # ~15% pa


def generate():
    dates = pd.date_range(START, END, freq="ME")
    n = len(dates)
    rng = np.random.default_rng(SEED)

    market = rng.normal(MARKET_MEAN, MARKET_VOL, n)
    sector_factors = {}
    for _, _, sector, *_ in SHARES:
        if sector not in sector_factors:
            sector_factors[sector] = None
    sector_vols = {row[2]: row[4] for row in SHARES}
    for sector in sector_factors:
        sector_factors[sector] = rng.normal(0.0, sector_vols[sector], n)

    cols = {}
    meta_rows = []
    for ticker, company, sector, beta, _sector_vol, idio_vol, alpha in SHARES:
        idio = rng.normal(0.0, idio_vol, n)
        r = alpha + beta * market + sector_factors[sector] + idio
        cols[ticker] = r
        meta_rows.append({"Ticker": ticker, "Company": company, "Sector": sector})

    returns_df = pd.DataFrame(cols, index=dates)
    returns_df.index.name = "Date"

    meta_df = pd.DataFrame(meta_rows)

    EQUITY_DIR.mkdir(parents=True, exist_ok=True)
    returns_df.to_csv(EQUITY_DIR / "uk_shares_returns.csv")
    meta_df.to_csv(EQUITY_DIR / "share_metadata.csv", index=False)
    print(f"Wrote {returns_df.shape[0]} months x {returns_df.shape[1]} shares to "
          f"{EQUITY_DIR / 'uk_shares_returns.csv'}")
    print(meta_df.to_string(index=False))


if __name__ == "__main__":
    generate()
