# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for the statistics-backfill graft logic (pure, no recorder)."""

from typing import Any

import pytest

from custom_components.recorder_downsampler import build_backfill_rows


def test_sum_backfill_grafts_history_shifted_to_baseline() -> None:
    # Source: cumulative sum 0.0 -> 0.5 -> 1.0.
    source = [
        {"start": 0, "state": 68.0, "sum": 0.0},
        {"start": 3600, "state": 68.5, "sum": 0.5},
        {"start": 7200, "state": 69.0, "sum": 1.0},  # overlap hour (= cutover)
    ]
    rows = build_backfill_rows(source, cutover=7200, has_sum=True)

    # Only the pre-cutover source rows are emitted; the mirror's own rows aren't
    # passed in and are never touched.
    assert [r["start"].timestamp() for r in rows] == [0, 3600]
    # offset = source sum at cutover (7200) = 1.0 -> history shifted to end ~0.
    assert [r["sum"] for r in rows] == [-1.0, -0.5]


def test_mean_backfill_copies_history_no_shift() -> None:
    source = [
        {"start": 0, "mean": 10.0, "min": 5.0, "max": 15.0},
        {"start": 3600, "mean": 12.0, "min": 6.0, "max": 20.0},  # cutover
    ]
    rows = build_backfill_rows(source, cutover=3600, has_sum=False)

    assert len(rows) == 1
    assert rows[0]["mean"] == 10.0 and rows[0]["min"] == 5.0 and rows[0]["max"] == 15.0
    assert "sum" not in rows[0]
    assert rows[0]["start"].timestamp() == 0


def test_mean_backfill_omits_absent_min_max() -> None:
    source = [{"start": 0, "mean": 10.0}, {"start": 3600, "mean": 12.0}]
    rows = build_backfill_rows(source, cutover=3600, has_sum=False)
    assert len(rows) == 1
    assert set(rows[0]) == {"start", "mean"}
    assert rows[0]["mean"] == 10.0


def test_new_mirror_cutover_now_shifts_all_history_to_zero() -> None:
    # A brand-new mirror has no stats, so the caller anchors the cutover at
    # "now" (after all source rows) -> the whole history shifts to end at ~0.
    source = [
        {"start": 0, "state": 1.0, "sum": 0.0},
        {"start": 3600, "state": 2.0, "sum": 1.0},
    ]
    rows = build_backfill_rows(source, cutover=10_000, has_sum=True)
    assert [r["sum"] for r in rows] == [-1.0, 0.0]  # ends at 0 at the cutover


@pytest.mark.parametrize(
    ("has_sum", "source"),
    [
        pytest.param(
            True,
            [
                {"start": 0, "state": 1.0, "sum": 0.0},
                {"start": 3600, "state": 2.0, "sum": 1.0},
                {"start": 7200, "state": 3.0, "sum": 2.0},
            ],
            id="sum",
        ),
        pytest.param(
            False,
            [
                {"start": 0, "mean": 10.0, "min": 5.0, "max": 15.0},
                {"start": 3600, "mean": 12.0, "min": 6.0, "max": 20.0},
                {"start": 7200, "mean": 14.0, "min": 7.0, "max": 25.0},
            ],
            id="mean",
        ),
    ],
)
def test_backfill_is_idempotent_without_a_flag(
    has_sum: bool, source: list[dict[str, Any]]
) -> None:
    # Holds for BOTH sum and mean: the graft only adds rows older than the
    # cutover, so a second pass (cutover = the earliest grafted row) finds
    # nothing left to add.
    first = build_backfill_rows(source, cutover=7200, has_sum=has_sum)
    assert first  # grafted something on the first pass

    earliest = min(r["start"].timestamp() for r in first)
    second = build_backfill_rows(source, cutover=earliest, has_sum=has_sum)
    assert second == []
