# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Pure statistics-graft logic for the backfill service (no recorder/HA deps)."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util


def build_backfill_rows(
    source_rows: list[dict[str, Any]],
    *,
    cutover: float,
    has_sum: bool,
) -> list[dict[str, Any]]:
    """Build the statistics rows to graft a source's history onto its mirror.

    ``source_rows`` are ``statistics_during_period`` rows (hourly), each with a
    float ``start`` (unix ts) plus the stat fields. ``cutover`` is the unix ts
    where the mirror's own series begins — the mirror's first recorded row if it
    already has stats, else "now" for a brand-new mirror.

    Only the source rows from **before** the cutover are emitted — the mirror's
    own rows are never touched. That matters because the recorder seeds a
    sensor's forward ``sum`` from its latest *short-term* statistics each cycle;
    rewriting the mirror's recorded rows would be undone on the next compile.
    Grafting only older history sidesteps that entirely.

    - **sum** (cumulative / energy): the mirror's ``sum`` starts near 0 at the
      cutover, so the grafted history is shifted by the source's ``sum`` at the
      cutover — historical sums end at ~0 there (and run negative further back).
      The Energy dashboard reads period *deltas*, which stay positive and
      continuous across the join.
    - **mean** (measurement / power): per-hour values are independent, so the
      pre-cutover rows are copied verbatim.

    Idempotent without any external flag: after a graft the mirror's earliest
    row *is* the grafted one, so a re-run (cutover = that earliest row) finds no
    source rows before it and emits nothing. Returned rows use a ``datetime``
    ``start`` (UTC, top of hour) for ``async_import_statistics``.
    """
    history = [r for r in source_rows if r["start"] < cutover]
    if not history:
        return []
    out: list[dict[str, Any]] = []

    if has_sum:
        # Shift so the grafted history meets the mirror's baseline at the
        # cutover: offset = the source's sum at (or just before) the cutover.
        at_or_before = [r for r in source_rows if r["start"] <= cutover]
        offset = (at_or_before[-1].get("sum") or 0.0) if at_or_before else 0.0
        for r in history:
            out.append(
                {
                    "start": dt_util.utc_from_timestamp(r["start"]),
                    "state": r.get("state"),
                    "sum": (r.get("sum") or 0.0) - offset,
                }
            )
    else:
        for r in history:
            row: dict[str, Any] = {"start": dt_util.utc_from_timestamp(r["start"])}
            for key in ("mean", "min", "max"):
                if r.get(key) is not None:
                    row[key] = r[key]
            out.append(row)

    return out
