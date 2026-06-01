# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for the pure aggregation helpers (no Home Assistant needed)."""

import pytest

from custom_components.recorder_downsampler.aggregation import (
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL,
    STATE_CLASS_TOTAL_INCREASING,
    TIME_WEIGHTABLE_METHODS,
    aggregate,
    aggregate_raw,
    aggregate_samples,
    aggregate_weighted_samples,
    resolve_method,
    resolve_precision,
    weighted_circular_mean,
    weighted_mean,
)
from custom_components.recorder_downsampler.const import (
    METHOD_AUTO,
    METHOD_CIRCULAR_MEAN,
    METHOD_FIRST,
    METHOD_LAST,
    METHOD_MAX,
    METHOD_MEAN,
    METHOD_MEDIAN,
    METHOD_MIN,
    PRECISION_AUTO,
    PRECISION_NONE,
)


@pytest.mark.parametrize(
    ("state_class", "expected"),
    [
        (STATE_CLASS_MEASUREMENT, METHOD_MEAN),
        (STATE_CLASS_TOTAL, METHOD_LAST),
        (STATE_CLASS_TOTAL_INCREASING, METHOD_LAST),
        (None, METHOD_LAST),
        ("weird", METHOD_LAST),
    ],
)
def test_resolve_auto(state_class: str | None, expected: str) -> None:
    assert resolve_method(METHOD_AUTO, state_class) == expected


def test_resolve_explicit_passthrough() -> None:
    # An explicit method is never overridden by state_class.
    assert resolve_method(METHOD_MAX, STATE_CLASS_MEASUREMENT) == METHOD_MAX


def test_aggregate_methods() -> None:
    vals = [10.0, 20.0, 30.0, 40.0]
    assert aggregate(vals, METHOD_MEAN) == 25.0
    assert aggregate(vals, METHOD_MEDIAN) == 25.0
    assert aggregate(vals, METHOD_MAX) == 40.0
    assert aggregate(vals, METHOD_MIN) == 10.0
    assert aggregate(vals, METHOD_FIRST) == 10.0
    assert aggregate(vals, METHOD_LAST) == 40.0


def test_aggregate_empty_is_none() -> None:
    assert aggregate([], METHOD_MEAN) is None
    assert aggregate([], METHOD_LAST) is None


def test_aggregate_unknown_method_raises() -> None:
    with pytest.raises(ValueError, match="unknown aggregation method"):
        aggregate([1.0], "bogus")


def test_aggregate_circular_mean_wraps_around_zero() -> None:
    # The point of circular_mean: 350° and 10° must average to 0°, not 180°.
    result = aggregate([350.0, 10.0], METHOD_CIRCULAR_MEAN)
    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-9) or result == pytest.approx(
        360.0, abs=1e-9
    )


def test_aggregate_circular_mean_matches_arithmetic_when_no_wrap() -> None:
    # Within a quadrant the circular mean ~= the arithmetic mean.
    result = aggregate([10.0, 20.0, 30.0], METHOD_CIRCULAR_MEAN)
    assert result is not None
    assert result == pytest.approx(20.0, abs=1e-6)


def test_aggregate_circular_mean_returns_in_zero_to_360() -> None:
    # Negative atan2 results must wrap into [0, 360).
    result = aggregate([10.0, 350.0, 0.0], METHOD_CIRCULAR_MEAN)
    assert result is not None
    assert 0.0 <= result < 360.0


def test_aggregate_circular_mean_cancelling_samples_return_none() -> None:
    # Opposing samples cancel out: the mean is undefined, so skip the interval
    # (same contract as an empty buffer) rather than emit an arbitrary atan2(0, 0).
    assert aggregate([0.0, 180.0], METHOD_CIRCULAR_MEAN) is None
    assert aggregate([90.0, 270.0], METHOD_CIRCULAR_MEAN) is None


def test_aggregate_circular_mean_single_value_passthrough() -> None:
    assert aggregate([42.0], METHOD_CIRCULAR_MEAN) == pytest.approx(42.0, abs=1e-9)


# -- time-weighted aggregation ----------------------------------------------


def test_time_weightable_methods_are_mean_and_circular_mean_only() -> None:
    # Only the methods whose result depends on dwell time. max/min/first/last
    # are samplers; median is an order statistic that doesn't take weights.
    assert TIME_WEIGHTABLE_METHODS == {METHOD_MEAN, METHOD_CIRCULAR_MEAN}


def test_weighted_mean_uniform_weights_matches_arithmetic_mean() -> None:
    # Equal weights reduce to the arithmetic mean — the sanity case that the
    # existing integration tests' expected values rely on.
    assert weighted_mean([(10.0, 1.0), (20.0, 1.0), (30.0, 1.0)]) == pytest.approx(20.0)


def test_weighted_mean_non_uniform_weights() -> None:
    # 100 held for 90 s, 200 held for 10 s -> mean leans toward 100.
    assert weighted_mean([(100.0, 90.0), (200.0, 10.0)]) == pytest.approx(110.0)


def test_weighted_mean_empty_or_zero_weight_is_none() -> None:
    # Same skip contract as aggregate(): nothing to average -> no row.
    assert weighted_mean([]) is None
    assert weighted_mean([(5.0, 0.0), (10.0, 0.0)]) is None


