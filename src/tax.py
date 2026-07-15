"""
UK income tax (rest-of-UK/non-Scottish bands) and State Pension module.

SCOPE / SIMPLIFICATIONS (per instruction - "simplified" tax basis, agreed before building this):
  - The ENTIRE pot is treated as a taxable pension wrapper (uncrystallised drawdown). Every pound
    withdrawn is taxable income. This deliberately does NOT model a 25% pension-commencement lump
    sum, ISA wrappers (tax-free) or GIA wrappers (capital gains, not income tax) - a real client's
    actual wrapper mix would need to be known to model that properly. Flagged as a natural next
    enhancement, not built this round.
  - Only rest-of-UK (England/Wales/NI) income tax bands are modelled - Scottish taxpayers have
    different (more numerous) bands and rates.
  - No National Insurance (doesn't apply to pension/state pension income), no dividend/savings
    allowances, no marriage allowance.
  - TAX BANDS ARE HELD CONSTANT IN TODAY'S MONEY (i.e. assumed to rise with inflation) over the
    whole modelled horizon. The current bands have in fact been frozen in NOMINAL terms by policy
    since 2021 (extended repeatedly), but assuming that freeze continues literally for a 30-year
    horizon would imply enormous, almost certainly unrealistic fiscal drag. Working in real terms
    (as the rest of this model already does for spending) is the standard simplifying convention
    for long-horizon retirement planning tools - flagged clearly as an assumption to revisit if a
    literal current-law projection is wanted instead.
  - State Pension is likewise treated as a real (CPI-uprated) income stream, which UNDERSTATES it
    slightly vs current policy (the "triple lock" uprates by the HIGHEST of inflation, average
    earnings growth, or 2.5% - so real terms State Pension has historically tended to grow slightly
    over time, not just track inflation) - a conservative simplification.

FIGURES (2026/27 tax year, confirmed via gov.uk, 14 Jul 2026):
  - Personal Allowance: GBP 12,570 (unchanged since 2021/22 - frozen)
  - Basic rate: 20% on income GBP 12,571 - GBP 50,270
  - Higher rate: 40% on income GBP 50,271 - GBP 125,140
  - Additional rate: 45% on income above GBP 125,140
  - Personal Allowance taper: reduced GBP 1 for every GBP 2 of income above GBP 100,000, reaching
    zero at GBP 125,140 (the well-known "60% marginal rate trap" between GBP 100k-125,140, since an
    extra GBP 1 of income both is taxed at 40% AND costs 50p of allowance -> 50p more taxed at 40% =
    60% effective marginal rate through this band).
  - Full new State Pension: GBP 241.30/week = GBP 12,547.60/year (2026/27, after the 4.8% triple-lock
    uprating from April 2026).
Sources: gov.uk/income-tax-rates; gov.uk/new-state-pension/what-youll-get; MoneySavingExpert
(triple-lock rise confirmation), all checked 14 Jul 2026.
"""
from __future__ import annotations
import numpy as np

PERSONAL_ALLOWANCE = 12_570.0
BASIC_RATE_LIMIT = 50_270.0      # upper edge of the 20% band
HIGHER_RATE_LIMIT = 125_140.0    # upper edge of the 40%/taper zone; 45% above this
PA_TAPER_START = 100_000.0
BASIC_RATE = 0.20
HIGHER_RATE = 0.40
ADDITIONAL_RATE = 0.45

FULL_NEW_STATE_PENSION_WEEKLY = 241.30
FULL_NEW_STATE_PENSION_ANNUAL = round(FULL_NEW_STATE_PENSION_WEEKLY * 52, 2)  # 12,547.60
DEFAULT_STATE_PENSION_AGE = 67  # adjustable - actual SPA depends on date of birth; client should
                                 # confirm their own via gov.uk's State Pension age calculator

# --- derived closed-form breakpoints (see src/tax.py docstring / project notes for the derivation) ---
# Segment boundaries of gross taxable income X:
_X1 = PERSONAL_ALLOWANCE                              # 12,570 - PA runs out
_X2 = BASIC_RATE_LIMIT                                 # 50,270 - basic rate band ends
_X3 = PA_TAPER_START                                   # 100,000 - PA taper begins
_X4 = HIGHER_RATE_LIMIT                                # 125,140 - PA taper ends / additional rate starts

_TAX_AT_X2 = BASIC_RATE * (_X2 - _X1)                                    # 7,540.00
_TAX_AT_X3 = _TAX_AT_X2 + HIGHER_RATE * (_X3 - _X2)                      # 27,432.00
_TAX_AT_X4 = _TAX_AT_X3 + 0.60 * (_X4 - _X3)                             # 42,516.00 (60% effective in taper zone)

# net(X) = X - tax(X), piecewise linear with slopes 1 / 0.8 / 0.6 / 0.4 / 0.55 across the 5 segments
_N1 = _X1                              # net(X1) = 12,570.00
_N2 = _X2 - _TAX_AT_X2                 # net(X2) = 42,730.00
_N3 = _X3 - _TAX_AT_X3                 # net(X3) = 72,568.00
_N4 = _X4 - _TAX_AT_X4                 # net(X4) = 82,624.00


