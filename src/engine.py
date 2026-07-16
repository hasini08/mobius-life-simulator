"""
Core decumulation simulation engine.

Methodology notes (what's "improved" vs the previous Mobius model):
  1. Stochastic sampling: adds a STATIONARY BLOCK BOOTSTRAP (Politis & Romano, 1994) as the default
     sampling method - random block lengths (geometric distribution, mean = block_mean months)
     preserve serial correlation / momentum in returns, which a naive month-by-month iid bootstrap
     (the previous model's approach) destroys. A fixed-length block bootstrap and a skewed
     Student-t parametric sampler are also implemented so the three can be compared.
  2. Inflation-linked spending: desired spend escalates with actual sampled UK CPI (not a fixed
     assumption), so inflation risk feeds through into the ruin probability - not modelled before.
  3. Spending guardrails: a Guyton-Klinger-style two-band rule that cuts spend in weak markets and
     raises it in strong ones, so the "impact of guardrails" (flagged in the spec as a desired
     feature) can be switched on/off and compared.
  4. Objective statistics extended per the spec/pptx: probability of ruin, quantiles of spending
     shortfall (unconditional and conditional on ruin), legacy quantiles, CVaR, max drawdown, IRR.

No mortality / tax / state pension modelling - deliberately out of scope per the project spec
("I would not spend much time on state pension and tax features at this stage").
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, replace
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

from portfolios import PORTFOLIOS, asset_class_weights, weighted_avg_fee, AC, scale_to_equity_weight
from mortality import (
    load_mortality_table, survival_curve, joint_survival_curve, sample_death_years, joint_death_years,
    life_expectancy,
)
import tax


def _gross_withdrawal_target(real_spend, cum_inflation, age_this_year, profile: "ClientProfile"):
    """Converts a real (today's-money) desired NET spend into the actual NOMINAL amount that must be
    withdrawn from the pot this year, accounting for UK income tax, State Pension (see tax.py) and
    any annuity income already in payment (see annuity.py). A no-op passthrough (identical to the
    old pre-tax formula, net of any annuity income) when profile.apply_tax is False, so every
    existing feature's behaviour is preserved exactly when both toggles are off. Tax bands and the
    State Pension are both held in real terms (see tax.py docstring) and grossed up in real terms
    before being reflated by cum_inflation; annuity income is a NOMINAL, non-inflating figure (a
    'level' annuity - see annuity.py), so it's converted to this year's real-terms equivalent
    (dividing by cum_inflation) before being combined with State Pension in the same gross-up call."""
    nominal_spend_target = real_spend * cum_inflation
    annuity_nominal = profile.annuity_income_nominal
    if not profile.apply_tax:
        # np.maximum (not the builtin max()) since real_spend/nominal_spend_target may be a numpy
        # array here (per-simulated-path, when guardrails are on) rather than a scalar.
        return np.maximum(nominal_spend_target - annuity_nominal, 0.0)
    sp = tax.state_pension_income(age_this_year, sp_age=profile.state_pension_age,
                                   sp_annual=profile.state_pension_annual)
    other_taxable_real = sp + annuity_nominal / cum_inflation
    real_gross_withdrawal = tax.gross_up_pot_withdrawal(real_spend, other_taxable_income=other_taxable_real)
    return real_gross_withdrawal * cum_inflation


def load_asset_returns() -> pd.DataFrame:
    df = pd.read_csv(DATA / "asset_class_returns.csv", index_col=0, parse_dates=True)
    df = df.dropna(how="all")
    # forward-shouldn't need to ffill: all classes fully populated except a couple of stray NaT rows
    df = df[df.index.notna()]
    return df


def load_cpi(asset_df: pd.DataFrame) -> pd.Series:
    cpi_col = [c for c in asset_df.columns if "CPI" in c][0]
    cpi = asset_df[cpi_col].dropna()
    return cpi  # annual YoY rate, monthly observations


def weighted_monthly_returns(weights: pd.Series, fee: float, asset_df: pd.DataFrame, label="custom") -> pd.Series:
    """Weighted asset-class monthly return for an arbitrary asset-class weight vector, net of a
    given weighted-average annual fee. Used both for the three named portfolios and for ad-hoc
    blends (e.g. the equity-allocation sweep)."""
    cols = [AC[k] for k in weights.index]
    w = weights.values
    gross = (asset_df[cols].values * w).sum(axis=1)
    net = gross - fee / 12.0
    return pd.Series(net, index=asset_df.index, name=label)


def portfolio_monthly_returns(name: str, asset_df: pd.DataFrame) -> pd.Series:
    """Weighted asset-class monthly return for a named portfolio, net of the portfolio's
    weighted-average annual fee (deducted monthly on a simple pro-rata basis)."""
    weights = asset_class_weights(name)
    fee = weighted_avg_fee(name)
    return weighted_monthly_returns(weights, fee, asset_df, label=name)


@dataclass
class ClientProfile:
    starting_age: int = 65
    horizon_years: int = 30
    starting_pot: float = 500_000.0
    initial_annual_spend: float = 20_000.0     # DESIRED NET (take-home) spend if apply_tax=True
    guardrails: bool = False
    guardrail_band: float = 0.20       # +/- 20% of the initial withdrawal rate
    guardrail_cut: float = 0.10        # cut spend 10% real if above upper band
    guardrail_raise: float = 0.10      # raise spend 10% real if below lower band
    apply_tax: bool = False            # gross up spend for UK income tax + State Pension (see tax.py)
    state_pension_annual: float = tax.FULL_NEW_STATE_PENSION_ANNUAL  # today's-money, from state_pension_age
    state_pension_age: int = tax.DEFAULT_STATE_PENSION_AGE
    # Guaranteed LEVEL (non-inflating) nominal annual income from an annuity purchased at outset -
    # see annuity.py's annuitize() helper, which sets this (and reduces starting_pot) together.
    # Unlike state_pension_annual, this is a NOMINAL £ figure that does NOT scale with cum_inflation
    # (that's what "level annuity" means - see annuity.py docstring), and it counts as taxable
    # income (like State Pension) whenever apply_tax is on, or simply offsets the pot withdrawal
    # need directly when apply_tax is off.
    annuity_income_nominal: float = 0.0


@dataclass
class SimResult:
    portfolio_name: str
    method: str
    n_sims: int
    paths: np.ndarray            # (n_sims, horizon_years+1) year-end portfolio values (real terms not applied; nominal)
    spend_paths: np.ndarray      # (n_sims, horizon_years) nominal annual spend actually taken
    ruin_year: np.ndarray        # (n_sims,) year index of ruin, or -1 if never ruined
    profile: ClientProfile

    @property
    def prob_ruin(self):
        return float((self.ruin_year >= 0).mean())

    @property
    def prob_ruin_se(self):
        """Standard error of the probability-of-ruin estimate. Each simulated path is an
        independent draw (even though months within a path are autocorrelated via the block
        bootstrap), so ruin/no-ruin across paths is a valid i.i.d. Bernoulli sample and the
        standard binomial SE applies: sqrt(p(1-p)/n)."""
        p = self.prob_ruin
        n = self.n_sims
        return float(np.sqrt(max(p * (1 - p), 0) / n))

    def prob_ruin_ci(self, z=1.96):
        """Approximate (default 95%) confidence interval for probability of ruin, clipped to [0,1]."""
        p, se = self.prob_ruin, self.prob_ruin_se
        return (max(0.0, p - z * se), min(1.0, p + z * se))

    @property
    def legacy(self):
        return self.paths[:, -1]

    def legacy_quantile(self, q):
        return float(np.quantile(self.legacy, q))

    def shortfall_years(self):
        """Average number of years spend had to be cut below desired (guardrail cuts + post-ruin
        shortfall) per simulated path."""
        desired = self.profile.initial_annual_spend
        shortfall = np.maximum(0, desired - self.spend_paths[:, :]) > 1e-6
        return shortfall.sum(axis=1)

    def summary(self):
        sy = self.shortfall_years()
        ci_lo, ci_hi = self.prob_ruin_ci()
        return {
            "Portfolio": self.portfolio_name,
            "Method": self.method,
            "N sims": self.n_sims,
            "Probability of ruin": self.prob_ruin,
            "Ruin prob SE": self.prob_ruin_se,
            "Ruin prob 95% CI": (ci_lo, ci_hi),
            "Median legacy": self.legacy_quantile(0.50),
            "5th pctl legacy": self.legacy_quantile(0.05),
            "95th pctl legacy": self.legacy_quantile(0.95),
            "Avg shortfall years": float(sy.mean()),
            "% paths with any shortfall": float((sy > 0).mean()),
        }


@dataclass
class MortalityResult:
    """Overlays stochastic mortality (a simulated death year per path) on an already-run SimResult,
    without re-running the financial simulation - the market paths are identical, only how they're
    interpreted changes: outcomes now depend on how the client's finances stood AT THEIR OWN DEATH,
    not just at the fixed horizon end."""
    sim_result: SimResult
    death_year: np.ndarray  # per-path simulated death year (0-indexed sim year); -1 = survives horizon
    life_basis: str         # human-readable description, e.g. "Single life (male, age 65)"

    @property
    def n_sims(self):
        return self.sim_result.n_sims

    @property
    def prob_survive_horizon(self):
        """Probability the client (or, for joint life, at least one of the couple) is still alive at
        the end of the modelled horizon."""
        return float((self.death_year < 0).mean())

    @property
    def prob_ruin_before_death(self):
        """Probability the pot hits zero WHILE the client is still alive - the outcome that actually
        matters for a real person, as opposed to the raw 'probability of ruin by horizon end' metric
        (which treats running out of money the year after you die the same as running out at 45 -
        clearly not equivalent in practice)."""
        ruined = self.sim_result.ruin_year >= 0
        survived_full_horizon = self.death_year < 0
        ruin_before_death = ruined & (survived_full_horizon | (self.sim_result.ruin_year <= self.death_year))
        return float(ruin_before_death.mean())

    def legacy_at_death(self):
        """Estate value at the client's OWN simulated death year (or at horizon end for paths where
        they outlive the horizon) - a more realistic legacy figure than 'value at year 30 regardless
        of whether the client is still alive then'."""
        paths = self.sim_result.paths  # shape (n_sims, years+1)
        n_sims, n_cols = paths.shape
        years = n_cols - 1
        idx = np.where(self.death_year < 0, years, self.death_year)
        idx = np.clip(idx, 0, years)
        return paths[np.arange(n_sims), idx]

    def summary(self):
        legacy = self.legacy_at_death()
        return {
            "Life basis": self.life_basis,
            "N sims": self.n_sims,
            "Probability of ruin before death": self.prob_ruin_before_death,
            "Probability of surviving full horizon": self.prob_survive_horizon,
            "Probability of ruin by horizon end (no mortality)": self.sim_result.prob_ruin,
            "Median legacy at death": float(np.median(legacy)),
            "5th pctl legacy at death": float(np.quantile(legacy, 0.05)),
            "95th pctl legacy at death": float(np.quantile(legacy, 0.95)),
        }


