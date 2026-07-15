# Mobius Wealth — Decumulation Simulator (v4, refined)

Refines the previous Mobius decumulation model using the Bloomberg data supplied 9 July 2026 and
the FNZ "Growth Passive Plus" holdings. Compares **Original** (mainstream retail fund lineup) vs
**Alternative** (tax/cost-efficient unit-linked lineup) vs **Better** (a more diversified
allocation), and adds **inflation-linked spending + spending guardrails + an improved stochastic
sampling engine** on top of the previous model's methodology.

**Competitive framing**: for client-facing use, **Original** stands in for the comparison/benchmark
offering (Aspen Advisers, aspenadvisers.com), **Alternative** is Mobius's own core offering, and
**Better** is Mobius's enhanced/diversified alternative. The underlying holdings data and modelling
methodology are unchanged either way — this is just how the three columns should be presented/named
in front of a client.

## What's in this delivery

- `output/Mobius_Wealth_Decumulation_Model_v4.xlsx` — the refined Excel model. Fully formula-driven
  (no hardcoded results), recalculates live (308,000+ formulas, 0 errors on a full LibreOffice
  recalc), includes its own 600-path-per-portfolio Monte Carlo engine plus Equity Sweep, Sensitivity
  Tables, Asset Correlation, Mortality and Tax sheets.
- `app/app.py` (+ `requirements.txt`) — a Streamlit app with an improved stochastic engine
  (stationary block bootstrap / skewed-distribution sampling), fan charts, legacy distributions,
  a full Monte Carlo equity allocation sweep, withdrawal-rate/guardrail-band sensitivity analysis,
  an equity glide path (dynamic de-risking allocation) comparison, an asset-class correlation
  heatmap, a 2D withdrawal-rate x equity-weight shortfall heatmap, mortality-adjusted outcomes
  (single or joint life), and UK income tax + State Pension grossing-up with a plain-English
  net-vs-gross breakdown.
  Run with `pip install -r app/requirements.txt && streamlit run app/app.py`.
- `src/engine.py`, `src/portfolios.py`, `src/extract_data.py`, `src/mortality.py`, `src/tax.py` —
  the Python simulation engine the app is built on (also independently runnable/testable from the
  command line - e.g. `python3 src/tax.py` prints a self-test of the tax engine).
- `data/asset_class_returns.csv`, `data/fund_returns.csv`, `data/mortality_qx.csv` — cleaned data
  extracted from the Bloomberg file and the S4 mortality table respectively.

## What's new vs the previous model

1. **Three-way comparison**: Original vs Alternative vs Better (previous model compared "60/40" /
   "60/40 Gross" / "Better", using different, older data).
2. **Inflation-linked spending** using UK CPI YoY (Bloomberg data), not a fixed assumption.
3. **Spending guardrails** (Guyton-Klinger style: cut spend in weak markets, raise it in strong
   ones) — togglable on/off, capped at ±50% of the original desired spend so repeated triggers
   can't compound without bound.
4. **Improved stochastic sampling** in the Python app: a stationary block bootstrap (Politis &
   Romano, 1994 — random block lengths preserve serial correlation that a naive month-by-month
   i.i.d. bootstrap destroys) and a skewed-distribution parametric option, alongside the classic
   i.i.d. bootstrap for direct comparison. The Excel workbook runs its own simpler annual bootstrap
   (auditable, no VBA/macros required) as the spreadsheet-native complement.
5. **Confidence intervals on every probability-of-ruin estimate** (binomial standard error + 95%
   CI), in both the workbook's Summary sheet and the app's headline statistics, so the Monte Carlo
   sampling noise is visible rather than implied to be exact.
6. **Equity allocation sweep**: scans total equity weight (Global + EM equities combined) from
   20%-100% per portfolio, rescaling the rest of the portfolio's weights proportionally, and shows
   where probability of ruin / legacy bottoms out or peaks — in the workbook's 'Equity Sweep' tab
   (historical/formula-driven) and, with full Monte Carlo re-simulation at each point, in the app.
7. **Sensitivity tables**: scans probability of ruin / final legacy against the initial withdrawal
   rate and the guardrail band width independently — in the workbook's 'Sensitivity Tables' tab and
   the app.
