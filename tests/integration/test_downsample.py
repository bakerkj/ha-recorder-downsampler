# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""End-to-end tests: a real source sensor, a real recorder, a real mirror.

These drive the integration through a real in-process Home Assistant with a
file-backed recorder: interval-timer firing under ``freezer``, the recorder
commit cycle, device attachment, non-numeric sources, precision rounding,
source outages, and reload teardown. The state-machine assertions are the core
contract; the recorder-row assertion proves the downsample lands in history.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import SERVICE_RELOAD, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.recorder import get_instance
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.recorder_downsampler import CONFIG_SCHEMA
from custom_components.recorder_downsampler.const import (
    CONF_BACKFILL_HISTORY,
    CONF_COPY_DISPLAY_PRECISION,
    CONF_DRY_RUN,
    CONF_INTERVAL,
    CONF_METHOD,
    CONF_PRECISION,
    CONF_RULES,
    CONF_WARN_UNEXCLUDED,
    DATA_MANAGER,
    DOMAIN,
    EVENT_BACKFILL_COMPLETED,
    METHOD_FIRST,
    METHOD_LAST,
    METHOD_MAX,
    METHOD_MEAN,
    METHOD_MEDIAN,
    METHOD_MIN,
)
from custom_components.recorder_downsampler.sensor import DownsampleSensor

from .conftest import NOW, wait_for_recorder

SOURCE = "sensor.demo_power"
MIRROR = "sensor.demo_power_downsampled"
MEAS = {"unit_of_measurement": "W", "state_class": "measurement"}


def _state(hass: HomeAssistant, entity_id: str) -> State:
    """Fetch a state asserting it exists (states.get is State | None)."""
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state


def _entry(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    """Fetch a registry entry asserting it exists (async_get is Entry | None)."""
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None, f"{entity_id} not in entity registry"
    return entry


def _device(hass: HomeAssistant, device_id: str) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get(device_id)
    assert device is not None, f"device {device_id} not in registry"
    return device


def _sensor_opts(hass: HomeAssistant, entity_id: str) -> Mapping[str, Any]:
    """The mirror's `sensor` registry options (display precision lives here)."""
    opts: Mapping[str, Any] = _entry(hass, entity_id).options.get("sensor", {})
    return opts


async def _setup(hass: HomeAssistant, **domain: object) -> None:
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE]}],
    }
    base.update(domain)
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()


async def test_mirror_created_and_attached(recorder_hass: HomeAssistant) -> None:
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})
    # The mirror entity exists (state may be unknown until the first interval).
    assert hass.states.get(MIRROR) is not None


async def test_mean_emitted_on_interval(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)  # seeds the buffer at setup
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # Mean is time-weighted; placing the second sample at the interval midpoint
    # gives both samples equal dwell time so the weighted mean equals (100+200)/2.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(SOURCE, "200", MEAS)  # second sample this interval
    await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(150.0)  # mean(100, 200)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (METHOD_MEAN, 27.5),  # (30+10+50+20)/4
        (METHOD_MEDIAN, 25.0),  # median(10,20,30,50)
        (METHOD_MAX, 50.0),
        (METHOD_MIN, 10.0),
        (METHOD_FIRST, 30.0),  # first sample seen this interval
        (METHOD_LAST, 20.0),  # most recent sample
    ],
)
async def test_each_method_aggregates_over_interval(
    recorder_hass: HomeAssistant, freezer: Any, method: str, expected: float
) -> None:
    """Every explicit aggregation method, driven end to end through a real emit.

    The same four samples (30, 10, 50, 20) feed each method; the values are
    chosen so all six results are DISTINCT — a method silently behaving like
    another (first vs min, mean vs median, last vs max) would fail here. These
    are plain `measurement` samples, so the method is honored as requested
    rather than via `auto`'s state-class routing (covered separately).
    """
    hass = recorder_hass
    samples = ("30", "10", "50", "20")  # first=30, last=20, min=10, max=50
    hass.states.async_set(SOURCE, samples[0], MEAS)  # seeds the buffer at setup
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: method})

    # Space the four samples evenly across the 11 s interval so each gets the
    # same dwell time — the time-weighted mean then equals the arithmetic mean
    # (and the unweighted methods see all four samples, not just the last).
    for i, v in enumerate(samples[1:], start=1):
        freezer.move_to(NOW + timedelta(seconds=11 * i / 4))
        hass.states.async_set(SOURCE, v, MEAS)
        await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.attributes["method"] == method
    assert float(st.state) == pytest.approx(expected)


async def test_mean_is_time_weighted_by_dwell_duration(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Mean weights each sample by how long it was the source's active state.

    A source that sat at 100 for 9 s then jumped to 200 for the last 1 s of a
    10 s interval should record ~110, not the arithmetic mean 150 — the
    arithmetic mean would over-represent a brief excursion. The carry-over
    from the previous interval is the source's last value at the boundary,
    which here is the setup seed.
    """
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # The source sits at 100 for 9 s, then jumps to 200 at t=9 — 200 is the
    # active value for the final 2 s (until the t=11 emit). Weights: 100 -> 9 s,
    # 200 -> 2 s -> weighted mean = (100*9 + 200*2) / 11 ≈ 118.18.
    freezer.move_to(NOW + timedelta(seconds=9))
    hass.states.async_set(SOURCE, "200", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx((100 * 9 + 200 * 2) / 11, abs=0.01)


async def test_emit_self_heals_stuck_unavailable_mirror(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """A mirror stuck `unavailable` recovers on the next emit by sampling the
    source's current value — even with no recovery event.

    Regression for the outdoor-outlet stall: recovery previously required a
    source state-change *event*. If that event was missed (a dropped
    subscription) while the source updates slowly, the mirror stayed
    unavailable indefinitely. ``_emit`` now self-heals from the current state.
    """
    hass = recorder_hass
    created: list[DownsampleSensor] = []
    real_init = DownsampleSensor.__init__

    def _capture(self: DownsampleSensor, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        created.append(self)

    with patch.object(DownsampleSensor, "__init__", _capture):
        hass.states.async_set(SOURCE, "5", MEAS)
        await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    # First emit -> the mirror publishes a value and is available.
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    st0 = hass.states.get(MIRROR)
    assert st0 is not None and st0.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    # Source goes unavailable -> the next emit marks the mirror unavailable.
    hass.states.async_set(SOURCE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=22))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    st1 = hass.states.get(MIRROR)
    assert st1 is not None and st1.state == STATE_UNAVAILABLE

    # Source recovers, but the mirror MISSES the event (simulate a dropped
    # subscription by clearing the buffer). The next emit must self-heal.
    mirror = next(e for e in created if e.entity_id == MIRROR)
    hass.states.async_set(SOURCE, "9", MEAS)
    await hass.async_block_till_done()
    mirror._buffer = []  # the recovery event never reached us
    freezer.move_to(NOW + timedelta(seconds=33))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(9.0)  # self-healed from current value


async def test_mirror_attaches_to_source_device(recorder_hass: HomeAssistant) -> None:
    """The mirror is linked onto the source's existing device card.

    YAML-discovery platforms have no config entry, so DeviceInfo is ignored;
    the mirror must instead be linked via the entity registry's device_id.
    """
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entry = MockConfigEntry(domain="power_monitor")
    entry.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("power_monitor", "pm1")},
    )
    src = ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "chan1",
        suggested_object_id="demo_power",
        config_entry=entry,
        device_id=device.id,
    )
    assert src.entity_id == SOURCE  # sanity: matches _setup's rule target

    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    mirror = ent_reg.async_get(MIRROR)
    assert mirror is not None
    assert mirror.device_id == device.id


async def test_non_numeric_source_samples_last(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """A non-numeric (string) source records its most-recent sample."""
    hass = recorder_hass
    src, mirror = "sensor.demo_mode", "sensor.demo_mode_downsampled"
    hass.states.async_set(src, "home", {})  # no state_class -> auto = last
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [{"name": "demo", "entity_ids": [src]}],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    hass.states.async_set(src, "away", {})
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(mirror)
    assert st is not None
    assert st.state == "away"


async def test_unavailable_source_marks_mirror_unavailable(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """When the source is down for a whole interval, the mirror goes unavailable."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(_state(hass, MIRROR).state) == pytest.approx(100.0)

    hass.states.async_set(SOURCE, STATE_UNAVAILABLE, MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=22))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, MIRROR).state == STATE_UNAVAILABLE


async def test_reload_orphan_raises_repair_not_removed(
    recorder_hass: HomeAssistant,
) -> None:
    """A reload that drops a still-present source no longer deletes its mirror —
    it raises a Repairs issue instead (we never auto-delete a mirror)."""
    hass = recorder_hass
    hass.states.async_set("sensor.a_power", "100", MEAS)
    hass.states.async_set("sensor.b_power", "100", MEAS)
    both = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {"name": "demo", "entity_ids": ["sensor.a_power", "sensor.b_power"]}
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: both})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.a_power_downsampled") is not None
    assert hass.states.get("sensor.b_power_downsampled") is not None

    # Reload with b_power dropped from the rule.
    manager = hass.data[DOMAIN][DATA_MANAGER]
    only_a = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_RULES: [{"name": "demo", "entity_ids": ["sensor.a_power"]}],
            }
        }
    )[DOMAIN]
    manager.update_config(only_a)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.a_power_downsampled") is not None
    # b_power's source still exists, so its mirror is preserved (not deleted)
    # and surfaced as a Repairs issue for the user to confirm.
    b_uid = "recorder_downsampler_sensor.b_power_downsampled"
    assert er.async_get(hass).async_get("sensor.b_power_downsampled") is not None
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(b_uid)) is not None
    )


async def test_reload_updates_changed_params(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """A reload that retunes a still-matched source updates its live mirror.

    The mirror is reconfigured in place (not recreated): the exposed method /
    interval change, and the new cadence and method govern the next emit.
    """
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.attributes["method"] == "mean"
    assert st.attributes["interval_seconds"] == 10

    # Reload the same source with a new method and a slower interval.
    manager = hass.data[DOMAIN][DATA_MANAGER]
    retuned = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:30",
                CONF_METHOD: METHOD_MAX,
                CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE]}],
            }
        }
    )[DOMAIN]
    manager.update_config(retuned)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.attributes["method"] == "max"  # reconfigured in place
    assert st.attributes["interval_seconds"] == 30

    # The retuned cadence and method now drive the emit: max over the interval.
    for val in ("100", "300", "200"):
        hass.states.async_set(SOURCE, val, MEAS)
        await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(300.0)  # max, not mean


