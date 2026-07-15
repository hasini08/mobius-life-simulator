"""
Rebuilds the Cash asset-class return series using the REAL Bank of England SONIA rate (series
IUDSOIA), replacing the corrupted "SONIA / short rate proxy" column that shipped in the Bloomberg
data file (that column looked like a naive %-change of the rate level itself - see portfolios.py
for the full explanation).

Input: a BoE/FRED export of IUDSOIA (daily rate, % pa) - user-supplied, in uploads.
Output: adds a spliced Cash column to data/asset_class_returns.csv, used by portfolios.py's AC map.

The BoE export only covers Jan 2014 onward. For 2000-2013 (pre-file period) this splices in the
"Blackrock ICS Sterling Liquidity Fund" money-market fund series as a proxy - validated as 99.7%
correlated and closely matched in level with real SONIA over the 2014-2026 overlap period, so the
splice doesn't introduce a visible discontinuity (see the printed comparison when run standalone).
"""
import openpyxl
import pandas as pd
from pathlib import Path

SONIA_SRC = "/root/.claude/uploads/6277f0d3-e8e6-5e30-977d-0317d499a601/9353c1c4-IUDSOIA__Bank_of_England__Database.xlsx"
DATA = Path(__file__).resolve().parent.parent / "data"
CASH_COL_NAME = "Cash (GBP) - SONIA-based, spliced with Blackrock ICS proxy pre-2014"


def load_sonia_monthly():
    wb = openpyxl.load_workbook(SONIA_SRC, data_only=True)
    ws = wb["Sheet1"]
    rows = []
    for row in ws.iter_rows(min_row=3):
        d, v = row[0].value, row[1].value
        if d is None or v is None:
            continue
        rows.append((d, v))
    df = pd.DataFrame(rows, columns=["date_str", "rate_pct"])
    df["date"] = pd.to_datetime(df["date_str"], format="%d %b %y")
    df = df.sort_values("date").reset_index(drop=True)
    df["ym"] = df["date"].dt.to_period("M")
    monthly = df.groupby("ym").last()[["date", "rate_pct"]]
    # annualised daily rate (%) -> monthly-equivalent compounding return
    monthly["monthly_return"] = (1 + monthly["rate_pct"] / 100) ** (1 / 12) - 1
    monthly["month_end"] = monthly["date"].dt.to_period("M").dt.to_timestamp("M")
    return monthly.set_index("month_end")["monthly_return"]


def build_and_save():
    sonia = load_sonia_monthly()
    sonia = sonia[sonia.index <= "2026-06-30"]  # match the rest of the dataset's cutoff

    ac = pd.read_csv(DATA / "asset_class_returns.csv", index_col=0, parse_dates=True)
    blk = ac["Blackrock ICS Sterling Liquidity Fund"]

    overlap = pd.DataFrame({"sonia": sonia, "blackrock_fund": blk}).dropna()
    corr = overlap.corr().iloc[0, 1]
    print(f"Real SONIA vs Blackrock fund proxy over the {len(overlap)}-month overlap: "
          f"correlation={corr:.4f}, mean diff={((overlap['blackrock_fund']-overlap['sonia']).mean()):.6f}")

    combined = blk.copy()
    combined.loc[sonia.index] = sonia
    combined.name = CASH_COL_NAME

    ac[CASH_COL_NAME] = combined
    ac.to_csv(DATA / "asset_class_returns.csv")
    print(f"Saved '{CASH_COL_NAME}' ({combined.count()} months) to asset_class_returns.csv")
    return combined


if __name__ == "__main__":
    build_and_save()
