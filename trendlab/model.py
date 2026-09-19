"""Walk-forward probabilistic forecasting.

What this does: estimate P(price is higher in ``horizon`` sessions) and the
distribution of plausible moves, using only information available at the time
of each prediction.

What this refuses to do: print a single confident price target. The honest
output of a model like this is a probability and a range, plus the evidence
that tells you whether to believe either one.

Validation is walk-forward — train on the past, predict the next block, roll
forward, never look back. A single shuffled train/test split on time-series data
leaks the future into the past and inflates every metric it reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance

from .config import ModelConfig
from .features import FEATURE_COLUMNS, build_dataset, latest_features


@dataclass
class Evaluation:
    """Out-of-sample scorecard. ``edge`` is the number that matters."""

    n_predictions: int
    accuracy: float
    baseline_accuracy: float   # always predicting the majority class
    edge: float                # accuracy - baseline_accuracy
    auc: float
    brier: float
    baseline_brier: float
    log_loss_value: float
    calibration: list[dict[str, float]]
    has_edge: bool
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Forecast:
    horizon_days: int
    probability_up: float
    confidence: str
    expected_return: float
    return_quantiles: dict[str, float]
    price_range: dict[str, float]
    last_close: float
    feature_importance: list[dict[str, Any]] = field(default_factory=list)
    evaluation: Evaluation | None = None
    caveat: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = self.__dict__.copy()
        out["evaluation"] = self.evaluation.to_dict() if self.evaluation else None
        return out


def _make_model(cfg: ModelConfig) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=cfg.n_estimators,
        max_depth=cfg.max_depth,
        learning_rate=cfg.learning_rate,
        random_state=cfg.random_state,
        early_stopping=False,
    )


def _calibration_table(y_true: np.ndarray, probs: np.ndarray, bins: int = 5) -> list[dict[str, float]]:
    """Do the stated probabilities match observed frequencies?

    A model that says '70%' should be right about 70% of the time. When the
    predicted and actual columns diverge badly, the probability is decoration.
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    table: list[dict[str, float]] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & (probs < hi if hi < 1.0 else probs <= hi)
        if mask.sum() < 5:  # too few samples to say anything
            continue
        table.append({
            "bucket": f"{lo:.0%}-{hi:.0%}",
            "predicted": round(float(probs[mask].mean()), 4),
            "actual": round(float(y_true[mask].mean()), 4),
            "count": int(mask.sum()),
        })
    return table


def walk_forward(
    frame: pd.DataFrame, cfg: ModelConfig | None = None
) -> tuple[pd.Series, pd.Series, Evaluation]:
    """Roll a model through history, predicting only forward.

    Returns (out-of-sample probabilities, realized labels, evaluation).
    """
    cfg = cfg or ModelConfig()
    X, y_ret, y_up = build_dataset(frame, cfg.horizon_days)

    if len(X) < cfg.min_train_bars + cfg.retrain_every:
        raise ValueError(
            f"need at least {cfg.min_train_bars + cfg.retrain_every} usable bars for "
            f"walk-forward validation, got {len(X)}. Request a longer history."
        )

    labels = y_up.to_numpy()
    oos_prob = np.full(len(X), np.nan)

    start = cfg.min_train_bars
    while start < len(X):
        stop = min(start + cfg.retrain_every, len(X))
        # Purge the bars whose labels overlap the test block: their forward
        # return is partly realized inside the test window, which would leak.
        train_end = start - cfg.horizon_days
        if train_end < 50:
            start = stop
            continue

        model = _make_model(cfg)
        model.fit(X.iloc[:train_end], y_up.iloc[:train_end])
        oos_prob[start:stop] = model.predict_proba(X.iloc[start:stop])[:, 1]
        start = stop

    mask = ~np.isnan(oos_prob)
    probs, truth = oos_prob[mask], labels[mask]
    index = X.index[mask]

    evaluation = _evaluate(truth, probs, cfg)
    return (
        pd.Series(probs, index=index, name="prob_up"),
        pd.Series(truth, index=index, name="actual_up"),
        evaluation,
    )


