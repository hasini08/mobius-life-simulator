"""
Final one-page PDF, in the EXACT same visual format as the app's own PDF export
(app/app.py's build_summary_pdf / _pdf_section_table): same title, same client-scenario line, same
two tables (Accumulation / Decumulation) with the same 7 columns, same Aspen/Mobius colour scheme,
same callout style, same disclaimer footer.

The only change from the live app's own export: the Decumulation table's "Mobius Better" row is the
NEW Better v4 construction (Berenberg / Protected Equities = Eq Gbl DM Novum Mgd Vol, confirmed - not
the current app's "Better" portfolio). Four Seasons is kept on its own FULL native window (matching
everywhere else in this project), while Better v4 runs on its own best-available window (2001-2025,
via the previous model's Asset Returns tab) since that's the real data for its holdings - the two
therefore aren't on an identical historical window, flagged in a footnote (not hidden).

Does NOT touch app.py / engine.py / portfolios.py.
"""
import numpy as np
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from engine import load_asset_returns, load_cpi, run_simulation, historical_single_path, ClientProfile, \
    weighted_monthly_returns
from portfolios import asset_class_weights, weighted_avg_fee
from build_better_v4_summary import (
    extract_old_model_returns, build_better_v4_weights, historical_walk, monte_carlo_prob_ruin,
    compute_irr, FLAT_FEE, _running_dd,
)
from build_recent_window_summary import max_drawdown, cvar, rolling_12m

OUT_PDF = "../output/Mobius_Wealth_vs_Aspen_Advisers_summary.pdf"

DISPLAY = {"Original": "Aspen Original", "Alternative": "Mobius Alternative",
           "Four Seasons": "Aspen Four Seasons", "Better v4": "Mobius Better"}
COLOR = {"Original": (107, 111, 118), "Alternative": (27, 175, 122),
         "Four Seasons": (73, 77, 84), "Better v4": (237, 161, 0)}

AGE, POT, SPEND, HORIZON = 65, 500_000.0, 20_000.0, 30


def historical_stats_arithmetic(monthly):
    """Matches app.py's own historical_stats formula exactly (arithmetic-mean based), so Better v4's
    figure is computed the same way as Original/Alternative/Four Seasons in this table - not the
    geometric method used in build_better_v4_summary.py's own detailed sheet (which deliberately
    matches the OLD MODEL's own formula convention instead - a different, equally valid, but
    separate methodology kept for that other deliverable)."""
    cagr = (1 + monthly.mean()) ** 12 - 1
    vol = monthly.std() * np.sqrt(12)
    return cagr, vol


def _downside_stats(monthly):
    """Max DD (single worst drawdown), Average DD (mean of the running drawdown series - a 'total/
    typical' drawdown figure, not just the single worst point), and CVaR at monthly and rolling-12m
    horizons - same definitions used in the detailed Better v4 workbook."""
    return dict(
        maxdd=max_drawdown(monthly), avgdd=float(np.mean(_running_dd(monthly.to_numpy()))),
        cvar_m=cvar(monthly), cvar_a=cvar(rolling_12m(monthly)),
    )


def _table_row(pdf, col_widths, name, row, color):
    pdf.set_text_color(*color)
    pdf.cell(col_widths[0], 8, name, border=1)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(col_widths[1], 8, f"{row['cagr']*100:.2f}% pa", border=1)
    pdf.cell(col_widths[2], 8, f"{row['vol']*100:.2f}% pa", border=1)
    pdf.cell(col_widths[3], 8, f"{row['prob_ruin']*100:.1f}%", border=1)
    pdf.cell(col_widths[4], 8, f"{row['cum_pct']:+.1f}%", border=1)
    irr = row["irr"]
    pdf.cell(col_widths[5], 8, f"{irr*100:.2f}%" if not np.isnan(irr) else "n/a", border=1)
    pdf.cell(col_widths[6], 8, f"{row['fee_pct']:.2f}%", border=1)
    pdf.ln()


