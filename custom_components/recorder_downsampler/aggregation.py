# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Pure aggregation helpers — no Home Assistant imports, easy to unit-test.

A downsample mirror buffers the values its source emitted during an interval,
then collapses them to a single recorded value with one of these methods —
numeric samples are aggregated, non-numeric ones are sampled (see
:func:`aggregate_samples`). ``auto`` resolves to a concrete method from the
source's ``state_class`` (see :func:`resolve_method`).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from .const import (
    METHOD_AUTO,
    METHOD_CIRCULAR_MEAN,
    METHOD_FIRST,
    METHOD_LAST,
    METHOD_MAX,
    METHOD_MEAN,
    METHOD_MEDIAN,
    METHOD_MIN,
    PRECISION_AUTO,
)

# state_class values (kept as plain strings to avoid importing the sensor
# component here — the signature-compat test pins these against HA's enum).
STATE_CLASS_MEASUREMENT = "measurement"
STATE_CLASS_TOTAL = "total"
STATE_CLASS_TOTAL_INCREASING = "total_increasing"


def resolve_method(method: str, state_class: str | None) -> str:
    """Resolve ``auto`` to a concrete method using the source's state_class.

    - measurement        -> mean   (instantaneous quantity; energy-consistent)
    - total / total_increasing -> last (preserve the cumulative counter so
                                  the Energy dashboard / sum statistics stay
                                  correct if pointed at the mirror)
    - anything else / unknown -> last (safe sample; works for non-numeric too)
    """
    if method != METHOD_AUTO:
        return method
    if state_class == STATE_CLASS_MEASUREMENT:
        return METHOD_MEAN
    # total / total_increasing (cumulative counters) and everything else: last.
    return METHOD_LAST


def resolve_precision(
    precision: str | int,
    state_class: str | None,
    suggested_precision: int | None,
) -> int | None:
    """Resolve a precision setting to a number of decimal places, or ``None``
    for no rounding.

    - ``total`` / ``total_increasing`` (cumulative counters) are *never*
      rounded — even an explicit integer — so Energy-dashboard ``sum``
      statistics and counter monotonicity stay intact.
    - an explicit integer rounds to that many places.
    - ``auto`` uses the source's ``suggested_display_precision`` if it declares
      one, else stays raw (we never invent precision).
    - anything else (``none``) stays raw.
    """
    if state_class in (STATE_CLASS_TOTAL, STATE_CLASS_TOTAL_INCREASING):
        return None
    if isinstance(precision, int):
        return precision
    if precision == PRECISION_AUTO:
        return suggested_precision
    return None


def aggregate(values: Sequence[float], method: str) -> float | None:
    """Collapse buffered numeric ``values`` to a single value via ``method``.

    Returns ``None`` if there is nothing to aggregate (empty buffer).
    ``method`` must already be concrete (call :func:`resolve_method` first).
    """
    if not values:
        return None
    if method == METHOD_MEAN:
        return statistics.fmean(values)
    if method == METHOD_MEDIAN:
        return statistics.median(values)
    if method == METHOD_MAX:
        return max(values)
    if method == METHOD_MIN:
        return min(values)
    if method == METHOD_FIRST:
        return values[0]
    if method == METHOD_LAST:
        return values[-1]
    if method == METHOD_CIRCULAR_MEAN:
        # Vector mean on the unit circle: treats each sample as degrees, returns
        # the resultant angle in [0, 360). When the samples cancel out (e.g.
        # [0, 180]) the resultant magnitude is ~0 and the mean is undefined —
        # return None so the mirror skips the interval (same as an empty buffer)
        # instead of recording an arbitrary atan2(0, 0) value.
        sin_sum = math.fsum(math.sin(math.radians(v)) for v in values)
        cos_sum = math.fsum(math.cos(math.radians(v)) for v in values)
        if math.hypot(sin_sum, cos_sum) < 1e-9:
            return None
        # A tiny-negative atan2 result can round to exactly 360.0 after `% 360`
        # in float; collapse that back to 0.0 so the result stays in [0, 360).
        angle = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
        return 0.0 if angle >= 360.0 else angle
    raise ValueError(f"unknown aggregation method: {method!r}")