def personal_allowance(gross_income):
    """The client's Personal Allowance (tax-free band) at a given gross income, after the >GBP100k
    taper (GBP1 lost per GBP2 of income above GBP100,000, reaching zero at GBP125,140). Informational/
    UI use - tax_due() below computes tax directly via closed-form segments that already build the
    taper in, rather than calling this."""
    x = np.asarray(gross_income, dtype=float)
    return np.where(x <= PA_TAPER_START, PERSONAL_ALLOWANCE,
                     np.where(x <= HIGHER_RATE_LIMIT,
                              np.maximum(0.0, PERSONAL_ALLOWANCE - (x - PA_TAPER_START) / 2.0), 0.0))


def tax_due(gross_income):
    """UK income tax due (rest-of-UK bands, 2026/27, with the >GBP100k Personal Allowance taper) on
    a given gross taxable income. Vectorised - accepts a scalar or a numpy array."""
    x = np.asarray(gross_income, dtype=float)
    tax = np.select(
        [x <= _X1, x <= _X2, x <= _X3, x <= _X4, x > _X4],
        [
            np.zeros_like(x),
            BASIC_RATE * (x - _X1),
            _TAX_AT_X2 + HIGHER_RATE * (x - _X2),
            _TAX_AT_X3 + 0.60 * (x - _X3),
            _TAX_AT_X4 + ADDITIONAL_RATE * (x - _X4),
        ],
    )
    return float(tax) if np.isscalar(gross_income) or (hasattr(gross_income, "ndim") and gross_income.ndim == 0) else tax


def net_income(gross_income):
    """Take-home income after tax—net(X) = X - tax_due(X)."""
    x = np.asarray(gross_income, dtype=float)
    return x - tax_due(x)


def gross_for_net(net_target):
    """Inverts net_income(): given a desired NET (take-home) income, returns the GROSS taxable
    income required to produce it. Closed-form piecewise inversion of the piecewise-linear
    net_income() function (which is continuous and strictly increasing, so the inverse is unique) -
    see the module docstring / _N1.._N4 derivation. Vectorised."""
    n = np.asarray(net_target, dtype=float)
    n = np.maximum(n, 0.0)
    x = np.select(
        [n <= _N1, n <= _N2, n <= _N3, n <= _N4, n > _N4],
        [
            n,
            (n - PERSONAL_ALLOWANCE * BASIC_RATE) / (1 - BASIC_RATE),
            (n - (HIGHER_RATE * _X2 - _TAX_AT_X2)) / (1 - HIGHER_RATE),
            (n - (0.60 * _X3 - _TAX_AT_X3)) / (1 - 0.60),
            (n - (ADDITIONAL_RATE * _X4 - _TAX_AT_X4)) / (1 - ADDITIONAL_RATE),
        ],
    )
    return float(x) if np.isscalar(net_target) or (hasattr(net_target, "ndim") and net_target.ndim == 0) else x


def state_pension_income(age, sp_age=DEFAULT_STATE_PENSION_AGE, sp_annual=FULL_NEW_STATE_PENSION_ANNUAL):
    """State Pension income (today's money) for a client of the given age. 0 before sp_age, the
    full annual amount from sp_age onward (no partial-year proration - annual model granularity).
    Vectorised over `age`."""
    age = np.asarray(age)
    return np.where(age >= sp_age, sp_annual, 0.0)


def gross_up_pot_withdrawal(net_target, other_taxable_income=0.0):
    """The core planning calculation: given a desired NET (take-home) income for the year and any
    OTHER taxable income already received that year (State Pension), returns how much must be
    withdrawn (GROSS, i.e. taxable) from the pot to hit that net income target. Vectorised."""
    total_gross_needed = gross_for_net(net_target)
    return np.maximum(total_gross_needed - other_taxable_income, 0.0)


if __name__ == "__main__":
    # sanity checks against hand-calculated examples
    checks = [
        (12570, 0.0), (30000, 3486.0), (50270, 7540.0),
        (110000, 33432.0),   # inside the 60% taper trap
        (150000, 53703.0),   # additional rate
    ]
    for gross, expected in checks:
        got = tax_due(gross)
        status = "OK" if abs(got - expected) < 0.01 else "MISMATCH"
        print(f"tax_due({gross}) = {got:.2f} (expected {expected:.2f}) [{status}]")

    print()
    for gross, _ in checks:
        net = net_income(gross)
        back = gross_for_net(net)
        status = "OK" if abs(back - gross) < 0.01 else "MISMATCH"
        print(f"gross={gross}, net={net:.2f}, inverted back to gross={back:.2f} [{status}]")

    print()
    print("State pension at 65 (pre-SPA):", state_pension_income(65))
    print("State pension at 67 (post-SPA):", state_pension_income(67))
    print("Full new State Pension annual:", FULL_NEW_STATE_PENSION_ANNUAL)

    print()
    # a client with a GBP20,000 net need who's already receiving State Pension needs less GROSS
    # withdrawal from the pot than one who isn't
    w_no_sp = gross_up_pot_withdrawal(20000, other_taxable_income=0)
    w_with_sp = gross_up_pot_withdrawal(20000, other_taxable_income=FULL_NEW_STATE_PENSION_ANNUAL)
    print(f"Pot withdrawal needed for GBP20,000 net, no SP: GBP{w_no_sp:,.2f}")
    print(f"Pot withdrawal needed for GBP20,000 net, with full SP: GBP{w_with_sp:,.2f}")