def run_mortality_overlay(sim_result: SimResult, mortality_table, sex="male", partner_age=None,
                           partner_sex=None, seed=123):
    """Overlays stochastic mortality on an already-run SimResult (same profile/horizon), sampling a
    death year per simulated path from the S4 pension-scheme mortality table and computing
    survival-adjusted outcomes. sex: 'male' or 'female' for a single life. Pass partner_age /
    partner_sex too for a JOINT life (couple) basis - in that case death_year is the year of the
    SECOND death (money conventionally needs to last until neither partner is alive), which is
    always later than either life's own death year, so joint-life ruin probabilities are typically
    higher than single-life ones at the same starting age (money has to last longer)."""
    rng = np.random.default_rng(seed)
    age = sim_result.profile.starting_age
    years = sim_result.profile.horizon_years
    n_sims = sim_result.n_sims
    qx_col = {"male": "qx_male", "female": "qx_female"}[sex]
    qx = mortality_table[qx_col]

    if partner_age is not None:
        partner_qx_col = {"male": "qx_male", "female": "qx_female"}[partner_sex]
        partner_qx = mortality_table[partner_qx_col]
        if sex == "male":
            death_year = joint_death_years(qx, partner_qx, age, partner_age, years, n_sims, rng)
        else:
            death_year = joint_death_years(partner_qx, qx, partner_age, age, years, n_sims, rng)
        life_basis = f"Joint life ({sex} {age} / {partner_sex} {partner_age})"
    else:
        death_year = sample_death_years(qx, age, years, n_sims, rng)
        life_basis = f"Single life ({sex}, age {age})"

    return MortalityResult(sim_result, death_year, life_basis)


