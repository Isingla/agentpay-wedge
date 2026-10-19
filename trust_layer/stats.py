"""Small stats helpers: Wilson intervals for rates.

LLM runs are not deterministic, so rates from real-model runs are
reported with a 95% Wilson interval across seeds (step 3: statistical
reproducibility). Scripted runs are exact — same seed, same numbers —
and the interval still applies when aggregating across seeds.
"""
import math


def wilson_interval(k, n, z=1.96):
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate_with_interval(k, n):
    lo, hi = wilson_interval(k, n)
    return {"k": k, "n": n, "rate": k / n if n else 0.0,
            "ci95": [round(lo, 4), round(hi, 4)]}
