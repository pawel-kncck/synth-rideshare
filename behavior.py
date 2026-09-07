"""Seeded decisions use these synthetic price and pickup-time preferences."""

import math


def finite_number(value, name, minimum=0, maximum=None, strictly_positive=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or (strictly_positive and value == 0)
        or (maximum is not None and value > maximum)
    ):
        bounds = "positive" if strictly_positive else f">= {minimum}"
        if maximum is not None:
            bounds += f" and <= {maximum}"
        raise ValueError(f"{name} must be a finite number {bounds}")


def decision_probability(baseline, price, eta_seconds, reference_price,
                         reference_eta_seconds, price_sensitivity, eta_sensitivity):
    """Adjust baseline log odds; a signed price sensitivity selects preference.

    Baseline is the probability at the reference fare and pickup ETA. A
    sensitivity of one changes log odds by one per reference-sized change.
    Endpoints 0 and 1 deliberately force a decision, useful for experiments.
    """
    if baseline in (0, 1):
        return float(baseline)
    score = (
        math.log(baseline / (1 - baseline))
        + price_sensitivity * (price / reference_price - 1)
        - eta_sensitivity * (eta_seconds / reference_eta_seconds - 1)
    )
    # Stable logistic, including extremely expensive fares or distant pickups.
    if score >= 0:
        return 1 / (1 + math.exp(-score))
    odds = math.exp(score)
    return odds / (1 + odds)
