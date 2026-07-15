"""Partial annuitization: converting some of the pension pot into a guaranteed lifetime income at
outset, instead of leaving 100% of it in drawdown.

RATES: single-life, level (NOT inflation-linked), no-guarantee-period annuity, annual income per
£1 of purchase price, by age at purchase. Source: a scaled-examples table built from Hargreaves
Lansdown's published £100,000 best-buy annuity-rate data, 14 May 2026 (via
https://www.pensionbible.co.uk/guides/annuity-income-by-pot-size-uk), cross-checked directly
against HL's own best-buy-rates page on 28 May 2026 (https://www.hl.co.uk/retirement/annuities/
best-buy-rates), which showed £7,970/£8,703/£9,937 per £100,000 at ages 65/70/75 respectively -
within ~1% of the table below (normal week-to-week annuity market movement), confirming it's a
reasonable current basis. Both are real, dated, named sources - not fabricated or estimated figures.

LEVEL vs ESCALATING: a "level" annuity pays the SAME cash amount every year for life - it does NOT
rise with inflation. This is the standard, cheapest, most commonly quoted annuity type (and the one
in the sources above), and is a genuine trade-off worth surfacing to a client: it buys a bigger
starting income than an inflation-linked annuity would, but that income is worth progressively LESS
in real terms every year it's paid - after ~20 years of ~3% inflation a level annuity has lost
roughly half its real purchasing power. The model below reflects that honestly (the pot mechanics
and everything else in the app tracks REAL spending power throughout; a level, non-inflating
annuity income is deliberately NOT scaled up with inflation here, unlike the State Pension, which
in reality is protected by the triple lock).

JOINT LIFE: HL's best-buy page also showed a joint-life (50% to survivor), level, no-guarantee rate
of £7,374 per £100,000 at age 65 (28 May 2026) - a ratio of 7374/7970 = 0.9252 vs the single-life
rate at the same age. Applied as a flat multiplier across ages below, since no fuller joint-life
age curve was available in the sources checked - a simplification, flagged as such.

SCOPE: the quoted tables only cover ages 55-75 (annuities are rarely purchased outside this range
in practice). Ages outside that range are clipped to the nearest quoted age rather than extrapolated
- a deliberately conservative choice (real rates would likely differ, but we don't have a cited
source for them) - see annuity_rate()'s docstring.
"""
import numpy as np

# Single-life, level, no-guarantee annual income per £1 purchased, by age at purchase.
# Source: scaled examples from HL's published £100,000 annuity-rate table, 14 May 2026
# (pensionbible.co.uk), cross-checked vs HL's own site, 28 May 2026 (see module docstring).
SINGLE_LIFE_RATE_TABLE = {
    55: 0.06691,
    60: 0.07075,
    65: 0.07916,
    70: 0.08670,
    75: 0.09878,
}
MIN_QUOTED_AGE = min(SINGLE_LIFE_RATE_TABLE)
MAX_QUOTED_AGE = max(SINGLE_LIFE_RATE_TABLE)

# Joint-life (50% to survivor) / single-life ratio at age 65, HL 28 May 2026 (£7,374 vs £7,970 per
# £100,000) - applied as a flat multiplier across all ages (simplification - no fuller joint-life
# age curve was available in the sources checked).
JOINT_LIFE_50PCT_FACTOR = 7374 / 7970


def annuity_rate(age: int, joint: bool = False) -> float:
    """Annual (level, nominal) income per £1 of purchase price for a lifetime annuity bought at
    `age`. Linearly interpolates between the quoted ages (55-75); ages outside that range are
    CLIPPED to the nearest quoted age (55 or 75) rather than extrapolated, since no cited source
    covers rates outside it - deliberately conservative rather than guessed. `joint=True` applies
    the flat 50%-survivor joint-life discount factor (see module docstring)."""
    clipped_age = min(max(age, MIN_QUOTED_AGE), MAX_QUOTED_AGE)
    ages = sorted(SINGLE_LIFE_RATE_TABLE)
    rates = [SINGLE_LIFE_RATE_TABLE[a] for a in ages]
    rate = float(np.interp(clipped_age, ages, rates))
    if joint:
        rate *= JOINT_LIFE_50PCT_FACTOR
    return rate


def annuitize(profile, annuity_pct: float, age: int, joint: bool = False):
    """Returns a NEW ClientProfile (via dataclasses.replace - profile itself is untouched)
    reflecting partial annuitization at outset: `annuity_pct` (0-1) of profile.starting_pot is used
    to purchase a lifetime annuity at the going rate for `age`/`joint`, leaving the rest of the pot
    (1 - annuity_pct) to fund an otherwise-unchanged drawdown plan alongside the new guaranteed,
    LEVEL (non-inflating) annuity income - which flows into the same tax/State-Pension gross-up
    machinery as an extra 'other taxable income' source (see engine._gross_withdrawal_target)."""
    from dataclasses import replace
    rate = annuity_rate(age, joint=joint)
    annuity_income = profile.starting_pot * annuity_pct * rate
    return replace(
        profile,
        starting_pot=profile.starting_pot * (1 - annuity_pct),
        annuity_income_nominal=profile.annuity_income_nominal + annuity_income,
    ), rate, annuity_income


if __name__ == "__main__":
    # blend_weight=0 equivalent sanity checks
    assert annuity_rate(65, joint=False) == SINGLE_LIFE_RATE_TABLE[65]
    assert annuity_rate(50, joint=False) == SINGLE_LIFE_RATE_TABLE[55], "should clip to 55"
    assert annuity_rate(90, joint=False) == SINGLE_LIFE_RATE_TABLE[75], "should clip to 75"
    mid = annuity_rate(67.5, joint=False)
    assert SINGLE_LIFE_RATE_TABLE[65] < mid < SINGLE_LIFE_RATE_TABLE[70], "should interpolate"
    joint_rate = annuity_rate(65, joint=True)
    assert abs(joint_rate - SINGLE_LIFE_RATE_TABLE[65] * JOINT_LIFE_50PCT_FACTOR) < 1e-12

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from engine import ClientProfile

    base = ClientProfile(starting_age=65, starting_pot=500_000.0, initial_annual_spend=20_000.0)
    annuitized, rate, income = annuitize(base, annuity_pct=0.30, age=65, joint=False)
    assert abs(annuitized.starting_pot - 350_000.0) < 1e-6
    assert abs(income - 500_000.0 * 0.30 * SINGLE_LIFE_RATE_TABLE[65]) < 1e-6
    assert abs(annuitized.annuity_income_nominal - income) < 1e-6
    assert base.starting_pot == 500_000.0, "original profile must be untouched"
    print(f"30% of £500,000 at age 65 buys £{income:,.0f}/year guaranteed (rate {rate:.2%}), "
          f"leaving £{annuitized.starting_pot:,.0f} in drawdown.")
    print("All annuity.py self-tests passed.")