async def test_precision_explicit_rounds_downsampled_value(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """An explicit `precision` rounds the value the recorder actually stores."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(
        hass,
        **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN, CONF_PRECISION: 1},
    )

    # Three samples evenly spaced over the 11 s interval -> uniform dwell -> the
    # time-weighted mean reduces to the arithmetic mean (100+101+100)/3.
    freezer.move_to(NOW + timedelta(seconds=11 / 3))
    hass.states.async_set(SOURCE, "101", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=22 / 3))
    hass.states.async_set(SOURCE, "100", MEAS)
    await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(100.3)  # round(mean(100,101,100), 1)


async def test_precision_auto_uses_suggested_display_precision(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Under `precision: auto`, the source's suggested_display_precision wins."""
    hass = recorder_hass
    attrs = {**MEAS, "suggested_display_precision": 0}
    hass.states.async_set(SOURCE, "100", attrs)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # Three samples evenly spaced over 11 s -> uniform dwell -> the time-
    # weighted mean reduces to the arithmetic mean (100+102+100)/3.
    freezer.move_to(NOW + timedelta(seconds=11 / 3))
    hass.states.async_set(SOURCE, "102", attrs)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=22 / 3))
    hass.states.async_set(SOURCE, "100", attrs)
    await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.state == "101"  # ndigits 0 -> a whole number, not "101.0"
    assert float(st.state) == pytest.approx(101.0)  # round(mean(100,102,100), 0)


async def test_source_appearing_after_setup(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """A registered source with no state yet must not crash on its first update.

    The mirror is constructed before the source has published any state (so its
    metadata backing attrs start empty); the first state event must ingest
    cleanly and copy the source's metadata onto the mirror.
    """
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor", "demo", "late1", suggested_object_id="demo_power"
    )  # registered, but no state -> mirror built with empty metadata

    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})
    # Registered but no source value yet -> no state written (so no recorded
    # `unknown`); the entity reads unavailable until its first value.
    assert er.async_get(hass).async_get(MIRROR) is not None  # entity exists
    assert hass.states.get(MIRROR) is None  # but holds no state yet

    hass.states.async_set(SOURCE, "100", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(100.0)
    assert st.attributes.get("unit_of_measurement") == "W"  # metadata copied late


async def test_dry_run_creates_no_mirror(recorder_hass: HomeAssistant) -> None:
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_DRY_RUN: True, CONF_INTERVAL: "00:00:10"})
    assert hass.states.get(MIRROR) is None


async def test_dry_run_resolves_and_logs_without_creating(
    recorder_hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """dry_run still resolves and LOGS the rollout — its whole value — but
    creates no mirror and tracks nothing as created."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.recorder_downsampler"
    ):
        await _setup(hass, **{CONF_DRY_RUN: True, CONF_INTERVAL: "00:00:10"})

    assert hass.states.get(MIRROR) is None
    manager = hass.data[DOMAIN][DATA_MANAGER]
    assert manager._created == {}  # nothing handed to the platform
    # Resolution still ran (so the user sees what *would* be mirrored) ...
    assert len(manager.resolve_targets()) == 1
    # ... and was logged, flagged as a dry run, with the source on its own
    # line carrying status + effective config.
    assert "DRY RUN — would mirror 1 source" in caplog.text
    assert 'rule "demo" — 1 source(s)' in caplog.text
    assert f"[DRY RUN] {SOURCE} — interval 0:00:10, method auto, precision auto" in (
        caplog.text
    )


def _orphan_issue_id(unique_id: str) -> str:
    return f"orphaned_mirror_{unique_id}"


def _register_orphan_mirror(hass: HomeAssistant, source_oid: str) -> tuple[str, str]:
    """Register a leftover mirror (and return its unique_id, entity_id)."""
    ent_reg = er.async_get(hass)
    unique_id = f"recorder_downsampler_sensor.{source_oid}_downsampled"
    ent_reg.async_get_or_create(
        "sensor", DOMAIN, unique_id, suggested_object_id=f"{source_oid}_downsampled"
    )
    return unique_id, f"sensor.{source_oid}_downsampled"


def _register_disabled_source(hass: HomeAssistant, object_id: str) -> None:
    monitor = MockConfigEntry(domain="power_monitor")
    monitor.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        "power_monitor",
        f"u_{object_id}",
        suggested_object_id=object_id,
        config_entry=monitor,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )


async def test_orphan_with_unknown_source_is_preserved(
    recorder_hass: HomeAssistant,
) -> None:
    """A leftover mirror whose source isn't known yet is PRESERVED, not deleted.

    Regression: at a cold boot a source's integration may not have registered
    or published its entity by the time reconcile runs. Treating that absent
    source as "removed" deleted the mirror and destroyed its long-term history
    (a real outdoor-outlet 5-hour gap). An unknown source must never trigger a
    deletion — only a source that is present-but-unmatched may.
    """
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    ent_reg = er.async_get(hass)
    # Source sensor.unloaded_power is neither in the registry nor the state
    # machine — exactly the not-yet-loaded case.
    _register_orphan_mirror(hass, "unloaded_power")
    assert ent_reg.async_get("sensor.unloaded_power_downsampled") is not None

    await _setup(hass)  # rule matches only SOURCE; the orphan's source is absent

    assert ent_reg.async_get(MIRROR) is not None  # live target kept
    # Preserved — its source might just be loading; we never destroy history.
    assert ent_reg.async_get("sensor.unloaded_power_downsampled") is not None


async def test_orphan_with_present_unmatched_source_raises_repair(
    recorder_hass: HomeAssistant,
) -> None:
    """A leftover mirror whose source still EXISTS but no rule matches it is
    NOT deleted — it raises a Repairs issue (we never auto-delete a mirror)."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    # Source is present (in the state machine) but not selected by the rule.
    hass.states.async_set("sensor.present_power", "5", MEAS)
    ent_reg = er.async_get(hass)
    uid, mirror_id = _register_orphan_mirror(hass, "present_power")
    assert ent_reg.async_get(mirror_id) is not None

    await _setup(hass)  # rule matches only SOURCE; present_power is unmatched

    assert ent_reg.async_get(MIRROR) is not None  # live target kept
    assert ent_reg.async_get(mirror_id) is not None  # preserved, not deleted
    assert ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid)) is not None


async def test_orphan_with_disabled_source_raises_repair(
    recorder_hass: HomeAssistant,
) -> None:
    """A mirror whose source is disabled is NOT deleted — a Repairs issue is
    raised instead, so the user decides."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    _register_disabled_source(hass, "disabled_power")
    uid, mirror_id = _register_orphan_mirror(hass, "disabled_power")

    await _setup(hass)

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(mirror_id) is not None  # kept, not deleted
    issue = ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid))
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


async def test_repair_delete_all_removes_mirrors_and_issues(
    recorder_hass: HomeAssistant,
) -> None:
    """The 'delete all' fix removes every flagged orphan and clears its issue."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    _register_disabled_source(hass, "disabled_power")
    uid, mirror_id = _register_orphan_mirror(hass, "disabled_power")
    await _setup(hass)
    manager = hass.data[DOMAIN][DATA_MANAGER]
    assert ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid))

    await manager.async_delete_all_orphans()

    assert er.async_get(hass).async_get(mirror_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid)) is None


async def test_repair_ignore_persists_and_suppresses(
    recorder_hass: HomeAssistant,
) -> None:
    """'Ignore' keeps the mirror, clears the issue, and doesn't re-raise."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    _register_disabled_source(hass, "disabled_power")
    uid, mirror_id = _register_orphan_mirror(hass, "disabled_power")
    await _setup(hass)
    manager = hass.data[DOMAIN][DATA_MANAGER]

    await manager.async_ignore_orphan(uid)
    assert er.async_get(hass).async_get(mirror_id) is not None  # kept
    assert ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid)) is None

    # Re-reconcile: ignored, so no issue comes back.
    manager.reconcile_orphans()
    assert ir.async_get(hass).async_get_issue(DOMAIN, _orphan_issue_id(uid)) is None
    assert er.async_get(hass).async_get(mirror_id) is not None


def _stat_meta(statistic_id: str, *, has_sum: bool) -> dict[str, Any]:
    return {
        "has_mean": not has_sum,
        "mean_type": (
            StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC
        ),
        "has_sum": has_sum,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_class": None,
        "unit_of_measurement": "kWh" if has_sum else "W",
    }


async def _seed_stats(
    hass: HomeAssistant, statistic_id: str, rows: list[dict[str, Any]], *, has_sum: bool
) -> None:
    meta = cast(StatisticMetaData, _stat_meta(statistic_id, has_sum=has_sum))
    async_import_statistics(hass, meta, cast("list[StatisticData]", rows))
    await async_wait_recording_done(hass)


async def _hourly(
    hass: HomeAssistant, statistic_id: str, types: set[str]
) -> list[dict[str, Any]]:
    data = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        NOW - timedelta(days=2),
        NOW + timedelta(hours=1),
        {statistic_id},
        "hour",
        None,
        types,
    )
    return cast("list[dict[str, Any]]", data.get(statistic_id, []))


async def _energy_mirror_with_stats(hass: HomeAssistant) -> tuple[str, str]:
    """Set up an energy mirror and seed source + mirror hourly statistics."""
    src, mir = "sensor.demo_energy", "sensor.demo_energy_downsampled"
    hass.states.async_set(
        src,
        "120",
        {
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
            "device_class": "energy",
        },
    )
    await _setup(hass, **{CONF_RULES: [{"name": "e", "entity_ids": [src]}]})
    assert er.async_get(hass).async_get(mir) is not None

    def h(k: int) -> datetime:  # k hours before NOW (top of the hour)
        return NOW - timedelta(hours=k)

    await _seed_stats(
        hass,
        src,
        [
            {"start": h(3), "state": 100.0, "sum": 0.0},
            {"start": h(2), "state": 110.0, "sum": 10.0},
            {"start": h(1), "state": 120.0, "sum": 20.0},  # cutover overlap
        ],
        has_sum=True,
    )
    await _seed_stats(
        hass,
        mir,
        [
            {"start": h(1), "state": 120.0, "sum": 0.0},
            {"start": h(0), "state": 121.0, "sum": 1.0},
        ],
        has_sum=True,
    )
    return src, mir


async def test_backfill_grafts_source_history_into_mirror(
    recorder_hass: HomeAssistant,
) -> None:
    """End-to-end: the mirror's statistics gain the source's pre-cutover history,
    shifted to a continuous, monotonic sum."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)

    manager = hass.data[DOMAIN][DATA_MANAGER]
    result = await manager.async_backfill([mir])
    await async_wait_recording_done(hass)
    assert any("grafted" in line for line in result)

    rows = await _hourly(hass, mir, {"state", "sum"})
    # history h(3),h(2) shifted by source sum at cutover (20) -> -20,-10, then
    # the mirror's own untouched rows 0, 1. Monotonic across the join.
    assert [r["sum"] for r in rows] == [-20.0, -10.0, 0.0, 1.0]


