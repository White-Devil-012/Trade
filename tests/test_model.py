"""Forecast honesty: the model must admit when it has nothing."""

import numpy as np
import pandas as pd
import pytest

from trendlab import model
from trendlab.config import ModelConfig
from trendlab.data import SyntheticProvider
from trendlab.indicators import enrich

FAST = ModelConfig(horizon_days=5, min_train_bars=400, retrain_every=120, n_estimators=40)


@pytest.fixture(scope="module")
def frame():
    return enrich(SyntheticProvider().history("MODEL", period="5y"))


def test_reports_no_edge_on_random_data(frame):
    """Synthetic bars are a random walk. Claiming an edge here would be a bug.

    This is the package's central honesty check: if walk-forward validation ever
    finds a large edge on data with no signal in it, the validation is leaking.
    """
    _, _, ev = model.walk_forward(frame, FAST)
    assert ev.n_predictions > 100
    assert abs(ev.edge) < 0.15, "implausible edge on a random walk - suspect leakage"
    assert 0.35 < ev.auc < 0.65, f"AUC {ev.auc} is too far from chance on random data"


def test_evaluation_requires_both_accuracy_and_calibration(frame):
    _, _, ev = model.walk_forward(frame, FAST)
    if ev.has_edge:
        # The gate is deliberately conjunctive: beating the base rate is not
        # enough if the probabilities are worse than quoting the base rate.
        assert ev.edge >= FAST.min_edge_over_baseline
        assert ev.brier < ev.baseline_brier
        assert ev.auc > 0.52


def test_forecast_price_range_is_monotonic(frame):
    fc = model.forecast(frame, FAST)
    pr = fc.price_range
    ordered = [pr["low"], pr["likely_low"], pr["mid"], pr["likely_high"], pr["high"]]
    assert ordered == sorted(ordered), f"range is not ordered: {ordered}"


def test_forecast_quantiles_are_ordered(frame):
    q = model.forecast(frame, FAST).return_quantiles
    assert q["p05"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p95"]


def test_probability_is_a_probability(frame):
    fc = model.forecast(frame, FAST)
    assert 0.0 <= fc.probability_up <= 1.0


def test_no_edge_forces_a_caveat(frame):
    fc = model.forecast(frame, FAST)
    if not fc.evaluation.has_edge:
        assert "no reliable out-of-sample edge" in fc.caveat
        assert fc.confidence.startswith("none")


def test_short_history_refuses_rather_than_guessing(frame):
    with pytest.raises(ValueError, match="walk-forward"):
        model.walk_forward(frame.iloc[:200], FAST)


def test_weighted_quantile_matches_unweighted_case():
    values = np.arange(0.0, 101.0)
    weights = np.ones_like(values)
    assert model._weighted_quantile(values, weights, 0.5) == pytest.approx(50.0, abs=0.6)


def test_mixture_shifts_with_probability():
    up = np.array([0.05, 0.06, 0.07])
    down = np.array([-0.05, -0.06, -0.07])
    bullish, w_bull = model._mixture(up, down, 0.9)
    bearish, w_bear = model._mixture(up, down, 0.1)
    assert np.average(bullish, weights=w_bull) > np.average(bearish, weights=w_bear)
