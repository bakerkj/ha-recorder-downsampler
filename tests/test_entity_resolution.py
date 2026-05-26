# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for rule -> entity-id resolution.

Uses the HA test harness (hass fixture + entity/device registries) but no
recorder — resolution is pure registry/state-machine matching.

Every test asserts both directions: the entities a rule SHOULD match, and that
plausible decoys which could match (a sibling channel, the same integration on
another device, a foreign ``*_power`` sensor, our own mirror) DO NOT.
"""

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.recorder_downsampler import resolve_rule_entities

# The include / exclude a representative power-monitor downsample rule uses. Kept
# here so the tests validate the exact regex shape in one place: mirror every
# *_power / *_energy channel EXCEPT the whole-feed grid / battery / solar /
# main-panel channels (split-phase feeds are *_power_leg_N, solar is *_power_N),
# which stay at full resolution. Power feeds end in _leg_N / _N (not _power) so
# the include never grabs them; the exclude exists to drop their *_energy twins.
_PM_INCLUDE = ["_power$", "_energy$"]
_PM_EXCLUDE = [
    r"^sensor\.(grid|main_panel)_power_leg_\d+(_energy)?$",
    r"^sensor\.battery_\d+_power_leg_\d+(_energy)?$",
    r"^sensor\.solar_power_\d+(_energy)?$",
]


@pytest.fixture
def populated(hass: HomeAssistant) -> HomeAssistant:
    """Register entities across platforms and devices for matching.

    Includes deliberate decoys:
    - ``sensor.office_plug_power`` on the *smart_plug* platform — a foreign
      ``*_power`` entity a bare ``_power$`` regex would wrongly grab.
    - power monitor channels split across two devices, so device_ids matching can be
      shown to exclude same-integration entities on a different device.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    monitor_entry = MockConfigEntry(domain="power_monitor")
    monitor_entry.add_to_hass(hass)
    smart_plug_entry = MockConfigEntry(domain="smart_plug")
    smart_plug_entry.add_to_hass(hass)

    monitor_device = dev_reg.async_get_or_create(
        config_entry_id=monitor_entry.entry_id,
        identifiers={("power_monitor", "pm1")},
    )
    other_device = dev_reg.async_get_or_create(
        config_entry_id=monitor_entry.entry_id,
        identifiers={("power_monitor", "pm2")},
    )

    def reg(
        platform: str,
        unique: str,
        object_id: str,
        config_entry: MockConfigEntry | None = None,
        device_id: str | None = None,
    ) -> None:
        ent_reg.async_get_or_create(
            "sensor",
            platform,
            unique,
            suggested_object_id=object_id,
            config_entry=config_entry,
            device_id=device_id,
        )

    # power monitor: two channels on monitor_device, one on other_device.
    reg("power_monitor", "g1", "water_heater_power", monitor_entry, monitor_device.id)
    reg("power_monitor", "g2", "water_heater_energy", monitor_entry, monitor_device.id)
    reg("power_monitor", "g3", "dryer_power", monitor_entry, other_device.id)
    # Foreign integration that also has a *_power entity — the leak decoy.
    reg("smart_plug", "s1", "office_plug_power", smart_plug_entry)
    # Unrelated integration / name.
    reg("wifi_light", "w1", "wifi_signal_rssi")
    # A pre-existing mirror — must never be re-matched.
    reg("recorder_downsampler", "m1", "dryer_power_downsampled")
    return hass


def _device_id(hass: HomeAssistant, entity_id: str) -> str:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None and entry.device_id is not None
    return entry.device_id


def test_integration_filter(populated: HomeAssistant) -> None:
    matched = resolve_rule_entities(
        populated, {"integration_filter": ["power_monitor"]}
    )
    assert "sensor.water_heater_power" in matched
    assert "sensor.dryer_power" in matched
    # other integrations are excluded, including a foreign *_power sensor
    assert "sensor.wifi_signal_rssi" not in matched
    assert "sensor.office_plug_power" not in matched
    # and never our own mirror
    assert "sensor.dryer_power_downsampled" not in matched