async def test_backfill_leaves_mirrors_own_rows_untouched(
    recorder_hass: HomeAssistant,
) -> None:
    """Guard for the recorder invariant we depend on: backfill only ADDS rows
    older than the mirror's first and never modifies the mirror's own (latest)
    rows. The recorder seeds a sensor's forward ``sum`` from those latest rows,
    so disturbing them would silently break continuity on the next compile. If a
    future HA change made the graft touch them, this fails loudly.
    """
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)
    before = {r["start"]: r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})}

    await hass.data[DOMAIN][DATA_MANAGER].async_backfill([mir])
    await async_wait_recording_done(hass)

    after = {r["start"]: r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})}
    # Every row the mirror already had is still present with the SAME sum ...
    for start, value in before.items():
        assert after[start] == value
    # ... and the backfill only added older rows.
    assert len(after) > len(before)
    assert min(after) < min(before)


async def test_backfill_is_idempotent_end_to_end(
    recorder_hass: HomeAssistant,
) -> None:
    """Re-running backfill leaves the mirror's statistics unchanged."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)
    manager = hass.data[DOMAIN][DATA_MANAGER]

    await manager.async_backfill([mir])
    await async_wait_recording_done(hass)
    before = [r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})]

    again = await manager.async_backfill([mir])
    await async_wait_recording_done(hass)
    after = [r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})]

    assert any("already backfilled" in line for line in again)
    assert after == before  # untouched on the second pass


async def test_backfill_guards_non_mirror_and_sourceless(
    recorder_hass: HomeAssistant,
) -> None:
    """Backfill skips non-mirrors and mirrors whose source has no statistics."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass)  # creates MIRROR; its source has no statistics
    manager = hass.data[DOMAIN][DATA_MANAGER]

    out = await manager.async_backfill(["sensor.not_a_mirror"])
    assert "not a downsampler mirror" in out[0]

    out = await manager.async_backfill([MIRROR])
    assert "no statistics" in out[0]


async def test_backfill_service_invokes_backfill(
    recorder_hass: HomeAssistant,
) -> None:
    """The recorder_downsampler.backfill_history service grafts the history."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)

    await hass.services.async_call(
        DOMAIN, "backfill_history", {"entity_id": [mir]}, blocking=True
    )
    await async_wait_recording_done(hass)

    rows = await _hourly(hass, mir, {"state", "sum"})
    assert [r["sum"] for r in rows] == [-20.0, -10.0, 0.0, 1.0]


async def test_backfill_grafts_mean_history_for_power_mirror(
    recorder_hass: HomeAssistant,
) -> None:
    """Mean (measurement) round-trip: a power mirror gains the source's
    pre-cutover mean/min/max history verbatim (no shift)."""
    hass = recorder_hass
    src, mir = "sensor.demo_w", "sensor.demo_w_downsampled"
    hass.states.async_set(
        src,
        "30",
        {
            "unit_of_measurement": "W",
            "state_class": "measurement",
            "device_class": "power",
        },
    )
    await _setup(hass, **{CONF_RULES: [{"name": "p", "entity_ids": [src]}]})
    assert er.async_get(hass).async_get(mir) is not None

    def h(k: int) -> datetime:
        return NOW - timedelta(hours=k)

    await _seed_stats(
        hass,
        src,
        [
            {"start": h(3), "mean": 10.0, "min": 5.0, "max": 15.0},
            {"start": h(2), "mean": 20.0, "min": 8.0, "max": 30.0},
            {"start": h(1), "mean": 30.0, "min": 9.0, "max": 40.0},  # cutover
        ],
        has_sum=False,
    )
    await _seed_stats(
        hass,
        mir,
        [
            {"start": h(1), "mean": 30.0, "min": 9.0, "max": 40.0},
            {"start": h(0), "mean": 31.0, "min": 9.5, "max": 41.0},
        ],
        has_sum=False,
    )

    manager = hass.data[DOMAIN][DATA_MANAGER]
    result = await manager.async_backfill([mir])
    await async_wait_recording_done(hass)
    assert any("grafted" in line for line in result)

    rows = await _hourly(hass, mir, {"mean", "min", "max"})
    # history h(3),h(2) copied verbatim, then the mirror's own h(1),h(0).
    assert [r["mean"] for r in rows] == [10.0, 20.0, 30.0, 31.0]
    assert [r["min"] for r in rows] == [5.0, 8.0, 9.0, 9.5]


async def test_backfill_skips_source_without_numeric_stats(
    recorder_hass: HomeAssistant,
) -> None:
    """A source whose statistics carry neither sum nor mean is skipped."""
    hass = recorder_hass
    src, mir = "sensor.demo_mode", "sensor.demo_mode_downsampled"
    hass.states.async_set(src, "auto", {})  # non-numeric source -> string mirror
    await _setup(hass, **{CONF_RULES: [{"name": "m", "entity_ids": [src]}]})
    assert er.async_get(hass).async_get(mir) is not None

    # Degenerate metadata: present in the registry but neither sum nor mean.
    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": src,
            "unit_class": None,
            "unit_of_measurement": None,
        },
        [{"start": NOW - timedelta(hours=1), "state": 1.0}],
    )
    await async_wait_recording_done(hass)

    out = await hass.data[DOMAIN][DATA_MANAGER].async_backfill([mir])
    assert "no numeric statistics" in out[0]


async def test_backfill_reports_no_history_when_no_earlier_rows(
    recorder_hass: HomeAssistant,
) -> None:
    """If the source has no statistics before the mirror's first row, there's
    nothing to graft (build returns no rows)."""
    hass = recorder_hass
    src, mir = "sensor.demo_energy", "sensor.demo_energy_downsampled"
    hass.states.async_set(
        src,
        "120",
        {
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
            "device_class": "energy",
        },
    )
    await _setup(hass, **{CONF_RULES: [{"name": "e", "entity_ids": [src]}]})
    cut = NOW - timedelta(hours=1)
    # Source and mirror both start at the same hour -> no earlier source rows.
    await _seed_stats(
        hass, src, [{"start": cut, "state": 120.0, "sum": 20.0}], has_sum=True
    )
    await _seed_stats(
        hass, mir, [{"start": cut, "state": 120.0, "sum": 0.0}], has_sum=True
    )

    out = await hass.data[DOMAIN][DATA_MANAGER].async_backfill([mir])
    assert "no source history" in out[0]


async def test_auto_backfill_flag_grafts_new_mirror_at_setup(
    recorder_hass: HomeAssistant,
) -> None:
    """With backfill_history: true, a brand-new mirror is grafted at setup.

    The mirror has no statistics yet, so the cutover anchors on "now" and the
    whole source history is shifted to end at ~0 there.
    """
    hass = recorder_hass
    src, mir = "sensor.demo_energy", "sensor.demo_energy_downsampled"
    hass.states.async_set(
        src,
        "120",
        {
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
            "device_class": "energy",
        },
    )

    def h(k: int) -> datetime:
        return NOW - timedelta(hours=k)

    # Seed source history BEFORE setup — the mirror doesn't exist yet.
    await _seed_stats(
        hass,
        src,
        [
            {"start": h(3), "state": 100.0, "sum": 0.0},
            {"start": h(2), "state": 110.0, "sum": 10.0},
            {"start": h(1), "state": 120.0, "sum": 20.0},
        ],
        has_sum=True,
    )

    await _setup(
        hass,
        **{
            CONF_BACKFILL_HISTORY: True,
            CONF_RULES: [{"name": "e", "entity_ids": [src]}],
        },
    )
    # The auto-backfill runs in a background task; wait for its grafted rows to
    # land (block_till_done alone can race the background task's recorder write).
    rows: list[dict[str, Any]] = []
    for _ in range(20):
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        rows = await _hourly(hass, mir, {"state", "sum"})
        if rows:
            break

    # cutover = now (after all source) -> offset = source sum at cutover (20),
    # whole history shifted to end at 0: -20, -10, 0. Monotonic.
    assert [r["sum"] for r in rows] == [-20.0, -10.0, 0.0]


async def _seed_pre_setup_energy(hass: HomeAssistant, src: str) -> None:
    """Register an energy source and seed 3 hourly sum rows, before setup.

    A mirror created later has no stats yet, so an auto-backfill anchors the
    cutover on "now" and shifts the whole source history to end at ~0
    (sums -20, -10, 0).
    """
    hass.states.async_set(
        src,
        "120",
        {
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
            "device_class": "energy",
        },
    )
    await _seed_stats(
        hass,
        src,
        [
            {"start": NOW - timedelta(hours=3), "state": 100.0, "sum": 0.0},
            {"start": NOW - timedelta(hours=2), "state": 110.0, "sum": 10.0},
            {"start": NOW - timedelta(hours=1), "state": 120.0, "sum": 20.0},
        ],
        has_sum=True,
    )


async def _wait_for_graft(hass: HomeAssistant, mirror: str) -> list[dict[str, Any]]:
    """Poll for an auto-backfill's grafted rows (background task can race)."""
    rows: list[dict[str, Any]] = []
    for _ in range(20):
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        rows = await _hourly(hass, mirror, {"state", "sum"})
        if rows:
            break
    return rows


