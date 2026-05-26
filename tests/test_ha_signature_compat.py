# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Early-warning guard against Home Assistant API drift.

This integration leans on a few HA internals/public APIs whose shape we depend
on. The ha-dev-compat workflow runs this file against HA's `dev` branch weekly;
a failure here means upstream changed something we use — not that this branch is
broken. Keep these assertions tight and few.
"""

import inspect


def test_recorder_is_entity_recorded_api() -> None:
    """We call ``recorder.is_entity_recorded(hass, entity_id)`` for warn-unexcluded."""
    from homeassistant.components.recorder import is_entity_recorded

    assert callable(is_entity_recorded)
    # We rely on the (hass, entity_id) -> bool signature.
    params = list(inspect.signature(is_entity_recorded).parameters)
    assert params[:2] == ["hass", "entity_id"]


def test_async_track_time_interval_signature() -> None:
    """We call async_track_time_interval(hass, action, interval)."""
    from homeassistant.helpers.event import async_track_time_interval

    params = list(inspect.signature(async_track_time_interval).parameters)
    assert params[:3] == ["hass", "action", "interval"]


def test_state_class_values() -> None:
    """resolve_method() compares against these literal state_class strings."""
    from homeassistant.components.sensor import SensorStateClass

    assert SensorStateClass.MEASUREMENT.value == "measurement"
    assert SensorStateClass.TOTAL.value == "total"
    assert SensorStateClass.TOTAL_INCREASING.value == "total_increasing"


def test_sensor_entity_native_value_attr() -> None:
    """DownsampleSensor sets _attr_native_value / _attr_native_unit_of_measurement."""
    from homeassistant.components.sensor import SensorEntity

    assert hasattr(SensorEntity, "native_value")
    assert hasattr(SensorEntity, "native_unit_of_measurement")
