"""Tunable defaults. Everything here is a starting point, not a recommendation."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BacktestConfig:
    """Trading frictions. Defaults are deliberately pessimistic."""

    commission_bps: float = 5.0      # per side, in basis points of notional
    slippage_bps: float = 5.0        # per side
    initial_equity: float = 100_000.0
    allow_short: bool = False
    # Trades execute on the bar *after* the signal fires, at that bar's open.
    execution_lag_bars: int = 1


@dataclass(frozen=True)
class ModelConfig:
    """Walk-forward forecasting setup."""

    horizon_days: int = 5            # forecast the return over the next N sessions
    min_train_bars: int = 500        # ~2 years before the first prediction
    retrain_every: int = 21          # refit roughly monthly
    n_estimators: int = 300
    max_depth: int = 3
    learning_rate: float = 0.03
    random_state: int = 7
    # A model is only worth listening to if it beats the base rate by this much.
    min_edge_over_baseline: float = 0.02


@dataclass(frozen=True)
class AppConfig:
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    cache_dir: str = ".cache/trendlab"
    default_period: str = "5y"