8. **Equity glide path** (dynamic allocation): the app can run a de-risking (or up-risking) glide
   path — equity weight moving linearly from a starting to an ending level over the horizon —
   compared against a fixed allocation held throughout. App-only (see the workbook's Instructions
   tab for why this isn't practical to replicate as a formula-only spreadsheet).
9. **Asset class correlation**: a monthly-return correlation matrix across all 11 broad asset
   classes, in both the workbook (a colour-scaled 'Asset Correlation' tab) and the app (a heatmap) —
   shows that several of the 'Better' portfolio's extra holdings (REITs, infrastructure) run ~0.75+
   correlated with global equities, so they add less true diversification than their labels suggest.
10. **Spending shortfall heatmap** (app only): sweeps probability of ruin / % paths with any
    shortfall across a 2D grid of withdrawal rate x equity weight simultaneously, so you can see how
    the two levers interact rather than just their separate 1D sensitivities.
11. **Mortality** (S4 pension-scheme table, both workbook and app): models the client's own
    mortality using the S4 table (CMI, UK self-administered pension scheme experience) rather than a
    general-population table — pension scheme members' mortality differs systematically (typically
    lighter) from the wider population, the standard reason UK pension actuaries use S-series tables
    for this kind of work. Supports single life (with a sex toggle) and joint life (partner age/sex,
    using the "at least one of the couple still alive" survival curve, assuming independence between
    the two lives). Adds mortality-adjusted metrics alongside the raw, no-mortality ones:
    **probability of ruin before death** (the pot hits zero while the client is still alive — usually
    much lower than the raw horizon-end ruin probability, since a large share of "ruin by year 30"
    paths only ruin after the client has already died; computed *exactly*, not by extra sampling,
    via each simulated path's ruin year weighted against the survival curve, using the standard
    assumption that markets and mortality are independent) and **legacy at death** (the estate value
    at a sampled death year rather than at a fixed year-30 cutoff). See the workbook's `Mortality`
    tab and the app's "Mortality-adjusted outcomes" section.
12. **Tax + State Pension** (NEW - both workbook and app): when switched on, "desired annual spend"
    is treated as the client's NET (take-home) target rather than a pre-tax number. The model grosses
    it up into the actual TAXABLE withdrawal that has to leave the pot (UK income tax, rest-of-UK
    bands), and nets it against the State Pension once it starts (defaults to the full new State
    Pension, adjustable start age). This is frequently a **bigger lever on probability of ruin than
    portfolio choice** — the State Pension alone can cover 40-60%+ of a modest spending need, which
    dramatically reduces how hard the private pot has to work for the (typically 20-25) years it's in
    payment. See the workbook's `Tax` tab (editable bands + an illustrative before/after-State-
    Pension worked example) and the app's "What tax and State Pension mean for this plan" section.
    **Simplified basis** (see Key methodology notes below) — this is the first of several planned
    refinements to this feature.
13. **Forward-looking Capital Market Assumptions** (NEW — both workbook and app): the pure
    historical bootstrap (2000–2026) is a real, unadjusted sample, but it's one window, and it
    happens to span an unusually strong run for global equities. `Inputs!cma_blend` (workbook) / the
    sidebar's "Forward-looking blend" (app) optionally recentres each asset class's *average*
    monthly return towards independently published 10-year forecasts (a compiled median across
    Vanguard/Schroders/JPMorgan/BlackRock and others, via Monevator), while leaving history's
    volatility, correlation and worst-case behaviour completely untouched — it shifts where the
    distribution is centred, not its shape. 0% (default) is identical to today's pure-history
    behaviour everywhere in this workbook; 100% recentres fully to the forecasts, which are mostly
    *lower* for equities/REITs and *higher* for some bonds than this sample's own history, so
    blending typically **raises** probability of ruin — that's the blend correctly showing the plan
    is more fragile than one strong historical window suggested, not a bug. See `src/cma.py` (Python)
    and the workbook's `CMA` tab.
14. **Partial annuitization comparison** (NEW — both workbook and app): models converting part of
    the pot into a guaranteed LIFETIME income at outset, using real, dated UK best-buy annuity rates
    (Hargreaves Lansdown, 14–28 May 2026 — see `src/annuity.py`/the workbook's `Annuity` tab for full
    sourcing). The guaranteed income is LEVEL (does **not** rise with inflation, unlike everything
    else in this model) and offsets the withdrawal need from the smaller remaining pot every year,
    for life — this typically cuts probability of ruin sharply, at the cost of giving up upside and,
    because it's level, real-terms purchasing power over time (roughly halving after ~20 years at
    ~3% inflation). The app additionally shows this mortality-adjusted ("before death") against raw,
    and supports joint-life (50%-to-survivor) vs single-life pricing.

## Key methodology notes and assumptions (please review before relying on this)

- **Cash now uses the real Bank of England SONIA rate**, as instructed. The Bloomberg file's own
  "Cash (GBP) — SONIA / short rate proxy" column was not usable (it looks like a naive %-change of
  the SONIA rate level itself, producing nonsensical swings of +100%/+300% in a single month for
  what should be a ~2.5–5% pa cash rate — worth flagging to whoever owns that Bloomberg pull). Cash
  is instead built from the Bank of England's IUDSOIA series (daily SONIA rate, user-supplied via a
  BoE/FRED export), converted to a monthly-equivalent return via (1+rate/100)^(1/12)-1. That export
  only covers Jan 2014 onward, so 2000–2013 is spliced with the "Blackrock ICS Sterling Liquidity
  Fund" money-market fund return series as a proxy — validated as 99.7% correlated and closely
  matched in level with real SONIA over the 2014–2026 overlap, so the splice doesn't introduce a
  visible discontinuity. See `src/build_sonia_cash.py`. Since Cash is only 1.5–2% of any of the
  three portfolios, this fix moved headline results only slightly.