async def test_per_rule_backfill_grafts_only_opted_in_rule(
    recorder_hass: HomeAssistant,
) -> None:
    """A per-rule backfill_history: true grafts only that rule's mirror.

    With the global default off, a second rule that doesn't opt in is left
    untouched — so auto-backfill can be staged one rule at a time.
    """
    hass = recorder_hass
    src_a, mir_a = "sensor.demo_a_energy", "sensor.demo_a_energy_downsampled"
    src_b, mir_b = "sensor.demo_b_energy", "sensor.demo_b_energy_downsampled"
    await _seed_pre_setup_energy(hass, src_a)
    await _seed_pre_setup_energy(hass, src_b)

    await _setup(
        hass,
        **{
            CONF_RULES: [
                {"name": "a", "entity_ids": [src_a], CONF_BACKFILL_HISTORY: True},
                {"name": "b", "entity_ids": [src_b]},  # inherits global (off)
            ]
        },
    )

    # opted-in rule's mirror is grafted; the other has no grafted history.
    assert [r["sum"] for r in await _wait_for_graft(hass, mir_a)] == [-20.0, -10.0, 0.0]
    assert await _hourly(hass, mir_b, {"state", "sum"}) == []


async def test_per_rule_backfill_false_opts_out_of_global(
    recorder_hass: HomeAssistant,
) -> None:
    """A per-rule backfill_history: false opts a rule out of a global true."""
    hass = recorder_hass
    src_a, mir_a = "sensor.demo_a_energy", "sensor.demo_a_energy_downsampled"
    src_b, mir_b = "sensor.demo_b_energy", "sensor.demo_b_energy_downsampled"
    await _seed_pre_setup_energy(hass, src_a)
    await _seed_pre_setup_energy(hass, src_b)

    await _setup(
        hass,
        **{
            CONF_BACKFILL_HISTORY: True,  # global on
            CONF_RULES: [
                {"name": "a", "entity_ids": [src_a]},  # inherits global (on)
                {"name": "b", "entity_ids": [src_b], CONF_BACKFILL_HISTORY: False},
            ],
        },
    )

    # inheriting rule is grafted; the opted-out rule is left untouched.
    assert [r["sum"] for r in await _wait_for_graft(hass, mir_a)] == [-20.0, -10.0, 0.0]
    assert await _hourly(hass, mir_b, {"state", "sum"}) == []


async def test_reload_enabling_backfill_grafts_like_startup(
    recorder_hass: HomeAssistant,
) -> None:
    """Turning a rule's backfill_history on via reload grafts its mirror — a
    reload is treated like startup for backfill, no restart needed."""
    hass = recorder_hass
    src, mir = "sensor.demo_energy", "sensor.demo_energy_downsampled"
    await _seed_pre_setup_energy(hass, src)

    # Set up with backfill OFF -> mirror created, nothing grafted yet.
    await _setup(hass, **{CONF_RULES: [{"name": "e", "entity_ids": [src]}]})
    assert er.async_get(hass).async_get(mir) is not None
    assert await _hourly(hass, mir, {"state", "sum"}) == []

    # Reload with the rule's backfill_history flipped on.
    manager = hass.data[DOMAIN][DATA_MANAGER]
    enabled = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_RULES: [
                    {"name": "e", "entity_ids": [src], CONF_BACKFILL_HISTORY: True}
                ],
            }
        }
    )[DOMAIN]
    manager.update_config(enabled)

    # The reload-triggered backfill runs in the background and grafts the mirror.
    assert [r["sum"] for r in await _wait_for_graft(hass, mir)] == [-20.0, -10.0, 0.0]


async def test_backfill_announces_event_and_persists_record(
    recorder_hass: HomeAssistant,
) -> None:
    """A real backfill fires the completion event and persists a run record."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)
    events = async_capture_events(hass, EVENT_BACKFILL_COMPLETED)
    manager = hass.data[DOMAIN][DATA_MANAGER]

    await manager.async_backfill([mir])
    await async_wait_recording_done(hass)

    assert len(events) == 1
    assert events[0].data["grafted"] == 1
    assert events[0].data["rows"] == 2  # h(3), h(2) — the rows before the cutover
    assert events[0].data["failed"] == 0
    # durable record persisted on the manager (and in its store).
    assert manager._last_backfill["grafted"] == 1
    assert manager._last_backfill["rows"] == 2
    assert "completed" in manager._last_backfill


async def test_backfill_dry_run_announces_nothing(
    recorder_hass: HomeAssistant,
) -> None:
    """dry_run reports but fires no event and writes no run record."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)
    events = async_capture_events(hass, EVENT_BACKFILL_COMPLETED)
    manager = hass.data[DOMAIN][DATA_MANAGER]

    await manager.async_backfill([mir], dry_run=True)

    assert events == []
    assert manager._last_backfill == {}


async def test_backfill_dry_run_writes_nothing(
    recorder_hass: HomeAssistant,
) -> None:
    """dry_run reports the planned graft but imports nothing and marks nothing."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)
    manager = hass.data[DOMAIN][DATA_MANAGER]
    before = {r["start"]: r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})}

    out = await manager.async_backfill([mir], dry_run=True)
    await async_wait_recording_done(hass)

    assert any("would graft" in line and "dry run" in line for line in out)
    # nothing imported ...
    after = {r["start"]: r["sum"] for r in await _hourly(hass, mir, {"state", "sum"})}
    assert after == before
    # ... and not marked backfilled, so a real run still works
    real = await manager.async_backfill([mir])
    assert any("grafted" in line for line in real)


async def test_backfill_all_mirrors_when_untargeted(
    recorder_hass: HomeAssistant,
) -> None:
    """Calling backfill with no target grafts every mirror."""
    hass = recorder_hass
    _src, mir = await _energy_mirror_with_stats(hass)

    manager = hass.data[DOMAIN][DATA_MANAGER]
    result = await manager.async_backfill()  # no entity_ids -> all mirrors
    await async_wait_recording_done(hass)

    assert any(mir in line and "grafted" in line for line in result)
    rows = await _hourly(hass, mir, {"state", "sum"})
    assert [r["sum"] for r in rows] == [-20.0, -10.0, 0.0, 1.0]


async def test_unload_entry_tears_down(recorder_hass: HomeAssistant) -> None:
    """Unloading the entry succeeds and drops the manager (entity removal then
    fires each mirror's async_on_remove -> _stop_timer, cancelling its timer)."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass)
    assert DATA_MANAGER in hass.data[DOMAIN]

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert DATA_MANAGER not in hass.data.get(DOMAIN, {})


async def test_dry_run_reload_stays_empty(recorder_hass: HomeAssistant) -> None:
    """A reload while still dry_run dispatches but creates nothing.

    Exercises the dry-run guard in the sensor platform's add-targets handler:
    even though a wider rule now resolves more sources, no mirror is created.
    """
    hass = recorder_hass
    hass.states.async_set("sensor.a_power", "100", MEAS)
    hass.states.async_set("sensor.b_power", "100", MEAS)
    await _setup(
        hass,
        **{
            CONF_DRY_RUN: True,
            CONF_INTERVAL: "00:00:10",
            CONF_RULES: [{"name": "demo", "entity_ids": ["sensor.a_power"]}],
        },
    )
    assert hass.states.get("sensor.a_power_downsampled") is None

    manager = hass.data[DOMAIN][DATA_MANAGER]
    wider = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_DRY_RUN: True,
                CONF_RULES: [
                    {"name": "demo", "entity_ids": ["sensor.a_power", "sensor.b_power"]}
                ],
            }
        }
    )[DOMAIN]
    manager.update_config(wider)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.a_power_downsampled") is None
    assert hass.states.get("sensor.b_power_downsampled") is None
    assert manager._created == {}
    assert len(manager.resolve_targets()) == 2  # resolution widened; log only


async def test_dry_run_then_live_reload_creates_mirrors(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Flipping dry_run true -> false on reload brings the mirrors up live."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_DRY_RUN: True, CONF_INTERVAL: "00:00:10"})
    assert hass.states.get(MIRROR) is None

    manager = hass.data[DOMAIN][DATA_MANAGER]
    live = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_DRY_RUN: False,
                CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE]}],
            }
        }
    )[DOMAIN]
    manager.update_config(live)
    await hass.async_block_till_done()

    assert hass.states.get(MIRROR) is not None
    assert len(manager._created) == 1

    # The now-live mirror emits on the next interval. Fire the second sample
    # at the interval midpoint so the time-weighted mean is (100+200)/2.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(SOURCE, "200", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(_state(hass, MIRROR).state) == pytest.approx(150.0)  # mean(100,200)


