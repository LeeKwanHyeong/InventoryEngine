"""No torch, pickle, remote URI, dynamic module loading or model search at inference."""

import math

from .features import ML_INDICES, normalized


def linear(values: list[float], weights: list, bias: list) -> list[float]:
    return [
        math.fsum(x * float(w) for x, w in zip(values, row, strict=True)) + float(b)
        for row, b in zip(weights, bias, strict=True)
    ]


def predict(artifact: dict, features: list[float]) -> float | int:
    values = normalized(features, artifact["normalizer"])
    weights = artifact["weights"]
    if artifact["strategy_type"] == "PREDICTIVE_ML":
        score = linear(
            [values[i] for i in ML_INDICES], weights["linear.weight"], weights["linear.bias"]
        )[0]
        return max(score, 0) + math.log1p(math.exp(-abs(score)))
    x = [math.tanh(v) for v in linear(values, weights["fc1.weight"], weights["fc1.bias"])]
    x = [math.tanh(v) for v in linear(x, weights["fc2.weight"], weights["fc2.bias"])]
    logits = linear(x, weights["actor.weight"], weights["actor.bias"])
    # Stable deterministic evaluation, smallest index resolves a tie.
    return max(range(4), key=lambda i: logits[i])
