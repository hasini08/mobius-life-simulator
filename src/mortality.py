"""
Mortality module - survival-probability math for single-life and joint-life (couple) decumulation
modelling, built against the S4 mortality table (Male_Data / Female_Data, Age + q_x, ages 20-120) -
one of the CMI's (Continuous Mortality Investigation) standard tables derived from UK
self-administered PENSION SCHEME experience. This is a better basis for a pension decumulation model
than a general-population table (e.g. ONS National Life Tables), since pension scheme members'
mortality experience differs systematically (typically lighter) from the population at large - the
standard reason UK pension actuaries use S-series tables for this kind of work.

qx convention: qx[age] = probability a person alive at their `age`th birthday dies before their
(age+1)th birthday (standard actuarial notation). The table runs ages 20-120, closing with qx=1 at
age 120 (a standard closure convention so every simulated life eventually "dies" in the model).

DATA SOURCE: user-supplied S4 mortality table (Male_Data / Female_Data sheets), extracted to
data/mortality_qx.csv by build_mortality_data.py.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MORTALITY_SRC = DATA / "mortality_qx.csv"

MIN_AGE = 20
MAX_AGE = 120  # S4 table closes at age 120 (qx=1)


def load_mortality_table(path=None):
    """Loads the S4 mortality table (data/mortality_qx.csv, built by build_mortality_data.py from the
    user-supplied Male_Data/Female_Data workbook) into a DataFrame indexed by age (20-120) with
    columns 'qx_male' and 'qx_female'.

    Raises FileNotFoundError with a clear message if the CSV hasn't been built yet (run
    src/build_mortality_data.py against the uploaded S4 table first)."""
    path = Path(path) if path else MORTALITY_SRC
    if not path.exists():
        raise FileNotFoundError(
            f"Mortality table not found at {path}. Run src/build_mortality_data.py (against the "
            "uploaded S4 mortality data workbook) to build it first."
        )
    df = pd.read_csv(path, index_col="age")
    return df


def survival_curve(qx: pd.Series, age: int, years: int) -> np.ndarray:
    """Given a qx table (indexed by age) and a starting age, returns an array of length years+1:
    S[0] = 1.0 (certainly alive today), S[t] = probability of surviving to age+t, for t = 0..years.
    Ages beyond the table's max are treated as qx=1 (certain death that year) - a standard closure
    convention so the curve always reaches 0."""
    s = np.empty(years + 1)
    s[0] = 1.0
    for t in range(1, years + 1):
        a = age + t - 1
        q = float(qx.get(a, 1.0))
        q = min(max(q, 0.0), 1.0)
        s[t] = s[t - 1] * (1 - q)
    return s


def joint_survival_curve(qx_male: pd.Series, qx_female: pd.Series, age_male: int, age_female: int,
                          years: int) -> np.ndarray:
    """Joint 'at least one of the couple still alive' survival curve - the relevant one for household
    decumulation, since spending typically continues (often at a somewhat lower level) until the
    SECOND death, not the first. Returns S_joint[t] = 1 - (1 - S_m[t]) * (1 - S_f[t]), independence
    assumed between the two lives (a standard, if imperfect, simplifying assumption - couples' deaths
    are not fully independent in reality, e.g. shared lifestyle/socioeconomic factors, but modelling
    that correlation needs data this exercise doesn't have)."""
    s_m = survival_curve(qx_male, age_male, years)
    s_f = survival_curve(qx_female, age_female, years)
    return 1 - (1 - s_m) * (1 - s_f)


def life_expectancy(qx: pd.Series, age: int, max_years: int = 80) -> float:
    """Curtate life expectancy at `age`: sum of survival probabilities S[1], S[2], ... (standard
    actuarial approximation e_x = sum_{t=1}^{inf} tPx, truncated once the survival curve is
    negligible)."""
    s = survival_curve(qx, age, max_years)
    return float(s[1:].sum())


def sample_death_years(qx: pd.Series, age: int, years: int, n_sims: int, rng) -> np.ndarray:
    """Vectorised inverse-transform sampling of a "year of death" (0-indexed simulation year, i.e.
    death between age+t and age+t+1 -> returns t) for n_sims independent lives, given a qx table and
    starting age. Returns -1 for paths where the life survives the full `years` horizon (i.e. is
    still alive at age+years) - used to determine, per Monte Carlo path, which simulated years the
    client is actually alive for."""
    s = survival_curve(qx, age, years)  # s[t] = P(alive at age+t), s[0]=1, monotonically non-increasing
    u = rng.random(n_sims)
    # death occurs in simulation year t (0-indexed) if s[t] >= u > s[t+1] i.e. alive at start of year t,
    # dead by its end. searchsorted on the DEcreasing s array via negation trick:
    death_year = np.searchsorted(-s, -u, side="left") - 1
    # u > s[0]=1 never happens; u <= s[years] means survives the whole horizon
    death_year = np.where(u <= s[-1], -1, death_year)
    death_year = np.clip(death_year, -1, years - 1)
    return death_year


def joint_death_years(qx_male, qx_female, age_male, age_female, years, n_sims, rng):
    """Per-path year of SECOND death for a couple (the relevant point at which household spending
    needs typically end), sampling each life's death year independently then taking the later one.
    Returns -1 where neither has died by the end of the horizon (at least one still alive)."""
    dy_m = sample_death_years(qx_male, age_male, years, n_sims, rng)
    dy_f = sample_death_years(qx_female, age_female, years, n_sims, rng)
    # -1 means "survives the whole horizon" - treat as "beyond horizon" (larger than any real death
    # year) when taking the max, then convert back to -1 if the result is still "beyond horizon"
    sentinel = years  # any real death year is < years
    dy_m2 = np.where(dy_m < 0, sentinel, dy_m)
    dy_f2 = np.where(dy_f < 0, sentinel, dy_f)
    second = np.maximum(dy_m2, dy_f2)
    return np.where(second >= sentinel, -1, second)


if __name__ == "__main__":
    table = load_mortality_table()
    qx_m, qx_f = table["qx_male"], table["qx_female"]

    curve_m = survival_curve(qx_m, age=65, years=30)
    curve_f = survival_curve(qx_f, age=65, years=30)
    print("Male survival curve, age 65, first 10 years:", curve_m[:10].round(4))
    print("Female survival curve, age 65, first 10 years:", curve_f[:10].round(4))
    print("Male life expectancy at 65:", round(life_expectancy(qx_m, 65), 2), "years")
    print("Female life expectancy at 65:", round(life_expectancy(qx_f, 65), 2), "years")

    joint = joint_survival_curve(qx_m, qx_f, age_male=67, age_female=65, years=30)
    print("Joint (couple, at least one alive) survival at year 30:", round(joint[30], 4),
          "vs male alone:", round(curve_m[30], 4), "female alone:", round(curve_f[30], 4))

    rng = np.random.default_rng(42)
    dy = sample_death_years(qx_m, 65, 30, 200_000, rng)
    print("Male: % surviving full 30yr horizon (sim):", (dy < 0).mean(), "vs closed-form:", curve_m[30])
    print("Male median death year (sim, among those who die within horizon):", np.median(dy[dy >= 0]))
