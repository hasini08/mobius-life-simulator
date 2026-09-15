"""
Smoke tests for the static config in app/pages/1_Portfolio_Builder_Game.py - specifically
GAME_BUCKETS, the consolidated asset-class menu the game shows instead of the main app's full
~26-series list. Each bucket is a fixed blend of the underlying return series (portfolios.AC)
plus a fixed fee assumption, so the easiest way to quietly break the game is to wire a bucket to
a series name the data doesn't have, or to sub-weights that don't add up.

The game file is a Streamlit PAGE script (executes top-level UI code on import, e.g. st.markdown()
calls that need a real Streamlit runtime) - rather than importing it directly, its config dict is
extracted via ast.literal_eval. This is deliberately read-only static analysis, not a run of the
actual page. (The page itself also asserts these same invariants at load time.)

Run with: pytest tests/ (from the repo root, after `pip install -r requirements-dev.txt`)
"""
import ast
from pathlib import Path

import pytest

from portfolios import AC

GAME_FILE = Path(__file__).resolve().parent.parent / "app" / "pages" / "1_Portfolio_Builder_Game.py"


def _extract_module_level_dict(tree: ast.Module, name: str) -> dict:
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        if target == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"module-level dict {name!r} not found in {GAME_FILE.name}")


@pytest.fixture(scope="module")
def game_tree():
    return ast.parse(GAME_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def game_buckets(game_tree):
    return _extract_module_level_dict(game_tree, "GAME_BUCKETS")


def test_there_is_a_curated_handful_of_buckets(game_buckets):
    """A sanity bound, not a design target - the list has moved between a heavily consolidated
    ~7-bucket menu and a more granular curated one (currently 17, one per named holding) across
    iterations; this just catches it silently creeping all the way back up to the main app's full
    ~26-series list (portfolios.AC), which would defeat the point of curating a menu at all."""
    assert 3 <= len(game_buckets) <= 20, f"expected a curated bucket menu, got {len(game_buckets)}"


def test_every_bucket_is_well_formed(game_buckets):
    valid_tiers = ("🟢", "🟡", "🔴")
    for name, cfg in game_buckets.items():
        assert set(cfg) >= {"series", "fee", "risk", "blurb"}, f"{name!r}: missing a required key"
        assert isinstance(cfg["series"], dict) and cfg["series"], f"{name!r}: 'series' must be a non-empty dict"
        assert cfg["risk"].startswith(valid_tiers), f"{name!r}: risk {cfg['risk']!r} must start with 🟢/🟡/🔴"
        assert isinstance(cfg["blurb"], str) and len(cfg["blurb"]) > 20, f"{name!r}: blurb looks too short"


def test_every_bucket_component_series_is_real_data(game_buckets):
    for name, cfg in game_buckets.items():
        unknown = [s for s in cfg["series"] if s not in AC]
        assert not unknown, f"{name!r}: component series not in portfolios.AC: {unknown}"


def test_every_bucket_component_weights_sum_to_one(game_buckets):
    for name, cfg in game_buckets.items():
        total = sum(cfg["series"].values())
        assert abs(total - 1.0) < 1e-9, f"{name!r}: component series weights sum to {total}, not 1.0"


def test_every_bucket_fee_is_a_plausible_fraction(game_buckets):
    """Fees are stored as a decimal fraction (0.0012 == 0.12% pa), not a percent - a value above
    a couple of percent almost certainly means someone wrote 1.2 meaning 1.2%."""
    for name, cfg in game_buckets.items():
        assert 0.0 <= cfg["fee"] < 0.02, f"{name!r}: fee {cfg['fee']} doesn't look like a decimal fraction"