async def test_mirror_downsampled_to_history(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    from .conftest import count_states

    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # Two intervals, each with a fresh value.
    for i, val in enumerate(("120", "140"), start=1):
        hass.states.async_set(SOURCE, val, MEAS)
        await hass.async_block_till_done()
        freezer.move_to(NOW + timedelta(seconds=10 * i + 1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        await wait_for_recorder(hass)

    assert count_states(hass, MIRROR) >= 1


async def test_power_and_energy_pair_under_one_rule(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Mirror a representative outlet rule: one measurement + one accumulator.

    A single rule matches a ``measurement`` power channel and a
    ``total_increasing`` energy channel, with an explicit ``precision: 2``. The
    two mirrors must diverge by type under ``method: auto``:

    - power (measurement)        -> mean, rounded to 2 dp;
    - energy (total_increasing)  -> last, and NEVER rounded despite precision: 2
      (rounding a kWh accumulator would corrupt the Energy dashboard's sum).
    """
    hass = recorder_hass
    power_src, power_mirror = "sensor.outlet_power", "sensor.outlet_power_downsampled"
    energy_src, energy_mirror = (
        "sensor.outlet_energy",
        "sensor.outlet_energy_downsampled",
    )
    power_attrs = {"unit_of_measurement": "W", "state_class": "measurement"}
    energy_attrs = {"unit_of_measurement": "kWh", "state_class": "total_increasing"}

    hass.states.async_set(power_src, "100", power_attrs)  # seed buffers at setup
    hass.states.async_set(energy_src, "5.10000", energy_attrs)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_PRECISION: 2,  # explicit — must still NOT round the accumulator
        CONF_RULES: [{"name": "outlet", "entity_ids": [power_src, energy_src]}],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    # Mean is time-weighted; firing the second power sample at the interval
    # midpoint gives both samples equal dwell so mean(100, 102) = 101 holds.
    # The energy reading is `last`-aggregated, so its timing is immaterial.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(power_src, "102", power_attrs)  # mean(100, 102) = 101
    hass.states.async_set(energy_src, "5.55555", energy_attrs)  # last, 5 dp
    await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    power = hass.states.get(power_mirror)
    energy = hass.states.get(energy_mirror)
    assert power is not None and energy is not None

    # measurement -> mean, rounded to the requested 2 dp.
    assert float(power.state) == pytest.approx(101.0)
    assert power.attributes["method"] == "mean"
    assert power.attributes["precision"] == 2

    # total_increasing -> last, kept at full precision (NOT rounded to 5.56).
    assert float(energy.state) == pytest.approx(5.55555)
    assert energy.attributes["method"] == "last"
    assert energy.attributes["precision"] == "raw"


# ---------------------------------------------------------------------------
# Broad-rollout coverage: a realistic power-monitor-style fleet matched by
# integration_filter / regex, the way a real multi-device config typically looks.
# Each (object_id -> seed, second, attrs)
# row picks a state_class so `auto` method/precision is exercised per type.
# ---------------------------------------------------------------------------
_FLEET: dict[str, tuple[str, str, dict[str, object]]] = {
    # measurement -> mean; precision auto follows suggested_display_precision.
    "pm_ch1_power": (
        "100",
        "200",
        {
            "unit_of_measurement": "W",
            "state_class": "measurement",
            "device_class": "power",
            "suggested_display_precision": 0,
        },
    ),
    "pm_ch2_power": (  # no suggested precision -> stays raw
        "10.4",
        "10.6",
        {
            "unit_of_measurement": "W",
            "state_class": "measurement",
            "device_class": "power",
        },
    ),
    "pm_ch1_current": (
        "2.0",
        "3.0",
        {
            "unit_of_measurement": "A",
            "state_class": "measurement",
            "device_class": "current",
            "suggested_display_precision": 1,
        },
    ),
    "pm_voltage_l1": (
        "120.0",
        "122.0",
        {
            "unit_of_measurement": "V",
            "state_class": "measurement",
            "device_class": "voltage",
        },
    ),
    # cumulative counters -> last, NEVER rounded.
    "pm_ch1_energy": (
        "5.10000",
        "5.55555",
        {
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
            "device_class": "energy",
        },
    ),
    "pm_water_pulses": ("1000", "1005", {"state_class": "total"}),
    # non-numeric -> last sample (string).
    "pm_status": ("idle", "running", {}),
}


@pytest.fixture
def monitor_fleet(recorder_hass: HomeAssistant) -> tuple[HomeAssistant, str]:
    """A power monitor device with a varied channel fleet, plus foreign decoys.

    Returns ``(hass, monitor_device_id)``. The decoys — a smart_plug ``*_power`` and a
    wifi_light rssi — must never be mirrored by a power-monitor-scoped rule.
    """
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    monitor_entry = MockConfigEntry(domain="power_monitor")
    monitor_entry.add_to_hass(hass)
    monitor_device = dev_reg.async_get_or_create(
        config_entry_id=monitor_entry.entry_id,
        identifiers={("power_monitor", "pm1")},
    )
    for oid, (seed, _second, attrs) in _FLEET.items():
        ent_reg.async_get_or_create(
            "sensor",
            "power_monitor",
            oid,
            suggested_object_id=oid,
            config_entry=monitor_entry,
            device_id=monitor_device.id,
        )
        hass.states.async_set(f"sensor.{oid}", seed, attrs)

    # Decoys on other integrations — a broad power monitor rule must skip these.
    smart_plug_entry = MockConfigEntry(domain="smart_plug")
    smart_plug_entry.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "sensor",
        "smart_plug",
        "office_plug_power",
        suggested_object_id="office_plug_power",
        config_entry=smart_plug_entry,
    )
    hass.states.async_set(
        "sensor.office_plug_power",
        "55",
        {
            "unit_of_measurement": "W",
            "state_class": "measurement",
            "device_class": "power",
        },
    )
    wifi_light_entry = MockConfigEntry(domain="wifi_light")
    wifi_light_entry.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "sensor",
        "wifi_light",
        "wifi_signal_rssi",
        suggested_object_id="wifi_signal_rssi",
        config_entry=wifi_light_entry,
    )
    hass.states.async_set("sensor.wifi_signal_rssi", "-60", {})
    return hass, monitor_device.id


def _mirrors(hass: HomeAssistant) -> set[str]:
    return {
        s.entity_id
        for s in hass.states.async_all("sensor")
        if s.entity_id.endswith("_downsampled")
    }


async def _emit_one_interval(hass: HomeAssistant, freezer: Any) -> None:
    """Push each fleet source's second sample, then fire the emit timer.

    Pushes are timed to the interval midpoint so the seed (at setup time) and
    the second sample each get half the interval — time-weighted mean then
    reduces to the arithmetic mean, matching the fleet's expected values.
    """
    freezer.move_to(NOW + timedelta(seconds=5.5))
    for oid, (_seed, second, attrs) in _FLEET.items():
        hass.states.async_set(f"sensor.{oid}", second, attrs)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_broad_rollout_creates_typed_mirrors(
    monitor_fleet: tuple[HomeAssistant, str], freezer: Any
) -> None:
    """A whole-integration rule mirrors every monitor channel, typed correctly.

    Exercises `method`/`precision: auto` across measurement, total_increasing,
    total, and non-numeric sources at once — and proves the foreign decoys are
    not mirrored and that mirrors attach to the monitor device.
    """
    hass, monitor_device_id = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [{"name": "all monitor", "integration_filter": ["power_monitor"]}],
    }  # method + precision default to auto
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    # Exactly one mirror per monitor source; the foreign decoys get none.
    assert _mirrors(hass) == {f"sensor.{oid}_downsampled" for oid in _FLEET}
    assert hass.states.get("sensor.office_plug_power_downsampled") is None
    assert hass.states.get("sensor.wifi_signal_rssi_downsampled") is None

    # Mirrors attach to the source's monitor device card.
    assert (
        _entry(hass, "sensor.pm_ch1_power_downsampled").device_id == monitor_device_id
    )

    await _emit_one_interval(hass, freezer)

    def attr(oid: str, key: str) -> Any:
        return _state(hass, f"sensor.{oid}_downsampled").attributes[key]

    def val(oid: str) -> float:
        return float(_state(hass, f"sensor.{oid}_downsampled").state)

    # measurement -> mean; precision auto = suggested_display_precision (raw if none)
    assert val("pm_ch1_power") == pytest.approx(150.0)  # mean(100,200), sdp 0
    assert attr("pm_ch1_power", "method") == "mean"
    assert attr("pm_ch1_power", "precision") == 0
    assert val("pm_ch2_power") == pytest.approx(10.5)
    assert attr("pm_ch2_power", "precision") == "raw"  # no sdp
    assert val("pm_ch1_current") == pytest.approx(2.5)
    assert attr("pm_ch1_current", "precision") == 1
    assert val("pm_voltage_l1") == pytest.approx(121.0)
    assert attr("pm_voltage_l1", "method") == "mean"

    # cumulative counters -> last, NEVER rounded
    assert val("pm_ch1_energy") == pytest.approx(5.55555)
    assert attr("pm_ch1_energy", "method") == "last"
    assert attr("pm_ch1_energy", "precision") == "raw"
    assert val("pm_water_pulses") == pytest.approx(1005.0)
    assert attr("pm_water_pulses", "method") == "last"
    assert attr("pm_water_pulses", "precision") == "raw"

    # non-numeric -> last sample (string)
    assert _state(hass, "sensor.pm_status_downsampled").state == "running"
    assert attr("pm_status", "method") == "last"


async def test_broad_regex_stays_within_integration(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """`integration_filter + _power$` (match_mode all) mirrors only monitor power.

    The foreign smart_plug `*_power` and every non-power monitor channel are excluded —
    proving the scoping holds end to end, not just in resolve_rule_entities.
    """
    hass, _ = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {
                "name": "monitor power",
                "integration_filter": ["power_monitor"],
                "entity_regex_include": ["_power$"],
                "match_mode": "all",
            }
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    assert _mirrors(hass) == {
        "sensor.pm_ch1_power_downsampled",
        "sensor.pm_ch2_power_downsampled",
    }
    assert hass.states.get("sensor.office_plug_power_downsampled") is None  # foreign
    assert (
        hass.states.get("sensor.pm_ch1_energy_downsampled") is None
    )  # non-power monitor


async def test_reload_widening_regex_adds_mirrors(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """Opening up the regex on reload adds the newly matched mirrors live."""
    hass, _ = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {
                "name": "monitor",
                "integration_filter": ["power_monitor"],
                "entity_regex_include": ["_power$"],
                "match_mode": "all",
            }
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pm_ch1_energy_downsampled") is None

    # Widen the regex to also cover energy + current, via a reload (no restart).
    wider = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_RULES: [
                    {
                        "name": "monitor",
                        "integration_filter": ["power_monitor"],
                        "entity_regex_include": ["_(power|energy|current)$"],
                        "match_mode": "all",
                    }
                ],
            }
        }
    )[DOMAIN]
    hass.data[DOMAIN][DATA_MANAGER].update_config(wider)
    await hass.async_block_till_done()

    # New matches appear; out-of-scope channels and the foreign decoy do not.
    assert hass.states.get("sensor.pm_ch1_energy_downsampled") is not None
    assert hass.states.get("sensor.pm_ch1_current_downsampled") is not None
    assert hass.states.get("sensor.pm_voltage_l1_downsampled") is None
    assert hass.states.get("sensor.pm_water_pulses_downsampled") is None
    assert hass.states.get("sensor.office_plug_power_downsampled") is None


# ---------------------------------------------------------------------------
# Pre-deployment confidence: the recorder-churn reduction is the whole point,
# and the energy mirror must be usable by long-term statistics.
# ---------------------------------------------------------------------------


async def test_one_row_per_interval_then_idle_adds_none(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """The core value-prop: one recorded row per interval regardless of how
    many times the source updated, and ZERO rows for an idle interval."""
    from .conftest import count_states

    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # Interval 1: establish a recorded baseline for the mirror.
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await wait_for_recorder(hass)
    base = count_states(hass, MIRROR)
    assert base >= 1

    # Interval 2: FOUR source updates in the window -> exactly ONE mirror row.
    for v in ("200", "201", "202", "203"):
        hass.states.async_set(SOURCE, v, MEAS)
        await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await wait_for_recorder(hass)
    busy = count_states(hass, MIRROR)
    assert busy - base == 1  # one row for the interval, not four
    # The raw source, by contrast, recorded every update this interval.
    assert count_states(hass, SOURCE) >= 4

    # Interval 3: no source updates -> mirror holds, writes nothing (no churn).
    freezer.move_to(NOW + timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await wait_for_recorder(hass)
    assert count_states(hass, MIRROR) == busy  # idle interval adds no row


async def test_energy_mirror_is_statistics_ready(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """The energy mirror copies state_class / unit / device_class so HA compiles
    the same long-term statistics (and the Energy dashboard can point at it)."""
    hass = recorder_hass
    src, mirror = "sensor.pm_energy", "sensor.pm_energy_downsampled"
    attrs = {
        "unit_of_measurement": "kWh",
        "state_class": "total_increasing",
        "device_class": "energy",
    }
    hass.states.async_set(src, "5.0", attrs)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [{"name": "energy", "entity_ids": [src]}],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    hass.states.async_set(src, "6.0", attrs)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(mirror)
    assert st is not None
    # Statistics-defining metadata is mirrored from the source.
    assert st.attributes["state_class"] == "total_increasing"
    assert st.attributes["unit_of_measurement"] == "kWh"
    assert st.attributes["device_class"] == "energy"
    # And the accumulator is recorded as last, unrounded.
    assert float(st.state) == pytest.approx(6.0)
    assert st.attributes["method"] == "last"
    assert st.attributes["precision"] == "raw"


async def test_unavailable_samples_ignored_within_interval(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Sentinel states arriving mid-interval are dropped, not aggregated."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)  # valid (seeded at setup)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    hass.states.async_set(SOURCE, STATE_UNAVAILABLE, MEAS)  # dropped
    await hass.async_block_till_done()
    # Fire the valid second sample at the interval midpoint so the seed and
    # this sample each get half the interval -> time-weighted mean is 150.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(SOURCE, "200", MEAS)  # valid
    await hass.async_block_till_done()
    hass.states.async_set(SOURCE, STATE_UNKNOWN, MEAS)  # dropped
    await hass.async_block_till_done()

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # mean of the two valid samples only; sentinels did not skew it.
    assert float(_state(hass, MIRROR).state) == pytest.approx(150.0)


async def test_mirror_recovers_after_outage(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """After a full-interval outage, the mirror resumes emitting on recovery."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(_state(hass, MIRROR).state) == pytest.approx(100.0)

    # Outage for a whole interval -> unavailable.
    hass.states.async_set(SOURCE, STATE_UNAVAILABLE, MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=22))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, MIRROR).state == STATE_UNAVAILABLE

    # Source recovers -> the next interval emits a value again.
    hass.states.async_set(SOURCE, "300", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=33))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(_state(hass, MIRROR).state) == pytest.approx(300.0)


async def test_warn_unexcluded_logs_still_downsampled_source(
    recorder_hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """With warn_unexcluded, a mirrored source that is still recorded is logged.

    The in-process recorder records everything (no exclude filter), so the
    source IS recorded — this is the rollout step-3 safety net.
    """
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.recorder_downsampler"
    ):
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                DOMAIN: {
                    CONF_WARN_UNEXCLUDED: True,
                    CONF_INTERVAL: "00:00:10",
                    CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE]}],
                }
            },
        )
        await hass.async_block_till_done()

    assert "STILL being recorded" in caplog.text
    assert SOURCE in caplog.text


