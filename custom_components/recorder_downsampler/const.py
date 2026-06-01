# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Constants for Recorder Downsampler."""

from datetime import timedelta

DOMAIN = "recorder_downsampler"

# Dispatcher signals the sensor platform listens on so a reload can add newly
# matched mirrors / retune existing ones without a restart.
SIGNAL_ADD_TARGETS = f"{DOMAIN}_add_targets"
SIGNAL_UPDATE_TARGETS = f"{DOMAIN}_update_targets"

# Bus event fired when a (non-dry-run) backfill finishes, with the run's counts
# (grafted / rows / skipped / failed / duration_s) so automations can react.
EVENT_BACKFILL_COMPLETED = f"{DOMAIN}_backfill_completed"

# Informational marker exposed on every mirror's attributes so users (and
# debugging) can tell a mirror apart from a normal sensor. Note: rules avoid
# re-matching mirrors via the ``_downsampled`` suffix, not this attribute.
ATTR_MANAGED_BY = "recorder_downsampler_managed"

# ---------------------------------------------------------------------------
# Top-level config keys (under ``recorder_downsampler:`` in configuration.yaml)
# ---------------------------------------------------------------------------
CONF_INTERVAL = "interval"  # recording cadence (time period); global default
CONF_METHOD = "method"  # aggregation method; global default
CONF_PRECISION = "precision"  # decimal places for the recorded value; global default
CONF_WARN_UNEXCLUDED = "warn_unexcluded"  # log when a source is still recorded
CONF_DRY_RUN = "dry_run"  # resolve + log only; create no mirror entities
CONF_BACKFILL_HISTORY = "backfill_history"  # auto-graft history; global + per-rule
CONF_RULES = "rules"

# ---------------------------------------------------------------------------
# Rule field keys (mirrors ha-recorder-tuning's selector vocabulary so the two
# integrations feel like siblings)
# ---------------------------------------------------------------------------
CONF_RULE_NAME = "name"
CONF_ENABLED = "enabled"

# Rule target selectors
CONF_INTEGRATION_FILTER = "integration_filter"  # list of integration/platform names
CONF_DEVICE_IDS = "device_ids"
CONF_ENTITY_IDS = "entity_ids"
CONF_ENTITY_GLOBS = "entity_globs"
CONF_ENTITY_REGEX_INCLUDE = "entity_regex_include"  # must match at least one
CONF_ENTITY_REGEX_EXCLUDE = "entity_regex_exclude"  # excluded if matches any

# How the positive selectors within a rule combine. "all" (default): an entity
# must satisfy every present selector. "any": union (legacy mode).
CONF_MATCH_MODE = "match_mode"
MATCH_MODE_ALL = "all"
MATCH_MODE_ANY = "any"
DEFAULT_MATCH_MODE = MATCH_MODE_ALL

# ---------------------------------------------------------------------------
# Aggregation methods
# ---------------------------------------------------------------------------
METHOD_AUTO = "auto"  # pick by state_class (see resolve_method)
METHOD_MEAN = "mean"
METHOD_MEDIAN = "median"
METHOD_MAX = "max"
METHOD_MIN = "min"
METHOD_LAST = "last"
METHOD_FIRST = "first"
# Vector mean for angular sources (wind direction, …): treats each sample as a
# unit vector on the unit circle and returns the resultant angle in [0, 360)
# degrees, so 350° and 10° average to 0° (not 180°). Matches HA's own
# ``mean_type=circular`` statistics for wind-direction sensors.
METHOD_CIRCULAR_MEAN = "circular_mean"
METHODS = [
    METHOD_AUTO,
    METHOD_MEAN,
    METHOD_MEDIAN,
    METHOD_MAX,
    METHOD_MIN,
    METHOD_LAST,
    METHOD_FIRST,
    METHOD_CIRCULAR_MEAN,
]

