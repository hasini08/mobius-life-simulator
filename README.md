# Mobius Wealth — Decumulation & Accumulation Simulator

A Streamlit tool that compares retirement portfolios (Mobius's own fund lineups vs a
competitor's) on **probability of ruin** — the chance a pension pot runs out of money before a
client's plan is meant to end — using Monte Carlo simulation over real historical market data.
Includes a full client-facing comparison app and a gamified internal version ("build your own
portfolio, see if it survives").

This README is written for whoever picks this project up next — it covers what everything is,
how to run it, and where the loose ends are, not just what's "new" in the latest delivery.

## At a glance

- **Live app**: https://mobius-life-simulator-czyadc2asardjl7oiuigmz.streamlit.app — nothing to
  install, just open it. The Portfolio Builder Game is the second page in the sidebar.
- **Repo**: `hasini08/mobius-life-simulator` on GitHub, `master` branch — any push
  auto-redeploys the live app within about a minute (see [Deployment](#deployment)).
- **Running the game live at an event?** Use [HOST_GUIDE.md](HOST_GUIDE.md) instead of this
  file — a short, non-technical, click-by-click guide for whoever's facilitating.
- **New to this codebase?** Read [What's actually running](#whats-actually-running) and
  [How it fits together](#how-it-fits-together) first — everything else in this README is
  reference material you'll dip into as needed, not something to read start to finish.
- **Two things to check before relying on this live**, both already flagged in
  [Known gaps](#known-gaps--where-things-were-left-off): whether the Google Sheets leaderboard
  backend has actually been set up (it silently falls back to a CSV file that isn't safe under
  concurrent writes if not), and whether the host PIN has been changed from its default.

## Contents

1. [Quick start](#quick-start)
2. [Running the test suite](#running-the-test-suite)
3. [What's actually running](#whats-actually-running)
4. [How it fits together](#how-it-fits-together)
5. [Repository map](#repository-map)
6. [`src/` script inventory](#src-script-inventory)
7. [Data files](#data-files)
8. [Adding a new portfolio](#adding-a-new-portfolio)
9. [Persistent leaderboard setup (Google Sheets)](#persistent-leaderboard-setup-google-sheets)
10. [Host controls (running a live multi-group session)](#host-controls-running-a-live-multi-group-session)
11. [Key methodology notes and assumptions](#key-methodology-notes-and-assumptions)
12. [Known gaps / where things were left off](#known-gaps--where-things-were-left-off)
13. [Deployment](#deployment)
14. [Suggested next steps](#suggested-next-steps)

## Quick start

```bash
git clone https://github.com/hasini08/mobius-life-simulator.git
cd mobius-life-simulator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt
streamlit run app/app.py
```

Opens at `http://localhost:8501`. The Portfolio Builder Game is a second page,
auto-discovered by Streamlit from `app/pages/` — it appears in the sidebar nav automatically,
no separate command needed.

## Running the test suite

```bash
pip install -r requirements-dev.txt
pytest tests/
```

29 smoke tests, run in a couple of seconds, no data/secrets setup needed beyond what's already in
the repo. They're scoped to catch a bad code/data edit silently producing **wrong** probability-
of-ruin numbers (a broken portfolio weight, a missing asset-class mapping, a non-deterministic
seed) — not a UI test suite, and not full coverage. Run this after touching anything in `src/`,
`data/portfolio_holdings.csv`, `data/asset_class_map.csv`, or the game's `ASSET_CLASS_INFO`
dict, before pushing — a red test here means a real number is now wrong somewhere in the app,
not a style nitpick.

Requires Python 3.10+ (developed/tested on 3.14; the deployed Cloud runtime's exact version is
unconfirmed — see the **portability gotcha** in [Known gaps](#known-gaps--where-things-were-left-off)
before using very new type-hint syntax anywhere in `app/`).

Every module in `src/` is also independently runnable for a self-test, e.g.
`python src/tax.py` prints a self-test of the tax engine, `python src/engine.py` runs a quick
Monte Carlo comparison across the registered portfolios and prints headline stats.

## What's actually running

Two pages, one Streamlit app, both reading the same `data/` and `src/`:

- **`app/app.py`** — the main comparison tool. Pick any two (or more) registered portfolios,
  compare them across accumulation (growing the pot) and/or decumulation (spending from it).
  Headline metric is probability of ruin; also covers fan charts, legacy distributions, an
  equity allocation sweep, withdrawal-rate/guardrail sensitivity, an asset-class correlation
  heatmap, mortality-adjusted outcomes (single/joint life), UK tax + State Pension
  grossing-up, partial annuitization, and a one-page PDF export for client meetings. Portfolio
  holdings/weights/fees are **data-driven** (`data/portfolio_holdings.csv` etc.), editable
  live in the app's sidebar — adding a brand new competitor portfolio needs no code changes.
- **`app/pages/1_Portfolio_Builder_Game.py`** — a gamified internal version. Players assign
  weights across a curated 16-class menu (`GAME_BUCKETS`, e.g. Global Equities, Berenberg /
  Protected Equities, UK Gilts 15yr+, Hedge Fund Trend — see the module docstring in that file
  for the full list, renames and rationale), in any combination (no cap on how many), each a
  fixed blend of the same underlying series the main app uses with a fixed, visible-but-not-editable
  fee per class. Hit reveal and see the headline probability of ruin plus fund growth (the same
  mix/fee simulated with no withdrawals) and downside-risk stats (Max/Average Drawdown, CVaR — the
  same `downside_stats()` the main app's client summary PDF uses) — plus a shared cross-device
  leaderboard, badges, historical crash-test buttons, and an interactive fan chart of their own
  pot's simulated range. Built for a live company-wide activity, not client use. (The asset-class
  menu has moved back and forth between this curated per-holding list and a more heavily
  consolidated ~7-bucket version across iterations, and earlier versions also had player-set
  per-row fees, capped how many asset classes a player could use, offered a broader "fund store
  categories" mode, and benchmarked players against Mobius Better; all dropped after event
  feedback.)

## How it fits together

```
Raw data (Bloomberg export, FNZ holdings files, Bloomberg Terminal share exports, etc.)
        │
        ▼  one-off extraction scripts (src/extract_*.py, src/build_sonia_cash.py, ...)
data/*.csv   ← the actual source of truth the app reads at runtime
        │
        ▼
src/portfolios.py   (loads portfolio holdings + asset-class map + metadata)
src/engine.py        (Monte Carlo simulation engine: run_simulation, equity_sweep, etc.)
src/mortality.py, src/tax.py, src/cma.py, src/annuity.py   (feature modules engine.py/app.py call into)
        │
        ▼
app/app.py  +  app/pages/1_Portfolio_Builder_Game.py   ← what a user actually sees
```

Everything downstream of `data/*.csv` recomputes live from that data — there are no
hardcoded results baked into the app. To add a new portfolio, asset class, or fund, edit the
relevant CSV (by hand, or via the app's own "Edit data" sidebar tab) rather than touching
`src/portfolios.py`.

## Repository map

| Path | What it is |
|---|---|
| `app/app.py` | The main Streamlit comparison app. |
| `app/pages/` | Additional Streamlit pages (currently just the Portfolio Builder Game) — auto-discovered by Streamlit, no registration needed. |
| `app/requirements.txt` | Exact pinned dependencies for both `app.py` and the game page (same environment). |
| `app/assets/mobius_logo_icon.png` | The real Mobius logo mark, committed to the repo - cropped (transparent background) from `Mobius Brand Identity Overview v01-2.pdf` (page 7). If the brand refreshes, ask marketing (`brand@mobiuslife.co.uk`, per the brand PDF's own contact page) for current master artwork rather than re-cropping from an old PDF. The brand colour palette (`CARBON_BLACK`, `LIGHT_SAGE`, `CLOUD_BLUE`, etc.) is hardcoded as constants at the top of both `app/app.py` and `app/pages/1_Portfolio_Builder_Game.py`, sourced from the same PDF's colour palette page (page 12) - update both files together if the brand refreshes. |
| `src/` | All Python logic — the core simulation engine, plus every one-off data-extraction/report/analysis script used to get here. See the [script inventory](#src-script-inventory) below. |
| `data/` | Cleaned CSVs the app reads at runtime — the actual source of truth. See [Data files](#data-files). |
| `data/equities/` | UK individual-share-level data (Weeks 5-8 thread) and the RAISE/AQR index data used in the equity-swap sensitivity tests. |
| `output/` | Generated deliverables (PDFs, Excel workbooks, Word docs, charts) — **gitignored**, regenerate via the relevant `src/build_*.py` script, not committed. |
| `game_state/` | Runtime state for the Portfolio Builder Game's leaderboard (`leaderboard.csv`) — **gitignored**, resets on every app restart/redeploy. See [Known gaps](#known-gaps--where-things-were-left-off). |
| `.devcontainer/` | Dev Container config, if opening this repo in a container-based IDE. |
| `.claude/launch.json` | Local dev-server shortcuts for Claude Code's browser preview tooling — not relevant to running the app normally. |

## `src/` script inventory

**Core engine — imported directly by the live app(s), this is what you'd actually edit to
change simulation behaviour:**

| File | Purpose |
|---|---|
| `engine.py` | The Monte Carlo simulation engine itself — `run_simulation`, `equity_sweep`, `sensitivity_withdrawal_rate`, `shortfall_heatmap`, `run_glide_path_simulation`, `historical_single_path`, etc. Start here to understand the methodology. |
| `portfolios.py` | Loads portfolio holdings/weights/fees, the asset-class name map, and per-portfolio display metadata from `data/*.csv`. |
| `mortality.py` | Single-life and joint-life survival-probability math (S4 pension-scheme table). |
| `tax.py` | UK income tax (rest-of-UK bands) + State Pension grossing-up. |
| `cma.py` | Forward-looking Capital Market Assumptions blend (historical bootstrap recentred towards published 10-year forecasts). |
| `annuity.py` | Partial annuitization — converting part of the pot into a guaranteed lifetime income. |

**One-off data extraction/prep — run once (or when source data changes) to (re)produce a
`data/*.csv`, not needed again otherwise:**

| File | Produces |
|---|---|
| `extract_data.py` | `data/asset_class_returns.csv`, `data/fund_returns.csv` from the Bloomberg export. |
| `build_sonia_cash.py` | Rebuilds the Cash asset-class series from the real BoE SONIA rate (the Bloomberg file's own cash column was unusable — see [methodology notes](#key-methodology-notes-and-assumptions)). |
| `build_mortality_data.py` | `data/mortality_qx.csv` from the S4 mortality table export. |
| `extract_uk_equity_data.py` | Real UK individual-share return/metadata data (Weeks 5-8 thread) from a Bloomberg Terminal export. |
| `generate_placeholder_equity_data.py` | Synthetic placeholder share data, used before the real Bloomberg Terminal export was available — superseded by `extract_uk_equity_data.py`'s output but kept for reference. |
| `load_raise_index.py` | Converts the real FTSE Russell RAISE index simulation report into `data/equities/raise_index_*.csv`. |
| `migrate_better_v4_into_main_data.py` | One-off migration that wired the validated "Better v4" construction into the main portfolio data. |

**Excel workbook build pipeline — 18 sequential stages that together build
`output/Mobius_Wealth_Decumulation_Model_v4.xlsx`, the fully formula-driven Excel-native
mirror of the app:**

`build_workbook_1.py` through `build_workbook_18.py`, plus `patch_instructions.py` (rewrites
just the workbook's Instructions sheet). Each stage's docstring names exactly what it adds
(Inputs → Portfolios → Returns → Historical Projection → Monte Carlo → Summary → Equity Sweep
→ Sensitivity Tables → Asset Correlation → Mortality → Tax → CMA → Annuity, in that order).
Run them in order from a byte-identical Excel starting point to regenerate the workbook from
scratch, or run the later stages standalone if only patching a specific sheet.

**Report/summary generators — each produces one specific deliverable in `output/`:**

| File | Produces |
|---|---|
| `build_final_summary_pdf.py`, `build_client_appendix_pdf.py` | The one-page client PDF summary + its methodology appendix (same visual format as the app's own PDF export). |
| `build_blue_box_summary.py` | A "blue box" summary workbook. |
| `build_better_v4_summary.py`, `build_recent_window_summary.py` | Standalone summary sheets for specific candidate constructions / time windows. |
| `build_equity_data_spec.py`, `build_equity_literature_review.py`, `build_equity_findings_report.py`, `build_equity_visualiser.py` | Week 5-8 internship deliverables (data extraction spec, literature review, findings writeup, editable Excel companion). |
| `build_raise_equity_swap_report.py`, `build_aqr_convexity_equity_swap_report.py` | One-pagers for Hugh testing specific "swap X% of equity for [strategy]" requests (RAISE, then AQR Convexity Fusion). |

**Individual UK equities thread (internship Weeks 5-8, tasks 13-16) — a separate, less
mature line of work from the main asset-class-level simulator:**

| File | Purpose |
|---|---|
| `equity_income.py` | The individual-share/basket decumulation testing framework itself. |
| `run_equity_income_analysis.py` | Demo/sanity-check runner for the framework above. |

**RAISE / AQR equity-swap sensitivity tests (specific stakeholder requests, not part of the
main app):**

| File | Purpose |
|---|---|
| `run_raise_index_analysis.py` | Tests the real FTSE Russell RAISE index constructions against Better. |
| `sensitivity_raise_in_better.py` | Sensitivity check on the RAISE-in-Better swap. |
| `test_raise_20pct_equity_swap.py`, `test_raise_in_better.py` | Hugh's original RAISE swap requests. |
| `test_aqr_convexity_20pct_equity_swap.py` | Same test, for AQR's Convexity Fusion strategy. |

**Verification:**

| File | Purpose |
|---|---|
| `verify_cma_annuity.py` | Cross-checks the Excel workbook's CMA blend + annuitization figures against the Python engine, to the penny. |

## Data files

- `data/portfolio_holdings.csv`, `data/asset_class_map.csv`, `data/portfolio_meta.csv`,
  `data/asset_class_comparison_groups.csv` — the actual portfolio definitions (holdings,
  weights, fees, asset-class mapping, display names). **Edit these** (by hand or via the app's
  "Edit data" tab) to add/change a portfolio — never hardcode a portfolio in `portfolios.py`.
- `data/asset_class_returns.csv` — monthly total-return series per broad asset class, the
  historical data every simulation bootstraps from.
- `data/fund_returns.csv`, `data/sonia_monthly.csv`, `data/mortality_qx.csv` — supporting
  series (individual fund histories, cash, mortality table).
- `data/equities/` — UK individual-share data (Weeks 5-8 thread) and RAISE/AQR index data
  for the equity-swap sensitivity tests. `*_real.csv` files are genuine Bloomberg Terminal
  exports; files without that suffix may be earlier/placeholder versions — check the
  extraction script's docstring if unsure which is current.

## Adding a new portfolio

**Easiest path — use the app itself**: sidebar → "Edit data" tab → "➕ Add a new portfolio",
then "✏️ Edit portfolio holdings & fees" to add its rows, then click "💾 Save as new default"
to write it back to the CSVs below. This is the recommended way for anyone who isn't
comfortable hand-editing CSVs — it enforces the right shapes for you and the asset-class
dropdown only offers valid options.

**Manual path — edit the CSVs directly** (useful for bulk edits or scripting):

**1. `data/portfolio_holdings.csv`** — one row per holding, columns:

| Column | Format | Notes |
|---|---|---|
| `Portfolio` | short internal key, e.g. `Better` | Every row for the same portfolio must use the exact same string — this is what groups rows together. Must be unique per portfolio (don't reuse an existing key unless you're intentionally replacing it). |
| `Holding` | free text, e.g. `Vanguard FTSE U.K. All Share Index Fund` | Just a label, not used for lookups — can be the fund name or, for asset-class-level portfolios like "Better", the same as the AssetClass. |
| `AssetClass` | must **exactly match** a `Label` in `data/asset_class_map.csv` | Case-sensitive, exact string match. If it doesn't match, the simulation will raise a `KeyError` the first time this portfolio is run — see step 3 below if you need a class that doesn't exist yet. |
| `Weight` | decimal fraction, e.g. `0.015` for 1.5% | **Not** a percentage (`1.5`) and not written as `1.5%`. All of one portfolio's rows should sum to `1.0` — the app carries a mismatched total through as-is with a warning rather than erroring, but a real comparison needs it to actually be 100%. |
| `OCF` | decimal fraction, e.g. `0.0012` for 0.12% pa | Same decimal convention as Weight, not a percentage. |
| `ISIN` | optional, can be blank | Not used by the simulation, just a reference. |

Example new row:
```csv
Portfolio,Holding,AssetClass,Weight,OCF,ISIN
My New Fund,L&G Global Equity Index Fund,Global Equities,0.60,0.0015,GB00XXXXXXXX
My New Fund,Vanguard Global Bond Fund,Global Agg Bonds,0.40,0.0015,
```

**2. `data/portfolio_meta.csv`** — one row per portfolio (optional but recommended; a
portfolio missing here still works, just falls back to plain defaults):

| Column | Format | Notes |
|---|---|---|
| `Portfolio` | must exactly match the `Portfolio` key used in `portfolio_holdings.csv` | |
| `DisplayName` | human-readable name shown in the app/charts/PDF, e.g. `Mobius Better` | Falls back to the internal `Portfolio` key if omitted. |
| `Owner` | `Mobius` or `Competitor` | Drives colour-coding throughout the app. Falls back to `Competitor` if omitted. |
| `Provider` | fund house name, e.g. `Legal & General` | Used in section captions and the PDF export. Falls back to `DisplayName` if omitted. |

**3. Only if introducing a genuinely new asset class** (not just a new portfolio built from
existing classes): you need real historical return data for it before it can be used at all.

  a. Add a monthly total-return column (decimal fractions, e.g. `0.0123` = 1.23%, indexed by
     month-end date) to `data/asset_class_returns.csv` — as long a history as possible, ideally
     covering the same 1999/2000–2026 window as everything else, though a shorter series still
     works (it just narrows the usable overlap window for any portfolio holding it — see the
     "Better" portfolio's own 2001-2025 window as an example of this trade-off).
  b. Add a row to `data/asset_class_map.csv`: `Label` (the friendly name you'll use in
     `AssetClass` above) → `BloombergColumn` (must exactly match the new column header from
     step (a)).
  c. Optional: add a row to `data/asset_class_comparison_groups.csv` (`AssetClass` →
     `ComparisonGroup`) if you want it rolled up into an existing like-for-like comparison
     bucket rather than standing alone as its own group.

## Persistent leaderboard setup (Google Sheets)

The Portfolio Builder Game's leaderboard code (`app/pages/1_Portfolio_Builder_Game.py`)
supports two backends: a local CSV file (`game_state/leaderboard.csv` — the default, zero
setup, but resets on every Streamlit Cloud restart/redeploy and isn't safe under genuinely
simultaneous writes) or a Google Sheet (persistent, shared, and each write is a single atomic
API call so concurrent submissions from different teams don't clobber each other). It
automatically uses Google Sheets if credentials are configured, and silently falls back to the
local file otherwise — check which one is active via the small status caption next to "Game
setup" and above the leaderboard table itself (🟢 Google Sheets / 🟡 Local file only).

**One-time setup to switch it on:**

1. **Create a Google Cloud service account**: in the [Google Cloud Console](https://console.cloud.google.com/),
   create (or reuse) a project, enable the **Google Sheets API**, then go to
   *IAM & Admin → Service Accounts → Create Service Account*. Give it any name (e.g.
   `mobius-game-leaderboard`) — no special roles needed.
2. Open the new service account → *Keys → Add Key → Create new key → JSON*. This downloads a
   JSON credentials file — keep it private, don't commit it to the repo.
3. **Create a Google Sheet** for the leaderboard (any name), then **share it** with the service
   account's email address (found in the JSON file as `client_email`, looks like
   `...@...iam.gserviceaccount.com`) with **Editor** access.
4. Note the Sheet's ID from its URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
5. In Streamlit Cloud, open this app's *Settings → Secrets* and paste (filling in your own
   values from the downloaded JSON, and the Sheet ID from step 4):

   ```toml
   leaderboard_sheet_key = "the-sheet-id-from-step-4"

   [gcp_service_account]
   type = "service_account"
   project_id = "..."
   private_key_id = "..."
   private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
   client_email = "...@....iam.gserviceaccount.com"
   client_id = "..."
   auth_uri = "https://accounts.google.com/o/oauth2/auth"
   token_uri = "https://oauth2.googleapis.com/token"
   auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
   client_x509_cert_url = "..."
   ```

   (Every field above except `leaderboard_sheet_key` comes straight from the downloaded JSON
   file — copy each value across as-is, keeping the `\n` line breaks literal inside
   `private_key`.)
6. Save — Streamlit Cloud restarts the app automatically. The status caption should now read
   🟢 Google Sheets.

For local development, the same secrets can go in `.streamlit/secrets.toml` — already covered
by this repo's `.gitignore`, since that file would contain a real private key.

## Host controls (running a live multi-group session)

*For a practical, non-technical "what do I click" guide to actually running a live session, see
[HOST_GUIDE.md](HOST_GUIDE.md) instead — this section explains how the feature works.*

When 20-30 groups play at once, they should all be playing the **same scenario** (same age,
horizon, pot, spend) rather than each picking their own. The "⚙️ Game setup
(host controls)" expander at the top of the game page handles this:

- **By default, everyone sees a read-only summary** of the current scenario (age, horizon, pot,
  spend) plus a caption showing who last published it and when. They
  cannot edit it.
- **The session host enters a PIN** in the same expander to unlock editable controls, sets the
  scenario, and clicks **"📡 Publish to all groups"**. Every other open tab/device picks up the
  new values automatically on their next interaction (slider drag, button click, etc.) — no
  refresh needed, and nothing for players to configure.
- The PIN defaults to `mobius2026` if not overridden. **Set a real one before a live session** by
  adding `host_pin = "your-pin-here"` to the same Streamlit Cloud secrets block described above
  (or `.streamlit/secrets.toml` locally) — otherwise anyone could log in as host and change the
  scenario for everyone mid-session.
- "🗑️ Clear leaderboard" also moved inside host mode, so a random player can't wipe everyone's
  scores.
- **Nobody gets the actual portfolio builder until a scenario has been published** — before
  that, every device (host included) just sees the "how to play" content and a waiting screen.
  Non-host players get a manual **"🔄 Check again"** button (deliberately no auto-refresh; an
  earlier attempt at one fought Streamlit's own reconnect cycle badly enough to reload every
  couple of seconds — see the git history around "Drop the auto-reload"). The host instead sees
  a "you're in host mode, hit Publish when ready" message, since it's their own tab and they
  don't need prompting to check for something they control.

**Suspense mode - scores stay hidden until the host reveals them.** Submitting a portfolio
("🔒 Lock in my portfolio") scores it and puts it on the leaderboard right away, but nobody sees
probability of ruin, the crash-test buttons, comparisons, or the leaderboard's numbers yet - every
player instead sees a "🤐 Portfolio locked in! Sit tight..." placeholder, and the stats banner and
leaderboard table only show submission counts, not scores. Only the host can reveal: the same
Leaderboard section gets a **"🎉 Reveal the winner to everyone"** button (host-only), which flips
one shared flag. The moment that flag flips, every player's own already-computed result unlocks
automatically on their next click/rerun - no per-player action needed, and each session gets a
one-time drumroll + confetti moment the first time it notices the reveal. The host can
**"🔒 Hide scores again"** to run another round without a full leaderboard clear; clearing the
leaderboard also re-hides scores automatically for a clean new game.

**How it works / its one real limitation**: the shared scenario (and the reveal flag) is held in
a single in-memory Python object (`st.cache_resource`, in `_host_state()`), which every session on the same server
process reads and writes — that's what makes the "publish once, everyone sees it instantly" bit
work with zero extra setup. This is correct for a normal Streamlit Community Cloud deployment
(one app = one process), but it means the published scenario **resets to the defaults on every
app restart/redeploy** (same caveat as the CSV leaderboard fallback) and **would silently stop
broadcasting to everyone if the app were ever scaled across multiple replicas** — each replica
would keep its own copy. Neither applies to the current single-instance Streamlit Cloud setup;
worth knowing if that ever changes. If it does, move this to the same Google Sheets backend the
leaderboard already uses (a small "current scenario" row instead of an append-only log).

## Key methodology notes and assumptions

Please read before relying on any of this for client-facing output — several of these are
judgement calls, not settled facts.

- **Cash uses the real Bank of England SONIA rate**, not the Bloomberg file's own cash column
  (which produced nonsensical ±100-300% monthly swings). Built from BoE's IUDSOIA series
  (2014+), spliced with the Blackrock ICS Sterling Liquidity Fund series pre-2014 (99.7%
  correlated over the overlap period) — see `build_sonia_cash.py`.
- **Every holding maps to a broad asset-class index**, not its own fund-level history — most
  individual fund series in the source data are too short (some ~20 months) for a 25+ year
  Monte Carlo. See `portfolios.py` for the exact mapping.
- **"Original" vs "Alternative" is mainly a fee story** — the source data shows these largely
  hold the *same* underlying index exposure; the difference being compared is cost, not
  market return.
- **"Better" portfolio weights were tuned empirically**, not derived from a single source of
  truth — REITs/Infrastructure in this data run ~0.75-0.76 correlated with global equities,
  so they diversify less than their labels suggest; real diversification comes mostly from
  bonds/credit. One reasonable construction, not the only one — sanity-check before relying
  on it.
- **Guardrails are typically a bigger lever on ruin probability than portfolio choice** —
  worth leading with in any client narrative.
- **Mortality** uses the S4 pension-scheme table (lighter than general-population tables, the
  standard choice for pension decumulation work), assumes independence from market returns,
  and — for joint life — independence between the two lives of a couple (a known
  simplification; real couples' deaths correlate somewhat via shared lifestyle factors).
- **Tax + State Pension** modelling is deliberately simplified: whole pot treated as a single
  taxable pension wrapper (no 25% pension-commencement lump sum, no ISA/GIA wrapper split),
  rest-of-UK tax bands only, both held in today's money for the whole horizon. State Pension
  defaults to the full new State Pension — a real client's entitlement varies by NI record.
- **Historical window is short** (1999/2000-2026, ~26 years) relative to a 30-year
  decumulation horizon, and happens to span an unusually strong run for global equities —
  treat any bootstrap of this window as illustrative, not predictive.
- **Forward-looking CMA figures** are a compiled median across third-party published
  forecasts (via Monevator: Vanguard/Schroders/JPMorgan/BlackRock etc.), not an in-house
  view. Three asset classes have no direct published match and are proxied from the closest
  category — see `cma.py`.
- **Annuity rates** are scaled examples from Hargreaves Lansdown's published best-buy table
  (May 2026), not a personalised quote — real quotes vary by provider, postcode and health.

## Known gaps / where things were left off

- **Portfolio Builder Game leaderboard defaults to a CSV file on Streamlit Cloud's ephemeral
  disk** (`game_state/leaderboard.csv`) unless Google Sheets credentials are configured — see
  [Persistent leaderboard setup](#persistent-leaderboard-setup-google-sheets) below. The code
  supports both (falls back to the CSV automatically if Sheets isn't configured or the API
  call fails for any reason), but **the actual Google Cloud setup steps still need to be done
  once** for real persistence — check whether that's been completed before relying on the
  leaderboard surviving a redeploy. This matters more than it sounds for a live session: the
  CSV fallback reads the whole file, appends a row, and rewrites it (not a single atomic write),
  so if two of 20-30 groups hit "reveal" in the same instant, one write can silently overwrite
  the other. The Google Sheets path doesn't have this problem (`append_row` is a single atomic
  API call) — **confirm Sheets is actually configured before any session with more than a
  handful of simultaneous groups.**
- **Not load-tested at 20-30 concurrent groups.** Streamlit Community Cloud runs the app as a
  single process/single CPU core; each reveal runs a 2,000-path Monte Carlo simulation
  (`run_simulation`, `method="stationary_block"`), which is real CPU work, not I/O-bound — if
  many groups hit "reveal" in the same few seconds, requests queue on that one core rather than
  running in parallel, so the last group to click could see a multi-second-plus wait rather than
  a crash. Worth a dry run beforehand with several people hitting reveal at once to see the
  actual delay, and warning the host it's expected rather than a bug. The Google Sheets API also
  has its own per-minute write/read quotas (generous for occasional reveals; not something 30
  people rapid-clicking "reveal" over and over would necessarily stay under) — the code already
  falls back to the local CSV silently if any Sheets call fails, so a quota hit degrades rather
  than crashes, but scores written during a fallback window won't merge back into Sheets later.
- **Host PIN defaults to a hardcoded value (`mobius2026`) if not set.** See
  [Host controls](#host-controls-running-a-live-multi-group-session) — set a real `host_pin` in
  secrets before a live session, otherwise any player could log in as host and change the
  scenario for everyone mid-game.
- **Individual UK equities work (internship Weeks 5-8)** is a separate, less mature thread
  from the main simulator — `equity_income.py` + the AQR/RAISE swap tests are real, working
  code, but this hasn't been folded into the main app's UI at all; it's currently
  script/notebook-style analysis only.
- **Portability gotcha**: `app/pages/1_Portfolio_Builder_Game.py` briefly broke the entire
  deployed app (not just that page) because a module-level type annotation used PEP 604/585
  syntax (`dict[str, list[str] | None]`) that needs Python 3.10+, and Streamlit Cloud's exact
  Python version wasn't confirmed. Fixed with `from __future__ import annotations` at the top
  of that file. **If you add a new page to `app/pages/`, either add that same import or avoid
  bleeding-edge type-hint syntax** — Streamlit appears to touch every page file when building
  the sidebar nav, so a syntax/import error in ANY page can crash the whole app, not just that
  page.
- **`tests/` covers the core math as a smoke suite, not full coverage.** 32 tests across
  `test_engine.py` (simulation shapes/determinism/directional sanity), `test_portfolios.py`
  (every portfolio's weights sum to 1, fees are plausible, every holding maps to real data), and
  `test_game_config.py` (the game's `GAME_BUCKETS` config — component series, fees and weights —
  checked via `ast.literal_eval` rather than importing the Streamlit page directly). Deliberately scoped to
  catch a bad data/code edit silently producing wrong probability-of-ruin numbers — not UI
  testing, not full coverage. See [Running the test suite](#running-the-test-suite) below. Every
  module is also independently runnable for a manual self-test (`python src/tax.py` etc.), and
  there's a handful of verification scripts (`verify_cma_annuity.py`).

## Deployment

Streamlit Community Cloud, connected to this repo's `master` branch — **any push to `master`
auto-redeploys within roughly a minute**. No manual deploy step. If the deployed app shows
"Oh no. Error running app.":

1. Go to [share.streamlit.io](https://share.streamlit.io), sign in, find this app in the
   dashboard (not the public app URL — the "Manage app" button on a crashed app page is
   unreliable).
2. Open its management page — there's a terminal/log panel showing the real Python traceback.
3. A crashed app sometimes needs a manual **Reboot** even after the underlying bug is fixed
   and pushed — it doesn't always retry automatically.

## Suggested next steps

- Confirm the Original-portfolio OCF assumptions against real fund factsheets.
- Sanity-check / adjust the "Better" portfolio weights with the team.
- Tax refinements: 25% pension-commencement lump sum, real ISA/GIA wrapper split, let the
  client enter their own State Pension forecast, consider Scottish tax bands.
- Annuity refinements: 25% lump sum before annuitizing, a fuller joint-life rate curve,
  enhanced/impaired-life rates.
- Fold the individual UK equities work (Weeks 5-8) into the main app's UI, if that thread is
  continuing.
- If the Portfolio Builder Game becomes a recurring company-wide activity, replace its CSV
  leaderboard with a real shared store (see [Known gaps](#known-gaps--where-things-were-left-off)).
- Turn this from an internship deliverable into a scalable practice-management product:
  branded client-facing reports generated straight from a saved scenario, a "batch mode" to
  re-run a whole book of clients against updated market data, a live FNZ data feed instead of
  a point-in-time Bloomberg pull, and an audit trail of what assumptions were live when a
  piece of advice was given (useful for Consumer Duty record-keeping).