def test_entity_ids_exact_match(populated: HomeAssistant) -> None:
    matched = resolve_rule_entities(
        populated,
        {"entity_ids": ["sensor.water_heater_power", "sensor.dryer_power"]},
    )
    assert matched == {"sensor.water_heater_power", "sensor.dryer_power"}
    # a sibling channel that EXISTS but was not listed is not pulled in
    assert "sensor.water_heater_energy" not in matched
    assert "sensor.office_plug_power" not in matched


def test_entity_ids_nonexistent_is_dropped(populated: HomeAssistant) -> None:
    # A typo'd / not-yet-existing id is silently dropped, never errors, and
    # never widens the match.
    matched = resolve_rule_entities(
        populated,
        {"entity_ids": ["sensor.water_heater_power", "sensor.does_not_exist"]},
    )
    assert matched == {"sensor.water_heater_power"}


def test_integration_filter_plus_entity_ids(populated: HomeAssistant) -> None:
    # A common pattern: integration_filter guards the rule to one integration,
    # entity_ids narrows. With match_mode "all" they intersect, so an id from
    # another integration listed by mistake is dropped by the guard.
    matched = resolve_rule_entities(
        populated,
        {
            "integration_filter": ["power_monitor"],
            "entity_ids": [
                "sensor.water_heater_power",
                "sensor.water_heater_energy",
                "sensor.office_plug_power",  # smart_plug — must be excluded
            ],
            "match_mode": "all",
        },
    )
    assert matched == {"sensor.water_heater_power", "sensor.water_heater_energy"}
    assert "sensor.office_plug_power" not in matched


def test_bare_regex_leaks_across_integrations(populated: HomeAssistant) -> None:
    # A bare _power$ regex with no scope grabs EVERY *_power entity, including
    # the foreign smart_plug one. This is exactly why a regex must be paired with a
    # scoping selector under match_mode "all" (see the test below).
    matched = resolve_rule_entities(populated, {"entity_regex_include": ["_power$"]})
    assert "sensor.office_plug_power" in matched  # the leak, demonstrated
    assert {"sensor.water_heater_power", "sensor.dryer_power"} <= matched
    # but never the energy channel (no _power suffix) nor the mirror
    assert "sensor.water_heater_energy" not in matched
    assert "sensor.dryer_power_downsampled" not in matched


def test_match_mode_all_intersects(populated: HomeAssistant) -> None:
    # power monitor AND name ends in _power -> only the monitor power channels; the
    # foreign smart_plug _power is contained out by the integration_filter.
    matched = resolve_rule_entities(
        populated,
        {
            "integration_filter": ["power_monitor"],
            "entity_regex_include": ["_power$"],
            "match_mode": "all",
        },
    )
    assert matched == {"sensor.water_heater_power", "sensor.dryer_power"}
    assert "sensor.office_plug_power" not in matched
    assert "sensor.water_heater_energy" not in matched


def test_match_mode_any_unions(populated: HomeAssistant) -> None:
    # match_mode "any" UNIONS selectors: every power monitor entity OR anything
    # ending in _power. This is where a broad regex leaks beyond the
    # integration — pin that behavior so the difference from "all" is explicit.
    matched = resolve_rule_entities(
        populated,
        {
            "integration_filter": ["power_monitor"],
            "entity_regex_include": ["_power$"],
            "match_mode": "any",
        },
    )
    # all power monitor (incl. the energy channel, which has no _power suffix) ...
    assert {
        "sensor.water_heater_power",
        "sensor.water_heater_energy",
        "sensor.dryer_power",
    } <= matched
    # ... PLUS the foreign _power entity (the union leak)
    assert "sensor.office_plug_power" in matched
    # still never the mirror
    assert "sensor.dryer_power_downsampled" not in matched