def _draw_iid(returns: np.ndarray, cpi: np.ndarray, n_months: int, n_sims: int, rng):
    idx = rng.integers(0, len(returns), size=(n_sims, n_months))
    return returns[idx], cpi[idx]


def _draw_fixed_block(returns: np.ndarray, cpi: np.ndarray, n_months: int, n_sims: int, rng, block=12):
    n_blocks = int(np.ceil(n_months / block))
    starts = rng.integers(0, len(returns) - block, size=(n_sims, n_blocks))
    r_out = np.empty((n_sims, n_blocks * block))
    c_out = np.empty((n_sims, n_blocks * block))
    for b in range(n_blocks):
        s = starts[:, b]
        for j in range(block):
            r_out[:, b * block + j] = returns[s + j]
            c_out[:, b * block + j] = cpi[s + j]
    return r_out[:, :n_months], c_out[:, :n_months]


def _draw_stationary_block(returns: np.ndarray, cpi: np.ndarray, n_months: int, n_sims: int, rng, block_mean=12):
    """Politis & Romano (1994) stationary bootstrap: block length ~ Geometric(1/block_mean),
    wraps around the sample circularly. Preserves local autocorrelation without a rigid block grid."""
    n = len(returns)
    p = 1.0 / block_mean
    r_out = np.empty((n_sims, n_months))
    c_out = np.empty((n_sims, n_months))
    for s in range(n_sims):
        i = rng.integers(0, n)
        t = 0
        while t < n_months:
            block_len = rng.geometric(p)
            for _ in range(block_len):
                if t >= n_months:
                    break
                r_out[s, t] = returns[i % n]
                c_out[s, t] = cpi[i % n]
                i += 1
                t += 1
            i = rng.integers(0, n)  # jump to a new random start for next block
    return r_out, c_out


def _draw_skew_t(returns: np.ndarray, n_months: int, n_sims: int, rng, cpi_mean, cpi_std):
    """Fit a skew-normal (fast, robust proxy for skewed Student-t) to historical monthly returns
    and sample iid from it. CPI sampled iid Normal(mean, std) from historical monthly YoY series."""
    from scipy import stats
    a, loc, scale = stats.skewnorm.fit(returns)
    r = stats.skewnorm.rvs(a, loc=loc, scale=scale, size=(n_sims, n_months), random_state=rng)
    c = rng.normal(cpi_mean, cpi_std, size=(n_sims, n_months))
    return r, c


