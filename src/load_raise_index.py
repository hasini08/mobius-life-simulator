"""
One-off: converts the REAL FTSE Russell "RAISE" index simulation report (a genuine external
factor-index construction, supplied as data/equities/raise_index_levels_daily.csv - extracted
from "Comparsion Report RAISE.xlsx") into the same monthly-simple-return shape the equity-income
framework already expects (see uk_shares_returns.csv), so it plugs into equity_income.py's
existing single-holding/basket registration mechanism unchanged.

Source: FTSE Russell, "Comparison Report (RAISE_AWD_RAISE, RAISE_AWD_RAISE_MOM_LEADERS,
RAISE_AWD_RAISE_LOW_VOL_LEADERS)", benchmark universe FTSE Developed Index (AWD), data period
16-Mar-2001 to 30-Jun-2026 - a real (backtested/simulated) index construction, NOT placeholder
data like generate_placeholder_equity_data.py's "PH-" shares. No fund/OCF exists for RAISE yet
(it is described as a "Simulation" in the source workbook), so the same illustrative
DEFAULT_SHARE_FEE used for the placeholder shares is applied here too - flag this if RAISE is
ever actually launched as an investable product with its own real fee.

Run once: `python load_raise_index.py`. Deterministic given the same input CSV.
"""
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EQUITY_DIR = DATA_DIR / "equities"
DAILY_LEVELS_CSV = EQUITY_DIR / "raise_index_levels_daily.csv"
MONTHLY_RETURNS_CSV = EQUITY_DIR / "raise_index_returns.csv"
META_CSV = EQUITY_DIR / "raise_index_metadata.csv"

RENAME = {
    "FTSE_AWD": "FTSE Developed AWD (benchmark)",
    "RAISE": "RAISE",
    "RAISE_Mom": "RAISE + Mom Leaders",
    "RAISE_LowVol": "RAISE + Low Vol Leaders",
}


def convert():
    daily = pd.read_csv(DAILY_LEVELS_CSV, index_col=0, parse_dates=True)
    daily = daily.rename(columns=RENAME)
    monthly_levels = daily.resample("ME").last()
    monthly_returns = monthly_levels.pct_change().dropna(how="all")
    monthly_returns.index.name = "Date"
    monthly_returns.to_csv(MONTHLY_RETURNS_CSV)

    meta = pd.DataFrame({
        "Name": list(RENAME.values()),
        "Type": ["Benchmark", "Real factor index (simulation)", "Real factor index (simulation)",
                 "Real factor index (simulation)"],
        "Source": ["FTSE Russell"] * 4,
    })
    meta.to_csv(META_CSV, index=False)

    print(f"Wrote {monthly_returns.shape[0]} months x {monthly_returns.shape[1]} series to {MONTHLY_RETURNS_CSV}")
    print(monthly_returns.describe().loc[["mean", "std"]].T)


if __name__ == "__main__":
    convert()