def test_regex_exclude_subtracts(populated: HomeAssistant) -> None:
    matched = resolve_rule_entities(
        populated,
        {
            "integration_filter": ["power_monitor"],
            "entity_regex_exclude": ["dryer"],
        },
    )
    assert "sensor.dryer_power" not in matched
    assert "sensor.water_heater_power" in matched


def test_device_ids_matches_only_that_device(populated: HomeAssistant) -> None:
    monitor_device_id = _device_id(populated, "sensor.water_heater_power")
    matched = resolve_rule_entities(populated, {"device_ids": [monitor_device_id]})
    assert matched == {"sensor.water_heater_power", "sensor.water_heater_energy"}
    # dryer_power is the SAME integration but on a different device -> excluded
    assert "sensor.dryer_power" not in matched
    assert "sensor.office_plug_power" not in matched


def test_device_ids_plus_regex_intersect(populated: HomeAssistant) -> None:
    # device + _power, match_mode all -> only the power channel ON that device.
    monitor_device_id = _device_id(populated, "sensor.water_heater_power")
    matched = resolve_rule_entities(
        populated,
        {
            "device_ids": [monitor_device_id],
            "entity_regex_include": ["_power$"],
            "match_mode": "all",
        },
    )
    assert matched == {"sensor.water_heater_power"}
    # energy channel on the device is dropped by the regex; dryer_power matches
    # the regex but is on another device.
    assert "sensor.water_heater_energy" not in matched
    assert "sensor.dryer_power" not in matched


def test_globs(populated: HomeAssistant) -> None:
    matched = resolve_rule_entities(populated, {"entity_globs": ["sensor.*_rssi"]})
    assert matched == {"sensor.wifi_signal_rssi"}


def test_mirrors_never_matched(populated: HomeAssistant) -> None:
    matched = resolve_rule_entities(
        populated, {"entity_globs": ["sensor.dryer_power*"]}
    )
    assert "sensor.dryer_power_downsampled" not in matched
    assert "sensor.dryer_power" in matched


def test_no_selectors_matches_nothing(populated: HomeAssistant) -> None:
    assert resolve_rule_entities(populated, {}) == set()


# ---------------------------------------------------------------------------
# A realistic whole-home energy-monitor rule: power + energy, minus the
# grid/battery/solar/main feeds. Exercises the exact multi-include + multi-exclude
# combination in one scenario (the rest of the file only covers one include or one
# exclude at a time), and the feed-protection edge cases.
# ---------------------------------------------------------------------------

# Load circuits we DO want downsampled (each has a power and an energy channel).
# kitchen_main_lights has "main" in its NAME but is a load, not a feed — it must
# stay in, proving feeds are matched by pattern, not by the word "main".
_PM_LOADS = [
    "refrigerator_power",
    "refrigerator_energy",
    "dryer_power",
    "dryer_energy",
    "kitchen_main_lights_power",
    "kitchen_main_lights_energy",
]

# Whole-feed channels that must NEVER be downsampled (kept full-res).
_PM_FEEDS = [
    "grid_power_leg_1",
    "grid_power_leg_2",
    "grid_power_leg_1_energy",
    "grid_power_leg_2_energy",
    "solar_power_1",
    "solar_power_2",
    "solar_power_1_energy",
    "solar_power_2_energy",
    "battery_1_power_leg_1",
    "battery_1_power_leg_1_energy",
    "battery_2_power_leg_2",
    "battery_2_power_leg_2_energy",
    "main_panel_power_leg_1",
    "main_panel_power_leg_2",
    "main_panel_power_leg_1_energy",
    "main_panel_power_leg_2_energy",
]