async def test_reload_service_applies_new_config(recorder_hass: HomeAssistant) -> None:
    """The recorder_downsampler.reload SERVICE re-reads YAML and applies it live.

    Exercises the real entry point (_reload -> async_integration_yaml_config ->
    update_config) that every other reload test bypasses by calling
    update_config directly.
    """
    hass = recorder_hass
    hass.states.async_set("sensor.a_power", "100", MEAS)
    hass.states.async_set("sensor.b_power", "100", MEAS)
    await _setup(
        hass,
        **{
            CONF_INTERVAL: "00:00:10",
            CONF_RULES: [{"name": "demo", "entity_ids": ["sensor.a_power"]}],
        },
    )
    assert hass.states.get("sensor.a_power_downsampled") is not None
    assert hass.states.get("sensor.b_power_downsampled") is None

    # The service reads configuration.yaml via async_integration_yaml_config;
    # patch that read to return a wider, schema-validated config.
    wider = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_RULES: [
                    {"name": "demo", "entity_ids": ["sensor.a_power", "sensor.b_power"]}
                ],
            }
        }
    )
    with patch(
        "custom_components.recorder_downsampler.async_integration_yaml_config",
        AsyncMock(return_value=wider),
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, {}, blocking=True)
        await hass.async_block_till_done()

    # b_power was added by the service-driven reload.
    assert hass.states.get("sensor.b_power_downsampled") is not None


async def test_reload_service_no_block_warns_and_keeps_config(
    recorder_hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """If reload finds no recorder_downsampler: block, it warns and keeps the
    previous config — no mirrors are torn down."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})
    assert hass.states.get(MIRROR) is not None
    manager = hass.data[DOMAIN][DATA_MANAGER]
    config_before = manager.config

    with (
        patch(
            "custom_components.recorder_downsampler.async_integration_yaml_config",
            AsyncMock(return_value={}),  # no recorder_downsampler: block
        ),
        caplog.at_level(
            logging.WARNING, logger="custom_components.recorder_downsampler"
        ),
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, {}, blocking=True)
        await hass.async_block_till_done()

    assert "found no recorder_downsampler: block" in caplog.text
    assert manager.config is config_before  # previous config preserved
    assert hass.states.get(MIRROR) is not None  # mirror not torn down


# ---------------------------------------------------------------------------
# Config-surface coverage: the rule/global knobs the selector vocabulary
# advertises but the earlier scenarios don't exercise yet — rule ordering, enabled,
# per-rule overrides, match_mode "any", precision "none", and the
# device_ids / globs / regex_exclude selectors driven end to end.
# ---------------------------------------------------------------------------


async def test_overlapping_rules_first_rule_wins(recorder_hass: HomeAssistant) -> None:
    """When two rules match the same source, the FIRST rule governs its mirror.

    The source is mirrored exactly once (not duplicated), configured by the
    earlier rule — proving the `seen` dedup and the "put specific rules first"
    contract in resolve_targets.
    """
    hass = recorder_hass
    hass.states.async_set("sensor.a_power", "100", MEAS)
    hass.states.async_set("sensor.b_power", "100", MEAS)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            # First (specific) rule claims a_power as MAX.
            {
                "name": "specific",
                "entity_ids": ["sensor.a_power"],
                "method": METHOD_MAX,
            },
            # Second (broad) rule also matches a_power, plus b_power, as MEAN.
            {
                "name": "broad",
                "entity_ids": ["sensor.a_power", "sensor.b_power"],
                "method": METHOD_MEAN,
            },
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    a = hass.states.get("sensor.a_power_downsampled")
    assert a is not None
    assert a.attributes["method"] == "max"  # first rule won, not the later mean
    assert a.attributes["rule"] == "specific"
    b = hass.states.get("sensor.b_power_downsampled")
    assert b is not None
    assert b.attributes["method"] == "mean"  # only the broad rule matched it
    # a_power mirrored exactly once — the second rule did not duplicate it.
    assert _mirrors(hass) == {
        "sensor.a_power_downsampled",
        "sensor.b_power_downsampled",
    }


async def test_disabled_rule_creates_no_mirror(recorder_hass: HomeAssistant) -> None:
    """`enabled: false` skips a rule entirely — its sources get no mirror."""
    hass = recorder_hass
    hass.states.async_set("sensor.on_power", "100", MEAS)
    hass.states.async_set("sensor.off_power", "100", MEAS)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {"name": "live", "entity_ids": ["sensor.on_power"]},
            {"name": "disabled", "entity_ids": ["sensor.off_power"], "enabled": False},
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    assert hass.states.get("sensor.on_power_downsampled") is not None
    assert hass.states.get("sensor.off_power_downsampled") is None
    assert _mirrors(hass) == {"sensor.on_power_downsampled"}


async def test_per_rule_overrides_globals(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """A rule's own interval / method / precision override the globals.

    Globals are slow + mean + auto; the rule overrides all three. The mirror
    must expose the rule's values, the emit must fire on the rule's faster
    cadence (the global cadence would not be due yet), and `precision: 0` — a
    falsy value — must be KEPT (round to integer), not mistaken for "inherit".
    """
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    config = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:01:00",  # global: 60s
        CONF_METHOD: METHOD_MEAN,  # global: mean
        CONF_PRECISION: "auto",  # global: auto
        CONF_RULES: [
            {
                "name": "override",
                "entity_ids": [SOURCE],
                "interval": "00:00:10",  # -> 10s
                "method": METHOD_MAX,  # -> max
                "precision": 0,  # -> integer; 0 is falsy but must be kept
            }
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: config})
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.attributes["method"] == "max"  # rule beat global mean
    assert st.attributes["interval_seconds"] == 10  # rule beat global 60s
    assert st.attributes["precision"] == 0  # 0 kept, not inherited as auto

    # Push samples and fire at the RULE's 10s cadence; the global 60s timer
    # would not be due at t+11s, so a value here proves the override governs.
    for v in ("100.4", "300.6", "200.5"):
        hass.states.async_set(SOURCE, v, MEAS)
        await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    # max = 300.6, rounded to 0 dp -> 301 (and rendered as an int, not "301.0").
    assert st.state == "301"
    assert float(st.state) == pytest.approx(301.0)


async def test_precision_none_keeps_raw_value(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """`precision: none` records the aggregate at full precision (no rounding)."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100.11", MEAS)  # measurement, no suggested dp
    await _setup(
        hass,
        **{
            CONF_INTERVAL: "00:00:10",
            CONF_METHOD: METHOD_MEAN,
            CONF_PRECISION: "none",
        },
    )

    # Two samples at uniform dwell -> time-weighted mean equals arithmetic mean.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(SOURCE, "100.14", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.attributes["precision"] == "raw"
    # mean(100.11, 100.14) = 100.125, kept whole — NOT rounded to 100.12 / 100.13.
    assert float(st.state) == pytest.approx(100.125)