def run_simulation(portfolio_name, asset_df, cpi_series, profile: ClientProfile,
                    method="stationary_block", n_sims=2000, block_mean=12, seed=42,
                    custom_weights=None, custom_fee=None):
    """If custom_weights (a pandas Series of asset-class weights) is supplied, it overrides the
    named portfolio's weights (custom_fee likewise overrides the fee) - used for the equity-
    allocation sweep, which needs to simulate blends that aren't one of the three named
    portfolios. portfolio_name is still used as the result's label in that case."""
    rng = np.random.default_rng(seed)
    if custom_weights is not None:
        fee = custom_fee if custom_fee is not None else weighted_avg_fee(portfolio_name)
        monthly_ret = weighted_monthly_returns(custom_weights, fee, asset_df, label=portfolio_name).dropna()
    else:
        monthly_ret = portfolio_monthly_returns(portfolio_name, asset_df).dropna()
    common_idx = monthly_ret.index.intersection(cpi_series.index)
    monthly_ret = monthly_ret.loc[common_idx].values
    # UK CPI YoY is a trailing-12-month rate, not a monthly return - each MONTH's reading already
    # tells you inflation over the preceding year, so we sample it jointly with returns (to preserve
    # the correlation between a bad market year and inflation) and then use just the LAST sampled
    # month's reading within each simulated year as that year's inflation rate (matching the annual
    # 'take the year-end YoY print' convention used in the Excel workbook), rather than compounding
    # 12 overlapping YoY readings together (which would double-count and overstate inflation).
    cpi_vals = cpi_series.loc[common_idx].values
    n_months = profile.horizon_years * 12

    if method == "iid":
        r, c = _draw_iid(monthly_ret, cpi_vals, n_months, n_sims, rng)
    elif method == "fixed_block":
        r, c = _draw_fixed_block(monthly_ret, cpi_vals, n_months, n_sims, rng, block=block_mean)
    elif method == "stationary_block":
        r, c = _draw_stationary_block(monthly_ret, cpi_vals, n_months, n_sims, rng, block_mean=block_mean)
    elif method == "skew_t":
        r, c = _draw_skew_t(monthly_ret, n_months, n_sims, rng,
                             cpi_vals.mean(), cpi_vals.std())
    else:
        raise ValueError(f"unknown method {method}")

    years = profile.horizon_years
    paths = np.empty((n_sims, years + 1))
    spend_paths = np.empty((n_sims, years))
    ruin_year = np.full(n_sims, -1)

    paths[:, 0] = profile.starting_pot
    wr0 = profile.initial_annual_spend / profile.starting_pot

    pot = np.full(n_sims, profile.starting_pot)
    real_spend = np.full(n_sims, profile.initial_annual_spend)  # in "today's money" real terms tracked separately
    cum_inflation = np.ones(n_sims)
    alive = np.full(n_sims, True)

    for y in range(years):
        # compound 12 months of returns within the year; inflation = last sampled month's YoY reading
        # for that year (a YoY rate is already a trailing-12-month figure - see note above)
        m0, m1 = y * 12, (y + 1) * 12
        year_growth = np.prod(1 + r[:, m0:m1], axis=1)
        year_infl = c[:, m1 - 1]
        cum_inflation *= (1 + year_infl)

        nominal_spend_target = real_spend * cum_inflation  # NET target - drives guardrails, unchanged by tax

        # guardrails: check current withdrawal rate vs band, adjust REAL spend level going forward.
        # Real spend is capped to +/-50% of the ORIGINAL desired spend so repeated triggers can't
        # compound without bound (a known failure mode of naive Guyton-Klinger implementations).
        # Guardrails react to the NET living-standard target vs the pot, regardless of tax/SP - a
        # documented simplification (see tax.py / Instructions).
        if profile.guardrails and y > 0:
            current_wr = np.where(pot > 0, nominal_spend_target / np.maximum(pot, 1), np.inf)
            upper = wr0 * (1 + profile.guardrail_band)
            lower = wr0 * (1 - profile.guardrail_band)
            cut = current_wr > upper
            raise_ = current_wr < lower
            real_spend = np.where(cut, real_spend * (1 - profile.guardrail_cut), real_spend)
            real_spend = np.where(raise_, real_spend * (1 + profile.guardrail_raise), real_spend)
            real_spend = np.clip(real_spend, 0.5 * profile.initial_annual_spend, 1.5 * profile.initial_annual_spend)
            nominal_spend_target = real_spend * cum_inflation

        age_this_year = profile.starting_age + y
        # nominal_gross_target = what must actually leave the pot: identical to nominal_spend_target
        # when apply_tax is off (exact backward compatibility); grossed up for tax net of State
        # Pension when on.
        nominal_gross_target = _gross_withdrawal_target(real_spend, cum_inflation, age_this_year, profile)
        # sp_nominal is ALWAYS computed (not gated by apply_tax) - mirrors Excel's stage-6/stage-18
        # formulas, which always add State Pension into the reported net-income figure regardless of
        # the tax toggle (only the WITHDRAWAL TARGET, via _gross_withdrawal_target above, excludes SP
        # when tax is off - SP simply isn't netted against the pot draw in that mode, but it's still
        # real income the client receives). Previously this was gated to 0.0 when apply_tax was False,
        # which silently dropped State Pension from the reported net entirely in that mode - a
        # cross-engine mismatch caught by verify_cma_annuity.py (Excel consistently ~£13k-£25k higher
        # than Python across the historical projection once State Pension age was reached).
        sp_nominal = tax.state_pension_income(age_this_year, sp_age=profile.state_pension_age,
                                               sp_annual=profile.state_pension_annual) * cum_inflation

        actual_spend = np.minimum(nominal_gross_target, np.maximum(pot, 0))
        actual_spend = np.where(alive, actual_spend, 0)
        pot = np.maximum(pot - actual_spend, 0) * year_growth
        pot = np.maximum(pot, 0)

        newly_ruined = alive & (pot <= 0) & (actual_spend < nominal_gross_target - 1e-6)
        ruin_year = np.where(newly_ruined & (ruin_year < 0), y, ruin_year)
        alive = alive & (pot > 0)

        paths[:, y + 1] = pot
        # spend_paths tracks NET (take-home) income actually received - State Pension and any
        # annuity income keep paying even on paths where the discretionary pot has already been
        # exhausted, so this is NOT masked to 0 post-ruin the way the pot withdrawal itself is.
        guaranteed_nominal = sp_nominal + profile.annuity_income_nominal
        spend_paths[:, y] = (tax.net_income(guaranteed_nominal + actual_spend) if profile.apply_tax
                              else guaranteed_nominal + actual_spend)

    return SimResult(portfolio_name, method, n_sims, paths, spend_paths, ruin_year, profile)