# ---------------------------------------------------------------------------
# Precision (decimal places applied to the recorded numeric value)
# ---------------------------------------------------------------------------
# "auto": type-aware — cumulative counters (total / total_increasing) are never
# rounded (protects Energy-dashboard sums); other sources use the source's
# suggested_display_precision if it declares one, else stay raw. An integer
# rounds to that many places (but accumulators are still protected). "none"
# forces raw. Non-numeric (string) sources are never rounded.
PRECISION_AUTO = "auto"
PRECISION_NONE = "none"

# ---------------------------------------------------------------------------
# Display precision seeding (distinct from `precision`, which rounds the
# RECORDED value). This controls the mirror's *frontend* display precision —
# the entity's `display_precision` registry option (the user-override slot).
# ---------------------------------------------------------------------------
# "never": never touch the mirror's display_precision (default). "once": at
# entity creation only, seed it from the source's effective display precision
# (the source's display_precision override if set, else its
# suggested_display_precision), then never re-assert it — so the user stays
# free to change it. "track": keep the mirror's display_precision following the
# source's effective precision for as long as the integration runs (re-synced
# whenever the source changes). Tracking is one-way and only ever writes the
# MIRROR — never the source, which the integration doesn't own.
# suggested_display_precision is always copied from the source regardless.
#
# Trade-off: "track" re-writes the mirror's registry option whenever the source
# changes, for the life of the integration; "once" sets it a single time at
# creation. Prefer "once" unless you actually retune source display precision
# and want mirrors to follow — it avoids the ongoing registry writes.
CONF_COPY_DISPLAY_PRECISION = "copy_display_precision"
COPY_DISPLAY_PRECISION_NEVER = "never"
COPY_DISPLAY_PRECISION_ONCE = "once"
COPY_DISPLAY_PRECISION_TRACK = "track"
COPY_DISPLAY_PRECISION_MODES = [
    COPY_DISPLAY_PRECISION_NEVER,
    COPY_DISPLAY_PRECISION_ONCE,
    COPY_DISPLAY_PRECISION_TRACK,
]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_INTERVAL = timedelta(minutes=1)
DEFAULT_METHOD = METHOD_AUTO
DEFAULT_PRECISION = PRECISION_AUTO
DEFAULT_WARN_UNEXCLUDED = True
# Creating mirror entities is non-destructive, so dry_run defaults off (unlike
# ha-recorder-tuning, where dry_run guards data deletion). Set dry_run: true to
# stage a rollout — the manager logs which sources it *would* mirror and which
# are not yet recorder-excluded, without creating any entities.
DEFAULT_DRY_RUN = False
# Auto-graft each new mirror's pre-creation history from its source (the
# backfill_history service, run automatically once per mirror). Off by default:
# it rewrites long-term statistics, which should be a deliberate, backed-up
# choice rather than a silent fleet-wide default. This is the GLOBAL default; a
# rule may override it with its own `backfill_history` (None inherits this,
# true/false overrides), so auto-backfill can be scoped to one rule at a time —
# the cautious way to validate the graft on a small cohort before going wide.
DEFAULT_BACKFILL_HISTORY = False
DEFAULT_COPY_DISPLAY_PRECISION = COPY_DISPLAY_PRECISION_NEVER

# Naming for the mirror entities. Role-based (not interval/method-based) so the
# id stays stable when you retune interval or method. The entity_id slug is the
# durable, hard-to-change part (it's the statistic_id and what dashboards
# reference), so it spells out "downsampled"; the friendly-name suffix is
# cosmetic and cheap to change later.
ENTITY_ID_SUFFIX = "_downsampled"
NAME_SUFFIX = " (DS)"

# hass.data keys
DATA_MANAGER = "manager"
# The validated YAML config, stashed by async_setup for async_setup_entry to
# consume. Lives here (not in the config entry's data) because validated values
# like interval timedeltas aren't JSON-serialisable.
DATA_YAML_CONFIG = "yaml_config"