def _downside_row(pdf, col_widths, name, row, color):
    pdf.set_text_color(*color)
    pdf.cell(col_widths[0], 8, name, border=1)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(col_widths[1], 8, f"{row['maxdd']*100:.2f}%", border=1)
    pdf.cell(col_widths[2], 8, f"{row['avgdd']*100:.2f}%", border=1)
    pdf.cell(col_widths[3], 8, f"{row['cvar_m']*100:.2f}%", border=1)
    pdf.cell(col_widths[4], 8, f"{row['cvar_a']*100:.2f}%", border=1)
    pdf.ln()


def build():
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    wr = SPEND / POT

    def main_engine_row(name, initial_annual_spend):
        w = asset_class_weights(name)
        fee = weighted_avg_fee(name)
        m = weighted_monthly_returns(w, fee, asset_df, label=name).dropna()
        cagr, vol = historical_stats_arithmetic(m)
        profile = ClientProfile(starting_age=AGE, horizon_years=HORIZON, starting_pot=POT,
                                 initial_annual_spend=initial_annual_spend, apply_tax=False,
                                 state_pension_annual=0.0)
        sim = run_simulation(name, asset_df, cpi, profile, method="stationary_block", n_sims=2000, seed=42)
        s = sim.summary()
        hist_df = historical_single_path(name, asset_df, cpi, profile)
        cum_pct = (hist_df["PortfolioValue"].iloc[-1] / POT - 1) * 100
        irr = compute_irr(hist_df)
        row = dict(cagr=cagr, vol=vol, prob_ruin=s["Probability of ruin"], cum_pct=cum_pct,
                   irr=irr, fee_pct=fee * 100, median_legacy=s["Median legacy"])
        row.update(_downside_stats(m))
        return row

    print("Computing Accumulation (Original vs Alternative)...")
    accum = {"Original": main_engine_row("Original", 0.0),
             "Alternative": main_engine_row("Alternative", 0.0)}

    print("Computing Four Seasons (full native window)...")
    fs_row = main_engine_row("Four Seasons", SPEND)

    print("Computing Better v4 (own 2001-2025 window)...")
    old_df = extract_old_model_returns()
    weights_v4 = build_better_v4_weights()
    better_v4_monthly = (old_df[weights_v4.index] * weights_v4.values).sum(axis=1) - FLAT_FEE / 12
    better_v4_monthly = better_v4_monthly.dropna()
    horizon_v4 = len(old_df) // 12
    cagr_v4, vol_v4 = historical_stats_arithmetic(better_v4_monthly)
    hist_df_v4 = historical_walk(better_v4_monthly, cpi, POT, SPEND, horizon_v4)
    cum_pct_v4 = (hist_df_v4["PortfolioValue"].iloc[-1] / POT - 1) * 100
    irr_v4 = compute_irr(hist_df_v4)
    prob_ruin_v4, mc_median_legacy_v4 = monte_carlo_prob_ruin(better_v4_monthly, cpi, POT, SPEND, horizon_v4)
    better_v4_row = dict(cagr=cagr_v4, vol=vol_v4, prob_ruin=prob_ruin_v4, cum_pct=cum_pct_v4,
                          irr=irr_v4, fee_pct=FLAT_FEE * 100, median_legacy=mc_median_legacy_v4)
    better_v4_row.update(_downside_stats(better_v4_monthly))

    # --- Build PDF, matching app.py's build_summary_pdf/_pdf_section_table exactly ---
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, "Mobius Wealth - Accumulation & Decumulation Comparison", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 7, "Prepared for Aspen Advisers UK", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(
        0, 6,
        f"Client: age {AGE}, £{POT:,.0f} starting pot, wanting £{SPEND:,.0f}/year in decumulation "
        f"({wr*100:.1f}% withdrawal rate) to last {HORIZON} years.",
    )
    pdf.ln(4)

    col_widths = [40, 24, 24, 24, 24, 22, 22]
    headers = ["Portfolio", "Annualised perf.", "Volatility", "Prob. of ruin", "Cumulative", "IRR", "Fee"]

    def section(title, names_rows):
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        for w, h in zip(col_widths, headers):
            pdf.cell(w, 8, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for key, row in names_rows:
            _table_row(pdf, col_widths, DISPLAY[key], row, COLOR[key])
        pdf.ln(3)

    dd_col_widths = [40, 35, 35, 35, 35]
    dd_headers = ["Portfolio", "Max DD", "Average DD", "CVaR 95 Mthly", "CVaR 95 Ann"]

    def downside_section(names_rows):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, "Downside stats", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        for w, h in zip(dd_col_widths, dd_headers):
            pdf.cell(w, 7, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for key, row in names_rows:
            _downside_row(pdf, dd_col_widths, DISPLAY[key], row, COLOR[key])
        pdf.ln(4)

    section("Accumulation (no withdrawals)", [("Original", accum["Original"]), ("Alternative", accum["Alternative"])])
    downside_section([("Original", accum["Original"]), ("Alternative", accum["Alternative"])])
    section("Decumulation (with withdrawals)", [("Four Seasons", fs_row), ("Better v4", better_v4_row)])
    downside_section([("Four Seasons", fs_row), ("Better v4", better_v4_row)])

    legacy_gain_accum = accum["Alternative"]["median_legacy"] - accum["Original"]["median_legacy"]
    fee_orig = weighted_avg_fee("Original") * 100
    fee_alt = weighted_avg_fee("Alternative") * 100
    legacy_phrase_accum = (
        f"grows to GBP {legacy_gain_accum:,.0f} more" if legacy_gain_accum > 0
        else f"grows to GBP {abs(legacy_gain_accum):,.0f} less"
    )
    pdf.set_font("Helvetica", "B", 11)
    pdf.multi_cell(
        0, 6,
        f"Accumulation - same underlying market exposure, lower cost ({fee_orig:.2f}% vs "
        f"{fee_alt:.2f}% pa): Mobius Alternative {legacy_phrase_accum} than Aspen Original over the "
        f"same horizon, for holdings that track essentially the same indices.",
    )
    pdf.ln(3)

    ruin_cut_pp = (fs_row["prob_ruin"] - better_v4_row["prob_ruin"]) * 100
    legacy_gain_decum = better_v4_row["median_legacy"] - fs_row["median_legacy"]
    ruin_phrase = (
        f"cuts probability of ruin by {ruin_cut_pp:.1f} percentage points" if ruin_cut_pp > 0.05
        else "keeps probability of ruin about the same"
    )
    legacy_phrase_decum = (
        f"lifts median legacy by GBP {legacy_gain_decum:,.0f}" if legacy_gain_decum > 0
        else f"reduces median legacy by GBP {abs(legacy_gain_decum):,.0f}"
    )
    pdf.set_font("Helvetica", "B", 11)
    pdf.multi_cell(
        0, 6,
        f"Decumulation - Mobius Better {ruin_phrase} and {legacy_phrase_decum} versus Aspen Four "
        f"Seasons, with lower volatility too ({better_v4_row['vol']*100:.2f}% vs {fs_row['vol']*100:.2f}% pa).",
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0, 5,
        "Illustrative only - based on a Monte Carlo simulation of historical UK/global market data "
        "(1999/2000-2026), not a guarantee of future performance or personalised advice. Generated by "
        "the Mobius Wealth Decumulation Simulator. Note: Mobius Better's figures above use a "
        "2001-2025 data window (the previous Mobius model's own Asset Returns tab, incl. Eq Gbl DM "
        "Novum Mgd Vol for the Berenberg/Protected Equities holding) rather than the 1999/2000-2026 "
        "window used for the other three portfolios - see the detailed Better v4 workbook for the "
        "full breakdown.",
    )
    pdf.output(OUT_PDF)
    print(f"\nSaved {OUT_PDF}")


if __name__ == "__main__":
    build()
