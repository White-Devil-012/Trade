"""Plain-English summaries of the analysis, for people who do not trade for a living.

Two rules shape every sentence here:

1. Never tell anyone to buy or sell. Describe what the numbers say, and how far
   they can be trusted.
2. When the model has no demonstrated edge, say so in the same breath as the
   number. A bare "62% chance of going up" reads as advice; "62%, but it has
   been no better than a guess for this stock" reads as what it is.
"""

from __future__ import annotations

from typing import Any

_TREND = {
    "strong_uptrend": ("Strong uptrend", "up",
                       "The price has been climbing steadily and sits well above its average over the past ~10 months."),
    "uptrend": ("Uptrend", "up",
                "The price is above its average over the past ~10 months, and that average is rising."),
    "range": ("Moving sideways", "flat",
              "The price has no clear direction right now."),
    "downtrend": ("Downtrend", "down",
                  "The price is below its average over the past ~10 months, and that average is falling."),
    "strong_downtrend": ("Strong downtrend", "down",
                         "The price has been falling steadily and sits well below its average over the past ~10 months."),
    "unknown": ("Not enough history", "flat",
                "There is not enough price history yet to judge the trend."),
}

_VOLATILITY = {
    "low": "Price swings have been smaller than usual.",
    "high": "Price swings have been bigger than usual.",
    "extreme": "Price swings have been much bigger than usual, so sharp moves are likely either way.",
}

_MOMENTUM = {
    "overbought": "Recent gains have been unusually fast.",
    "oversold": "Recent losses have been unusually fast.",
}


def _money(value: float, currency: bool) -> str:
    """Indian digit grouping: 1,23,456.78."""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    sign = "-" if value < 0 else ""
    return f"{sign}{'₹' if currency else ''}{whole}.{frac}"


def mood(regime: dict[str, Any], below_52w_high: float | None) -> dict[str, Any]:
    """What the market is doing with this stock right now, in a sentence or three."""
    headline, tone, opener = _TREND.get(regime.get("trend", "unknown"), _TREND["unknown"])
    sentences = [opener]

    if regime.get("volatility") in _VOLATILITY:
        sentences.append(_VOLATILITY[regime["volatility"]])
    if regime.get("momentum") in _MOMENTUM:
        sentences.append(_MOMENTUM[regime["momentum"]])

    if below_52w_high is not None:
        if below_52w_high > -0.02:
            sentences.append("It is trading close to its 52-week high.")
        else:
            sentences.append(f"It is {abs(below_52w_high):.0%} below its 52-week high.")

    return {"headline": headline, "tone": tone, "sentences": sentences}


def prediction(forecast: dict[str, Any] | None, kind: str = "equity",
               error: str | None = None) -> dict[str, Any]:
    """The model's forecast, with its track record attached to the same sentence."""
    noun = {"fund": "fund", "index": "index"}.get(kind, "stock")
    currency = kind != "index"

    if not forecast:
        return {
            "headline": "No forecast",
            "lean": "none",
            "reliable": False,
            "sentences": [
                f"There is not enough price history for this {noun} to test a forecast fairly."
                if not error or "need at least" in error else
                "The forecast could not be calculated."
            ],
        }

    p = float(forecast["probability_up"])
    horizon = int(forecast["horizon_days"])
    ev = forecast.get("evaluation") or {}
    reliable = bool(ev.get("has_edge"))
    accuracy = float(ev.get("accuracy", 0.0))
    baseline = float(ev.get("baseline_accuracy", 0.0))
    guess = "up" if float(ev.get("up_rate", 0.5)) >= 0.5 else "down"

    if not reliable:
        headline, lean = "No reliable signal", "none"
    elif p >= 0.55:
        headline, lean = "Leans up", "up"
    elif p <= 0.45:
        headline, lean = "Leans down", "down"
    else:
        headline, lean = "No clear lean", "none"

    sentences = [
        f"Our model puts the chance of the price being higher in {horizon} trading days at {p:.0%}."
    ]
    if reliable:
        sentences.append(
            f"In past tests on this {noun} it was right {accuracy:.0%} of the time, against "
            f"{baseline:.0%} for always guessing '{guess}'. That is a small edge, not a guarantee."
        )
    elif accuracy > baseline:
        sentences.append(
            f"But in past tests on this {noun} it was right {accuracy:.0%} of the time, barely above "
            f"the {baseline:.0%} you would get by always guessing '{guess}'. Treat it as a coin flip."
        )
    else:
        sentences.append(
            f"But in past tests on this {noun} it was right only {accuracy:.0%} of the time, no better "
            f"than always guessing '{guess}' ({baseline:.0%}). Treat it as a coin flip."
        )

    price_range = forecast.get("price_range") or {}
    if "likely_low" in price_range and "likely_high" in price_range:
        sentences.append(
            f"Half the time, similar {horizon}-day moves ended between "
            f"{_money(price_range['likely_low'], currency)} and {_money(price_range['likely_high'], currency)}."
        )

    return {
        "headline": headline,
        "lean": lean,
        "reliable": reliable,
        "probability_up": round(p, 4),
        "horizon_days": horizon,
        "sentences": sentences,
    }
