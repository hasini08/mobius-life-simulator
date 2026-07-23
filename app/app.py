"""
Mobius Wealth Decumulation Simulator - interactive app.

Run with:
    pip install -r requirements.txt
    streamlit run app.py

Lets a user compare Mobius against ANY registered portfolio - a competitor's fund, or another
Mobius construction - not just the four bundled examples (Original/Alternative/Four Seasons/
Better), switch sampling method, and toggle spending guardrails on/off, to see the impact on
probability of ruin, spending shortfall and legacy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from scipy.optimize import brentq
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from engine import (
    load_asset_returns, load_cpi, run_simulation, historical_single_path, ClientProfile, equity_sweep,
    sensitivity_withdrawal_rate, sensitivity_guardrail_band, run_glide_path_simulation,
    asset_correlation_matrix, shortfall_heatmap, run_mortality_overlay, weighted_monthly_returns,
    downside_stats,
)
import portfolios as portfolios_mod
from portfolios import (
    PORTFOLIOS, PORTFOLIO_META, portfolio_summary, weighted_avg_fee, asset_class_weights, AC,
    HOLDINGS_CSV, ASSET_MAP_CSV, PORTFOLIO_META_CSV,
)
from mortality import load_mortality_table, survival_curve, joint_survival_curve, life_expectancy
import tax
import cma as cma_mod

st.set_page_config(page_title="Mobius Wealth Decumulation Simulator", layout="wide")

# Streamlit re-runs this ENTIRE script on every widget interaction, even ones unrelated to the
# simulation (e.g. toggling a checkbox in the Advanced tab) - without caching, that means every
# portfolio's Monte Carlo simulation reruns from scratch on every single click, which is slow
# enough to feel laggy in a live demo. Caching by the exact inputs means an unrelated interaction
# hits the cache (near-instant); only an actual change to age/pot/spend/method/data etc. triggers
# a real recompute.
@st.cache_data(show_spinner=False)
def _cached_load_asset_returns(_mtime: float) -> pd.DataFrame:
    """_mtime (the CSV's last-modified time, unused inside the function) is the cache key that
    forces a reload whenever the file actually changes (e.g. via the 'save as new default' button
    in the asset-upload expander) - without it, this would cache forever and never notice edits."""
    return load_asset_returns()


@st.cache_data(show_spinner=False)
def _cached_run_simulation(name, asset_df, cpi, profile, method, n_sims, block_mean, seed):
    return run_simulation(name, asset_df, cpi, profile, method=method, n_sims=n_sims,
                           block_mean=block_mean, seed=seed)

# Client-facing branding: internal simulation keys (used throughout src/portfolios.py and
# src/engine.py) are left untouched - only how a portfolio is LABELLED and COLOURED in this app
# changes, driven entirely by data/portfolio_meta.csv (DisplayName/Owner/Provider per portfolio),
# so ANY registered competitor's fund - not just Aspen's - gets sensible branding automatically,
# with no code changes. A portfolio missing a metadata row (e.g. freshly added and not yet
# labelled) falls back to its own internal name and a "Competitor" colour. Mobius-owned portfolios
# get a warm brand palette; everyone else's get neutral greys, so a plain glance always tells you
# which side of the comparison is ours - both palettes are colourblind-safe categorical orders,
# assigned by each portfolio's registration order within its own Owner group so colours stay
# stable as portfolios are added.
MOBIUS_PALETTE = ["#1baf7a", "#eda100", "#3b7dd8", "#a855c9", "#d8546b"]
COMPETITOR_PALETTE = ["#6b6f76", "#494d54", "#9a9ea5", "#2f3237", "#c7cad0"]


def display_name(name: str) -> str:
    meta = PORTFOLIO_META.get(name)
    return meta["DisplayName"] if meta and meta.get("DisplayName") else name


def portfolio_owner(name: str) -> str:
    meta = PORTFOLIO_META.get(name)
    return meta["Owner"] if meta and meta.get("Owner") else "Competitor"


def portfolio_provider(name: str) -> str:
    meta = PORTFOLIO_META.get(name)
    return meta["Provider"] if meta and meta.get("Provider") else display_name(name)


def portfolio_color(name: str) -> str:
    owner = portfolio_owner(name)
    palette = MOBIUS_PALETTE if owner == "Mobius" else COMPETITOR_PALETTE
    same_owner = [n for n in PORTFOLIOS if portfolio_owner(n) == owner]
    idx = same_owner.index(name) if name in same_owner else 0
    return palette[idx % len(palette)]


def providers_label(names, owner: str = "Competitor") -> str:
    """Comma-joined, de-duplicated Provider names for whichever of `names` have the given Owner -
    used to build section captions/PDF headers dynamically instead of hardcoding 'Aspen Advisers
    UK'. Returns '' if none of `names` match that owner."""
    seen = []
    for n in names:
        if portfolio_owner(n) == owner:
            p = portfolio_provider(n)
            if p not in seen:
                seen.append(p)
    return ", ".join(seen)


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def similar_exposure(name_a: str, name_b: str, tolerance: float = 0.05) -> bool:
    """Whether two portfolios' asset-class weights are close enough to call 'the same underlying
    exposure' - e.g. Original vs Alternative differ by a percentage point or two per asset class
    (rounding noise in the FNZ 'Same Index' extraction), not a real allocation difference, whereas
    a genuinely different competitor's fund typically differs by tens of percentage points.
    Measured as total variation distance (half the sum of absolute weight differences, 0-1) against
    `tolerance` - default 5%, i.e. up to ~2.5 percentage points of total drift is still 'the same'."""
    wa, wb = asset_class_weights(name_a), asset_class_weights(name_b)
    all_classes = wa.index.union(wb.index)
    ra = wa.reindex(all_classes, fill_value=0.0)
    rb = wb.reindex(all_classes, fill_value=0.0)
    return (ra - rb).abs().sum() / 2 <= tolerance


def ordered_names(names) -> list:
    """De-duplicates while preserving whatever order the caller/sidebar selection supplied -
    portfolios are no longer assumed to be one of a fixed set of 4 names."""
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


def save_portfolios_to_csv(portfolios_dict: dict, path=HOLDINGS_CSV) -> None:
    """Writes the full current (possibly session-edited) PORTFOLIOS dict back to the long-format
    holdings sheet, so edits persist as the new default for future sessions/restarts - not just
    this one. Overwrites the whole file (all portfolios), matching what portfolios.py itself loads."""
    rows = []
    for name, holdings in portfolios_dict.items():
        for holding, asset_class, weight, ocf in holdings:
            rows.append({"Portfolio": name, "Holding": holding, "AssetClass": asset_class,
                         "Weight": weight, "OCF": ocf})
    pd.DataFrame(rows).to_csv(path, index=False)


def save_asset_map_to_csv(ac_dict: dict, path=ASSET_MAP_CSV) -> None:
    """Writes the full current (possibly session-extended) AC label->column map back to its sheet."""
    rows = [{"Label": k, "BloombergColumn": v} for k, v in ac_dict.items()]
    pd.DataFrame(rows).to_csv(path, index=False)


def save_portfolio_meta_to_csv(meta_dict: dict, path=PORTFOLIO_META_CSV) -> None:
    """Writes the full current (possibly session-extended) PORTFOLIO_META dict back to its sheet."""
    rows = [{"Portfolio": name, "DisplayName": m.get("DisplayName", name),
             "Owner": m.get("Owner", "Competitor"), "Provider": m.get("Provider", name)}
            for name, m in meta_dict.items()]
    pd.DataFrame(rows).to_csv(path, index=False)


def historical_stats(name, asset_df):
    """Deterministic annualised return & volatility from the portfolio's own historical monthly
    returns (not a Monte Carlo average) - matches how the previous model's summary block computed
    these ('Compound ret pa' / 'Volatility pa')."""
    weights = asset_class_weights(name)
    fee = weighted_avg_fee(name)
    monthly = weighted_monthly_returns(weights, fee, asset_df, label=name).dropna()
    cagr = (1 + monthly.mean()) ** 12 - 1
    vol = monthly.std() * np.sqrt(12)
    return cagr, vol


def portfolio_data_period(name, asset_df) -> str:
    """The actual first-to-last month of usable history behind THIS portfolio's own figures -
    narrower than the sidebar's historical-window slider whenever the portfolio's own holdings
    don't cover the full window (e.g. Mobius Better's holdings only overlap from 2001), since
    weighted_monthly_returns/dropna() already restricts to the common overlap across its holdings.
    Every portfolio can have a different period, which is why this is shown per-row, not once."""
    weights = asset_class_weights(name)
    fee = weighted_avg_fee(name)
    monthly = weighted_monthly_returns(weights, fee, asset_df, label=name).dropna()
    if monthly.empty:
        return "n/a"
    return f"{monthly.index.min():%Y-%m} to {monthly.index.max():%Y-%m}"


def compute_irr(hist_df: pd.DataFrame, spend_column: str = "Spend") -> float:
    """Money-weighted internal rate of return on the CLIENT's own cash-flow experience (matches the
    'IRR' line in the previous Mobius model's summary block) - different from 'Annualised performance'
    (CAGR), which only measures how the underlying investments grew and ignores cash flows in/out.

    Cash flows (annual, from the historical single-path projection, using TRUE elapsed time rather
    than row count since the final row is often a partial year): the starting pot as an outflow in
    year 0, each year's actual withdrawal as an inflow to the client, and the FINAL year's withdrawal
    plus whatever's left in the pot (the 'legacy') as one lump sum - since both the last withdrawal
    and the leftover balance represent value that ultimately flows to the client/their estate.

    spend_column: "Spend" (default) is TOTAL income - includes State Pension/annuity on top of pot
    withdrawals, since historical_single_path always folds those in regardless of the tax toggle.
    Pass "Withdrawal" instead for a POT-ONLY IRR that isolates this portfolio's own investment
    return - State Pension is a flat, guaranteed amount identical across every portfolio, so including
    it inflates "investment return" for reasons that have nothing to do with portfolio quality.

    For a pure accumulation scenario (no withdrawals) this exactly equals the realised compound growth
    rate of THIS historical path - it can still read a little differently from the 'Annualised
    performance' card above, which uses the historical AVERAGE monthly return (the same input the
    Monte Carlo simulation draws on) rather than one specific realised sequence; the two are related
    but not identical measures."""
    values = hist_df["PortfolioValue"].to_numpy()
    spends = hist_df[spend_column].to_numpy()
    if len(values) < 2:
        return float("nan")
    cash_flows = [-values[0]] + list(spends[1:-1]) + [spends[-1] + values[-1]]
    # Use TRUE elapsed time in years, not row count - historical_single_path's final row is often a
    # partial year (history runs out mid-year), so treating it as a full year would understate the
    # exponent and throw off the annualised rate, breaking the "collapses to CAGR" property above.
    dates = hist_df["Date"].to_numpy()
    years_elapsed = (dates - dates[0]) / np.timedelta64(1, "D") / 365.25

    def npv(rate):
        return sum(cf / (1 + rate) ** t for cf, t in zip(cash_flows, years_elapsed))

    try:
        return brentq(npv, -0.99, 10.0)
    except ValueError:
        return float("nan")


def render_comparison_section(title, caption, names, sim_results, hist_profile_kwargs, asset_df, cpi,
                                show_ruin: bool = True, show_pot_only_irr: bool = False):
    """Renders a row of metric cards (annualised performance, volatility, probability of ruin,
    cumulative performance) plus a cumulative-performance line chart (the actual historical sequence
    of returns, not a simulated fan) - the same style of summary the previous Mobius model showed for
    its portfolios. Used for both the Accumulation and Decumulation sections below.

    show_ruin: Accumulation has no withdrawals, so probability of ruin is always ~0% there and not a
    meaningful comparison point - the caller passes False to drop it entirely for that section.
    show_pot_only_irr: Decumulation's IRR includes State Pension (see compute_irr) unless split out -
    the caller passes True there to show both Total-income and Pot-only IRR side by side; Accumulation
    never needs this since it has no State Pension in the first place (the two would be identical).

    Returns the historical single-path DataFrames, keyed by name, in case the caller wants them."""
    st.markdown(f"#### {title}")
    if caption:
        st.caption(caption)
    hist_paths = {}
    cols = st.columns(len(names)) if names else [st]
    for col, name in zip(cols, names):
        cagr, vol = historical_stats(name, asset_df)
        s = sim_results[name].summary()
        hist_df = historical_single_path(name, asset_df, cpi, ClientProfile(**hist_profile_kwargs))
        hist_paths[name] = hist_df
        start_val = hist_profile_kwargs["starting_pot"]
        end_val = hist_df["PortfolioValue"].iloc[-1]
        cum_pct = (end_val / start_val - 1) * 100
        irr = compute_irr(hist_df)
        with col:
            with st.container(border=True):
                st.markdown(
                    f"<div style='color:{portfolio_color(name)}; font-weight:700; "
                    f"font-size:1.05rem;'>{display_name(name)}</div>",
                    unsafe_allow_html=True,
                )
                if show_ruin:
                    ruin_pct = s["Probability of ruin"] * 100
                    if ruin_pct < 10:
                        ruin_color = "#0ca30c"   # good
                    elif ruin_pct < 30:
                        ruin_color = "#c98500"   # warning (darkened from the standard warning hue for contrast on white)
                    else:
                        ruin_color = "#d03b3b"   # critical
                    st.markdown(
                        "<div style='font-size:0.72rem; color:#898781; font-weight:600; "
                        "text-transform:uppercase; letter-spacing:0.04em; margin-top:6px;'>"
                        "Probability of ruin</div>"
                        f"<div style='font-size:2.3rem; font-weight:800; color:{ruin_color}; "
                        f"line-height:1.15;'>{ruin_pct:.1f}%</div>",
                        unsafe_allow_html=True,
                    )
                    st.caption("Share of simulated futures where the pot hits £0 before the plan ends - "
                               "the single most important number here.")
                    st.divider()
                st.metric("Annualised performance", f"{cagr*100:.2f}% pa",
                          help="Compound annual growth rate, from this portfolio's own historical monthly returns net of fees.")
                st.metric("Volatility", f"{vol*100:.2f}% pa",
                          help="Annualised standard deviation of monthly returns - how bumpy the ride is.")
                st.metric("Cumulative performance", f"{cum_pct:+.1f}%",
                          help="Total growth of the starting pot over the full historical horizon shown in the chart below.")
                if show_pot_only_irr:
                    irr_pot = compute_irr(hist_df, spend_column="Withdrawal")
                    st.metric("Total-income IRR", f"{irr*100:.2f}% pa" if not np.isnan(irr) else "n/a",
                              help="Money-weighted return on ALL cash the client receives: starting pot out, "
                                   "each year's actual withdrawal PLUS State Pension in, and the final "
                                   "withdrawal plus whatever's left (the legacy) in as one lump sum. "
                                   "Includes State Pension - a flat, guaranteed amount identical regardless "
                                   "of which portfolio is held, so this isn't a pure measure of investment "
                                   "quality; see Pot-only IRR below for that.")
                    st.metric("Pot-only IRR", f"{irr_pot*100:.2f}% pa" if not np.isnan(irr_pot) else "n/a",
                              help="Same money-weighted calculation as Total-income IRR, but EXCLUDING State "
                                   "Pension - isolating the return generated by this portfolio's own "
                                   "investments. The fairer figure for comparing one portfolio against "
                                   "another, since State Pension is the same regardless of which is held.")
                else:
                    st.metric("IRR", f"{irr*100:.2f}% pa" if not np.isnan(irr) else "n/a",
                              help="Money-weighted return on the client's own cash flows: starting pot out, each "
                                   "year's actual withdrawal in, and the final withdrawal plus whatever's left "
                                   "(the legacy) in as one lump sum, using this specific historical sequence. "
                                   "Related to, but not identical to, Annualised performance above (which uses "
                                   "the historical average return rather than one realised sequence).")
    if names:
        fig = go.Figure()
        for name in names:
            fig.add_trace(go.Scatter(
                x=hist_paths[name]["Date"], y=hist_paths[name]["PortfolioValue"], mode="lines",
                name=display_name(name), line=dict(width=3, color=portfolio_color(name)),
            ))
        fig.update_layout(
            xaxis_title="Date", yaxis_title="Portfolio value (£)", height=380,
            margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Replays the actual historical sequence of returns from the earliest available date - one "
            "concrete real-world path (matching the 'Portfolio Value' chart style from the previous "
            "Mobius model), alongside the Monte Carlo-based statistics in the cards above."
        )
    return hist_paths


def _holdings_column_config():
    """Weight/OCF/FeeContribution are stored as raw fractions (0.325, 0.0007) - display them as
    percentages everywhere so they read naturally, without changing the underlying data."""
    return {
        "Weight": st.column_config.NumberColumn("Weight", format="percent"),
        "OCF": st.column_config.NumberColumn("OCF", format="percent"),
        "FeeContribution": st.column_config.NumberColumn("Fee contribution", format="percent"),
    }


def render_holdings_section(names) -> None:
    """'What each portfolio holds' for a single section (Accumulation or Decumulation) - kept as
    its own reusable block so each section is self-contained (cards, chart, holdings all together)
    instead of one shared holdings block sitting between the two sections."""
    names = ordered_names(names)
    st.subheader("What each portfolio holds")
    st.caption(
        "Full underlying holdings for every portfolio above, with each holding's asset-class "
        "mapping and fee (OCF), plus the portfolio's overall weighted-average fee."
    )
    if not names:
        st.caption("No portfolios selected.")
        return
    cols = st.columns(2) if len(names) > 1 else [st.container()]
    for i, name in enumerate(names):
        with cols[i % len(cols)]:
            st.markdown(f"**{display_name(name)}** — weighted-average OCF: {weighted_avg_fee(name)*100:.3f}% pa")
            st.dataframe(portfolio_summary(name), use_container_width=True,
                         column_config=_holdings_column_config())
    st.caption(
        "Fund-level returns are mapped to broad asset-class index returns (Bloomberg data, 1999/2000-"
        "2026) for the simulation, since several individual fund return histories are too short for a "
        "reliable long-run bootstrap. Holdings, weights and fees can be edited live in the sidebar's "
        "'Edit data' tab."
    )


def _pdf_section_table(pdf, section_title, names, sim_results, profile_kwargs, asset_df, cpi,
                        show_ruin: bool = True, intro_text: str = None):
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 8, section_title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    if intro_text:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 6, intro_text)
        pdf.ln(2)

    # Prob. of ruin is dropped for Accumulation (no withdrawals ever happen there, so it's always
    # ~0% and not a meaningful comparison point) but kept for Decumulation, where it's the headline
    # risk metric - same distinction the live app's cards already make. Pot-only IRR is likewise
    # only shown for Decumulation - for Accumulation the two figures are identical by construction
    # (no withdrawals, no State Pension), so a second column would just repeat the first. Base
    # widths are untouched from the Accumulation-only layout (total 137mm, well under the ~190mm
    # page width) - the two extra Decumulation columns (19+14=33mm) are added on top, not carved
    # out of the others, so the Accumulation table's column widths never change.
    col_widths = [38, 24, 17, 18, 16, 14, 30]
    headers = ["Portfolio", "Annualised perf.", "Volatility", "Cumulative", "IRR", "Fee", "Data period"]
    if show_ruin:
        col_widths.insert(3, 19)
        headers.insert(3, "Prob. of ruin")
        irr_pos = headers.index("IRR")
        headers[irr_pos] = "Total IRR"
        col_widths.insert(irr_pos + 1, 14)
        headers.insert(irr_pos + 1, "Pot IRR")

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 8, h, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for name in ordered_names(names):
        cagr, vol = historical_stats(name, asset_df)
        s = sim_results[name].summary()
        fee_pct = weighted_avg_fee(name) * 100
        hist_df = historical_single_path(name, asset_df, cpi, ClientProfile(**profile_kwargs))
        cum_pct = (hist_df["PortfolioValue"].iloc[-1] / profile_kwargs["starting_pot"] - 1) * 100
        irr = compute_irr(hist_df)
        irr_pot = compute_irr(hist_df, spend_column="Withdrawal") if show_ruin else irr
        period = portfolio_data_period(name, asset_df)
        r, g, b = (int(portfolio_color(name).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        pdf.set_text_color(r, g, b)
        col = 0
        pdf.cell(col_widths[col], 8, display_name(name), border=1); col += 1
        pdf.set_text_color(0, 0, 0)
        pdf.cell(col_widths[col], 8, f"{cagr*100:.2f}% pa", border=1); col += 1
        pdf.cell(col_widths[col], 8, f"{vol*100:.2f}% pa", border=1); col += 1
        if show_ruin:
            pdf.cell(col_widths[col], 8, f"{s['Probability of ruin']*100:.1f}%", border=1); col += 1
        pdf.cell(col_widths[col], 8, f"{cum_pct:+.1f}%", border=1); col += 1
        pdf.cell(col_widths[col], 8, f"{irr*100:.2f}%" if not np.isnan(irr) else "n/a", border=1); col += 1
        if show_ruin:
            pdf.cell(col_widths[col], 8, f"{irr_pot*100:.2f}%" if not np.isnan(irr_pot) else "n/a", border=1); col += 1
        pdf.cell(col_widths[col], 8, f"{fee_pct:.2f}%", border=1); col += 1
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(col_widths[col], 8, period, border=1)
        pdf.set_font("Helvetica", "", 8)
        pdf.ln()
    pdf.ln(3)

    dd_col_widths = [40, 35, 35, 35, 35]
    dd_headers = ["Portfolio", "Max DD*", "Average DD*", "CVaR 95 Mthly", "CVaR 95 Ann"]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, "Downside stats", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_fill_color(230, 230, 230)
    for w, h in zip(dd_col_widths, dd_headers):
        pdf.cell(w, 7, h, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for name in ordered_names(names):
        dd = downside_stats(name, asset_df)
        r, g, b = (int(portfolio_color(name).lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        pdf.set_text_color(r, g, b)
        pdf.cell(dd_col_widths[0], 7, display_name(name), border=1)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(dd_col_widths[1], 7, f"{dd['maxdd']*100:.2f}%", border=1)
        pdf.cell(dd_col_widths[2], 7, f"{dd['avgdd']*100:.2f}%", border=1)
        pdf.cell(dd_col_widths[3], 7, f"{dd['cvar_m']*100:.2f}%", border=1)
        pdf.cell(dd_col_widths[4], 7, f"{dd['cvar_a']*100:.2f}%", border=1)
        pdf.ln()
    pdf.ln(4)


def build_summary_pdf(accum_results: dict, decum_results: dict, accum_profile_kwargs: dict,
                       decum_profile_kwargs: dict, asset_df, cpi, age: int, pot: float,
                       spend: float, horizon: int, wr: float) -> bytes:
    """One-page client-facing takeaway covering both the Accumulation and Decumulation comparisons
    currently selected in the sidebar, as a PDF an adviser can hand over or attach to an email
    after the meeting."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    all_names = list(accum_results.keys()) + [n for n in decum_results if n not in accum_results]
    prepared_for = providers_label(all_names, owner="Competitor") or "Mobius Life"

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, "Mobius Wealth - Accumulation & Decumulation Comparison", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 7, f"Prepared for {prepared_for}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # "To last N years" is ambiguous on its own (last until when? guaranteed, or just targeted?) -
    # spelled out explicitly here as "the pot needs to last until age X without running out" so a
    # reader doesn't have to infer it.
    client_line = (
        f"Client: age {age}, £{pot:,.0f} starting pot, wanting to draw £{spend:,.0f}/year in "
        f"decumulation ({wr*100:.1f}% withdrawal rate) for {horizon} years - i.e. the pot needs to "
        f"last until age {age + horizon} without running out."
    )

    # Accumulation: table (no Prob. of ruin column - always ~0% with no withdrawals, not a useful
    # comparison point there) then its own narrative directly underneath, not grouped with
    # Decumulation's at the bottom of the page.
    _pdf_section_table(pdf, "Accumulation (no withdrawals)", list(accum_results.keys()), accum_results,
                        accum_profile_kwargs, asset_df, cpi, show_ruin=False)
    if len(accum_results) == 2:
        a, b = accum_results.keys()
        same_exposure = similar_exposure(a, b)
        s_a, s_b = accum_results[a].summary(), accum_results[b].summary()
        legacy_gain = s_b["Median legacy"] - s_a["Median legacy"]
        fee_a, fee_b = weighted_avg_fee(a) * 100, weighted_avg_fee(b) * 100
        legacy_phrase = (
            f"grows to GBP {legacy_gain:,.0f} more" if legacy_gain > 0
            else f"grows to GBP {abs(legacy_gain):,.0f} less"
        )
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(0, 0, 0)
        if same_exposure:
            pdf.multi_cell(
                0, 6,
                f"Accumulation - same underlying market exposure, different cost ({fee_a:.2f}% vs "
                f"{fee_b:.2f}% pa): {display_name(b)} {legacy_phrase} than {display_name(a)} over the "
                f"same horizon, for holdings that track essentially the same indices.",
            )
        else:
            pdf.multi_cell(
                0, 6,
                f"Accumulation - {display_name(b)} {legacy_phrase} than {display_name(a)} over the same "
                f"horizon (fee: {fee_a:.2f}% vs {fee_b:.2f}% pa). These two hold different underlying "
                f"asset-class exposure, so this reflects cost AND market exposure, not cost alone.",
            )
        pdf.ln(2)

    # Decumulation: client context first (spend/withdrawal rate only apply once withdrawals start,
    # so this doesn't belong under Accumulation above), then table (Prob. of ruin kept - the
    # headline risk metric here), then its own narrative directly underneath.
    _pdf_section_table(pdf, "Decumulation (with withdrawals)", list(decum_results.keys()), decum_results,
                        decum_profile_kwargs, asset_df, cpi, show_ruin=True, intro_text=client_line)
    if len(decum_results) == 2:
        x, y = decum_results.keys()
        sx, sy = decum_results[x].summary(), decum_results[y].summary()
        # name the lower-probability-of-ruin one "b" (the one the sentence is framed around),
        # regardless of which position it was passed in, so the phrasing always reads correctly
        if sy["Probability of ruin"] <= sx["Probability of ruin"]:
            a, b, s_a, s_b = x, y, sx, sy
        else:
            a, b, s_a, s_b = y, x, sy, sx
        ruin_diff_pp = (s_a["Probability of ruin"] - s_b["Probability of ruin"]) * 100
        legacy_diff = s_b["Median legacy"] - s_a["Median legacy"]
        _, vol_a = historical_stats(a, asset_df)
        _, vol_b = historical_stats(b, asset_df)
        legacy_phrase = (
            f"lifts median legacy by GBP {legacy_diff:,.0f}" if legacy_diff > 0
            else f"reduces median legacy by GBP {abs(legacy_diff):,.0f}"
        )
        vol_phrase = (
            f"with lower volatility too ({vol_b*100:.2f}% vs {vol_a*100:.2f}% pa)" if vol_b < vol_a
            else f"volatility: {vol_b*100:.2f}% vs {vol_a*100:.2f}% pa"
        )
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(
            0, 6,
            f"Decumulation - {display_name(b)} cuts probability of ruin by {ruin_diff_pp:.1f} "
            f"percentage points versus {display_name(a)}, and {legacy_phrase}, {vol_phrase}.",
        )
        pdf.ln(2)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0, 5,
        "Source: Mobius Analysis. All data is simulated based upon historical data using Monte Carlo "
        "simulation methodology. The Monte Carlo simulation is a mathematical technique that uses "
        "random sampling to estimate the possible outcomes of a process or system. It's used to model "
        "complex systems with inherent uncertainty, providing insight into how likely different "
        "outcomes are. See attached Appendix for assumptions used.",
    )
    pdf.ln(2)
    pdf.multi_cell(
        0, 5,
        "* Important: Maximum Drawdown (Max DD) and Average Drawdown (Avg DD) figures presented in "
        "this report are calculated based on portfolio performance excluding the impact of investor "
        "withdrawals. As a result, these statistics reflect the strategy's underlying investment "
        "performance rather than cash-flow-driven declines in portfolio value.",
    )
    pdf.ln(2)
    pdf.multi_cell(
        0, 5,
        "Total IRR / Pot IRR (Decumulation table): Total IRR is the money-weighted return on all "
        "cash received, including State Pension where applicable; Pot IRR excludes State Pension, "
        "isolating the return generated by the portfolio's own investments. State Pension is a flat, "
        "guaranteed amount identical regardless of which portfolio is held, so Pot IRR is the fairer "
        "basis for comparing portfolios against each other.",
    )

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(140, 140, 140)
    pdf.multi_cell(
        0, 3.2,
        "Mobius Life Limited is authorised by the Prudential Regulation Authority and regulated by the "
        "Financial Conduct Authority and the Prudential Regulation Authority. Mobius Life Administration "
        "Services is not authorised or regulated.",
    )
    pdf.ln(1)
    pdf.multi_cell(
        0, 3.2,
        "Mobius Life Limited (Registered No. 3104978) and Mobius Life Administration Services "
        "(Registered No. 5754821) are registered in England and Wales at: 2nd Floor, 2 Copthall Avenue, "
        "London, EC2R 7DA.",
    )
    return bytes(pdf.output())


st.title("Mobius Wealth — Accumulation & Decumulation Simulator")
st.caption(
    "Compare any registered portfolio - a competitor's fund, or another Mobius construction - "
    "against Mobius's own: Accumulation tests growing the pot with no withdrawals, Decumulation "
    "tests drawing an income from it. Pick which portfolios to compare in the sidebar. Defaults to "
    "Aspen Advisers UK's own 'Growth Passive Plus' and 'Four Seasons Fund' lineups vs Mobius's "
    "Alternative and Better. Uses Bloomberg data to 14 July 2026."
)
hero_container = st.container()
with st.expander("New to this tool? Read this first"):
    st.markdown(
        "This tool asks: **\"if a client retires with this pot and spends this much every year, "
        "how likely is the money to run out?\"** — by simulating thousands of possible futures for "
        "the markets (and, if you switch it on, the client's own lifespan), rather than assuming "
        "one fixed rate of return.\n\n"
        "**Set the client's details in the sidebar on the left** (age, pot size, desired spend, "
        "which portfolios to compare) — everything else on this page updates automatically. The "
        "sidebar is grouped top-to-bottom: the client's basic numbers, which portfolios to compare, "
        "then a set of OPTIONAL features (guardrails, forward-looking returns, mortality, tax/State "
        "Pension) you can switch on one at a time to see how much difference each makes.\n\n"
        "**The single most important number is 'Probability of ruin'** — the share of simulated "
        "futures in which the pot hits zero before the plan's time horizon is up. Lower is safer. "
        "Everything else (fan charts, legacy distributions, sensitivity scans) is there to help "
        "explain *why* that number is what it is, and what levers move it."
    )

with st.expander("Glossary — plain-English meaning of the terms used on this page"):
    st.markdown(
        "- **Probability of ruin**: out of all the simulated futures, the share where the pot hits "
        "£0 before the plan is meant to end. The headline 'is this plan safe enough' number.\n"
        "- **Simulation / Monte Carlo run**: instead of guessing one future for the stock market, the "
        "tool plays out thousands of different possible futures (good decades, bad decades, "
        "everything in between) and reports how many of them go well.\n"
        "- **Withdrawal rate**: the desired yearly spend divided by the starting pot, as a percentage. "
        "A simple measure of how hard the pot is being asked to work.\n"
        "- **Guardrails**: an optional rule that trims spending a bit in a run of bad markets, and "
        "allows a bit more in a run of good ones, instead of spending the exact same amount no matter "
        "what happens to investments.\n"
        "- **Forward-looking blend (CMA)**: by default the tool learns from actual market history "
        "(2000-2026). This slider lets you also factor in what professional forecasters currently "
        "expect for the NEXT 10 years, which tends to be more cautious than the recent past.\n"
        "- **Mortality**: switches the tool from 'assume the client lives to the end of the plan' to "
        "'use realistic odds of the client being alive at each age', which changes how you should read "
        "the ruin and legacy figures.\n"
        "- **Annuity / annuitization**: swapping part of the pot, once, for a guaranteed income paid "
        "for the rest of the client's life — like a personal pension income that can never run out, "
        "in exchange for giving up that portion of the pot.\n"
        "- **Legacy**: whatever is left in the pot at the end of the plan (or at death, in the "
        "mortality-adjusted figures) — what the client would leave behind.\n"
        "- **Sensitivity analysis / sweep**: re-running the simulation while turning one dial at a "
        "time (spend, equity exposure, guardrail settings) to see how much each one actually matters.\n"
        "- **Tax & State Pension**: switches the model from a single pre-tax spending number to a "
        "more realistic picture where withdrawals are taxed and the State Pension helps cover some "
        "of the spend once it starts."
    )

with st.expander("All assumptions — what's baked into these numbers"):
    st.markdown(
        "**Data sources**\n"
        "- Fund/asset-class return history: Bloomberg data (to 14 July 2026), plus the FNZ holdings "
        "files for Four Seasons (14 July 2026) and Original/Alternative.\n"
        "- Cash uses the real Bank of England SONIA rate, not the Bloomberg file's own cash column "
        "(which implausibly showed a small negative rate).\n"
        "- Inflation: actual historical UK CPI YoY (Bloomberg), not a fixed assumed rate — so "
        "inflation risk feeds through into the probability-of-ruin figure itself.\n"
        "- Mortality: the S4 pension-scheme table (CMI, UK self-administered pension scheme "
        "experience), not a general-population table.\n"
        "- Forward-looking returns (CMA slider): a compiled median across published third-party "
        "long-run forecasts, blended with historical data — see `src/cma.py`.\n"
        "- Annuity rates: real, dated UK best-buy rates (Hargreaves Lansdown, 14–28 May 2026), "
        "**not** a personalised quote — actual quotes vary by provider, postcode and health.\n\n"
        "**Portfolio construction**\n"
        "- This tool can compare Mobius against ANY registered portfolio - a different competitor's "
        "fund, or another Mobius construction - via 'Add a new portfolio' in the sidebar's 'Edit "
        "data' tab. The points below describe the four BUNDLED example portfolios specifically; a "
        "newly-added portfolio carries whatever provenance you give it when you register it.\n"
        "- Every holding is mapped to the best-matching BROAD asset-class series (full history back "
        "to 1999/2000) rather than its own short fund history, because many individual fund series "
        "in the data are too short (some as little as 20 months) to bootstrap a 25+ year simulation "
        "from directly.\n"
        "- Aspen Growth Passive Plus vs Mobius Alternative largely hold the SAME underlying index exposure (the "
        "FNZ data literally labels several pairs 'Same Index') — the difference being compared is "
        "fee (AMC), not market return.\n"
        "- Four Seasons holdings/weights come from the FNZ data supplied 14 July 2026; a few holdings "
        "are mapped to 'Commodities' as the closest available proxy for gold/natural-resources "
        "exposure.\n"
        "- Mobius Better's construction (11 holdings incl. global agg bonds, quality/managed-vol/EM "
        "equities, HY/ABS/EM corporate credit, US property REITS, commodities, and two hedge-fund "
        "strategies) is sourced from the previous Mobius model's own asset-class data, at a flat "
        "0.07% pa fee across all holdings. Its own 11 return series only overlap from 2001, so "
        "Mobius Better's usable historical window (2001-2025) is shorter than the main dataset's "
        "(1999/2000-2026) — narrowing the historical data window above can't extend this.\n"
        "- All fund/holding weights and fees can be viewed and edited live in the sidebar's 'Edit "
        "data' tab — the numbers on this page always reflect whatever is currently in that editor, "
        "not a fixed hardcoded lineup.\n\n"
        "**Methodology simplifications**\n"
        "- Monte Carlo sampling defaults to a stationary block bootstrap (Politis & Romano, 1994) "
        "over actual monthly history, preserving serial correlation/momentum that a naive month-by-"
        "month shuffle would destroy. A fixed-length block bootstrap and a skewed Student-t "
        "parametric sampler are also available for comparison, but full multivariate-normal (MVN) "
        "sampling across all asset classes jointly is not implemented.\n"
        "- Mortality is assumed independent of market returns (a standard simplifying assumption).\n"
        "- Tax + State Pension modelling is deliberately simplified for this first pass: the whole "
        "pot is treated as a single taxable pension wrapper (every pound withdrawn counts as income) "
        "— no 25% pension-commencement tax-free lump sum, and no separate ISA (tax-free) or GIA "
        "(capital-gains) wrapper split is modelled. Rest-of-UK tax bands only (Scotland differs). "
        "2026/27 tax bands and the State Pension are both held constant in today's money (assumed to "
        "rise with inflation) for the whole horizon.\n"
        "- No 25% pension-commencement tax-free lump sum is modelled before annuitizing either, "
        "consistent with the tax simplification above.\n\n"
        "**Known limitations**\n"
        "- Short historical window: even the longest series only goes back to 1999/2000, so the "
        "Monte Carlo is bootstrapping from roughly 25-27 years of market history — it has not lived "
        "through, for example, a repeat of the 1970s stagflation shock.\n"
        "- 'Better' portfolio weights were tuned empirically by testing candidate allocations through "
        "this simulation engine itself, then replaced with a specific, since-confirmed real-world "
        "construction ('Better v4') — it is one reasonable diversified construction, not the only "
        "possible one.\n"
        "- Restricting the historical data window (control above) makes Monte Carlo results noisier "
        "the shorter it gets — a handful of years isn't much to bootstrap 30-year outcomes from."
    )

_asset_returns_mtime = (portfolios_mod.DATA_DIR / "asset_class_returns.csv").stat().st_mtime
asset_df = _cached_load_asset_returns(_asset_returns_mtime)
cpi = load_cpi(asset_df)

with st.sidebar:
    tab_client, tab_data, tab_advanced = st.tabs(["Client", "Edit data", "Advanced"])
    with tab_client:
        st.header("Client")
        age = st.number_input("Starting age", 40, 90, 65,
                               help="The client's age at the start of the plan.")
        horizon = st.slider("Time horizon (years)", 5, 40, 30,
                             help="How many years the plan needs to last - e.g. to a target age of 95.")
        pot = st.number_input("Starting pot (£)", 10_000, 10_000_000, 500_000, step=10_000,
                               help="The total value of the pension pot today.")
        spend = st.number_input(
            "Desired annual spend, today's money (£)", 1_000, 500_000, 20_000, step=1_000,
            help="The amount the client wants IN THEIR POCKET each year, in today's prices. If 'Include "
                 "income tax + State Pension' is switched on below, this is treated as the NET (take-home) "
                 "figure - the model works out how much extra has to come out of the pot to cover tax.",
        )
        wr = spend / pot
        st.metric("Initial withdrawal rate", f"{wr*100:.2f}%",
                  help="Desired spend ÷ starting pot. A common rule of thumb puts 'safe' withdrawal rates "
                       "around 3.5-4%, but the right number depends heavily on the portfolio, guardrails, "
                       "tax/State Pension and how long the money needs to last - that's what the rest of "
                       "this tool is for.")

        st.header("Historical data window")
        _data_min = asset_df.index.min().date()
        _data_max = asset_df.index.max().date()
        window_start, window_end = st.slider(
            "Restrict every chart and statistic to this period",
            min_value=_data_min, max_value=_data_max, value=(_data_min, _data_max),
            help="Narrows ALL charts and statistics on this page to only this historical window - "
                 "e.g. drag the left edge in to test the last 10 years instead of the full history, "
                 "the way you'd test whether a conclusion still holds on more recent data. Each "
                 "portfolio still only uses whatever of ITS OWN holdings' data falls within this "
                 "window - narrowing it doesn't invent data a portfolio doesn't have.",
        )
        if window_start > _data_min or window_end < _data_max:
            _years_shown = (window_end - window_start).days / 365.25
            st.caption(f"Using {window_start} to {window_end} (~{_years_shown:.0f} years) instead of "
                       "the full available history. Very short windows make Monte Carlo results "
                       "noisier - a handful of years isn't much to bootstrap from.")
        # NOTE: the actual asset_df filtering happens AFTER the sidebar block closes (not here) -
        # tab_data below can add new asset-class columns via an outer join, which would silently
        # re-widen the date range again if we filtered before that ran. Filtering once, last,
        # after any upload has already happened, keeps the slider's bounds authoritative.

        st.header("What to show")
        view_mode = st.radio(
            "Which comparison do you want to see?",
            ["Both", "Accumulation only", "Decumulation only"],
            help="Accumulation tests growing the pot with no withdrawals; Decumulation tests spending "
                 "from it. Pick whichever portfolios to compare in each section below - pick one mode "
                 "if you only need a single scenario for this conversation; 'Both' recomputes and shows "
                 "everything, which takes a little longer.",
        )
        show_accum = view_mode in ("Both", "Accumulation only")
        show_decum = view_mode in ("Both", "Decumulation only")

        st.header("Portfolios to compare")
        if show_accum:
            accum_chosen = st.multiselect(
                "Accumulation portfolios", list(PORTFOLIOS.keys()), default=["Original", "Alternative"],
                format_func=display_name, key="accum_chosen",
                help="Drives the Accumulation section (growing the pot, no withdrawals). Pick any "
                     "registered portfolio here - a competitor's fund vs a Mobius one, or several at "
                     "once - not just the two built-in defaults.",
            )
        else:
            accum_chosen = []
            st.caption("Not shown — set 'What to show' above to Accumulation or Both.")

        if show_decum:
            chosen = st.multiselect(
                "Decumulation portfolios", list(PORTFOLIOS.keys()), default=["Four Seasons", "Better"],
                format_func=display_name,
                help="Drives the Decumulation section (spending from the pot) and the 'Detailed "
                     "analysis' section further down. Pick any registered portfolio here.",
            )
        else:
            chosen = []
            st.caption("Not shown — set 'What to show' above to Decumulation or Both.")

    with tab_data:
        with st.expander("📤 Add new asset-class return data"):
            st.caption(
                "Upload a CSV with a Date column plus one or more monthly-return columns (e.g. the "
                "previous model's Hedge Fund Credit Suisse / HF Trend / Novum Mgd Vol series) to make "
                "them selectable as asset classes in the portfolio editor below - no code changes needed. "
                "Values should be monthly simple returns as fractions (e.g. 0.0123 for 1.23%)."
            )
            uploaded_asset_file = st.file_uploader("Asset-class return data (CSV)", type="csv",
                                                    key="asset_upload")
            if uploaded_asset_file is not None:
                try:
                    new_data = pd.read_csv(uploaded_asset_file)
                    date_col = new_data.columns[0]
                    new_data[date_col] = pd.to_datetime(new_data[date_col])
                    new_data = new_data.set_index(date_col)
                    new_data.index = new_data.index.to_period("M").to_timestamp("M")
                    new_data.index.name = asset_df.index.name
                    new_cols = [c for c in new_data.columns if c not in asset_df.columns]
                    if not new_cols:
                        st.warning("No new columns found (names already exist or file has none besides Date).")
                    else:
                        asset_df = asset_df.join(new_data[new_cols], how="outer")
                        for c in new_cols:
                            AC[c] = c  # label == column name directly; no separate short-name needed
                        st.success(f"Added {len(new_cols)} asset class(es): {', '.join(new_cols)} - "
                                   "now selectable in the portfolio editor below.")
                        if st.button("💾 Save uploaded data as new default", key="save_asset_upload"):
                            asset_df.to_csv(portfolios_mod.DATA_DIR / "asset_class_returns.csv")
                            save_asset_map_to_csv(AC)
                            st.success("Saved - available in future sessions too.")
                except Exception as e:
                    st.error(f"Couldn't read this file: {e}")

        with st.expander("➕ Add a new portfolio (e.g. another provider's fund)"):
            st.caption(
                "Register a brand-new portfolio - a different competitor's fund, or another Mobius "
                "construction - so it shows up everywhere (labels, colours, section titles, PDF export) "
                "exactly like the built-in ones, with no code changes. It starts with zero holdings; add "
                "rows to it in 'Edit portfolio holdings & fees' below, then pick it in 'Portfolios to "
                "compare' in the Client tab."
            )
            new_name = st.text_input("Internal name (must be unique)", key="new_portfolio_name",
                                      placeholder="e.g. Legal & General PMC")
            new_display = st.text_input("Display name (shown throughout the app)",
                                         key="new_portfolio_display", placeholder="e.g. L&G Multi-Asset Fund")
            new_owner = st.radio("Whose fund is this?", ["Competitor", "Mobius"], key="new_portfolio_owner",
                                  horizontal=True,
                                  help="Drives the colour scheme - Mobius portfolios get the warm brand "
                                       "palette, everyone else's get neutral greys.")
            new_provider = st.text_input("Provider / fund house name (used in section captions and the PDF)",
                                          key="new_portfolio_provider", placeholder="e.g. Legal & General")
            if st.button("Create portfolio", key="create_new_portfolio"):
                if not new_name.strip():
                    st.error("Give the portfolio a name first.")
                elif new_name in PORTFOLIOS:
                    st.error(f"'{new_name}' already exists - pick a different name or edit it below.")
                else:
                    PORTFOLIOS[new_name] = []
                    PORTFOLIO_META[new_name] = {
                        "DisplayName": new_display.strip() or new_name,
                        "Owner": new_owner,
                        "Provider": new_provider.strip() or (new_display.strip() or new_name),
                    }
                    st.session_state["edit_portfolio_name"] = new_name
                    st.success(f"Created '{new_name}' - add its holdings below, then save both to make "
                               "it the default for future sessions too.")

        with st.expander("✏️ Edit portfolio holdings & fees"):
            st.caption(
                "Edit any portfolio's holdings, asset-class mapping or fee directly - every chart and "
                "statistic below recalculates immediately, no code changes needed. 'Save as new "
                "default' writes your edit to data/portfolio_holdings.csv so it's still there next time "
                "the app starts; without saving, edits only last for this session."
            )
            edit_name = st.selectbox("Portfolio to edit", list(PORTFOLIOS.keys()),
                                      format_func=display_name, key="edit_portfolio_name")
            edit_df = portfolio_summary(edit_name)[["Holding", "AssetClass", "Weight", "OCF"]]
            edited_df = st.data_editor(
                edit_df, num_rows="dynamic", key=f"editor_{edit_name}", use_container_width=True,
                column_config={
                    "AssetClass": st.column_config.SelectboxColumn("Asset class", options=list(AC.keys())),
                    "Weight": st.column_config.NumberColumn("Weight", format="percent", min_value=0.0,
                                                              max_value=1.0, step=0.0001),
                    "OCF": st.column_config.NumberColumn("OCF (fee)", format="percent", min_value=0.0,
                                                          max_value=0.05, step=0.0001),
                },
            )
            edited_df = edited_df.dropna(subset=["Holding", "AssetClass", "Weight", "OCF"])
            total_w = edited_df["Weight"].sum()
            st.caption(
                f"Total weight: {total_w:.1%}"
                + ("" if abs(total_w - 1.0) < 0.01 else " — doesn't sum to 100%, carried through as-is "
                                                         "(not auto-normalised), same as the source data.")
            )
            # Mutates the SAME dict object engine.py imported (from portfolios import PORTFOLIOS) - so
            # every downstream function that reads PORTFOLIOS[name] picks this up immediately, with no
            # other code changes needed. Re-applied fresh every rerun from the editor's current state.
            PORTFOLIOS[edit_name] = list(
                edited_df[["Holding", "AssetClass", "Weight", "OCF"]].itertuples(index=False, name=None)
            )
            if st.button(f"💾 Save {display_name(edit_name)} as new default", key=f"save_{edit_name}"):
                save_portfolios_to_csv(PORTFOLIOS)
                if edit_name not in PORTFOLIO_META:
                    PORTFOLIO_META[edit_name] = {"DisplayName": edit_name, "Owner": "Competitor",
                                                  "Provider": edit_name}
                save_portfolio_meta_to_csv(PORTFOLIO_META)
                st.success(f"Saved - {display_name(edit_name)} is now the default for future sessions too.")

    with tab_advanced:
        st.header("Guardrails")
        guardrails = st.checkbox(
            "Apply spending guardrails", value=False,
            help="If on, spending automatically flexes a little with how markets are doing - trimmed back "
                 "a bit after weak markets, allowed to rise a bit after strong ones - instead of staying "
                 "perfectly fixed regardless of what's happened to the pot.",
        )
        band = st.slider(
            "How far spending can drift before guardrails kick in (± %)", 0.05, 0.40, 0.20, step=0.05,
            help="A narrower band reacts sooner (more frequent small adjustments); a wider band only "
                 "steps in for bigger swings.",
        )
        cut = st.slider("Spending cut when markets are running hot (i.e. pot depleting too fast)", 0.0, 0.30, 0.10, step=0.05)
        raise_ = st.slider("Spending rise when markets have done well (pot comfortably ahead)", 0.0, 0.30, 0.10, step=0.05)

        st.header("How the model tests the future")
        method = st.selectbox(
            "Simulation approach",
            ["stationary_block", "fixed_block", "iid", "skew_t"],
            format_func=lambda m: {
                "stationary_block": "Realistic historical patterns (recommended)",
                "fixed_block": "Historical patterns, fixed 12-month chunks",
                "iid": "Simple random shuffle of historical months",
                "skew_t": "Statistical model tuned for extreme/crash years",
            }[m],
            help="All four methods draw on the same 2000-2026 market history - they differ in HOW they "
                 "recombine it into thousands of possible futures. 'Realistic historical patterns' "
                 "(technical name: stationary block bootstrap) keeps realistic runs of good/bad months "
                 "together, which is usually the most representative choice; the others are useful for "
                 "comparison and stress-testing.",
        )
        block_mean = st.slider("Typical length of a good/bad market run being modelled (months)", 3, 24, 12) if "block" in method else 12
        n_sims = st.select_slider(
            "Number of simulated futures", [500, 1000, 2000, 3000, 5000], value=2000,
            help="More simulated futures = a more statistically reliable answer, at the cost of taking "
                 "longer to run. 2,000 is a good default.",
        )
        seed = st.number_input(
            "Random seed", 0, 999999, 42,
            help="Just fixes which set of random futures gets simulated, so re-running with the same "
                 "settings gives the same answer. Change it to sanity-check that results aren't a fluke "
                 "of one particular random draw.",
        )

        st.header("How optimistic should the assumptions be?")
        cma_blend_pct = st.slider(
            "Lean on future forecasts, not just the past (0% = pure history, 100% = pure forecast)",
            0, 100, 0, step=5, format="%d%%",
            help="In short: by default (0%), the tool assumes the future looks like 2000-2026, which was "
                 "a strong run for stock markets. Moving this slider makes the tool assume somewhat lower "
                 "average returns instead, in line with what professional forecasters currently expect "
                 "for the next 10 years - a more cautious, arguably more realistic, test of the plan. Day-"
                 "to-day ups and downs and worst-case scenarios still come from real market history either "
                 "way - only the AVERAGE return assumption moves.",
        )
        cma_blend = cma_blend_pct / 100.0
        if cma_blend > 0:
            _cma_selected_classes = set()
            for _n in list(accum_chosen) + list(chosen if show_decum else []):
                _cma_selected_classes |= set(asset_class_weights(_n).index)
            _cma_uncovered = sorted(_cma_selected_classes - set(cma_mod.CMA_ANNUAL.keys()))
            if _cma_uncovered:
                st.caption(
                    f"Most asset classes' average returns are being pulled **{cma_blend_pct}% of the way** "
                    "from their historical average towards their forward-looking forecast (source: "
                    "Monevator's compilation of published 10-year forecasts) - **except** "
                    f"{', '.join(_cma_uncovered)}, which have no published forward-looking forecast and "
                    "stay at their historical average regardless of this slider. See 'What the forward-"
                    "looking blend means' below for the full picture."
                )
            else:
                st.caption(
                    f"Every asset class held by your selected portfolios is being pulled "
                    f"**{cma_blend_pct}% of the way** from its historical average towards its forward-"
                    "looking forecast (source: Monevator's compilation of published 10-year forecasts). "
                    "See 'What the forward-looking blend means' below for the numbers."
                )

        st.header("How long might the client actually live?")
        use_mortality = st.checkbox(
            "Factor in realistic life expectancy", value=False,
            help="Off (default): the tool tests the plan as if the client is certain to live to the end "
                 "of the horizon. On: it uses realistic UK pension-scheme survival odds at each age, so "
                 "you can also see 'ruin BEFORE death' - often a much better and more meaningful number "
                 "than the raw 'ruin by year 30' figure, since running out of money after everyone "
                 "involved has already died isn't really a failure of the plan.",
        )
        if use_mortality:
            sex = st.selectbox("Sex", ["male", "female"], format_func=str.title)
            joint_life = st.checkbox(
                "Couple (plan should last as long as EITHER partner is alive)", value=False,
            )
            partner_age, partner_sex = None, None
            if joint_life:
                partner_sex = st.selectbox("Partner's sex", ["female", "male"], format_func=str.title)
                partner_age = st.number_input("Partner's starting age", 40, 90, age - 2)
                st.caption(
                    "This only extends how long the money needs to last - to whichever partner dies "
                    "SECOND, not just this one - it does not add a second pot or income. Enter the "
                    "'Starting pot' and 'Desired annual spend' above as the COUPLE'S COMBINED totals, "
                    "not just this person's own figures, for this to reflect their real household "
                    "position."
                )
            st.caption(
                "Survival odds come from the S4 table (CMI, UK pension-scheme member experience) rather "
                "than general population statistics, since pension scheme members tend to live somewhat "
                "longer than the population average - a more accurate basis for a retirement plan."
            )
        else:
            joint_life, partner_age, partner_sex, sex = False, None, None, "male"

        st.header("Tax & State Pension")
        apply_tax = st.checkbox(
            "Include income tax + State Pension", value=False,
            help="When on, 'Desired annual spend' above is treated as the NET (take-home) amount the "
                 "client wants. The model works out how much has to be withdrawn from the pot (gross, "
                 "taxable) to actually deliver that, and adds the State Pension as a second income "
                 "stream once it starts. When off (the default), spend is treated as a single "
                 "pre-tax number, as in the rest of this tool.",
        )
        if apply_tax:
            sp_amount = st.number_input(
                "Full State Pension, today's £ per year", 0, 50_000,
                int(round(tax.FULL_NEW_STATE_PENSION_ANNUAL)), step=100,
                help="Defaults to the full new State Pension for 2026/27 (£12,547.60/yr, i.e. £241.30/"
                     "week). Lower this if the client won't get the full amount - e.g. gaps in their "
                     "National Insurance record - or use their own State Pension forecast figure if known.",
            )
            sp_age = st.number_input(
                "State Pension age", 55, 75, tax.DEFAULT_STATE_PENSION_AGE,
                help="The age State Pension starts. This varies by date of birth - check the client's "
                     "exact age at gov.uk/state-pension-age. 67 is a reasonable default for most people "
                     "retiring around now.",
            )
            st.caption(
                "Basis: UK rest-of-UK income tax (England/Wales/Northern Ireland - Scotland has "
                "different bands), 2026/27 rates, with the WHOLE pot treated as a taxable pension "
                "wrapper (every pound withdrawn counts as income - no 25% tax-free lump sum or ISA/"
                "GIA modelling yet, so this slightly OVERSTATES tax if some of the money is actually in "
                "tax-free wrappers). Tax bands and the State Pension are both held in today's money "
                "(assumed to grow with inflation) for the whole plan, rather than literally freezing "
                "today's thresholds for 30 years."
            )
        else:
            sp_amount, sp_age = tax.FULL_NEW_STATE_PENSION_ANNUAL, tax.DEFAULT_STATE_PENSION_AGE

# Historical data window filter - applied here (after the sidebar, incl. any asset-class upload in
# tab_data) rather than inline where the slider is defined, so an upload's outer join can't silently
# re-widen the date range the slider shows. Every chart/statistic below sees only this window.
asset_df = asset_df[(asset_df.index.date >= window_start) & (asset_df.index.date <= window_end)]

# Forward-looking CMA blend: recentre each asset class's mean monthly return, leaving volatility/
# correlation/shape untouched (see src/cma.py). Applied ONCE here, before any simulation function
# is called, so every chart and statistic in the app below is automatically consistent - no other
# engine changes are needed since every simulation function takes asset_df as a plain parameter.
cma_shift_table = None
if cma_blend > 0:
    cma_shift_table = cma_mod.cma_shifts(asset_df, AC)
    asset_df = cma_mod.apply_cma_blend(asset_df, AC, cma_blend)

# Accumulation portfolios always run with NO withdrawals - a separate comparison from the
# decumulation multiselect below, since accumulation is about pure growth, not spending.
# Guardrails/tax are moot with zero spend, so left off regardless of the sidebar toggles (which
# apply to decumulation only). accum_chosen can be ANY registered portfolio(s), not a fixed pair.
accum_profile_kwargs = dict(
    starting_age=age, horizon_years=horizon, starting_pot=float(pot), initial_annual_spend=0.0,
    guardrails=False, guardrail_band=band, guardrail_cut=cut, guardrail_raise=raise_,
    # State Pension is zeroed here too (not just apply_tax) - historical_single_path/run_simulation
    # always fold State Pension into "Spend" regardless of apply_tax, so leaving it non-zero would
    # leak an external income stream into what's meant to be a pure, no-cash-flow pot-growth figure.
    apply_tax=False, state_pension_annual=0.0, state_pension_age=int(sp_age),
)
accum_results = {}
if show_accum:
    for name in accum_chosen:
        profile = ClientProfile(**accum_profile_kwargs)
        accum_results[name] = _cached_run_simulation(name, asset_df, cpi, profile, method, n_sims,
                                                      block_mean, seed)

profile_kwargs = dict(
    starting_age=age, horizon_years=horizon, starting_pot=float(pot), initial_annual_spend=float(spend),
    guardrails=guardrails, guardrail_band=band, guardrail_cut=cut, guardrail_raise=raise_,
    apply_tax=apply_tax, state_pension_annual=float(sp_amount), state_pension_age=int(sp_age),
)

results = {}
for name in chosen:
    profile = ClientProfile(**profile_kwargs)
    results[name] = _cached_run_simulation(name, asset_df, cpi, profile, method, n_sims,
                                            block_mean, seed)

# ACCUMULATION section - the 5-second takeaway, filled into the container declared right under the
# title so it renders at the TOP of the page even though it depends on the sidebar inputs computed
# above. accum_chosen can be any registered portfolio(s), not a fixed pair - the "cost difference
# alone" narrative/chart below only applies when exactly two are picked AND they share the same
# underlying asset-class exposure (the case the tool was originally built for: Aspen vs Mobius
# holding the same indices at different fees); anything else just gets the plain comparison cards.
if show_accum:
    with hero_container:
        accum_title = (f"Accumulation — {' vs '.join(display_name(n) for n in accum_chosen)}"
                        if accum_chosen else "Accumulation")
        render_comparison_section(
            accum_title,
            "No withdrawals: growing the pot from today until the horizon ends. Probability of ruin isn't "
            "shown here since it's always ~0% with nothing being withdrawn and not a meaningful comparison "
            "point - volatility, annualised performance and cumulative performance are the metrics that "
            "matter for this comparison.",
            accum_chosen, accum_results, accum_profile_kwargs, asset_df, cpi,
            show_ruin=False,
        )

        if len(accum_chosen) == 2:
            a, b = accum_chosen
            wa = asset_class_weights(a)
            same_exposure = similar_exposure(a, b)
            fee_a, fee_b = weighted_avg_fee(a) * 100, weighted_avg_fee(b) * 100
            s_a, s_b = accum_results[a].summary(), accum_results[b].summary()
            legacy_gain = s_b["Median legacy"] - s_a["Median legacy"]
            legacy_phrase = (f"**grows to £{legacy_gain:,.0f} more**" if legacy_gain > 0
                              else f"**grows to £{abs(legacy_gain):,.0f} less**")

            if same_exposure:
                st.success(
                    f"**Same underlying market exposure, different cost** ({fee_a:.2f}% vs {fee_b:.2f}% pa): "
                    f"over {horizon} years, {display_name(b)} {legacy_phrase} than {display_name(a)}, for "
                    "holdings that track essentially the same indices."
                )

                # Isolates cost alone: BOTH lines use the SAME asset-class weights (confirmed above), so
                # the only variable that differs is the fee - a clean, deterministic (no simulation
                # noise) illustration of what the cost difference alone is worth.
                st.markdown("**What that cost difference alone is worth, left untouched**")
                fee_a_frac, fee_b_frac = weighted_avg_fee(a), weighted_avg_fee(b)
                monthly_a = weighted_monthly_returns(wa, fee_a_frac, asset_df, label="fee_check_a").dropna()
                monthly_b = weighted_monthly_returns(wa, fee_b_frac, asset_df, label="fee_check_b").dropna()
                growth_a = 1 + monthly_a.mean()
                growth_b = 1 + monthly_b.mean()
                months = np.arange(horizon * 12 + 1)
                val_a = pot * growth_a ** months
                val_b = pot * growth_b ** months
                years_axis = months / 12.0
                fig_fee = go.Figure()
                fig_fee.add_trace(go.Scatter(
                    x=years_axis, y=val_a, name=display_name(a),
                    line=dict(color=portfolio_color(a), width=2, dash="dot"),
                ))
                fig_fee.add_trace(go.Scatter(
                    x=years_axis, y=val_b, name=display_name(b), fill="tonexty",
                    fillcolor=_hex_to_rgba(portfolio_color(b), 0.20), line=dict(color=portfolio_color(b), width=3),
                ))
                fig_fee.update_layout(
                    xaxis_title="Year", yaxis_title="£", height=340,
                    margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.2),
                )
                st.plotly_chart(fig_fee, use_container_width=True)
                fee_gap = val_b[-1] - val_a[-1]
                st.caption(
                    f"Both lines hold the SAME asset-class exposure and the SAME average market growth "
                    f"({(growth_a**12 - 1)*100:.2f}% pa gross assumption, held equal) - the only "
                    f"difference is {display_name(a)}'s {fee_a:.2f}% vs {display_name(b)}'s {fee_b:.2f}% pa "
                    f"charge. On a £{pot:,.0f} pot with no withdrawals, that alone is worth "
                    f"**£{fee_gap:,.0f}** after {horizon} years."
                )
            else:
                st.info(
                    f"**{display_name(b)}** {legacy_phrase} than **{display_name(a)}** over {horizon} years "
                    f"(fee: {fee_a:.2f}% vs {fee_b:.2f}% pa) - note these two hold DIFFERENT underlying "
                    "asset-class exposure, so this reflects both the cost difference AND different market "
                    "exposure, not cost alone."
                )
        render_holdings_section(accum_chosen)
        st.divider()

# Plain-English recap of the current scenario - so anyone opening this tool (not just the person who
# set the sidebar controls) can see at a glance what's actually being compared, before wading into
# the detailed statistics and charts below. Wording branches on view_mode since accumulation has no
# spend/withdrawal-rate concept to recap.
if show_decum:
    _summary_bits = [
        f"Comparing **{', '.join(display_name(n) for n in chosen) if chosen else 'no portfolios (pick some in the sidebar)'}** "
        f"for a **{age}-year-old** with a **£{pot:,.0f}** pot, wanting **£{spend:,.0f}/year** "
        f"({wr*100:.1f}% withdrawal rate) to last **{horizon} years**."
    ]
    if guardrails:
        _summary_bits.append("Spending guardrails are **ON** (cuts spend in weak markets, raises it in strong ones).")
    if apply_tax:
        _summary_bits.append(
            f"Tax + State Pension are **included** (the £{spend:,.0f} above is treated as take-home; "
            f"State Pension starts at age {sp_age})."
        )
    if cma_blend > 0:
        _summary_bits.append(f"Returns are blended **{cma_blend_pct}%** toward forward-looking forecasts.")
    if use_mortality:
        _summary_bits.append(
            f"Mortality is **included** ({sex}{', joint life' if joint_life else ''}) — see the "
            "mortality-adjusted figures further down for 'before death' outcomes."
        )
    st.info(" ".join(_summary_bits))
elif show_accum:
    _accum_label = (', '.join(display_name(n) for n in accum_chosen) if accum_chosen
                    else 'no portfolios (pick some in the sidebar)')
    _summary_bits = [
        f"Comparing **{_accum_label}** for a **{age}-year-old** growing a "
        f"**£{pot:,.0f}** pot over **{horizon} years** (no withdrawals)."
    ]
    if cma_blend > 0:
        _summary_bits.append(f"Returns are blended **{cma_blend_pct}%** toward forward-looking forecasts.")
    st.info(" ".join(_summary_bits))

if apply_tax and show_decum:
    st.subheader("What tax and State Pension mean for this plan")
    gross_before_sp = tax.gross_up_pot_withdrawal(spend, other_taxable_income=0.0)
    tax_before_sp = tax.tax_due(gross_before_sp)
    gross_after_sp = tax.gross_up_pot_withdrawal(spend, other_taxable_income=sp_amount)
    tax_after_sp = tax.tax_due(gross_after_sp + sp_amount)
    pot_saving = gross_before_sp - gross_after_sp

    st.info(
        f"To put **£{spend:,.0f}/year** in the client's pocket, more than that has to come out of the "
        f"pot, because withdrawals are taxed as income. Once State Pension starts at age {sp_age}, it "
        f"covers part of that need directly, so the pot has to supply **£{pot_saving:,.0f}/year less** "
        f"- one of the biggest levers in this whole plan, often bigger than the choice of portfolio."
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Age {age}–{sp_age - 1} (before State Pension)**")
        st.metric("Gross withdrawal needed from the pot", f"£{gross_before_sp:,.0f}")
        st.metric("Income tax paid", f"£{tax_before_sp:,.0f}")
    with col2:
        st.markdown(f"**Age {sp_age}+ (once State Pension starts)**")
        st.metric("Gross withdrawal needed from the pot", f"£{gross_after_sp:,.0f}",
                   delta=f"-£{pot_saving:,.0f} vs before", delta_color="inverse")
        st.metric("State Pension received (also taxable)", f"£{sp_amount:,.0f}")
    st.caption(
        "Figures above are illustrative, in today's money, and don't include investment growth or "
        "guardrail adjustments - they show the mechanics of the tax/State Pension calculation in "
        "isolation. The simulations below apply the same calculation every year, on the actual "
        "(inflating, guardrail-adjusted) spending target, for every simulated market path."
    )

if cma_blend > 0:
    st.subheader("What the forward-looking blend means for this plan")
    _cma_ruin_line = (
        "Expect the probability of ruin below to be somewhat HIGHER than at the 0% setting - that's "
        "the blend doing its job, not a bug: it's a fairer, less rose-tinted test of the plan."
        if show_decum else
        "Expect the growth figures above to be somewhat LOWER than at the 0% setting - that's the "
        "blend doing its job, not a bug: it's a fairer, less rose-tinted test of the plan."
    )
    st.info(
        f"**In short: this plan is now being tested against more cautious return assumptions, not "
        f"just the strong 2000-2026 stretch.** All the results {'below' if show_decum else 'above'} use "
        f"returns pulled **{cma_blend_pct}%** of the way from actual history towards current analyst "
        "forecasts for the next 10 years. Growth assets like equities and REITs are generally pulled "
        "DOWN (forecasters expect more modest returns than that strong historical run), while some "
        f"bonds are pulled UP. {_cma_ruin_line}"
    )
    hist_annual, blended_annual, forecast_annual = {}, {}, {}
    for label, col in AC.items():
        if label not in cma_mod.CMA_ANNUAL or col not in asset_df.columns:
            continue
        shift_full = cma_shift_table[label]
        current_monthly_mean = asset_df[col].dropna().mean()  # already blended by cma_blend
        hist_monthly_mean = current_monthly_mean - cma_blend * shift_full
        hist_annual[label] = (1 + hist_monthly_mean) ** 12 - 1
        # exact figure actually driving the simulation below (not re-derived/approximated)
        blended_annual[label] = (1 + current_monthly_mean) ** 12 - 1
        forecast_annual[label] = cma_mod.CMA_ANNUAL[label]

    # Any asset class actually held by a selected portfolio but with NO published forecast is shown
    # explicitly (historical average only, forecast/blended left blank) rather than silently omitted -
    # so it's visible that this slider does nothing for that holding, instead of looking covered.
    _selected_classes = set()
    for _n in list(accum_chosen) + list(chosen if show_decum else []):
        _selected_classes |= set(asset_class_weights(_n).index)
    for label in sorted(_selected_classes - set(cma_mod.CMA_ANNUAL.keys())):
        col = AC.get(label)
        if col is None or col not in asset_df.columns:
            continue
        hist_annual[label] = (1 + asset_df[col].dropna().mean()) ** 12 - 1
        blended_annual[label] = hist_annual[label]  # unaffected by the slider - no forecast to blend towards
        forecast_annual[label] = float("nan")

    cma_table = pd.DataFrame({
        "Historical average (2000-2026), pa": hist_annual,
        "Forward-looking forecast (10yr), pa": forecast_annual,
        f"Blended at {cma_blend_pct}%, pa": blended_annual,
    })
    cma_table.index = [
        i + (" *" if i in cma_mod.PROXIED_ASSET_CLASSES else "")
        + (" †" if i not in cma_mod.CMA_ANNUAL else "")
        for i in cma_table.index
    ]
    st.dataframe(cma_table.style.format("{:.1%}", na_rep="no forecast"), use_container_width=True)
    st.caption(
        "† No published forward-looking forecast exists for this asset class at all - it stays at its "
        "historical average regardless of the slider (shown here for transparency, not silently hidden). "
        "* No published forward-looking forecast exists for this exact sub-category - proxied with "
        "the closest published category (see src/cma.py for which, and why). Source: Monevator's "
        "compilation of published 10-year GBP nominal return forecasts (Vanguard, Schroders, "
        "JPMorgan, BlackRock and others), https://monevator.com/investment-return-forecasts/."
    )

if show_decum:
    if results:
        render_comparison_section(
            f"Decumulation — {' vs '.join(display_name(n) for n in ordered_names(results))}",
            "With withdrawals: the client spends from this pot every year, so probability of ruin is the "
            "headline risk metric here, alongside volatility, annualised performance and cumulative "
            "performance.",
            ordered_names(results), results, profile_kwargs, asset_df, cpi,
            show_pot_only_irr=True,
        )
        st.download_button(
            "Download one-page summary (PDF)",
            data=build_summary_pdf(accum_results, results, accum_profile_kwargs, profile_kwargs, asset_df,
                                    cpi, age, pot, spend, horizon, wr),
            file_name="Mobius_Wealth_vs_Aspen_Advisers_summary.pdf",
            mime="application/pdf",
            help="A one-page takeaway covering both the Accumulation and Decumulation comparisons above - "
                 "hand it to the client or attach it to a follow-up email.",
        )
        render_holdings_section(ordered_names(results))
        st.divider()

    st.subheader("Headline statistics")
    summary_rows = []
    for name in ordered_names(results):
        s = results[name].summary()
        ci_lo, ci_hi = s.pop("Ruin prob 95% CI")
        s["Ruin prob 95% CI"] = f"{ci_lo:.1%} - {ci_hi:.1%}"
        s["Portfolio"] = display_name(name)
        summary_rows.append(s)
    summary_df = pd.DataFrame(summary_rows).set_index("Portfolio")
    fmt = {
        "Probability of ruin": "{:.1%}", "Ruin prob SE": "{:.2%}", "Median legacy": "£{:,.0f}",
        "5th pctl legacy": "£{:,.0f}", "95th pctl legacy": "£{:,.0f}", "Avg shortfall years": "{:.2f}",
        "% paths with any shortfall": "{:.1%}",
    }
    # defined at module level (not just inside `if use_mortality:`) since the annuitization comparison
    # section further down needs it too, and can run even when the main mortality toggle is off.
    mort_fmt = {
        "Probability of ruin before death": "{:.1%}",
        "Probability of surviving full horizon": "{:.1%}",
        "Probability of ruin by horizon end (no mortality)": "{:.1%}",
        "Median legacy at death": "£{:,.0f}",
        "5th pctl legacy at death": "£{:,.0f}",
        "95th pctl legacy at death": "£{:,.0f}",
    }
    st.dataframe(summary_df.style.format(fmt), use_container_width=True)
    st.caption(
        f"Probability-of-ruin estimates are based on {n_sims:,} simulated paths per portfolio, so they "
        f"carry sampling noise (shown as SE and a 95% CI above) - not a source of forecasting precision "
        f"beyond what {n_sims:,} random draws can support. Increase 'Number of simulations' in the "
        f"sidebar to tighten the interval; roughly 4x the sims halves the margin of error."
    )

    st.subheader("How the pot value could evolve over time")
    st.caption(
        "All portfolios plotted together so they're directly comparable. The bold line is each "
        "portfolio's median (typical) simulated outcome; the shaded band around it is the middle 50% "
        "of simulated futures (the middle 90% is available in 'Detailed analysis' below for the wider "
        "range)."
    )
    fig_overlay = go.Figure()
    for name in ordered_names(results):
        res = results[name]
        years = np.arange(res.profile.horizon_years + 1)
        q25, q50, q75 = (np.percentile(res.paths, q, axis=0) for q in (25, 50, 75))
        color = portfolio_color(name)
        fig_overlay.add_trace(go.Scatter(x=years, y=q75, line=dict(width=0), showlegend=False,
                                          hoverinfo="skip"))
        fig_overlay.add_trace(go.Scatter(x=years, y=q25, fill="tonexty", line=dict(width=0),
                                          showlegend=False, hoverinfo="skip",
                                          fillcolor=_hex_to_rgba(color, 0.18)))
        fig_overlay.add_trace(go.Scatter(x=years, y=q50, mode="lines", name=display_name(name),
                                          line=dict(width=3, color=color)))
    fig_overlay.update_layout(
        xaxis_title="Year", yaxis_title="Portfolio value (£)", height=440,
        margin=dict(l=10, r=10, t=20, b=10), hovermode="x unified",
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig_overlay, use_container_width=True)

    st.subheader("What might be left over at the end (the 'legacy')")
    st.caption(
        "Each box shows the range of estate values left across all simulated futures for that portfolio - "
        "the line in the middle of each box is the median, the box itself covers the middle 50% of "
        "outcomes, and the whiskers/dots show how wide the full range can get, including the occasional "
        "very high or very low result."
    )
    fig2 = go.Figure()
    for name in ordered_names(results):
        res = results[name]
        fig2.add_trace(go.Box(y=res.legacy, name=display_name(name), boxmean=True,
                               marker_color=portfolio_color(name)))
    fig2.update_layout(yaxis_title="Estate at end of horizon (£)", height=400, showlegend=False)
    st.plotly_chart(fig2, use_container_width=True)

# Detailed analysis is available whenever ANYTHING is shown (not just Decumulation) - several tabs
# below (Correlation, Historical check) are general-purpose, not decumulation-specific, so they
# shouldn't disappear just because the sidebar is set to "Accumulation only". Tabs that genuinely
# only make sense with a chosen decumulation portfolio (Mortality, Equity sweep, Sensitivity,
# Glide path, Annuity) guard themselves on `chosen` being non-empty and show a short explanatory
# message instead of silently doing nothing.
if show_accum or show_decum:
    st.divider()
    show_detail = st.checkbox(
        "Show detailed analysis (correlations, mortality, historical check, sensitivities, glide path, annuity, holdings)",
        value=False,
    )
    if show_detail:
        tab_corr, tab_mort, tab_hist, tab_sweep, tab_sens, tab_glide, tab_ann, tab_hold = st.tabs([
            "Correlation", "Mortality", "Historical check", "Equity sweep", "Sensitivity",
            "Glide path", "Annuity", "Holdings",
        ])
        with tab_corr:
            st.subheader("How much do these investments actually move together?")
            st.markdown(
                "**In plain terms:** 'diversification' - spreading money across different investment types to "
                "reduce risk - only really works between investments that DON'T move up and down together. This "
                "heatmap shows how closely each pair of asset types has historically moved in the same direction: "
                "**1.0 (dark red)** means they move almost perfectly together (little diversification benefit "
                "between them); **0 (white)** means no relationship; **negative (dark blue)** means they tend to "
                "move in opposite directions (the strongest diversification benefit)."
            )
            # Scoped to the asset classes actually held by the currently selected portfolios, not
            # every class ever registered across every portfolio (which has grown well past the
            # original 11 as more portfolios with their own naming - e.g. Better's - were added,
            # and would otherwise make this both a cluttered, hard-to-read grid and a stale claim
            # about exactly how many classes it covers).
            _corr_selected_classes = set()
            for _n in list(accum_chosen) + list(chosen if show_decum else []):
                _corr_selected_classes |= set(asset_class_weights(_n).index)
            corr_full = asset_correlation_matrix(asset_df)
            _corr_cols = [c for c in corr_full.columns if c in _corr_selected_classes] or list(corr_full.columns)
            corr = corr_full.loc[_corr_cols, _corr_cols]

            _reit_note = ""
            if {"REITs", "Infrastructure", "Global Equities"}.issubset(set(corr.columns)):
                _reit_note = (
                    f" Notably, REITs and Infrastructure run ~"
                    f"{corr.loc['REITs', 'Global Equities']:.2f}-{corr.loc['Infrastructure', 'Global Equities']:.2f} "
                    "correlated with Global Equities here, so they add less true diversification than their "
                    "labels might suggest."
                )
            st.caption(
                f"Based on monthly returns across the {len(corr)} asset classes held by the portfolio(s) "
                f"currently selected, over the full history (1999/2000-2026).{_reit_note}"
            )
            fig_corr = go.Figure(data=go.Heatmap(
                z=corr.values, x=corr.columns.tolist(), y=corr.index.tolist(),
                colorscale="RdBu", zmid=0, zmin=-1, zmax=1,
                text=corr.round(2).values, texttemplate="%{text}", textfont=dict(size=10),
                colorbar=dict(title="Correlation"),
            ))
            fig_corr.update_layout(height=max(400, 32 * len(corr)), margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_corr, use_container_width=True)

        with tab_mort:
            if use_mortality and not chosen:
                st.info("Pick at least one Decumulation portfolio (or switch 'What to show' to "
                        "Decumulation or Both) to see mortality-adjusted decumulation outcomes.")
            elif use_mortality:
                st.subheader("What the results look like factoring in life expectancy")
                mortality_table = load_mortality_table()
                qx_map = {"male": mortality_table["qx_male"], "female": mortality_table["qx_female"]}

                mortality_results = {}
                for name in chosen:
                    mortality_results[name] = run_mortality_overlay(
                        results[name], mortality_table, sex=sex,
                        partner_age=partner_age if joint_life else None,
                        partner_sex=partner_sex if joint_life else None,
                    )

                life_basis_label = next(iter(mortality_results.values())).life_basis if mortality_results else ""
                st.markdown(
                    f"**In plain terms:** based on **{life_basis_label}**, the figures below re-check each "
                    "already-simulated market future against realistic odds of the client being alive at each "
                    "age. 'Before death' outcomes are usually much better than the raw figures further up the "
                    "page, because a lot of 'runs out of money by year 30' paths only actually run out after the "
                    "client (or, for a couple, both partners) has already died - which isn't really a failure of "
                    "the plan."
                )

                col_surv, col_stats = st.columns([1, 1.4])

                with col_surv:
                    st.markdown("**Odds of still being alive, year by year**")
                    years_axis = np.arange(horizon + 1)
                    fig_surv = go.Figure()
                    own_curve = survival_curve(qx_map[sex], age, horizon)
                    fig_surv.add_trace(go.Scatter(x=years_axis, y=own_curve, name=f"{sex.title()}, age {age}",
                                                   line=dict(width=2)))
                    if joint_life:
                        partner_curve = survival_curve(qx_map[partner_sex], partner_age, horizon)
                        fig_surv.add_trace(go.Scatter(x=years_axis, y=partner_curve,
                                                       name=f"{partner_sex.title()}, age {partner_age}",
                                                       line=dict(width=2, dash="dot")))
                        if sex == "male":
                            joint_curve = joint_survival_curve(qx_map["male"], qx_map["female"], age, partner_age, horizon)
                        else:
                            joint_curve = joint_survival_curve(qx_map["male"], qx_map["female"], partner_age, age, horizon)
                        fig_surv.add_trace(go.Scatter(x=years_axis, y=joint_curve, name="Joint (at least one alive)",
                                                       line=dict(width=3, color="#1f77b4")))
                    fig_surv.update_layout(xaxis_title="Year", yaxis_title="Probability alive", yaxis_range=[0, 1],
                                            height=380, margin=dict(l=10, r=10, t=20, b=10),
                                            legend=dict(orientation="h", y=-0.2))
                    st.plotly_chart(fig_surv, use_container_width=True)

                    le_own = life_expectancy(qx_map[sex], age)
                    le_text = f"Average life expectancy — {sex}, age {age}: **{le_own:.1f} more years**"
                    if joint_life:
                        le_partner = life_expectancy(qx_map[partner_sex], partner_age)
                        le_text += f"  \nPartner ({partner_sex}, age {partner_age}): **{le_partner:.1f} more years**"
                    st.markdown(le_text)
                    st.caption("An average, not a guarantee - about half of people this age will live longer, half less.")

                with col_stats:
                    st.markdown("**Factoring in life expectancy vs. the raw figures above**")
                    mort_rows = []
                    for name in ordered_names(mortality_results):
                        mr = mortality_results[name]
                        s = mr.summary()
                        s["Portfolio"] = display_name(name)
                        del s["Life basis"]
                        del s["N sims"]
                        mort_rows.append(s)
                    mort_df = pd.DataFrame(mort_rows).set_index("Portfolio")
                    st.dataframe(mort_df.style.format(mort_fmt), use_container_width=True)
                    st.caption(
                        "'Ruin before death' = the pot hits zero while the client (or, for joint life, at least "
                        "one partner) is still alive — the outcome that actually matters, vs. the raw "
                        "'horizon-end' ruin probability which penalises paths that only run out of money after "
                        "everyone involved has already died. 'Legacy at death' values the estate at the client's "
                        "own simulated death year rather than at a fixed year-30 cutoff."
                    )

        with tab_hist:
            st.subheader("What would have actually happened historically?")
            st.caption(
                "A reality check alongside the simulations above: instead of thousands of possible futures, this "
                "replays the ONE sequence of returns that actually occurred, starting from the earliest available "
                "date, so you can see one concrete real-world example rather than only statistical ranges."
            )
            hist_name = st.selectbox("Portfolio for historical check", chosen if chosen else list(PORTFOLIOS.keys()),
                                      format_func=display_name)
            hist_df = historical_single_path(hist_name, asset_df, cpi, ClientProfile(**profile_kwargs))
            fig3 = go.Figure()
            fig3.add_trace(go.Scatter(x=hist_df["Date"], y=hist_df["PortfolioValue"], mode="lines+markers", name="Portfolio value"))
            fig3.update_layout(height=350, yaxis_title="£", margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig3, use_container_width=True)

        with tab_sweep:
            st.subheader("How much should be in shares vs. safer assets?")
            st.markdown(
                "**In plain terms:** more in shares (equities) usually means higher potential growth but bigger "
                "swings; more in bonds/cash usually means a smoother ride but less growth potential. This section "
                "re-tests each portfolio at different overall share exposures, from cautious (20%) to all-in "
                "(100%), to show that trade-off directly."
            )
            st.caption(
                "Technical detail: rescales each chosen portfolio to hit a target TOTAL equity weight (all "
                "of that portfolio's equity/growth holdings combined - e.g. Global + EM equities for Aspen's "
                "funds, or Mobius Better's own quality/managed-vol/EM equity sleeves), preserving the relative "
                "split within the equity sleeve and within the rest of the portfolio, then re-runs the full "
                "simulation at each point."
            )
            run_sweep = st.checkbox(
                "Test different share-vs-safer-assets mixes (re-runs each portfolio 9 times - slower)",
                value=False,
            )
            if run_sweep and not chosen:
                st.info("Pick at least one Decumulation portfolio (or switch 'What to show' to "
                        "Decumulation or Both) to run this.")
            elif run_sweep and chosen:
                sweep_n_sims = st.select_slider(
                    "Simulations per sweep point", [300, 500, 1000, 2000], value=500, key="sweep_n_sims",
                    help="Kept lower than the main simulation count by default since 9 points x N portfolios "
                         "multiplies the total simulation work.",
                )
                equity_grid = np.arange(0.20, 1.01, 0.10)
                sweep_results = {}
                with st.spinner("Running equity sweep across all chosen portfolios..."):
                    for name in chosen:
                        profile = ClientProfile(**profile_kwargs)
                        sweep_results[name] = equity_sweep(
                            name, asset_df, cpi, profile, equity_weights=equity_grid, method=method,
                            n_sims=sweep_n_sims, seed=seed,
                        )

                sweep_col1, sweep_col2 = st.columns(2)
                with sweep_col1:
                    fig4 = go.Figure()
                    for name, df_sweep in sweep_results.items():
                        fig4.add_trace(go.Scatter(
                            x=df_sweep.index * 100, y=df_sweep["Probability of ruin"] * 100,
                            mode="lines+markers", name=display_name(name), line=dict(color=portfolio_color(name)),
                        ))
                    fig4.update_layout(
                        title="Probability of ruin vs total equity weight",
                        xaxis_title="Total equity weight (%)", yaxis_title="Probability of ruin (%)",
                        height=400, margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig4, use_container_width=True)
                with sweep_col2:
                    fig5 = go.Figure()
                    for name, df_sweep in sweep_results.items():
                        fig5.add_trace(go.Scatter(
                            x=df_sweep.index * 100, y=df_sweep["Median legacy"],
                            mode="lines+markers", name=display_name(name), line=dict(color=portfolio_color(name)),
                        ))
                    fig5.update_layout(
                        title="Median legacy vs total equity weight",
                        xaxis_title="Total equity weight (%)", yaxis_title="Median legacy (£)",
                        height=400, margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig5, use_container_width=True)

                st.caption(
                    "Each point is an independent Monte Carlo run at that equity weight, so the lines carry their "
                    "own sampling noise (more so than the headline statistics above, since fewer sims are used per "
                    "point here) - read them as a trend across the grid rather than precise values at any one weight."
                )
                with st.expander("Equity sweep — full data"):
                    for name, df_sweep in sweep_results.items():
                        st.markdown(f"**{display_name(name)}**")
                        display_df = df_sweep.copy()
                        display_df["Ruin prob 95% CI"] = display_df["Ruin prob 95% CI"].apply(
                            lambda ci: f"{ci[0]:.1%} - {ci[1]:.1%}"
                        )
                        st.dataframe(display_df.style.format(fmt), use_container_width=True)

        with tab_sens:
            st.subheader("Which decisions actually move the needle?")
            st.markdown(
                "**In plain terms:** this tests, one at a time, the two things a client and adviser can actually "
                "control day-to-day - how much is spent, and (if guardrails are used) how sensitive the guardrails "
                "are - to see how much each one changes the probability of ruin. The share-vs-safer-assets test "
                "above covers the third lever; together they show which decisions matter most."
            )
            run_sensitivity = st.checkbox(
                "Test how sensitive the plan is to spending level and guardrail settings (slower)",
                value=False,
            )
            if run_sensitivity and not chosen:
                st.info("Pick at least one Decumulation portfolio (or switch 'What to show' to "
                        "Decumulation or Both) to run this.")
            elif run_sensitivity and chosen:
                sens_n_sims = st.select_slider(
                    "Simulations per sensitivity point", [300, 500, 1000, 2000], value=500, key="sens_n_sims",
                )
                wr_tab, band_tab, heatmap_tab = st.tabs(["Spending level", "Guardrail sensitivity", "Spend x shares combined"])

                with wr_tab:
                    st.caption(
                        "Re-runs the plan at a range of yearly spending levels (as a % of the starting pot), in "
                        "place of the sidebar's 'Desired annual spend' figure - guardrails apply as configured in "
                        "the sidebar."
                    )
                    wr_grid = np.arange(0.02, 0.071, 0.005)
                    wr_results = {}
                    with st.spinner("Running withdrawal-rate sensitivity..."):
                        for name in chosen:
                            profile = ClientProfile(**profile_kwargs)
                            wr_results[name] = sensitivity_withdrawal_rate(
                                name, asset_df, cpi, profile, wr_grid=wr_grid, method=method,
                                n_sims=sens_n_sims, seed=seed,
                            )
                    fig6 = go.Figure()
                    for name, df_wr in wr_results.items():
                        fig6.add_trace(go.Scatter(x=df_wr.index * 100, y=df_wr["Probability of ruin"] * 100,
                                                   mode="lines+markers", name=display_name(name),
                                                   line=dict(color=portfolio_color(name))))
                    fig6.update_layout(title="Probability of ruin vs withdrawal rate",
                                        xaxis_title="Initial withdrawal rate (%)", yaxis_title="Probability of ruin (%)",
                                        height=400, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig6, use_container_width=True)
                    with st.expander("Withdrawal-rate sensitivity — full data"):
                        for name, df_wr in wr_results.items():
                            st.markdown(f"**{display_name(name)}**")
                            d = df_wr.copy()
                            d["Ruin prob 95% CI"] = d["Ruin prob 95% CI"].apply(lambda ci: f"{ci[0]:.1%} - {ci[1]:.1%}")
                            st.dataframe(d.style.format(fmt), use_container_width=True)

                with band_tab:
                    st.caption(
                        "Guardrails are forced ON here (regardless of the sidebar toggle) to isolate their own "
                        "effect - a narrow band adjusts spending often (fewer plans run out of money, but more "
                        "years with a spending cut); a wide band rarely adjusts (closer to spending exactly the "
                        "same £ amount every year, adjusted only for inflation)."
                    )
                    band_grid = np.arange(0.05, 0.41, 0.05)
                    band_results = {}
                    with st.spinner("Running guardrail-band sensitivity..."):
                        for name in chosen:
                            profile = ClientProfile(**profile_kwargs)
                            band_results[name] = sensitivity_guardrail_band(
                                name, asset_df, cpi, profile, band_grid=band_grid, method=method,
                                n_sims=sens_n_sims, seed=seed,
                            )
                    band_col1, band_col2 = st.columns(2)
                    with band_col1:
                        fig7 = go.Figure()
                        for name, df_band in band_results.items():
                            fig7.add_trace(go.Scatter(x=df_band.index * 100, y=df_band["Probability of ruin"] * 100,
                                                       mode="lines+markers", name=display_name(name),
                                                       line=dict(color=portfolio_color(name))))
                        fig7.update_layout(title="Probability of ruin vs guardrail band",
                                            xaxis_title="Guardrail band (± %)", yaxis_title="Probability of ruin (%)",
                                            height=380, margin=dict(l=10, r=10, t=40, b=10))
                        st.plotly_chart(fig7, use_container_width=True)
                    with band_col2:
                        fig8 = go.Figure()
                        for name, df_band in band_results.items():
                            fig8.add_trace(go.Scatter(x=df_band.index * 100, y=df_band["Avg shortfall years"],
                                                       mode="lines+markers", name=display_name(name),
                                                       line=dict(color=portfolio_color(name))))
                        fig8.update_layout(title="Avg shortfall years vs guardrail band",
                                            xaxis_title="Guardrail band (± %)", yaxis_title="Avg years with a spend cut",
                                            height=380, margin=dict(l=10, r=10, t=40, b=10))
                        st.plotly_chart(fig8, use_container_width=True)
                    with st.expander("Guardrail-band sensitivity — full data"):
                        for name, df_band in band_results.items():
                            st.markdown(f"**{display_name(name)}**")
                            d = df_band.copy()
                            d["Ruin prob 95% CI"] = d["Ruin prob 95% CI"].apply(lambda ci: f"{ci[0]:.1%} - {ci[1]:.1%}")
                            st.dataframe(d.style.format(fmt), use_container_width=True)

                with heatmap_tab:
                    st.caption(
                        "Combines spending level and share-vs-safer-assets mix into one grid, for a single "
                        "portfolio, so you can see how they interact - a spending level that's fine at one share "
                        "exposure can be unsustainable at another, and vice versa."
                    )
                    heatmap_portfolio = st.selectbox("Portfolio", chosen, key="heatmap_portfolio", format_func=display_name)
                    heatmap_metric = st.radio(
                        "Metric", ["Probability of ruin", "% paths with any shortfall"], horizontal=True,
                        key="heatmap_metric",
                    )
                    wr_grid_hm = np.arange(0.02, 0.071, 0.01)
                    eq_grid_hm = np.arange(0.20, 1.01, 0.20)
                    with st.spinner("Running shortfall heatmap..."):
                        hm_df = shortfall_heatmap(
                            heatmap_portfolio, asset_df, cpi, ClientProfile(**profile_kwargs),
                            wr_grid=wr_grid_hm, equity_weights=eq_grid_hm,
                            metric="prob_ruin" if heatmap_metric == "Probability of ruin" else "shortfall_pct",
                            method=method, n_sims=sens_n_sims, seed=seed,
                        )
                    fig10 = go.Figure(data=go.Heatmap(
                        z=hm_df.values * 100,
                        x=[f"{c:.0%}" for c in hm_df.columns], y=[f"{r:.1%}" for r in hm_df.index],
                        colorscale="Reds", text=(hm_df.values * 100).round(1), texttemplate="%{text}%",
                        colorbar=dict(title=f"{heatmap_metric} (%)"),
                    ))
                    fig10.update_layout(
                        title=f"{heatmap_metric} — {display_name(heatmap_portfolio)}",
                        xaxis_title="Total equity weight", yaxis_title="Withdrawal rate",
                        height=450, margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig10, use_container_width=True)
                    with st.expander("Shortfall heatmap — full data"):
                        st.dataframe(hm_df.style.format("{:.1%}"), use_container_width=True)

        with tab_glide:
            st.subheader("Should the share exposure reduce as the client ages?")
            st.markdown(
                "**In plain terms:** many advisers gradually shift a portfolio from more shares to more safer "
                "assets as a client gets older ('de-risking'), rather than keeping the mix fixed for 30 years. "
                "This compares a fixed mix held throughout against one that glides smoothly from a starting share "
                "exposure down to a lower ending one."
            )
            run_glide = st.checkbox("Compare a fixed mix vs. gradually de-risking with age (slower)", value=False)
            if run_glide and not chosen:
                st.info("Pick at least one Decumulation portfolio (or switch 'What to show' to "
                        "Decumulation or Both) to run this.")
            elif run_glide and chosen:
                glide_portfolio = st.selectbox("Portfolio", chosen, key="glide_portfolio", format_func=display_name)
                glide_col1, glide_col2, glide_col3 = st.columns(3)
                with glide_col1:
                    glide_start = st.slider("Starting share exposure", 0.20, 1.00, 0.70, step=0.05, key="glide_start")
                with glide_col2:
                    glide_end = st.slider("Ending share exposure", 0.20, 1.00, 0.40, step=0.05, key="glide_end")
                with glide_col3:
                    glide_n_sims = st.select_slider("Simulations", [300, 500, 1000, 2000], value=1000, key="glide_n_sims")
                glide_method = method if method in ("iid", "fixed_block", "stationary_block") else "stationary_block"
                if glide_method != method:
                    st.caption("The 'extreme/crash years' simulation approach isn't supported for this comparison - "
                               "using 'Realistic historical patterns' instead just for this section.")

                with st.spinner("Running glide path comparison..."):
                    profile = ClientProfile(**profile_kwargs)
                    # fixed-weight comparison at the STARTING equity weight, held constant for the whole horizon
                    from portfolios import scale_to_equity_weight, weighted_avg_fee as _wfee
                    fixed_weights = scale_to_equity_weight(glide_portfolio, glide_start)
                    fixed_fee = _wfee(glide_portfolio)
                    fixed_res = run_simulation(
                        glide_portfolio, asset_df, cpi, profile, method=glide_method, n_sims=glide_n_sims,
                        seed=seed, custom_weights=fixed_weights, custom_fee=fixed_fee,
                    )
                    glide_res = run_glide_path_simulation(
                        glide_portfolio, asset_df, cpi, profile, start_equity_weight=glide_start,
                        end_equity_weight=glide_end, method=glide_method, n_sims=glide_n_sims, seed=seed,
                    )

                glide_summary = pd.DataFrame([
                    {**fixed_res.summary(), "Strategy": f"Fixed at {glide_start:.0%} equity"},
                    {**glide_res.summary(), "Strategy": f"Glide {glide_start:.0%} → {glide_end:.0%} equity"},
                ]).set_index("Strategy")
                ci_col = glide_summary.pop("Ruin prob 95% CI")
                glide_summary["Ruin prob 95% CI"] = ci_col.apply(lambda ci: f"{ci[0]:.1%} - {ci[1]:.1%}")
                st.dataframe(glide_summary.style.format(fmt), use_container_width=True)

                fig9 = go.Figure()
                for label, res in [(f"Fixed {glide_start:.0%}", fixed_res), (f"Glide {glide_start:.0%}→{glide_end:.0%}", glide_res)]:
                    median_path = np.median(res.paths, axis=0)
                    fig9.add_trace(go.Scatter(x=np.arange(len(median_path)), y=median_path, mode="lines", name=label))
                fig9.update_layout(title="Median portfolio value over time: fixed vs glide path",
                                    xaxis_title="Year", yaxis_title="Portfolio value (£)", height=400,
                                    margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig9, use_container_width=True)

        with tab_ann:
            st.subheader("Should part of the pot be swapped for a guaranteed income?")
            st.markdown(
                "**In plain terms:** an annuity means handing over part of the pot, once, in exchange for an "
                "income that's paid for as long as the client lives, no matter how long that is or what happens "
                "to the stock market. This section compares 'give up X% of the pot for guaranteed income' against "
                "'leave the whole pot invested and draw from it'."
            )
            st.caption(
                "Uses real, dated UK best-buy annuity rates (see src/annuity.py for sources) and the same "
                "survival-odds table as the mortality section above, so the comparison shows 'before death' "
                "outcomes rather than just the raw horizon-end ruin probability."
            )
            run_annuity = st.checkbox("Compare annuitizing part of the pot vs. staying fully invested (slower)", value=False)
            if run_annuity and not chosen:
                st.info("Pick at least one Decumulation portfolio (or switch 'What to show' to "
                        "Decumulation or Both) to run this.")
            elif run_annuity and chosen:
                from annuity import annuitize, annuity_rate, MIN_QUOTED_AGE, MAX_QUOTED_AGE

                ann_col1, ann_col2, ann_col3, ann_col4 = st.columns(4)
                with ann_col1:
                    annuity_portfolio = st.selectbox("Portfolio", chosen, key="annuity_portfolio", format_func=display_name)
                with ann_col2:
                    annuity_pct = st.slider("% of the pot to swap for guaranteed income", 0, 100, 30, step=5,
                                             key="annuity_pct_slider") / 100.0
                with ann_col3:
                    annuity_joint_default = bool(use_mortality and joint_life)
                    annuity_joint = st.checkbox(
                        "Keep paying a partner after the client dies (at a lower rate)", value=annuity_joint_default,
                        key="annuity_joint",
                        help="Technical name: joint life, 50% to survivor. Costs more per £ of guaranteed income "
                             "than a single-life annuity, since it's expected to pay out for longer.",
                    )
                with ann_col4:
                    ann_n_sims = st.select_slider("Simulations", [500, 1000, 2000, 3000], value=2000, key="ann_n_sims")

                if age < MIN_QUOTED_AGE or age > MAX_QUOTED_AGE:
                    st.caption(
                        f"Note: the quoted annuity-rate sources only cover ages {MIN_QUOTED_AGE}-{MAX_QUOTED_AGE} "
                        f"- using the age-{MIN_QUOTED_AGE if age < MIN_QUOTED_AGE else MAX_QUOTED_AGE} rate as a "
                        f"conservative proxy for age {age} rather than an unsourced extrapolation."
                    )

                base_profile = ClientProfile(**profile_kwargs)
                annuitized_profile, ann_rate, annuity_income = annuitize(
                    base_profile, annuity_pct, age, joint=annuity_joint
                )
                st.info(
                    f"Annuitizing **{annuity_pct:.0%}** of the £{pot:,.0f} pot at age {age} "
                    f"({'joint life' if annuity_joint else 'single life'}) buys a guaranteed "
                    f"**£{annuity_income:,.0f}/year for life**, at today's rate of {ann_rate:.2%}. This income is "
                    "LEVEL - it does NOT rise with inflation, unlike everything else in this plan, so its real "
                    "purchasing power falls over time (roughly halving after ~20 years at ~3% inflation). It "
                    f"leaves **£{annuitized_profile.starting_pot:,.0f}** in drawdown alongside the guaranteed income."
                )

                with st.spinner("Running annuitization comparison..."):
                    res_drawdown = run_simulation(annuity_portfolio, asset_df, cpi, base_profile, method=method,
                                                   n_sims=ann_n_sims, block_mean=block_mean, seed=seed)
                    res_annuitized = run_simulation(annuity_portfolio, asset_df, cpi, annuitized_profile, method=method,
                                                     n_sims=ann_n_sims, block_mean=block_mean, seed=seed)

                ann_summary = pd.DataFrame([
                    {**res_drawdown.summary(), "Strategy": "100% drawdown"},
                    {**res_annuitized.summary(), "Strategy": f"{annuity_pct:.0%} annuitized"},
                ]).set_index("Strategy")
                ann_ci = ann_summary.pop("Ruin prob 95% CI")
                ann_summary["Ruin prob 95% CI"] = ann_ci.apply(lambda ci: f"{ci[0]:.1%} - {ci[1]:.1%}")
                st.dataframe(ann_summary.style.format(fmt), use_container_width=True)

                st.markdown("**Mortality-adjusted comparison**")
                ann_sex = sex if use_mortality else st.selectbox(
                    "Sex (for mortality-adjusted outcomes)", ["male", "female"], format_func=str.title,
                    key="annuity_sex",
                )
                ann_partner_age = partner_age if (use_mortality and joint_life) else (age - 2)
                ann_partner_sex = partner_sex if (use_mortality and joint_life) else ("female" if ann_sex == "male" else "male")
                mortality_table_ann = load_mortality_table()
                mr_drawdown = run_mortality_overlay(
                    res_drawdown, mortality_table_ann, sex=ann_sex,
                    partner_age=ann_partner_age if annuity_joint else None,
                    partner_sex=ann_partner_sex if annuity_joint else None,
                )
                mr_annuitized = run_mortality_overlay(
                    res_annuitized, mortality_table_ann, sex=ann_sex,
                    partner_age=ann_partner_age if annuity_joint else None,
                    partner_sex=ann_partner_sex if annuity_joint else None,
                )
                mort_ann_rows = []
                for label, mr in [("100% drawdown", mr_drawdown), (f"{annuity_pct:.0%} annuitized", mr_annuitized)]:
                    s = mr.summary()
                    s["Strategy"] = label
                    del s["Life basis"], s["N sims"]
                    mort_ann_rows.append(s)
                mort_ann_df = pd.DataFrame(mort_ann_rows).set_index("Strategy")
                st.dataframe(mort_ann_df.style.format(mort_fmt), use_container_width=True)
                st.caption(
                    f"Life basis: **{mr_drawdown.life_basis}**. 'Ruin before death' is the outcome that actually "
                    "matters for the client - the pot running out while they (or their partner) are still alive - "
                    "as opposed to the raw horizon-end figure above, which also counts paths that only run dry "
                    "after everyone involved has already died."
                )

                fig_ann = go.Figure()
                for label, res in [("100% drawdown", res_drawdown), (f"{annuity_pct:.0%} annuitized", res_annuitized)]:
                    median_path = np.median(res.paths, axis=0)
                    fig_ann.add_trace(go.Scatter(x=np.arange(len(median_path)), y=median_path, mode="lines", name=label))
                fig_ann.update_layout(
                    title="Median pot value over time: drawdown vs annuitized (annuity income paid separately, not shown)",
                    xaxis_title="Year", yaxis_title="Pot value (£)", height=400,
                    margin=dict(l=10, r=10, t=40, b=10),
                )
                st.plotly_chart(fig_ann, use_container_width=True)
                st.caption(
                    "Annuitizing shrinks the pot immediately (money moves out to buy the annuity) but replaces "
                    "part of the withdrawal need with guaranteed income for life, which is why probability of "
                    "ruin normally falls even though the pot chart above looks smaller throughout. Rates used: "
                    "single-life, LEVEL income (doesn't rise with inflation), no minimum payment period, per "
                    "published Hargreaves Lansdown best-buy data (14-28 May 2026, see src/annuity.py) - a "
                    "simplification. It doesn't model taking a 25% tax-free lump sum first (PCLS), a minimum "
                    "guaranteed payment period, and the joint-life (paying a partner too) discount uses one "
                    "age-65 data point applied at every age. Real quotes vary by provider, postcode and health, "
                    "and should always be checked with an actual quote before a client acts on this."
                )

        with tab_hold:
            with st.expander("Portfolio holdings & assumptions"):
                for name in ordered_names(chosen):
                    st.markdown(f"**{display_name(name)}** — weighted-average OCF: {weighted_avg_fee(name)*100:.3f}% pa")
                    st.dataframe(portfolio_summary(name), use_container_width=True,
                                 column_config=_holdings_column_config())
                st.markdown(
                    "Fund-level returns are mapped to broad asset-class index returns (Bloomberg data, "
                    "1999/2000–2026) for the simulation, since several individual fund return histories are "
                    "too short for a reliable long-run bootstrap. See `src/portfolios.py` for the full mapping "
                    "and fee assumptions."
                )
