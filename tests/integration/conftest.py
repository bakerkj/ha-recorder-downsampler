# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Shared fixtures for integration tests.

Spins up a real in-process Home Assistant with a file-backed SQLite recorder
(no network). Mirrors the fixture pattern of the sibling ha-recorder-tuning
repo so the two test suites feel the same.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import pytest
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import session_scope
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

NOW = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Allow the per-mirror emit timer to outlive the test.

    Each ``DownsampleSensor`` runs a perpetual ``async_track_time_interval``
    timer that is cancelled on entity removal. The integration is YAML-only
    with no unload path yet (manager TODO), so the entities aren't torn down
    within a test and the timer is still pending at cleanup. HA cancels these
    on real shutdown, so this is a test-harness artifact, not a leak.
    """
    return True


@pytest.fixture
def recorder_config() -> dict[str, Any]:
    """Short purge window, no commit delay — deterministic recorder."""
    return {"purge_keep_days": 5, "commit_interval": 0}


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder: Any) -> Any:
    """Ensure the recorder DB is prepared before hass marks itself ready."""
    return async_test_recorder


@pytest.fixture
async def recorder_hass(
    hass: HomeAssistant,
    recorder_mock: Any,
    enable_custom_integrations: None,
    freezer: Any,
) -> AsyncGenerator[HomeAssistant, None]:
    """HA with recorder running, clock frozen at NOW."""
    freezer.move_to(NOW)
    await async_wait_recording_done(hass)
    # The test recorder creates its engine with echo on, which sets the
    # sqlalchemy.engine logger to INFO and floods test output with raw SQL.
    # The engine exists now, so turn that back down without affecting our own
    # logging assertions (custom_components.recorder_downsampler).
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    yield hass


async def wait_for_recorder(hass: HomeAssistant) -> None:
    await async_wait_recording_done(hass)


def count_states(hass: HomeAssistant, entity_id: str) -> int:
    """Number of recorded ``States`` rows for *entity_id*."""
    with session_scope(hass=hass) as session:
        meta = (
            session.query(StatesMeta).filter(StatesMeta.entity_id == entity_id).first()
        )
        if meta is None:
            return 0
        return (
            session.query(States).filter(States.metadata_id == meta.metadata_id).count()
        )