- **Fund-level return histories are mostly too short for a 25+-year Monte Carlo** (several funds in
  the FNZ holdings only have 20 months of data). Every holding is instead mapped to the best-fit
  broad asset-class index (11 asset classes, full history 1999/2000–2026) — see `src/portfolios.py`
  for the exact mapping.
- **Original vs Alternative is mainly a fee story.** The FNZ data shows these two largely hold the
  *same* underlying index exposure (several pairs explicitly labelled "Same Index") — the
  difference is cost (AMC), not market return. Alternative AMCs are given in the source data;
  Original OCFs are **not** given, so typical published OCFs for this fund type have been assumed
  (documented per-holding in the `Portfolios` sheet) — confirm against real factsheets before
  relying on this for client-facing output.
- **"Better" portfolio weights were tuned empirically**, not just copied from the previous model's
  narrative. Running candidates through the simulation revealed that REITs and Infrastructure
  indices in this data are highly correlated with global equities (~0.75–0.76) — they do **not**
  diversify the way the previous model's slide deck implied over this sample; genuine
  diversification came mostly from bonds/credit (lower CAGR, ~2–4.5% pa vs ~7.6–8.9% pa for
  equities). The tuned mix keeps meaningful growth exposure and adds a genuine bond/credit-led
  diversifying sleeve. **This is one reasonable construction, not the only one** — please sanity
  check and adjust.
- **Guardrails are the more powerful lever for cutting ruin probability** in this data, more so than
  portfolio choice alone — worth leading with in any client-facing narrative.
- **Cash Plus and Four Seasons Fund** (the other two FNZ portfolios) are **not modelled** — by
  instruction, since the agreed scope was Original vs Alternative vs Better (all Growth Passive
  Plus variants).
