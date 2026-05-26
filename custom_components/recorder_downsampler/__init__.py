# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Recorder Downsampler — mirror fast sensors into slow, recorded siblings.

The source entity keeps updating at full speed (live display is unaffected —
the frontend reads live state, not the recorder). For each matched source we
create one mirror sensor that emits an aggregated value on a fixed interval, so
the *recorder* sees one row per interval instead of one per source update.

Pair each rule with a matching ``recorder:`` exclude so the raw source stops
churning — an integration cannot change the recorder's include/exclude filter
itself (that filter is built once at recorder setup), so we only *warn* when a
mirrored source is still being recorded (see ``warn_unexcluded``).

Config lives under ``recorder_downsampler:`` in configuration.yaml and reuses the
selector vocabulary of the sibling ha-recorder-tuning integration.
"""

from __future__ import annotations

import logging
import re

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import SERVICE_RELOAD, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BACKFILL_HISTORY,
    CONF_COPY_DISPLAY_PRECISION,
    CONF_DEVICE_IDS,
    CONF_DRY_RUN,
    CONF_ENABLED,
    CONF_ENTITY_GLOBS,
    CONF_ENTITY_IDS,
    CONF_ENTITY_REGEX_EXCLUDE,
    CONF_ENTITY_REGEX_INCLUDE,
    CONF_INTEGRATION_FILTER,
    CONF_INTERVAL,
    CONF_MATCH_MODE,
    CONF_METHOD,
    CONF_PRECISION,
    CONF_RULE_NAME,
    CONF_RULES,
    CONF_WARN_UNEXCLUDED,
    COPY_DISPLAY_PRECISION_MODES,
    DATA_MANAGER,
    DATA_YAML_CONFIG,
    DEFAULT_BACKFILL_HISTORY,
    DEFAULT_COPY_DISPLAY_PRECISION,
    DEFAULT_DRY_RUN,
    DEFAULT_INTERVAL,
    DEFAULT_MATCH_MODE,
    DEFAULT_METHOD,
    DEFAULT_PRECISION,
    DEFAULT_WARN_UNEXCLUDED,
    DOMAIN,
    MATCH_MODE_ALL,
    MATCH_MODE_ANY,
    METHODS,
    PRECISION_AUTO,
    PRECISION_NONE,
    SIGNAL_ADD_TARGETS,
    SIGNAL_UPDATE_TARGETS,
)

# Re-exported so the sensor platform and tests keep importing from the package
# root. The implementations live in focused modules.
from .backfill import build_backfill_rows
from .manager import RecorderDownsampleManager, Target, is_recorded
from .resolve import resolve_rule_entities

_LOGGER = logging.getLogger(__name__)


def _regex_pattern(value: str) -> str:
    """Validate a regex string at config-load time."""
    try:
        re.compile(value)
    except re.error as err:
        raise vol.Invalid(f"invalid regex {value!r}: {err}") from err
    return value


# precision is `auto`, `none`, or a non-negative integer (decimal places).
_PRECISION_VALUE = vol.Any(
    PRECISION_AUTO, PRECISION_NONE, vol.All(vol.Coerce(int), vol.Range(min=0))
)


_RULE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_RULE_NAME): str,
        vol.Optional(CONF_INTEGRATION_FILTER, default=[]): [str],
        vol.Optional(CONF_DEVICE_IDS, default=[]): [str],
        vol.Optional(CONF_ENTITY_IDS, default=[]): [str],
        vol.Optional(CONF_ENTITY_GLOBS, default=[]): [str],
        vol.Optional(CONF_ENTITY_REGEX_INCLUDE, default=[]): [_regex_pattern],
        vol.Optional(CONF_ENTITY_REGEX_EXCLUDE, default=[]): [_regex_pattern],
        vol.Optional(CONF_MATCH_MODE, default=DEFAULT_MATCH_MODE): vol.In(
            [MATCH_MODE_ALL, MATCH_MODE_ANY]
        ),
        # None => inherit the top-level default.
        vol.Optional(CONF_INTERVAL, default=None): vol.Any(
            None, cv.positive_time_period
        ),
        vol.Optional(CONF_METHOD, default=None): vol.Any(None, vol.In(METHODS)),
        vol.Optional(CONF_PRECISION, default=None): vol.Any(None, _PRECISION_VALUE),
        # None => inherit the top-level dry_run; true/false override it per rule.
        vol.Optional(CONF_DRY_RUN, default=None): vol.Any(None, bool),
        # None => inherit the top-level backfill_history; true/false override it
        # per rule, so auto-backfill can be scoped to a single rule's mirrors.
        vol.Optional(CONF_BACKFILL_HISTORY, default=None): vol.Any(None, bool),
        vol.Optional(CONF_ENABLED, default=True): bool,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(
                    CONF_INTERVAL, default=DEFAULT_INTERVAL
                ): cv.positive_time_period,
                vol.Optional(CONF_METHOD, default=DEFAULT_METHOD): vol.In(METHODS),
                vol.Optional(
                    CONF_PRECISION, default=DEFAULT_PRECISION
                ): _PRECISION_VALUE,
                vol.Optional(
                    CONF_WARN_UNEXCLUDED, default=DEFAULT_WARN_UNEXCLUDED
                ): bool,
                vol.Optional(CONF_DRY_RUN, default=DEFAULT_DRY_RUN): bool,
                vol.Optional(
                    CONF_BACKFILL_HISTORY, default=DEFAULT_BACKFILL_HISTORY
                ): bool,
                vol.Optional(
                    CONF_COPY_DISPLAY_PRECISION,
                    default=DEFAULT_COPY_DISPLAY_PRECISION,
                ): vol.In(COPY_DISPLAY_PRECISION_MODES),
                vol.Optional(CONF_RULES, default=[]): [_RULE_SCHEMA],
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


# ---------------------------------------------------------------------------
# Setup + reload service
# ---------------------------------------------------------------------------


PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Read the YAML config and anchor the single config entry.

    The integration is YAML-configured, but it also owns a single config entry
    so it (and its mirror entities) show under Settings → Devices & Services.
    The validated config is stashed in ``hass.data`` for ``async_setup_entry``
    to consume — it can't live in the entry's data because validated values
    like timedeltas aren't JSON-serialisable.
    """
    domain_config = config.get(DOMAIN)
    if domain_config is None:
        return True

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_YAML_CONFIG] = domain_config

    # Create the entry on first run; on later starts the import aborts (single
    # instance) and HA sets up the existing entry, consuming the stash above.
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data={}
        )
    )

    async def _reload(call: ServiceCall) -> None:
        new_config = await async_integration_yaml_config(hass, DOMAIN)
        if new_config is None or DOMAIN not in new_config:
            _LOGGER.warning(
                "%s: reload found no %s: block in configuration.yaml", DOMAIN, DOMAIN
            )
            return
        hass.data[DOMAIN][DATA_YAML_CONFIG] = new_config[DOMAIN]
        manager: RecorderDownsampleManager | None = hass.data.get(DOMAIN, {}).get(
            DATA_MANAGER
        )
        if manager is not None:
            manager.update_config(new_config[DOMAIN])

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, _reload, schema=vol.Schema({}))

    async def _backfill(call: ServiceCall) -> None:
        manager: RecorderDownsampleManager | None = hass.data.get(DOMAIN, {}).get(
            DATA_MANAGER
        )
        if manager is None:
            return
        entity_ids = call.data.get("entity_id") or None
        dry_run = bool(call.data.get("dry_run", False))
        # async_backfill logs each result + a summary, fires the completion
        # event, and raises a persistent notification.
        await manager.async_backfill(entity_ids, dry_run=dry_run)

    hass.services.async_register(
        DOMAIN,
        "backfill_history",
        _backfill,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_ids,
                vol.Optional("dry_run", default=False): bool,
            }
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from its config entry.

    The entry is just an anchor for UI visibility; the live config is the YAML
    stashed by ``async_setup``. Mirror entities created by the forwarded sensor
    platform are owned by this entry (so they appear under the integration card)
    while still being linked onto their source's device.
    """
    domain_config = hass.data.get(DOMAIN, {}).get(DATA_YAML_CONFIG)
    if domain_config is None:
        # Entry exists but the YAML block was removed — set up with defaults so
        # nothing breaks; no rules means no mirrors.
        domain_config = CONFIG_SCHEMA({DOMAIN: {}})[DOMAIN]

    manager = RecorderDownsampleManager(hass, domain_config, entry_id=entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_MANAGER] = manager

    # Load the user's prior "ignore" choices before reconciling.
    await manager.async_load()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reconcile orphaned mirrors only AFTER HA has fully started. At cold-boot
    # setup a source's integration may not have registered or published its
    # entities yet; treating that not-yet-loaded source as "removed" deleted the
    # mirror and destroyed its history (an outdoor outlet lost 5h this way).
    # Deferring means the registry + state machine are complete, and reconcile
    # now only removes a mirror whose source is present-but-unmatched. Runs
    # immediately when HA is already running (reload / runtime add / tests).
    @callback
    def _reconcile_orphans_when_started(_hass: HomeAssistant) -> None:
        manager.reconcile_orphans()

    entry.async_on_unload(async_at_started(hass, _reconcile_orphans_when_started))

    # Opt-in: auto-graft history onto any not-yet-backfilled mirror whose rule
    # (or the global default) enables it. Run in the background so setup isn't
    # blocked by reading the source statistics, and only after the platform has
    # created the entities. Idempotent, so later boots are a fast no-op
    # (already-backfilled mirrors are skipped).
    if manager.wants_auto_backfill():
        entry.async_create_background_task(
            hass, manager.async_backfill_new(), "recorder_downsampler_backfill"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry and tear down its platform."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(DATA_MANAGER, None)
    return unloaded


__all__ = [
    "CONFIG_SCHEMA",
    "RecorderDownsampleManager",
    "SIGNAL_ADD_TARGETS",
    "SIGNAL_UPDATE_TARGETS",
    "Target",
    "build_backfill_rows",
    "is_recorded",
    "resolve_rule_entities",
]
