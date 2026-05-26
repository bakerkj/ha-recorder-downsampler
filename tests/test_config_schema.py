# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for CONFIG_SCHEMA validation and defaults."""

from datetime import timedelta
from typing import Any, cast

import pytest
import voluptuous as vol

from custom_components.recorder_downsampler import CONFIG_SCHEMA
from custom_components.recorder_downsampler.const import (
    CONF_COPY_DISPLAY_PRECISION,
    CONF_DRY_RUN,
    CONF_INTERVAL,
    CONF_METHOD,
    CONF_PRECISION,
    CONF_RULES,
    CONF_WARN_UNEXCLUDED,
    DEFAULT_INTERVAL,
    DEFAULT_METHOD,
    DEFAULT_PRECISION,
    DOMAIN,
    METHOD_MEAN,
    PRECISION_NONE,
)


def _validate(domain_block: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], CONFIG_SCHEMA({DOMAIN: domain_block})[DOMAIN])


def test_empty_applies_defaults() -> None:
    cfg = _validate({})
    assert cfg[CONF_INTERVAL] == DEFAULT_INTERVAL
    assert cfg[CONF_METHOD] == DEFAULT_METHOD
    assert cfg[CONF_PRECISION] == DEFAULT_PRECISION
    assert cfg[CONF_WARN_UNEXCLUDED] is True
    assert cfg[CONF_DRY_RUN] is False
    assert cfg[CONF_RULES] == []


def test_interval_parsed_to_timedelta() -> None:
    cfg = _validate({CONF_INTERVAL: "00:05:00"})
    assert cfg[CONF_INTERVAL] == timedelta(minutes=5)


def test_rule_with_selectors() -> None:
    cfg = _validate(
        {
            CONF_RULES: [
                {
                    "name": "Fast power sensors",
                    "integration_filter": ["power_monitor"],
                    "entity_regex_include": ["_power$"],
                    "interval": "00:01:00",
                    "method": "mean",
                }
            ]
        }
    )
    rule = cfg[CONF_RULES][0]
    assert rule["name"] == "Fast power sensors"
    assert rule["interval"] == timedelta(minutes=1)
    assert rule["method"] == METHOD_MEAN
    assert rule["enabled"] is True  # default


def test_rule_inherits_when_unset() -> None:
    cfg = _validate({CONF_RULES: [{"name": "r"}]})
    rule = cfg[CONF_RULES][0]
    assert rule[CONF_INTERVAL] is None  # None => inherit top-level
    assert rule[CONF_METHOD] is None
    assert rule[CONF_PRECISION] is None
    assert rule[CONF_DRY_RUN] is None


def test_rule_dry_run_accepts_bool_and_defaults_to_inherit() -> None:
    assert _validate({CONF_RULES: [{"name": "r"}]})[CONF_RULES][0][CONF_DRY_RUN] is None
    on = _validate({CONF_RULES: [{"name": "r", "dry_run": True}]})[CONF_RULES][0]
    assert on[CONF_DRY_RUN] is True
    off = _validate({CONF_RULES: [{"name": "r", "dry_run": False}]})[CONF_RULES][0]
    assert off[CONF_DRY_RUN] is False


def test_copy_display_precision_default_and_values() -> None:
    assert _validate({})[CONF_COPY_DISPLAY_PRECISION] == "never"  # default
    for mode in ("never", "once", "track"):
        assert (
            _validate({CONF_COPY_DISPLAY_PRECISION: mode})[CONF_COPY_DISPLAY_PRECISION]
            == mode
        )
    with pytest.raises(vol.Invalid):
        _validate({CONF_COPY_DISPLAY_PRECISION: "sometimes"})


def test_precision_accepts_auto_none_and_int() -> None:
    assert _validate({CONF_PRECISION: "none"})[CONF_PRECISION] == PRECISION_NONE
    assert _validate({CONF_PRECISION: 2})[CONF_PRECISION] == 2
    assert _validate({CONF_PRECISION: 0})[CONF_PRECISION] == 0  # round to integer


def test_invalid_method_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _validate({CONF_METHOD: "bogus"})


def test_invalid_precision_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _validate({CONF_PRECISION: -1})
    with pytest.raises(vol.Invalid):
        _validate({CONF_PRECISION: "lots"})


def test_invalid_regex_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _validate({CONF_RULES: [{"name": "r", "entity_regex_include": ["("]}]})
