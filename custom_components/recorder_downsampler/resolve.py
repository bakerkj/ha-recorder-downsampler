# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Rule -> source-entity-id resolution (module-level so unit tests call it directly)."""

from __future__ import annotations

import logging
import re
from fnmatch import fnmatch
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DEVICE_IDS,
    CONF_ENTITY_GLOBS,
    CONF_ENTITY_IDS,
    CONF_ENTITY_REGEX_EXCLUDE,
    CONF_ENTITY_REGEX_INCLUDE,
    CONF_INTEGRATION_FILTER,
    CONF_MATCH_MODE,
    CONF_RULE_NAME,
    DEFAULT_MATCH_MODE,
    ENTITY_ID_SUFFIX,
    MATCH_MODE_ANY,
)

_LOGGER = logging.getLogger(__name__)


def _all_known_entity_ids(hass: HomeAssistant) -> set[str]:
    """Union of registry entity_ids and live state-machine entity_ids."""
    ent_reg = er.async_get(hass)
    ids = {e.entity_id for e in ent_reg.entities.values()}
    ids.update(hass.states.async_entity_ids())
    return ids


def resolve_rule_entities(
    hass: HomeAssistant, rule: dict[str, Any], *, log: bool = False
) -> set[str]:
    """Return the set of source entity_ids a single rule matches.

    Positive selectors combine per ``match_mode`` ("all" = intersection,
    "any" = union). ``entity_regex_exclude`` is always subtracted. Our own
    mirror entities (``*_downsampled`` / managed) are never matched, so a rule
    can't downsample a downsample.

    ``log=True`` emits the skipped-disabled-source diagnostics; the caller
    passes it only on the once-per-cycle rollout pass, since this function runs
    several times per reconcile.
    """
    ent_reg = er.async_get(hass)
    match_mode = rule.get(CONF_MATCH_MODE, DEFAULT_MATCH_MODE)
    universe = _all_known_entity_ids(hass)

    selector_sets: list[set[str]] = []

    integrations = rule.get(CONF_INTEGRATION_FILTER) or []
    if integrations:
        wanted = set(integrations)
        selector_sets.append(
            {e.entity_id for e in ent_reg.entities.values() if e.platform in wanted}
        )

    device_ids = rule.get(CONF_DEVICE_IDS) or []
    if device_ids:
        wanted_dev = set(device_ids)
        selector_sets.append(
            {
                e.entity_id
                for e in ent_reg.entities.values()
                if e.device_id in wanted_dev
            }
        )

    entity_ids = rule.get(CONF_ENTITY_IDS) or []
    if entity_ids:
        selector_sets.append(set(entity_ids) & universe)

    globs = rule.get(CONF_ENTITY_GLOBS) or []
    if globs:
        selector_sets.append(
            {eid for eid in universe if any(fnmatch(eid, g) for g in globs)}
        )

    regex_inc = rule.get(CONF_ENTITY_REGEX_INCLUDE) or []
    if regex_inc:
        patterns = [re.compile(p) for p in regex_inc]
        selector_sets.append(
            {eid for eid in universe if any(p.search(eid) for p in patterns)}
        )

    if not selector_sets:
        matched: set[str] = set()
    elif match_mode == MATCH_MODE_ANY:
        matched = set().union(*selector_sets)
    else:  # MATCH_MODE_ALL
        matched = set(selector_sets[0])
        for s in selector_sets[1:]:
            matched &= s

    regex_exc = rule.get(CONF_ENTITY_REGEX_EXCLUDE) or []
    if regex_exc:
        ex_patterns = [re.compile(p) for p in regex_exc]
        matched = {
            eid for eid in matched if not any(p.search(eid) for p in ex_patterns)
        }

    # Never mirror our own mirrors.
    matched = {eid for eid in matched if not eid.endswith(ENTITY_ID_SUFFIX)}

    # Skip disabled entities: a disabled source has no state, so mirroring it
    # only creates a dead mirror stuck `unavailable` (e.g. unwired hardware
    # channels the source integration disables). Entities live in the state
    # machine but absent from the registry (async_get -> None) are kept.
    disabled = {
        eid
        for eid in matched
        if (entry := ent_reg.async_get(eid)) is not None
        and entry.disabled_by is not None
    }
    if disabled and log:
        # Logged once per rollout (the caller gates this with log=True). Count
        # at INFO; each skipped source on its own DEBUG line so every line is
        # individually prefixed (no unprefixed multi-line continuation).
        name = rule.get(CONF_RULE_NAME, "?")
        _LOGGER.info('rule "%s": skipped %d disabled source(s)', name, len(disabled))
        for eid in sorted(disabled):
            _LOGGER.debug('rule "%s" skipped disabled source: %s', name, eid)
    matched -= disabled
    return matched
