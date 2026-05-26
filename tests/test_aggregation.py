# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for the pure aggregation helpers (no Home Assistant needed)."""

import pytest

from custom_components.recorder_downsampler.aggregation import (
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL,
    STATE_CLASS_TOTAL_INCREASING,
    aggregate,
    aggregate_raw,
    aggregate_samples,
    resolve_method,
    resolve_precision,
)
from custom_components.recorder_downsampler.const import (
    METHOD_AUTO,
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