def aggregate_raw(values: Sequence[str], method: str) -> str | None:
    """Collapse buffered non-numeric (string) ``values`` to one sample.

    Strings can only be *sampled*, not averaged — ``mean``/``median``/``max``/
    ``min`` are meaningless on text — so ``first`` returns the first sample and
    every other method (including the numeric ones) collapses to ``last``, the
    safe most-recent sample. Returns ``None`` for an empty buffer.
    """
    if not values:
        return None
    if method == METHOD_FIRST:
        return values[0]
    return values[-1]


def aggregate_samples(samples: Sequence[str], method: str) -> float | str | None:
    """Collapse one interval's raw source states to a single recorded value.

    If every sample parses as a number, aggregate numerically with ``method``;
    otherwise the source is non-numeric, so sample it as strings (see
    :func:`aggregate_raw`). Returns ``None`` for an empty interval.
    """
    if not samples:
        return None
    numbers: list[float] = []
    for sample in samples:
        try:
            numbers.append(float(sample))
        except TypeError, ValueError:
            return aggregate_raw(samples, method)
    return aggregate(numbers, method)


# Methods whose result depends on how long each sample was the source's active
# state (dwell time). For these we time-weight; for the rest (max/min/first/
# last sample, median order-statistic) weighting is either meaningless or
# definitionally awkward, so we strip weights and reuse the unweighted path.
TIME_WEIGHTABLE_METHODS = frozenset({METHOD_MEAN, METHOD_CIRCULAR_MEAN})


def weighted_mean(weighted_values: Sequence[tuple[float, float]]) -> float | None:
    """Arithmetic mean weighted by per-sample dwell time.

    ``weighted_values`` is a sequence of ``(value, weight)`` pairs where each
    weight is the duration (seconds) the value was the source's active state.
    Returns ``None`` for an empty input or zero total weight (degenerate
    interval — same skip contract as an empty buffer).
    """
    total_weight = math.fsum(w for _, w in weighted_values)
    if total_weight <= 0:
        return None
    weighted_sum = math.fsum(v * w for v, w in weighted_values)
    return weighted_sum / total_weight


def weighted_circular_mean(
    weighted_values: Sequence[tuple[float, float]],
) -> float | None:
    """Vector mean on the unit circle weighted by per-sample dwell time.

    Samples are degrees; result is in ``[0, 360)``. As with the unweighted
    circular mean, a near-zero resultant (samples cancel out) returns ``None``
    rather than fabricating an arbitrary ``atan2(0, 0) = 0°``.
    """
    if not weighted_values:
        return None
    sin_sum = math.fsum(math.sin(math.radians(v)) * w for v, w in weighted_values)
    cos_sum = math.fsum(math.cos(math.radians(v)) * w for v, w in weighted_values)
    if math.hypot(sin_sum, cos_sum) < 1e-9:
        return None
    angle = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    return 0.0 if angle >= 360.0 else angle


def aggregate_weighted_samples(
    weighted_samples: Sequence[tuple[str, float]],
    method: str,
) -> float | str | None:
    """Time-weighted variant of :func:`aggregate_samples`.

    ``weighted_samples`` is a sequence of ``(raw-state-string, weight)`` pairs.
    Numeric sources are routed to the weighted-mean / weighted-circular-mean
    branch for the two methods that take weighting; other methods strip the
    weights and reuse the unweighted path (so `max`/`min`/`first`/`last`/
    `median` behave unchanged). Non-numeric sources fall through to string
    sampling — weighting on text is meaningless, so the weights are dropped.
    """
    if not weighted_samples:
        return None
    numbers: list[tuple[float, float]] = []
    for sample, weight in weighted_samples:
        try:
            numbers.append((float(sample), weight))
        except TypeError, ValueError:
            return aggregate_raw([s for s, _ in weighted_samples], method)
    if method == METHOD_MEAN:
        return weighted_mean(numbers)
    if method == METHOD_CIRCULAR_MEAN:
        return weighted_circular_mean(numbers)
    # Unweighted methods: strip weights, reuse the existing path.
    return aggregate([v for v, _ in numbers], method)