async def test_match_mode_any_unions_end_to_end(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """`match_mode: any` UNIONS the selectors, end to end.

    integration_filter power monitor OR `_power$` -> every monitor channel PLUS the
    foreign smart_plug `*_power` (the cross-integration leak), but never the wifi_light
    rssi (neither monitor nor `*_power`). Contrast test_broad_regex_stays_within...
    which pins the `all` (intersection) behavior.
    """
    hass, _ = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {
                "name": "monitor or any power",
                "integration_filter": ["power_monitor"],
                "entity_regex_include": ["_power$"],
                "match_mode": "any",
            }
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    expected = {f"sensor.{oid}_downsampled" for oid in _FLEET}
    expected.add("sensor.office_plug_power_downsampled")  # foreign *_power, unioned in
    assert _mirrors(hass) == expected
    assert hass.states.get("sensor.wifi_signal_rssi_downsampled") is None


async def test_device_ids_selector_end_to_end(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """A `device_ids` rule mirrors every channel on that device, and nothing off it."""
    hass, monitor_device_id = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [{"name": "by device", "device_ids": [monitor_device_id]}],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    assert _mirrors(hass) == {f"sensor.{oid}_downsampled" for oid in _FLEET}
    # The decoys are on other devices / no device -> not matched.
    assert hass.states.get("sensor.office_plug_power_downsampled") is None
    assert hass.states.get("sensor.wifi_signal_rssi_downsampled") is None


async def test_entity_globs_selector_end_to_end(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """An `entity_globs` rule matches only the globbed ids, end to end."""
    hass, _ = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [{"name": "ch1 glob", "entity_globs": ["sensor.pm_ch1_*"]}],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    assert _mirrors(hass) == {
        "sensor.pm_ch1_power_downsampled",
        "sensor.pm_ch1_current_downsampled",
        "sensor.pm_ch1_energy_downsampled",
    }
    # ch2 / voltage / other channels and the foreign decoy are excluded.
    assert hass.states.get("sensor.pm_ch2_power_downsampled") is None
    assert hass.states.get("sensor.pm_voltage_l1_downsampled") is None
    assert hass.states.get("sensor.office_plug_power_downsampled") is None


async def test_entity_regex_exclude_subtracts_end_to_end(
    monitor_fleet: tuple[HomeAssistant, str],
) -> None:
    """`entity_regex_exclude` subtracts matches from an otherwise-scoped rule."""
    hass, _ = monitor_fleet
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_RULES: [
            {
                "name": "monitor minus energy",
                "integration_filter": ["power_monitor"],
                "entity_regex_exclude": ["_energy$"],
            }
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    # Every monitor channel EXCEPT the energy accumulator, which the exclude removed.
    assert hass.states.get("sensor.pm_ch1_energy_downsampled") is None
    assert hass.states.get("sensor.pm_ch1_power_downsampled") is not None
    expected = {
        f"sensor.{oid}_downsampled" for oid in _FLEET if not oid.endswith("_energy")
    }
    assert _mirrors(hass) == expected
    assert hass.states.get("sensor.office_plug_power_downsampled") is None


async def test_per_rule_dry_run_overrides_live_global(
    recorder_hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """With the global live, a rule can still be staged in dry_run on its own.

    The inheriting rule mirrors; the dry_run rule resolves and logs but creates
    nothing. The summary reports the mixed state and each source carries its
    own [LIVE] / [DRY RUN] tag.
    """
    hass = recorder_hass
    hass.states.async_set("sensor.live_power", "100", MEAS)
    hass.states.async_set("sensor.staged_power", "100", MEAS)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_DRY_RUN: False,  # global: live
        CONF_RULES: [
            {"name": "live", "entity_ids": ["sensor.live_power"]},  # inherit -> live
            {
                "name": "staged",
                "entity_ids": ["sensor.staged_power"],
                "dry_run": True,  # held dry on its own
            },
        ],
    }
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.recorder_downsampler"
    ):
        assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
        await hass.async_block_till_done()

    assert hass.states.get("sensor.live_power_downsampled") is not None
    assert hass.states.get("sensor.staged_power_downsampled") is None
    assert _mirrors(hass) == {"sensor.live_power_downsampled"}
    manager = hass.data[DOMAIN][DATA_MANAGER]
    assert set(manager._created) == {
        "recorder_downsampler_sensor.live_power_downsampled"
    }
    # Mixed-state summary + per-source tags.
    assert "mirroring 1 source sensor(s); 1 more in DRY RUN" in caplog.text
    assert "[LIVE] sensor.live_power" in caplog.text
    assert "[DRY RUN] sensor.staged_power" in caplog.text


async def test_per_rule_dry_run_overrides_dry_global(
    recorder_hass: HomeAssistant,
) -> None:
    """With the global in dry_run, a rule can opt INTO live on its own."""
    hass = recorder_hass
    hass.states.async_set("sensor.go_power", "100", MEAS)
    hass.states.async_set("sensor.wait_power", "100", MEAS)
    base = {
        CONF_WARN_UNEXCLUDED: False,
        CONF_INTERVAL: "00:00:10",
        CONF_DRY_RUN: True,  # global: dry
        CONF_RULES: [
            # opts into live despite the global dry_run
            {"name": "go", "entity_ids": ["sensor.go_power"], "dry_run": False},
            {"name": "wait", "entity_ids": ["sensor.wait_power"]},  # inherit -> dry
        ],
    }
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: base})
    await hass.async_block_till_done()

    assert hass.states.get("sensor.go_power_downsampled") is not None
    assert hass.states.get("sensor.wait_power_downsampled") is None
    assert _mirrors(hass) == {"sensor.go_power_downsampled"}


async def test_reload_flips_rule_dry_run(recorder_hass: HomeAssistant) -> None:
    """Flipping a rule's dry_run true->false adds its mirror; false->true removes it."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)

    # Start with the rule held in dry_run -> no mirror.
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_DRY_RUN: False,
                CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE], "dry_run": True}],
            }
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get(MIRROR) is None

    manager = hass.data[DOMAIN][DATA_MANAGER]

    def cfg(rule_dry: bool) -> Any:
        return CONFIG_SCHEMA(
            {
                DOMAIN: {
                    CONF_WARN_UNEXCLUDED: False,
                    CONF_INTERVAL: "00:00:10",
                    CONF_DRY_RUN: False,
                    CONF_RULES: [
                        {"name": "demo", "entity_ids": [SOURCE], "dry_run": rule_dry}
                    ],
                }
            }
        )[DOMAIN]

    # Flip the rule live -> mirror appears.
    manager.update_config(cfg(False))
    await hass.async_block_till_done()
    assert hass.states.get(MIRROR) is not None
    assert len(manager._created) == 1

    # Flip back to dry -> mirror is torn down.
    manager.update_config(cfg(True))
    await hass.async_block_till_done()
    assert hass.states.get(MIRROR) is None
    assert manager._created == {}


# ---------------------------------------------------------------------------
# copy_display_precision: seed the mirror's frontend display precision once at
# creation, from the source's effective precision, then never re-assert it.
# ---------------------------------------------------------------------------


def _register_source(
    hass: HomeAssistant, object_id: str, options: dict[str, Any]
) -> str:
    """Register a source entity and set its `sensor` registry options."""
    ent_reg = er.async_get(hass)
    entry = MockConfigEntry(domain="power_monitor")
    entry.add_to_hass(hass)
    e = ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        f"uid_{object_id}",
        suggested_object_id=object_id,
        config_entry=entry,
    )
    ent_reg.async_update_entity_options(e.entity_id, "sensor", options)
    return e.entity_id


async def test_copy_display_precision_once_seeds_from_source_override(
    recorder_hass: HomeAssistant,
) -> None:
    """`once` seeds the mirror's display_precision from the source's override,
    and preserves the mirror's own suggested_display_precision."""
    hass = recorder_hass
    src = _register_source(
        hass, "demo_power", {"display_precision": 1, "suggested_display_precision": 0}
    )
    assert src == SOURCE
    # The source's STATE carries a suggested precision -> the mirror copies it.
    hass.states.async_set(SOURCE, "100", {**MEAS, "suggested_display_precision": 0})

    await _setup(
        hass, **{CONF_INTERVAL: "00:00:10", CONF_COPY_DISPLAY_PRECISION: "once"}
    )

    opts = _sensor_opts(hass, MIRROR)
    assert opts.get("display_precision") == 1  # seeded from the source override
    assert opts.get("suggested_display_precision") == 0  # NOT clobbered by the seed


async def test_copy_display_precision_once_uses_suggested_when_no_override(
    recorder_hass: HomeAssistant,
) -> None:
    """With no user override on the source, `once` falls back to the source's
    suggested_display_precision."""
    hass = recorder_hass
    src = _register_source(hass, "demo_power", {"suggested_display_precision": 2})
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(
        hass, **{CONF_INTERVAL: "00:00:10", CONF_COPY_DISPLAY_PRECISION: "once"}
    )

    opts = _sensor_opts(hass, MIRROR)
    assert opts.get("display_precision") == 2


async def test_copy_display_precision_never_leaves_mirror_alone(
    recorder_hass: HomeAssistant,
) -> None:
    """The default `never` mode sets no display_precision override on the mirror."""
    hass = recorder_hass
    src = _register_source(
        hass, "demo_power", {"display_precision": 1, "suggested_display_precision": 0}
    )
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})  # copy_display_precision default

    opts = _sensor_opts(hass, MIRROR)
    assert opts.get("display_precision") is None


async def test_copy_display_precision_once_skips_existing_mirror(
    recorder_hass: HomeAssistant,
) -> None:
    """`once` only seeds brand-new mirrors — an existing display_precision (e.g.
    a user's choice from a prior run) is never overwritten."""
    hass = recorder_hass
    src = _register_source(hass, "demo_power", {"display_precision": 1})
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    # Pre-register the mirror as if it already existed, with the user's own
    # display_precision of 5.
    ent_reg = er.async_get(hass)
    mirror = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MIRROR}",
        suggested_object_id="demo_power_downsampled",
    )
    assert mirror.entity_id == MIRROR
    ent_reg.async_update_entity_options(MIRROR, "sensor", {"display_precision": 5})

    await _setup(
        hass, **{CONF_INTERVAL: "00:00:10", CONF_COPY_DISPLAY_PRECISION: "once"}
    )

    opts = _sensor_opts(hass, MIRROR)
    assert opts.get("display_precision") == 5  # untouched, not reseeded to 1


