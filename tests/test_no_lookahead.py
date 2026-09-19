"""Lookahead guards.

These are the tests worth having. Lookahead bias is silent: it does not raise,
it does not produce NaNs, it just makes a strategy look profitable in a way it
can never be live. Each test below checks a specific way it could creep in.
"""

import numpy as np
import pandas as pd
import pytest

from trendlab import backtest, features
from trendlab.config import BacktestConfig, ModelConfig
from trendlab.data import SyntheticProvider
from trendlab.indicators import enrich


def _frame(closes) -> pd.DataFrame:
    """Build a minimal OHLCV frame from a list of closes.

    Uses a plain array rather than a pre-indexed Series on purpose: handing
    pandas a Series with a mismatched index silently aligns it to all-NaN.
    """
    values = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"open": values, "high": values, "low": values, "close": values,
         "volume": np.ones(len(values))},
        index=pd.bdate_range("2024-01-01", periods=len(values)),
    )


@pytest.fixture(scope="module")
def frame():
    return enrich(SyntheticProvider().history("LOOKAHEAD", period="5y"))


def test_features_do_not_change_when_future_arrives(frame):
    """A feature row computed today must be identical to the row computed later."""
    full = features.build(frame)
    partial = features.build(frame.iloc[:-60])
    overlap = partial.dropna().index
    assert len(overlap) > 200
    pd.testing.assert_frame_equal(full.loc[overlap], partial.loc[overlap])


def test_target_is_strictly_forward(frame):
    """The label must describe the future, and must be absent at the very end."""
    horizon = 5
    y = features.target(frame, horizon)
    close = frame["close"]
    assert y.iloc[0] == pytest.approx(close.iloc[horizon] / close.iloc[0] - 1.0)
    # The final `horizon` bars cannot have a label yet.
    assert y.iloc[-horizon:].isna().all()
    assert not y.iloc[:-horizon].isna().any()


def test_dataset_drops_unlabeled_tail(frame):
    horizon = 10
    X, y_ret, y_up = features.build_dataset(frame, horizon)
    assert len(X) == len(y_ret) == len(y_up)
    # Nothing in the training set may sit inside the unlabeled tail.
    assert X.index.max() <= frame.index[-(horizon + 1)]


def test_backtest_applies_execution_lag():
    """A signal known on bar t must not earn bar t's return."""
    # Flat, then one explosive day. A strategy told to go long *on* the spike day
    # only earns it if the backtester is cheating.
    f = _frame([100.0, 100.0, 100.0, 200.0, 200.0])

    # Position decided on the spike bar itself (index 3).
    late = pd.Series([0.0, 0.0, 0.0, 1.0, 1.0], index=f.index)
    result = backtest.run(f, late, BacktestConfig(commission_bps=0, slippage_bps=0))
    # It enters on bar 4 and earns bar 4's return (0%), missing the +100%.
    assert result.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)

    # Position decided the bar *before* the spike does capture it.
    early = pd.Series([0.0, 0.0, 1.0, 1.0, 1.0], index=f.index)
    result_early = backtest.run(f, early, BacktestConfig(commission_bps=0, slippage_bps=0))
    assert result_early.metrics["total_return"] == pytest.approx(1.0, abs=1e-9)


def test_backtest_charges_costs_on_turnover():
    f = _frame([100.0] * 6)
    flipping = pd.Series([1.0, 0.0, 1.0, 0.0, 1.0, 0.0], index=f.index)
    cfg = BacktestConfig(commission_bps=10, slippage_bps=10)
    result = backtest.run(f, flipping, cfg)
    # Price never moves, so every loss is friction.
    assert result.metrics["total_return"] < 0
    assert result.trades >= 4


def test_zero_position_is_flat():
    f = _frame(np.linspace(100, 200, 50))
    result = backtest.run(f, pd.Series(0.0, index=f.index))
    assert result.metrics["total_return"] == pytest.approx(0.0)
    # Buy & hold over the same window doubles, so the benchmark is unaffected.
    assert result.benchmark_metrics["total_return"] > 0.9


def test_shorts_are_blocked_unless_enabled():
    f = _frame(np.linspace(100, 50, 30))
    short = pd.Series(-1.0, index=f.index)
    blocked = backtest.run(f, short, BacktestConfig(allow_short=False))
    assert (blocked.position >= 0).all()
    allowed = backtest.run(f, short, BacktestConfig(allow_short=True, commission_bps=0, slippage_bps=0))
    assert allowed.metrics["total_return"] > 0  # shorting a falling series pays


def test_walk_forward_never_trains_on_the_future(frame, monkeypatch):
    """Assert directly on the slices handed to fit() during walk-forward.

    Every training block must end strictly before its test block begins, with
    a purge gap of at least `horizon` bars so no training label overlaps it.
    """
    from trendlab import model

    cfg = ModelConfig(horizon_days=5, min_train_bars=400, retrain_every=100, n_estimators=10)
    seen = []
    original_fit = model.HistGradientBoostingClassifier.fit

    def spy_fit(self, X, y, *args, **kwargs):
        seen.append(X.index.max())
        return original_fit(self, X, y, *args, **kwargs)

    monkeypatch.setattr(model.HistGradientBoostingClassifier, "fit", spy_fit)
    probs, truth, _ = model.walk_forward(frame, cfg)

    assert len(seen) >= 2
    X, _, _ = features.build_dataset(frame, cfg.horizon_days)
    positions = {ts: i for i, ts in enumerate(X.index)}

    # Pair each fit with the first prediction it produced, in order.
    predicted_positions = sorted(positions[ts] for ts in probs.index)
    block_starts = predicted_positions[:: cfg.retrain_every]
    for train_max, test_start in zip(seen, block_starts):
        assert positions[train_max] <= test_start - cfg.horizon_days, (
            "training data reached into the test block"
        )