@pytest.fixture
def monitor_fleet(hass: HomeAssistant) -> HomeAssistant:
    """A power monitor fleet: load circuits, the grid/battery/solar/main feeds, and
    decoys (a foreign *_power, a non power/energy channel, a mirror)."""
    ent_reg = er.async_get(hass)
    monitor = MockConfigEntry(domain="power_monitor")
    monitor.add_to_hass(hass)
    smart_plug = MockConfigEntry(domain="smart_plug")
    smart_plug.add_to_hass(hass)

    def reg(
        platform: str, object_id: str, entry: MockConfigEntry | None = None
    ) -> None:
        ent_reg.async_get_or_create(
            "sensor",
            platform,
            object_id,
            suggested_object_id=object_id,
            config_entry=entry,
        )

    for oid in _PM_LOADS + _PM_FEEDS:
        reg("power_monitor", oid, monitor)
    reg("smart_plug", "office_plug_power", smart_plug)  # foreign *_power leak decoy
    reg("power_monitor", "refrigerator_current", monitor)  # not _power/_energy
    reg("power_monitor", "main_panel_voltage", monitor)  # a feed, but not pwr/energy
    reg("recorder_downsampler", "refrigerator_power_downsampled")  # our own mirror
    return hass


def test_rule_downsamples_loads_not_feeds(monitor_fleet: HomeAssistant) -> None:
    matched = resolve_rule_entities(
        monitor_fleet,
        {
            "integration_filter": ["power_monitor"],
            "entity_regex_include": _PM_INCLUDE,
            "entity_regex_exclude": _PM_EXCLUDE,
            "match_mode": "all",
        },
    )
    # Exactly the load circuits (power AND energy) — nothing more, nothing less.
    assert matched == {f"sensor.{oid}" for oid in _PM_LOADS}
    # "main" in a load name does not get it excluded.
    assert "sensor.kitchen_main_lights_power" in matched
    assert "sensor.kitchen_main_lights_energy" in matched
    # No grid/battery/solar/main-panel feed leaks through (power or energy).
    for oid in _PM_FEEDS:
        assert f"sensor.{oid}" not in matched
    # Non power/energy channels never match the include.
    assert "sensor.refrigerator_current" not in matched
    assert "sensor.main_panel_voltage" not in matched
    # Foreign integration is contained out by integration_filter.
    assert "sensor.office_plug_power" not in matched
    # And never our own mirror.
    assert "sensor.refrigerator_power_downsampled" not in matched


def test_exclude_is_load_bearing(monitor_fleet: HomeAssistant) -> None:
    # Drop the exclude and the *_energy feed channels leak in (they end in
    # _energy), proving the exclude is doing real work. The bare power feeds
    # still don't appear — they end in _leg_N / _N, so _power$ never matches.
    matched = resolve_rule_entities(
        monitor_fleet,
        {
            "integration_filter": ["power_monitor"],
            "entity_regex_include": _PM_INCLUDE,
            "match_mode": "all",
        },
    )
    leaked = {
        "sensor.grid_power_leg_1_energy",
        "sensor.solar_power_1_energy",
        "sensor.battery_1_power_leg_1_energy",
        "sensor.main_panel_power_leg_1_energy",
    }
    assert leaked <= matched
    # The power feeds are absent regardless (no _power suffix to match).
    assert "sensor.grid_power_leg_1" not in matched
    assert "sensor.solar_power_1" not in matched


def test_disabled_entities_are_skipped(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    # A disabled source (e.g. an unwired monitor channel the integration disables)
    # has no state, so mirroring it only creates a dead `unavailable` mirror.
    ent_reg = er.async_get(hass)
    monitor = MockConfigEntry(domain="power_monitor")
    monitor.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "wired",
        suggested_object_id="wired_power",
        config_entry=monitor,
    )
    ent_reg.async_get_or_create(
        "sensor",
        "power_monitor",
        "unwired",
        suggested_object_id="unwired_power",
        config_entry=monitor,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.recorder_downsampler"
    ):
        matched = resolve_rule_entities(
            hass,
            {"name": "monitor", "integration_filter": ["power_monitor"]},
            log=True,
        )
    assert "sensor.wired_power" in matched
    assert "sensor.unwired_power" not in matched
    # The skip is logged (once per rollout): count at info, names at debug.
    assert 'rule "monitor": skipped 1 disabled source(s)' in caplog.text
    assert "sensor.unwired_power" in caplog.text