async def test_copy_display_precision_track_follows_source(
    recorder_hass: HomeAssistant,
) -> None:
    """`track` syncs the mirror to the source's effective precision at startup
    and again whenever the source changes — one-way, never writing the source."""
    hass = recorder_hass
    src = _register_source(hass, "demo_power", {"display_precision": 1})
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)
    ent_reg = er.async_get(hass)

    await _setup(
        hass, **{CONF_INTERVAL: "00:00:10", CONF_COPY_DISPLAY_PRECISION: "track"}
    )

    # Initial sync: mirror matches the source.
    assert _sensor_opts(hass, MIRROR).get("display_precision") == 1

    # Change the SOURCE's precision -> the mirror follows.
    ent_reg.async_update_entity_options(SOURCE, "sensor", {"display_precision": 4})
    await hass.async_block_till_done()
    assert _sensor_opts(hass, MIRROR).get("display_precision") == 4

    # The source itself is only ever what WE set in the test — the integration
    # never writes it (one-way tracking).
    assert _sensor_opts(hass, SOURCE).get("display_precision") == 4


async def test_copy_display_precision_track_clears_when_source_clears(
    recorder_hass: HomeAssistant,
) -> None:
    """If the source drops its precision, the mirror's tracked override clears too."""
    hass = recorder_hass
    src = _register_source(hass, "demo_power", {"display_precision": 3})
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)
    ent_reg = er.async_get(hass)

    await _setup(
        hass, **{CONF_INTERVAL: "00:00:10", CONF_COPY_DISPLAY_PRECISION: "track"}
    )
    assert _sensor_opts(hass, MIRROR).get("display_precision") == 3

    # Source clears its override -> mirror's tracked override is removed too.
    ent_reg.async_update_entity_options(SOURCE, "sensor", {})
    await hass.async_block_till_done()
    assert _sensor_opts(hass, MIRROR).get("display_precision") is None


# ---------------------------------------------------------------------------
# Config entry: the integration owns a single entry so it shows in the UI; the
# mirrors are owned by that entry yet still linked onto the source's device.
# ---------------------------------------------------------------------------


async def test_config_entry_created_and_owns_mirror(
    recorder_hass: HomeAssistant,
) -> None:
    """A single config entry is created and the mirror is owned by it (so it
    appears under the integration card), while still on the source device."""
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entry = MockConfigEntry(domain="power_monitor")
    entry.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("power_monitor", "pm1")}
    )
    src = ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "chan1",
        suggested_object_id="demo_power",
        config_entry=entry,
        device_id=device.id,
    )
    assert src.entity_id == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1  # single instance
    mirror = ent_reg.async_get(MIRROR)
    assert mirror is not None
    assert mirror.config_entry_id == entries[0].entry_id  # owned by our entry
    assert mirror.device_id == device.id  # still on the source's device card


async def test_existing_mirror_is_adopted_not_duplicated(
    recorder_hass: HomeAssistant,
) -> None:
    """A mirror left over with no config entry (the YAML-platform era) is
    adopted by the entry on setup — not duplicated."""
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    unique_id = f"{DOMAIN}_{MIRROR}"
    ent_reg.async_get_or_create(
        "sensor", DOMAIN, unique_id, suggested_object_id="demo_power_downsampled"
    )
    assert _entry(hass, MIRROR).config_entry_id is None  # pre-migration state
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert _entry(hass, MIRROR).config_entry_id == entry.entry_id  # adopted
    matches = [e for e in ent_reg.entities.values() if e.unique_id == unique_id]
    assert len(matches) == 1  # exactly one mirror, not duplicated


async def test_co_owns_source_device(recorder_hass: HomeAssistant) -> None:
    """Setup adds our config entry to the source's device, so the SAME device
    shows under both the source integration's card and ours."""
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    monitor = MockConfigEntry(domain="power_monitor")
    monitor.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=monitor.entry_id, identifiers={("power_monitor", "pm1")}
    )
    src = ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "chan1",
        suggested_object_id="demo_power",
        config_entry=monitor,
        device_id=device.id,
    )
    assert src.entity_id == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    ours = hass.config_entries.async_entries(DOMAIN)[0]
    dev = _device(hass, device.id)
    assert ours.entry_id in dev.config_entries  # co-owned -> shows on our card
    assert monitor.entry_id in dev.config_entries  # still the source integration's


async def test_drops_co_ownership_when_mirror_torn_down(
    recorder_hass: HomeAssistant,
) -> None:
    """Tearing a mirror down drops our device co-ownership (but the source
    integration keeps the device).

    A true orphan is never auto-deleted now (it raises a Repair instead), so we
    exercise the teardown via an explicit dry_run flip, which does remove the
    live mirror.
    """
    hass = recorder_hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    monitor = MockConfigEntry(domain="power_monitor")
    monitor.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=monitor.entry_id, identifiers={("power_monitor", "pm1")}
    )
    ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "chan1",
        suggested_object_id="demo_power",
        config_entry=monitor,
        device_id=device.id,
    )
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})
    ours = hass.config_entries.async_entries(DOMAIN)[0]
    assert ours.entry_id in _device(hass, device.id).config_entries

    # Flip the rule to dry_run -> mirror torn down -> co-ownership dropped.
    manager = hass.data[DOMAIN][DATA_MANAGER]
    dry = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_WARN_UNEXCLUDED: False,
                CONF_INTERVAL: "00:00:10",
                CONF_RULES: [{"name": "demo", "entity_ids": [SOURCE], "dry_run": True}],
            }
        }
    )[DOMAIN]
    manager.update_config(dry)
    await hass.async_block_till_done()

    assert hass.states.get(MIRROR) is None
    dev = _device(hass, device.id)
    assert ours.entry_id not in dev.config_entries  # we dropped it
    assert monitor.entry_id in dev.config_entries  # source integration keeps it


async def test_mirror_value_published_at_creation(
    recorder_hass: HomeAssistant,
) -> None:
    """When the source already has a value, the mirror is born populated — no
    `unknown` while waiting for the first interval emit."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    st = hass.states.get(MIRROR)
    assert st is not None
    assert st.state not in ("unknown", "unavailable")  # not the startup blip
    assert float(st.state) == pytest.approx(100.0)


async def test_seed_sample_still_counts_in_first_interval(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """Publishing at creation must not consume the seed: the first interval's
    mean still includes the value present at setup."""
    hass = recorder_hass
    hass.states.async_set(SOURCE, "100", MEAS)  # seeded + published at creation
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})
    assert float(_state(hass, MIRROR).state) == pytest.approx(100.0)  # born at 100

    # Fire the second sample at the interval midpoint so the seed and this
    # sample each get half the interval -> time-weighted mean = (100+200)/2.
    freezer.move_to(NOW + timedelta(seconds=5.5))
    hass.states.async_set(SOURCE, "200", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # mean(100, 200) — the seed was kept, not dropped by the creation publish.
    assert float(_state(hass, MIRROR).state) == pytest.approx(150.0)


async def test_unit_falls_back_to_registry_when_source_attrs_omit_it(
    recorder_hass: HomeAssistant,
) -> None:
    """A source mid-race can present a numeric state with ``state_class`` set
    but no ``unit_of_measurement`` in its attrs (e.g. the ``average``
    integration latches its unit from its sources on first read and can miss
    it on a cold boot). Without a fallback, the mirror would seed-publish a
    unitless value into long-term stats and permanently trip the recorder's
    unit-mismatch validator against historical rows in the source's real
    unit. We fall back to the source's registry-cached unit, which is what
    new stats rows must match."""
    hass = recorder_hass
    # Pre-register the source in the entity registry WITH a unit — this is
    # what survives across restarts from prior runs when the source was
    # publishing correctly.
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor",
        "demo",
        "src_no_unit_attr",
        suggested_object_id="demo_power",
        unit_of_measurement="°F",
    )
    # Mid-race: source state is numeric and records stats, but its attrs
    # don't carry unit_of_measurement.
    hass.states.async_set(SOURCE, "71.7", {"state_class": "measurement"})
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})

    # Mirror is born populated with the correct unit from the registry — not
    # unitless. New stats rows then match the historical unit, so no
    # unit-mismatch repair is raised.
    st = _state(hass, MIRROR)
    assert float(st.state) == pytest.approx(71.7)
    assert st.attributes.get("unit_of_measurement") == "°F"


async def test_no_state_written_until_first_value(
    recorder_hass: HomeAssistant, freezer: Any
) -> None:
    """With no usable source value at setup there's nothing to publish, so the
    mirror writes NO state — no `unknown` is recorded; it reads unavailable
    until its first value (we don't fabricate one)."""
    from .conftest import count_states

    hass = recorder_hass
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor", "demo", "late", suggested_object_id="demo_power"
    )  # registered, no state
    await _setup(hass, **{CONF_INTERVAL: "00:00:10", CONF_METHOD: METHOD_MEAN})
    await wait_for_recorder(hass)

    # Entity exists, but no state is written and nothing is recorded yet — the
    # initial `unknown` never lands in history.
    assert ent_reg.async_get(MIRROR) is not None
    assert hass.states.get(MIRROR) is None
    assert count_states(hass, MIRROR) == 0

    # Once the source delivers a value, the mirror publishes and the FIRST
    # recorded row is a real value (not `unknown`).
    hass.states.async_set(SOURCE, "100", MEAS)
    await hass.async_block_till_done()
    freezer.move_to(NOW + timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await wait_for_recorder(hass)

    assert float(_state(hass, MIRROR).state) == pytest.approx(100.0)
    assert count_states(hass, MIRROR) == 1  # only the real value, no unknown


async def test_initial_mirror_creation_waits_for_source_integration(
    recorder_hass: HomeAssistant,
) -> None:
    """On a cold boot the mirror isn't created until the integration its source
    belongs to has set up — not the whole-system started event."""
    from homeassistant.const import EVENT_COMPONENT_LOADED
    from homeassistant.core import CoreState
    from homeassistant.setup import ATTR_COMPONENT

    hass = recorder_hass
    hass.set_state(CoreState.starting)  # pretend we're still booting
    src = _register_source(hass, "demo_power", {})  # platform = power_monitor
    assert src == SOURCE
    hass.states.async_set(SOURCE, "100", MEAS)

    await _setup(hass, **{CONF_INTERVAL: "00:00:10"})

    # Deferred: the source's integration (power_monitor) isn't set up yet.
    assert hass.states.get(MIRROR) is None
    assert hass.data[DOMAIN][DATA_MANAGER]._created == {}

    # The source integration finishes setting up -> our mirror is created
    # (and born populated, since the source already has a value).
    hass.bus.async_fire(EVENT_COMPONENT_LOADED, {ATTR_COMPONENT: "power_monitor"})
    await hass.async_block_till_done()

    st = hass.states.get(MIRROR)
    assert st is not None
    assert float(st.state) == pytest.approx(100.0)