- **Mortality is now modelled** (see point 11 above) using the S4 pension-scheme table you supplied,
  in place of a general-population (e.g. ONS) table — a better fit for pension decumulation work.
  Mortality assumes independence from market returns (standard simplifying assumption), and, for
  joint life, independence between the two lives of a couple (in reality couples' deaths are not
  fully independent — e.g. shared lifestyle/socioeconomic factors — but modelling that correlation
  needs data this exercise doesn't have).
- **Tax + State Pension are now modelled** (see point 12 above), on a deliberately SIMPLIFIED basis
  for this first pass: the whole pot is treated as a taxable pension wrapper (every pound withdrawn
  counts as income) — no 25% pension-commencement lump sum, ISA (tax-free) or GIA (capital gains, not
  income) modelling, since that needs to know a real client's actual wrapper mix. Rest-of-UK income
  tax bands only (Scotland has different, more numerous bands). 2026/27 figures throughout: Personal
  Allowance £12,570, 20%/40%/45% bands, the well-known ~60% marginal-rate "trap" between £100k-
  £125,140 from the Personal Allowance taper, and the full new State Pension at £12,547.60/year
  (confirmed via gov.uk, 14 Jul 2026). Tax bands and the State Pension are both held in TODAY'S MONEY
  (assumed to rise with inflation) for the whole horizon — current policy has in fact frozen the tax
  bands in nominal terms since 2021 (repeatedly extended), but assuming that freeze holds literally
  for a 30-year retirement horizon would imply an increasingly severe, almost certainly unrealistic
  real-terms tax rise; working in real terms throughout (as the rest of this model already does for
  spending) is the standard convention for long-horizon retirement tools. State Pension is likewise
  treated as growing with inflation, which slightly UNDERSTATES it (the "triple lock" uprates by the
  highest of inflation / average earnings growth / 2.5%, so it has tended to grow a bit faster than
  inflation over time) — a conservative simplification. **Natural next steps on this feature**: model
  the 25% pension-commencement tax-free lump sum and a real ISA/GIA wrapper split, and let the client
  enter their own State Pension forecast (real entitlement varies by National Insurance record) rather
  than defaulting to the full amount.
- **Annual model in Excel** (the spec's own suggestion) vs **monthly model in the Python app** (for
  finer sequencing-risk detail). The two engines are cross-validated to agree on methodology
  (verified the Excel Historical Projection sheet matches the Python engine's deterministic path to
  the penny) - the Excel Monte Carlo uses a coarser annual i.i.d. bootstrap so its precise ruin
  probabilities will differ somewhat from the Python app's monthly block-bootstrap; that
  difference is itself an illustration of point 4 above.
- **Historical window is short** (1999/2000–2026, ~26 years) relative to a 30-year decumulation
  horizon and happened to be strong for global equities — ruin probabilities from any bootstrap of
  this window should be treated as illustrative, not predictive.
- **Equity Sweep / Sensitivity Tables use a single historical path per grid point**, not a full
  Monte Carlo replication, for tractability inside a spreadsheet (27+ grid points × thousands of
  paths each isn't practical as live-recalculating formulas) — CAGR, volatility, max drawdown and
  the historical legacy/shortfall outcome are exact for the actual 2000–2026 sequence, but don't
  carry a confidence interval the way the app's Monte Carlo version of the same sweeps does. Both
  versions were cross-checked to agree (Excel and Python give the same CAGR/vol/drawdown/legacy
  figures to floating-point precision across all 27 equity-weight grid points and all withdrawal
  rate / guardrail band grid points tested).
- **A data-accuracy pass on this round of features caught and fixed one real bug**: the Python
  engine's single deterministic historical-path check (`historical_single_path`) was silently
  dropping the most recent month of real market return data whenever it fell after UK CPI's last
  published reading (CPI lags the return data by about a month), because it intersected the return
  and inflation series before slicing into years. That understated the final year's growth and put
  the deterministic check about 0.5% off from the Excel workbook's equivalent (which correctly uses
  all available return months and just carries forward the last known CPI reading for inflation).
  Fixed so both engines now match to the penny again — see `src/engine.py`'s `historical_single_path`
  docstring for the detail. The Monte Carlo engine was not affected (it needs paired return/inflation
  draws for the bootstrap by design, and only loses 1 of 318 months of *pool* data, not a bias).
- **Forward-looking CMA figures are a compiled median across published third-party forecasts**
  (Monevator's compilation of Vanguard/Schroders/JPMorgan/BlackRock and others), not this firm's own
  house view. Three of the eleven asset classes (UK Gilts 15yr+, Securitised Credit, Infrastructure)
  have no direct published forward-looking match and are proxied from the closest available category
  — flagged individually in `src/cma.py` and on the workbook's `CMA` tab. The blend shifts each asset
  class's *mean* monthly return only (an additive constant shift); it deliberately leaves the
  historical bootstrap's volatility, fat tails and cross-asset correlation completely untouched, so
  it recentres the plan's expected outcome without inventing a new (unvalidated) return distribution.
- **Annuity rates are scaled examples from Hargreaves Lansdown's published best-buy table**
  (14–28 May 2026, cross-checked across two dates for consistency — see `src/annuity.py`), **not a
  personalised quote** — real annuity quotes vary by provider, postcode and health, and
  enhanced/impaired-life rates (available to smokers or those with certain medical conditions) can
  be materially higher than the standard rates used here. No 25% pension-commencement tax-free lump
  sum is modelled before annuitizing (consistent with the tax feature's current simplification).
  Joint-life pricing uses one flat discount factor derived from a single age-65 data point, not a
  full joint-life age curve. Rates for ages outside the quoted 55–75 range are clipped to the nearest
  quoted age rather than extrapolated, since no cited source covers rates outside it.

## Suggested next steps

- Confirm the Original-portfolio OCF assumptions against real fund factsheets.
- Sanity-check / adjust the "Better" portfolio weights with the team - it's a reasonable starting
  point, not a final recommendation.
- Consider extending the historical window with a second data source (the previous model apparently
  had returns back to 1988) to reduce sampling-window sensitivity.
- If useful, wire the Python engine's stationary block bootstrap results back into the Excel
  Summary sheet as a static "as of" comparison table.
- Tax refinements: model the 25% pension-commencement tax-free lump sum and a real ISA/GIA wrapper
  split (needs the client's actual wrapper mix, not just a total pot value); let the client enter
  their own State Pension forecast instead of defaulting to the full new State Pension amount;
  consider Scottish tax bands as a toggle.
- Annuity refinements: model the 25% tax-free lump sum before annuitizing; source a fuller joint-
  life rate curve (rather than one flat discount factor); consider enhanced/impaired-life rates.
- Widen the CMA proxy coverage if/when a primary provider publishes forecasts for UK Gilts 15yr+,
  Securitised Credit and Infrastructure specifically, rather than relying on the closest-category
  proxies currently used.
- Turn this from an analyst tool into a scalable practice-management product: branded client-facing
  PDF/Word reports generated straight from a saved scenario, a "batch mode" to re-run the whole book
  of clients against updated market data in one go, a live FNZ data feed instead of a point-in-time
  Bloomberg pull, and an audit trail of what assumptions/toggles were live when a piece of advice was
  given (useful for Consumer Duty record-keeping).