def _evaluate(truth: np.ndarray, probs: np.ndarray, cfg: ModelConfig) -> Evaluation:
    from sklearn.metrics import log_loss, roc_auc_score

    predictions = (probs >= 0.5).astype(int)
    accuracy = float((predictions == truth).mean())

    # The bar to clear: always guessing whichever class is more common.
    base_rate = float(truth.mean())
    baseline_accuracy = max(base_rate, 1 - base_rate)

    try:
        auc = float(roc_auc_score(truth, probs))
    except ValueError:  # only one class present
        auc = 0.5

    brier = float(np.mean((probs - truth) ** 2))
    baseline_brier = float(np.mean((base_rate - truth) ** 2))
    try:
        ll = float(log_loss(truth, probs, labels=[0, 1]))
    except ValueError:
        ll = float("nan")

    edge = accuracy - baseline_accuracy
    # Two independent checks: it must out-guess the base rate *and* its
    # probabilities must be sharper than a constant base-rate forecast.
    has_edge = bool(edge >= cfg.min_edge_over_baseline and brier < baseline_brier and auc > 0.52)

    if has_edge:
        verdict = (
            f"Beats the base rate by {edge:.1%} out-of-sample over {len(truth)} predictions. "
            f"Modest but measurable — size positions accordingly."
        )
    elif edge > 0:
        verdict = (
            f"Only {edge:.1%} above the base rate — inside the noise band for "
            f"{len(truth)} predictions. Treat the probability as uninformative."
        )
    else:
        verdict = (
            f"No edge: {accuracy:.1%} accuracy against a {baseline_accuracy:.1%} base rate. "
            f"The model is not finding a signal in this symbol. Use the regime and "
            f"backtest panels instead."
        )

    return Evaluation(
        n_predictions=int(len(truth)),
        accuracy=round(accuracy, 4),
        baseline_accuracy=round(baseline_accuracy, 4),
        edge=round(edge, 4),
        auc=round(auc, 4),
        brier=round(brier, 4),
        baseline_brier=round(baseline_brier, 4),
        log_loss_value=round(ll, 4),
        calibration=_calibration_table(truth, probs),
        has_edge=has_edge,
        verdict=verdict,
    )


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Quantile of a weighted empirical distribution."""
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    # Midpoint cumulative weights: the standard convention for weighted quantiles.
    cumulative = (np.cumsum(weights) - 0.5 * weights) / weights.sum()
    return float(np.interp(q, cumulative, values))


def _mixture(
    up_moves: np.ndarray, down_moves: np.ndarray, prob_up: float
) -> tuple[np.ndarray, np.ndarray]:
    """Weight the two historical move pools by the forecast probability."""
    if len(up_moves) == 0 or len(down_moves) == 0:
        pool = up_moves if len(up_moves) else down_moves
        return pool, np.ones(len(pool))
    samples = np.concatenate([up_moves, down_moves])
    weights = np.concatenate([
        np.full(len(up_moves), prob_up / len(up_moves)),
        np.full(len(down_moves), (1 - prob_up) / len(down_moves)),
    ])
    return samples, weights


def _confidence_label(prob: float, has_edge: bool) -> str:
    if not has_edge:
        return "none - model showed no out-of-sample edge"
    distance = abs(prob - 0.5)
    if distance >= 0.15:
        return "high"
    if distance >= 0.07:
        return "moderate"
    return "low"


def _importance(model, X_train: pd.DataFrame, y_train: pd.Series, cfg: ModelConfig, top: int = 8):
    """Permutation importance on held-out-ish recent data, for explainability."""
    tail = min(250, len(X_train) // 4)
    if tail < 30:
        return []
    try:
        result = permutation_importance(
            model, X_train.iloc[-tail:], y_train.iloc[-tail:],
            n_repeats=5, random_state=cfg.random_state, scoring="accuracy",
        )
    except Exception:
        return []
    order = np.argsort(result.importances_mean)[::-1][:top]
    return [
        {"feature": FEATURE_COLUMNS[i], "importance": round(float(result.importances_mean[i]), 4)}
        for i in order if result.importances_mean[i] > 0
    ]


def forecast(frame: pd.DataFrame, cfg: ModelConfig | None = None) -> Forecast:
    """Produce a live forecast for the latest bar, with its own report card."""
    cfg = cfg or ModelConfig()
    probs, truth, evaluation = walk_forward(frame, cfg)

    X, y_ret, y_up = build_dataset(frame, cfg.horizon_days)
    model = _make_model(cfg)
    model.fit(X, y_up)

    live_row = latest_features(frame)
    if live_row is None:
        raise ValueError("not enough history to build a current feature row")

    prob_up = float(model.predict_proba(live_row)[0, 1])

    # The move distribution is a probability-weighted mixture of the historical
    # up-moves and down-moves. Drawing quantiles and the mean from that single
    # mixture keeps them mutually consistent -- mixing a mean from one
    # distribution with quantiles from another can place the centre outside its
    # own interquartile range.
    up_moves = y_ret[y_up == 1].to_numpy()
    down_moves = y_ret[y_up == 0].to_numpy()
    samples, weights = _mixture(up_moves, down_moves, prob_up)

    quantiles = {
        f"p{int(q * 100):02d}": round(_weighted_quantile(samples, weights, q), 4)
        for q in (0.05, 0.25, 0.50, 0.75, 0.95)
    }
    expected_return = float(np.average(samples, weights=weights))

    last_close = float(frame["close"].iloc[-1])
    price_range = {
        "low": round(last_close * (1 + quantiles["p05"]), 2),
        "likely_low": round(last_close * (1 + quantiles["p25"]), 2),
        "mid": round(last_close * (1 + quantiles["p50"]), 2),
        "likely_high": round(last_close * (1 + quantiles["p75"]), 2),
        "high": round(last_close * (1 + quantiles["p95"]), 2),
    }

    caveat = (
        "This is a probability over a distribution of outcomes, not a price target. "
        "The p05-p95 band is where the move landed 90% of the time historically; "
        "it says nothing about the 10% of the time it did not."
    )
    if not evaluation.has_edge:
        caveat = (
            "The model showed no reliable out-of-sample edge on this symbol, so this "
            "probability carries no information. " + caveat
        )

    return Forecast(
        horizon_days=cfg.horizon_days,
        probability_up=round(prob_up, 4),
        confidence=_confidence_label(prob_up, evaluation.has_edge),
        expected_return=round(expected_return, 4),
        return_quantiles=quantiles,
        price_range=price_range,
        last_close=round(last_close, 2),
        feature_importance=_importance(model, X, y_up, cfg),
        evaluation=evaluation,
        caveat=caveat,
    )


def model_position(frame: pd.DataFrame, cfg: ModelConfig | None = None, threshold: float = 0.55) -> pd.Series:
    """Position series from walk-forward probabilities, for backtesting the model.

    Only out-of-sample probabilities are used, so this backtest reflects what the
    model would actually have signalled in real time.
    """
    cfg = cfg or ModelConfig()
    probs, _, _ = walk_forward(frame, cfg)
    return (probs >= threshold).astype(float).reindex(frame.index).fillna(0.0)