def equity_sweep(portfolio_name, asset_df, cpi_series, profile: ClientProfile,
                  equity_weights=None, method="stationary_block", n_sims=2000, seed=42):
    """Scans total equity weight (Global + EM equities combined) from low to high, rescaling the
    named portfolio's non-equity 'shape' proportionally at each point (see
    portfolios.scale_to_equity_weight), and reports probability of ruin / legacy stats at each
    level - replicating the previous model's key finding-generation method (its pptx scanned
    20-100% equity per portfolio variant to show where ruin probability bottoms out)."""
    if equity_weights is None:
        equity_weights = np.arange(0.20, 1.01, 0.10)
    rows = []
    for eq_w in equity_weights:
        weights = scale_to_equity_weight(portfolio_name, eq_w)
        fee = weighted_avg_fee(portfolio_name)  # fee held constant - sweep isolates allocation effect
        res = run_simulation(portfolio_name, asset_df, cpi_series, profile, method=method,
                              n_sims=n_sims, seed=seed, custom_weights=weights, custom_fee=fee)
        s = res.summary()
        s["Equity weight"] = round(float(eq_w), 4)
        rows.append(s)
    return pd.DataFrame(rows).set_index("Equity weight")


def sensitivity_withdrawal_rate(portfolio_name, asset_df, cpi_series, base_profile: ClientProfile,
                                 wr_grid=None, method="stationary_block", n_sims=2000, seed=42):
    """Scans the initial withdrawal rate (spend / starting pot), holding pot size, guardrail
    settings, horizon etc fixed at base_profile's values, and reports probability of ruin / legacy
    stats at each rate - the single biggest lever a client actually controls."""
    if wr_grid is None:
        wr_grid = np.arange(0.02, 0.071, 0.005)
    rows = []
    for wr in wr_grid:
        profile = replace(base_profile, initial_annual_spend=float(wr) * base_profile.starting_pot)
        res = run_simulation(portfolio_name, asset_df, cpi_series, profile, method=method,
                              n_sims=n_sims, seed=seed)
        s = res.summary()
        s["Withdrawal rate"] = round(float(wr), 4)
        rows.append(s)
    return pd.DataFrame(rows).set_index("Withdrawal rate")