def test_weighted_circular_mean_uniform_weights_matches_unweighted() -> None:
    # Equal weights on the wrap-around case still yield 0° (not 180°).
    result = weighted_circular_mean([(350.0, 1.0), (10.0, 1.0)])
    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-9) or result == pytest.approx(
        360.0, abs=1e-9
    )


def test_weighted_circular_mean_dwell_time_biases_result() -> None:
    # Wind sat at 350° for 90 s, then swung to 10° for 10 s of the interval.
    # The dwell-time-weighted mean must sit near 350°, not the unweighted 0°.
    result = weighted_circular_mean([(350.0, 90.0), (10.0, 10.0)])
    assert result is not None
    # Closer to 350° than to 0° (within ~5°).
    assert 345.0 <= result <= 355.0


def test_weighted_circular_mean_cancellation_returns_none() -> None:
    # Equal-weight opposing samples cancel exactly -> undefined -> None.
    assert weighted_circular_mean([(0.0, 1.0), (180.0, 1.0)]) is None


def test_aggregate_weighted_samples_routes_mean_through_weighted() -> None:
    # Two samples, second held 9x longer -> the weighted mean leans toward it.
    result = aggregate_weighted_samples([("100", 1.0), ("200", 9.0)], METHOD_MEAN)
    assert result == pytest.approx(190.0)


def test_aggregate_weighted_samples_routes_circular_through_weighted() -> None:
    result = aggregate_weighted_samples(
        [("350", 90.0), ("10", 10.0)], METHOD_CIRCULAR_MEAN
    )
    assert result is not None and isinstance(result, float)
    assert 345.0 <= result <= 355.0


def test_aggregate_weighted_samples_unweighted_methods_strip_weights() -> None:
    # max/min/first/last/median don't take weights — they should behave the
    # same regardless of how lopsided the weighting is.
    samples = [("30", 1.0), ("10", 99.0), ("50", 1.0), ("20", 1.0)]
    assert aggregate_weighted_samples(samples, METHOD_MAX) == 50.0
    assert aggregate_weighted_samples(samples, METHOD_MIN) == 10.0
    assert aggregate_weighted_samples(samples, METHOD_FIRST) == 30.0
    assert aggregate_weighted_samples(samples, METHOD_LAST) == 20.0
    assert aggregate_weighted_samples(samples, METHOD_MEDIAN) == 25.0


def test_aggregate_weighted_samples_non_numeric_falls_back_to_string_sampling() -> None:
    # A single non-numeric sample demotes the whole interval; weights are
    # meaningless on text, so we drop them and sample-most-recent.
    assert (
        aggregate_weighted_samples([("1", 1.0), ("auto", 9.0), ("2", 1.0)], METHOD_MEAN)
        == "2"
    )


def test_aggregate_weighted_samples_empty_is_none() -> None:
    assert aggregate_weighted_samples([], METHOD_MEAN) is None


def test_aggregate_raw_first_and_last() -> None:
    vals = ["home", "away", "home"]
    assert aggregate_raw(vals, METHOD_FIRST) == "home"
    assert aggregate_raw(vals, METHOD_LAST) == "home"
    assert aggregate_raw(["a", "b", "c"], METHOD_LAST) == "c"


def test_aggregate_raw_numeric_methods_degrade_to_last() -> None:
    # mean/median/max/min are meaningless on strings -> safe most-recent sample.
    vals = ["on", "off", "on"]
    for method in (METHOD_MEAN, METHOD_MEDIAN, METHOD_MAX, METHOD_MIN):
        assert aggregate_raw(vals, method) == "on"


def test_aggregate_raw_empty_is_none() -> None:
    assert aggregate_raw([], METHOD_LAST) is None


def test_aggregate_samples_numeric() -> None:
    assert aggregate_samples(["10", "20", "30"], METHOD_MEAN) == 20.0


def test_aggregate_samples_falls_back_to_string_when_not_all_numeric() -> None:
    # One non-numeric sample demotes the whole interval to string sampling.
    assert aggregate_samples(["1", "auto", "2"], METHOD_MEAN) == "2"
    assert aggregate_samples(["home", "away"], METHOD_FIRST) == "home"


def test_aggregate_samples_empty_is_none() -> None:
    assert aggregate_samples([], METHOD_MEAN) is None


@pytest.mark.parametrize("precision", [PRECISION_AUTO, PRECISION_NONE, 0, 2])
@pytest.mark.parametrize(
    "state_class", [STATE_CLASS_TOTAL, STATE_CLASS_TOTAL_INCREASING]
)
def test_precision_accumulators_always_raw(
    precision: str | int, state_class: str
) -> None:
    # Cumulative counters are never rounded, even with an explicit integer.
    assert resolve_precision(precision, state_class, 3) is None


def test_precision_auto_uses_suggested_when_present() -> None:
    assert resolve_precision(PRECISION_AUTO, STATE_CLASS_MEASUREMENT, 1) == 1


def test_precision_auto_raw_when_no_suggested() -> None:
    assert resolve_precision(PRECISION_AUTO, STATE_CLASS_MEASUREMENT, None) is None


def test_precision_explicit_int_rounds_measurement() -> None:
    assert resolve_precision(2, STATE_CLASS_MEASUREMENT, None) == 2
    assert resolve_precision(0, STATE_CLASS_MEASUREMENT, 3) == 0  # explicit beats hint


def test_precision_none_is_raw() -> None:
    assert resolve_precision(PRECISION_NONE, STATE_CLASS_MEASUREMENT, 3) is None