def asset_correlation_matrix(asset_df: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation matrix of monthly returns across the 11 broad asset classes (using their
    friendly labels, not the raw Bloomberg column names) - drives the app's diversification heatmap.
    Excludes the CPI column (a level, not a return) if present."""
    cols = {v: k for k, v in AC.items()}  # raw source column -> friendly label
    df = asset_df[[c for c in asset_df.columns if c in cols]].rename(columns=cols)
    return df.corr()


def shortfall_heatmap(portfolio_name, asset_df, cpi_series, base_profile: ClientProfile,
                       wr_grid=None, equity_weights=None, metric="prob_ruin",
                       method="stationary_block", n_sims=1000, seed=42):
    """2-D sweep combining withdrawal rate and total equity weight - the two levers with the
    biggest individual effect on outcomes - into one grid, so their INTERACTION is visible (e.g. a
    high withdrawal rate might be survivable at one equity weight but not another). metric:
    'prob_ruin' (probability of ruin) or 'shortfall_pct' (% of paths with any spending shortfall).
    Returns a DataFrame indexed by withdrawal rate, columned by equity weight."""
    if wr_grid is None:
        wr_grid = np.arange(0.02, 0.071, 0.01)
    if equity_weights is None:
        equity_weights = np.arange(0.20, 1.01, 0.20)
    rows = {}
    for wr in wr_grid:
        row = {}
        for eq_w in equity_weights:
            weights = scale_to_equity_weight(portfolio_name, float(eq_w))
            fee = weighted_avg_fee(portfolio_name)
            profile = replace(base_profile, initial_annual_spend=float(wr) * base_profile.starting_pot)
            res = run_simulation(portfolio_name, asset_df, cpi_series, profile, method=method,
                                  n_sims=n_sims, seed=seed, custom_weights=weights, custom_fee=fee)
            if metric == "prob_ruin":
                row[round(float(eq_w), 4)] = res.prob_ruin
            else:
                sy = res.shortfall_years()
                row[round(float(eq_w), 4)] = float((sy > 0).mean())
        rows[round(float(wr), 4)] = row
    df = pd.DataFrame(rows).T
    df.index.name = "Withdrawal rate"
    df.columns.name = "Equity weight"
    return df


def sensitivity_guardrail_band(portfolio_name, asset_df, cpi_series, base_profile: ClientProfile,
                                band_grid=None, method="stationary_block", n_sims=2000, seed=42):
    """Scans the guardrail band width (+/- % of the initial withdrawal rate that triggers a spend
    cut/raise), with guardrails forced ON regardless of base_profile.guardrails, to isolate the
    band's own effect on ruin probability vs shortfall frequency (a narrow band cuts/raises spend
    often - fewer ruins, more shortfall years; a wide band rarely intervenes - closer to a fixed
    real withdrawal strategy)."""
    if band_grid is None:
        band_grid = np.arange(0.05, 0.41, 0.05)
    rows = []
    for band in band_grid:
        profile = replace(base_profile, guardrails=True, guardrail_band=float(band))
        res = run_simulation(portfolio_name, asset_df, cpi_series, profile, method=method,
                              n_sims=n_sims, seed=seed)
        s = res.summary()
        s["Guardrail band"] = round(float(band), 4)
        rows.append(s)
    return pd.DataFrame(rows).set_index("Guardrail band")


def _draw_indices_iid(n_hist, n_months, n_sims, rng):
    return rng.integers(0, n_hist, size=(n_sims, n_months))


def _draw_indices_fixed_block(n_hist, n_months, n_sims, rng, block=12):
    n_blocks = int(np.ceil(n_months / block))
    starts = rng.integers(0, n_hist - block, size=(n_sims, n_blocks))
    idx = np.empty((n_sims, n_blocks * block), dtype=int)
    for b in range(n_blocks):
        s = starts[:, b]
        for j in range(block):
            idx[:, b * block + j] = s + j
    return idx[:, :n_months]


def _draw_indices_stationary_block(n_hist, n_months, n_sims, rng, block_mean=12):
    p = 1.0 / block_mean
    idx = np.empty((n_sims, n_months), dtype=int)
    for s in range(n_sims):
        i = rng.integers(0, n_hist)
        t = 0
        while t < n_months:
            block_len = rng.geometric(p)
            for _ in range(block_len):
                if t >= n_months:
                    break
                idx[s, t] = i % n_hist
                i += 1
                t += 1
            i = rng.integers(0, n_hist)
    return idx


def run_glide_path_simulation(portfolio_name, asset_df, cpi_series, profile: ClientProfile,
                               start_equity_weight, end_equity_weight,
                               method="stationary_block", n_sims=2000, block_mean=12, seed=42):
    """Like run_simulation, but the TOTAL equity weight glides linearly from start_equity_weight
    (year 1) to end_equity_weight (final year) - a de-risking (or up-risking) glide path over the
    decumulation horizon, rather than one fixed allocation throughout.

    Implementation note: every year's weight vector is applied to the SAME bootstrap-sampled
    historical-month sequence (one coherent draw of "which historical months this path lives
    through" per simulated path), just looked up through a different weight vector each year. This
    is exact for the index-based methods (iid / fixed_block / stationary_block); skew_t is not
    supported here because it samples from a fitted distribution rather than historical indices, and
    the distribution would need refitting at every equity weight - use equity_sweep's fixed-weight
    skew_t runs at the start/end weights to bound a glide path's likely range instead."""
    if method not in ("iid", "fixed_block", "stationary_block"):
        raise ValueError("glide path simulation only supports iid / fixed_block / stationary_block")
    rng = np.random.default_rng(seed)
    fee = weighted_avg_fee(portfolio_name)

    years = profile.horizon_years
    if years <= 1:
        eq_path = [start_equity_weight]
    else:
        eq_path = [start_equity_weight + (end_equity_weight - start_equity_weight) * y / (years - 1)
                   for y in range(years)]
    year_series = [weighted_monthly_returns(scale_to_equity_weight(portfolio_name, eq_w), fee, asset_df,
                                             label=portfolio_name) for eq_w in eq_path]

    common_idx = year_series[0].index
    for s in year_series[1:]:
        common_idx = common_idx.intersection(s.index)
    common_idx = common_idx.intersection(cpi_series.index)
    year_arrays = [s.loc[common_idx].values for s in year_series]
    cpi_vals = cpi_series.loc[common_idx].values
    n_hist = len(common_idx)
    n_months = years * 12

    if method == "iid":
        idx = _draw_indices_iid(n_hist, n_months, n_sims, rng)
    elif method == "fixed_block":
        idx = _draw_indices_fixed_block(n_hist, n_months, n_sims, rng, block=block_mean)
    else:
        idx = _draw_indices_stationary_block(n_hist, n_months, n_sims, rng, block_mean=block_mean)

    c = cpi_vals[idx]  # inflation doesn't depend on the weight vector - same lookup every year

    paths = np.empty((n_sims, years + 1))
    spend_paths = np.empty((n_sims, years))
    ruin_year = np.full(n_sims, -1)
    paths[:, 0] = profile.starting_pot
    wr0 = profile.initial_annual_spend / profile.starting_pot
    pot = np.full(n_sims, profile.starting_pot)
    real_spend = np.full(n_sims, profile.initial_annual_spend)
    cum_inflation = np.ones(n_sims)
    alive = np.full(n_sims, True)

    for y in range(years):
        m0, m1 = y * 12, (y + 1) * 12
        idx_year = idx[:, m0:m1]
        r_year = year_arrays[y][idx_year]  # this year's returns, THROUGH this year's glide weight
        year_growth = np.prod(1 + r_year, axis=1)
        year_infl = c[:, m1 - 1]
        cum_inflation *= (1 + year_infl)

        nominal_spend_target = real_spend * cum_inflation
        if profile.guardrails and y > 0:
            current_wr = np.where(pot > 0, nominal_spend_target / np.maximum(pot, 1), np.inf)
            upper = wr0 * (1 + profile.guardrail_band)
            lower = wr0 * (1 - profile.guardrail_band)
            cut = current_wr > upper
            raise_ = current_wr < lower
            real_spend = np.where(cut, real_spend * (1 - profile.guardrail_cut), real_spend)
            real_spend = np.where(raise_, real_spend * (1 + profile.guardrail_raise), real_spend)
            real_spend = np.clip(real_spend, 0.5 * profile.initial_annual_spend, 1.5 * profile.initial_annual_spend)
            nominal_spend_target = real_spend * cum_inflation

        age_this_year = profile.starting_age + y
        nominal_gross_target = _gross_withdrawal_target(real_spend, cum_inflation, age_this_year, profile)
        # sp_nominal is ALWAYS computed (not gated by apply_tax) - see run_simulation() above for why.
        sp_nominal = tax.state_pension_income(age_this_year, sp_age=profile.state_pension_age,
                                               sp_annual=profile.state_pension_annual) * cum_inflation

        actual_spend = np.minimum(nominal_gross_target, np.maximum(pot, 0))
        actual_spend = np.where(alive, actual_spend, 0)
        pot = np.maximum(pot - actual_spend, 0) * year_growth
        pot = np.maximum(pot, 0)

        newly_ruined = alive & (pot <= 0) & (actual_spend < nominal_gross_target - 1e-6)
        ruin_year = np.where(newly_ruined & (ruin_year < 0), y, ruin_year)
        alive = alive & (pot > 0)

        paths[:, y + 1] = pot
        guaranteed_nominal = sp_nominal + profile.annuity_income_nominal
        spend_paths[:, y] = (tax.net_income(guaranteed_nominal + actual_spend) if profile.apply_tax
                              else guaranteed_nominal + actual_spend)

    method_label = f"{method}+glide({start_equity_weight:.0%}->{end_equity_weight:.0%})"
    return SimResult(portfolio_name, method_label, n_sims, paths, spend_paths, ruin_year, profile)


def sims_needed_for_margin(p_estimate, margin=0.02, z=1.96):
    """How many simulated paths are needed so the 95% CI on probability of ruin is within
    +/-margin, given a rough estimate of p (use 0.5 if unknown, which is the conservative
    worst case - maximises required n)."""
    p = min(max(p_estimate, 1e-6), 1 - 1e-6)
    return int(np.ceil((z ** 2) * p * (1 - p) / (margin ** 2)))


def repeated_seeds_diagnostic(portfolio_name, asset_df, cpi_series, profile, method="stationary_block",
                               n_sims=2000, n_seeds=10, base_seed=100):
    """Runs the same simulation with n_seeds different random seeds and reports the spread of the
    resulting probability-of-ruin estimates - an empirical check that the analytical binomial SE
    (SimResult.prob_ruin_se) is a reasonable description of the actual run-to-run noise."""
    estimates = []
    for i in range(n_seeds):
        res = run_simulation(portfolio_name, asset_df, cpi_series, profile, method=method,
                              n_sims=n_sims, seed=base_seed + i)
        estimates.append(res.prob_ruin)
    estimates = np.array(estimates)
    return {
        "n_seeds": n_seeds, "n_sims_per_seed": n_sims,
        "mean_prob_ruin": float(estimates.mean()), "empirical_std": float(estimates.std(ddof=1)),
        "analytical_se_at_mean": float(np.sqrt(estimates.mean() * (1 - estimates.mean()) / n_sims)),
        "min": float(estimates.min()), "max": float(estimates.max()),
    }


def historical_single_path(portfolio_name, asset_df, cpi_series, profile: ClientProfile, start_date=None,
                            custom_weights=None, custom_fee=None):
    """Deterministic projection running the ACTUAL historical sequence of monthly returns from a
    chosen start date, for audit / reasonableness-check purposes (mirrors the old model's
    'Model Hist' sheet, and the Excel workbook's 'Historical Projection' sheet - the two are cross-
    checked to match to the penny).

    If custom_weights (a pandas Series of asset-class weights) is supplied, it overrides the named
    portfolio's weights (custom_fee likewise overrides the fee) - mirrors run_simulation's own
    custom_weights/custom_fee override, so an edited/ad-hoc portfolio's historical chart can be
    computed without needing to be registered in portfolios.py. portfolio_name is still used as the
    result's label in that case.

    NOTE: unlike run_simulation (which intersects returns with CPI's index because it needs PAIRED
    (return, inflation) draws for the bootstrap), this function does NOT drop return months that lack
    a same-month CPI reading. UK CPI YoY is published with roughly a one-month lag behind the return
    data here, so intersecting the two would silently throw away the most recent month's real market
    return every time - each year's inflation instead uses the latest CPI reading available AS OF
    that year's last month (falling back to an earlier month if the current one isn't out yet), which
    is exactly what the Excel 'Portfolio Annual Returns' sheet does (COUNTIF/INDEX against the last
    non-blank CPI cell)."""
    if custom_weights is not None:
        fee = custom_fee if custom_fee is not None else weighted_avg_fee(portfolio_name)
        monthly_ret = weighted_monthly_returns(custom_weights, fee, asset_df, label=portfolio_name).dropna()
    else:
        monthly_ret = portfolio_monthly_returns(portfolio_name, asset_df).dropna()

    if start_date is None:
        start_date = monthly_ret.index[0]
    start_date = pd.Timestamp(start_date)
    idx = monthly_ret.index[monthly_ret.index >= start_date][: profile.horizon_years * 12]

    pot = profile.starting_pot
    real_spend = profile.initial_annual_spend
    cum_inflation = 1.0
    wr0 = profile.initial_annual_spend / profile.starting_pot
    rows = [(idx[0] if len(idx) else start_date, pot, 0.0)]
    for y in range(profile.horizon_years):
        yr_idx = idx[y * 12:(y + 1) * 12]
        if len(yr_idx) == 0:
            break
        growth = float((1 + monthly_ret.loc[yr_idx]).prod())
        cpi_upto = cpi_series.loc[:yr_idx[-1]]
        infl = float(cpi_upto.iloc[-1]) if len(cpi_upto) else 0.0
        cum_inflation *= (1 + infl)
        nominal_spend_target = real_spend * cum_inflation  # NET target - drives guardrails, unchanged by tax
        if profile.guardrails and y > 0 and pot > 0:
            wr = nominal_spend_target / pot
            if wr > wr0 * (1 + profile.guardrail_band):
                real_spend *= (1 - profile.guardrail_cut)
            elif wr < wr0 * (1 - profile.guardrail_band):
                real_spend *= (1 + profile.guardrail_raise)
            real_spend = min(max(real_spend, 0.5 * profile.initial_annual_spend), 1.5 * profile.initial_annual_spend)
            nominal_spend_target = real_spend * cum_inflation

        age_this_year = profile.starting_age + y
        nominal_gross_target = _gross_withdrawal_target(real_spend, cum_inflation, age_this_year, profile)
        # sp_nominal is ALWAYS computed (not gated by apply_tax) - see run_simulation() for why.
        sp_nominal = tax.state_pension_income(age_this_year, sp_age=profile.state_pension_age,
                                               sp_annual=profile.state_pension_annual) * cum_inflation
        withdrawal = min(float(nominal_gross_target), max(pot, 0))  # what actually leaves the pot
        pot = max(pot - withdrawal, 0) * growth
        # "Spend" = NET (take-home) income actually received, consistent with run_simulation's
        # spend_paths - identical to `withdrawal` when apply_tax and annuity_income_nominal are both off.
        guaranteed_nominal = sp_nominal + profile.annuity_income_nominal
        net_received = (float(tax.net_income(guaranteed_nominal + withdrawal)) if profile.apply_tax
                         else guaranteed_nominal + withdrawal)
        rows.append((yr_idx[-1], pot, net_received))
    return pd.DataFrame(rows, columns=["Date", "PortfolioValue", "Spend"])


if __name__ == "__main__":
    asset_df = load_asset_returns()
    cpi = load_cpi(asset_df)
    profile = ClientProfile(starting_age=65, horizon_years=30, starting_pot=500_000, initial_annual_spend=20_000)

    print("Historical single-path check (Original, from earliest date):")
    print(historical_single_path("Original", asset_df, cpi, profile).tail(3))

    print("\nMonte Carlo comparison (2,000 sims, stationary block bootstrap):")
    for name in PORTFOLIOS:
        res = run_simulation(name, asset_df, cpi, profile, method="stationary_block", n_sims=2000)
        print(res.summary())
