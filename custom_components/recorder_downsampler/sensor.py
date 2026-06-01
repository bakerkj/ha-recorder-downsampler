# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""The downsample mirror sensor.

One ``DownsampleSensor`` per matched source. It buffers the source's values as
they arrive (live), and on a fixed interval writes a single aggregated value —
so the recorder sees one row per interval. The frontend keeps reading the
*source* at full speed; only the recorded history is downsampled.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    CoreState,
    Event,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.setup import async_when_setup
from homeassistant.util import dt as dt_util

from . import (
    SIGNAL_ADD_TARGETS,
    SIGNAL_UPDATE_TARGETS,
    RecorderDownsampleManager,
    Target,
)
from .aggregation import (
    aggregate_weighted_samples,
    resolve_method,
    resolve_precision,
)
from .const import (
    ATTR_MANAGED_BY,
    COPY_DISPLAY_PRECISION_ONCE,
    COPY_DISPLAY_PRECISION_TRACK,
    DATA_MANAGER,
    DOMAIN,
    ENTITY_ID_SUFFIX,
    NAME_SUFFIX,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up downsample mirrors for the config entry.

    The create-block runs synchronously as it always did — but on a cold boot
    it's delayed until the integrations our sources belong to have set up, so
    those sources already have values and the mirrors are born populated. If HA
    is already running (a reload, a runtime add, or the test harness) it runs
    immediately.
    """
    manager: RecorderDownsampleManager = hass.data[DOMAIN][DATA_MANAGER]

    @callback
    def _create_initial_mirrors(*_: object) -> None:
        targets = manager.resolve_targets(log=True)
        manager.log_rollout(targets)
        # Only LIVE targets become entities; dry-run targets are logged but
        # never created (dry_run may be set globally or per rule).
        live = [t for t in targets if not t.dry_run]
        if live:
            async_add_entities([DownsampleSensor(hass, t) for t in live])
            manager.mark_created(live)
        # Co-own the source devices so they also show under our integration card.
        manager.reconcile_device_ownership()

    if hass.state is CoreState.running:
        # Reload / runtime add / tests: sources are already up — create now.
        _create_initial_mirrors()
    else:
        # Cold boot: create once the integrations our sources belong to are set
        # up. Source platforms come from the (persistent) entity registry.
        ent_reg = er.async_get(hass)
        source_domains = {
            e.platform
            for t in manager.resolve_targets()
            if (e := ent_reg.async_get(t.source_entity_id)) is not None
        }
        if not source_domains:
            # Can't tell which integrations to wait for — fall back to the end
            # of startup so we still create before HA is fully up.
            entry.async_on_unload(async_at_started(hass, _create_initial_mirrors))
        else:
            pending = set(source_domains)

            async def _source_component_loaded(
                _hass: HomeAssistant, component: str
            ) -> None:
                pending.discard(component)
                if not pending:
                    _create_initial_mirrors()

            for domain in source_domains:
                async_when_setup(hass, domain, _source_component_loaded)

    # Reloads dispatch newly matched (live) targets here so they appear without
    # a restart. (Removed / now-dry targets are torn down by the manager via
    # the registry.)
    @callback
    def _on_add_targets(new_targets: list[Target]) -> None:
        live_new = [t for t in new_targets if not t.dry_run]
        if not live_new:
            return
        async_add_entities([DownsampleSensor(hass, t) for t in live_new])
        manager.mark_created(live_new)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ADD_TARGETS, _on_add_targets)
    )


class DownsampleSensor(SensorEntity):
    """A slow, recorded mirror of a fast source sensor."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, target: Target) -> None:
        self.hass = hass
        self._source = target.source_entity_id
        self._interval: timedelta = target.interval
        self._method = target.method
        self._precision = target.precision
        self._rule_name = target.rule_name
        self._copy_display_precision = target.copy_display_precision

        # Raw source states seen this interval, paired with the wall-clock time
        # the state became active (`last_updated`). Collapsed on each emit;
        # sentinel states (unavailable/unknown) are not buffered. The timestamp
        # is what time-weighted methods (`mean`, `circular_mean`) use to weight
        # each sample by its dwell time.
        self._buffer: list[tuple[str, float]] = []

        # The source's most recent buffered value, persisted across emits. At
        # the start of each new interval this is the value the source was
        # already at — so it gets prepended as a virtual interval-start sample
        # for time-weighting (otherwise a sample that arrives mid-interval
        # would drop the leading sub-interval, biasing the weighted mean).
        self._carry_value: str | None = None

        # Wall-clock timestamp (seconds since epoch) of the previous emit, or
        # of setup if no emit has happened yet. The lower bound of the current
        # interval's time-weighting window; the upper bound is the next emit.
        self._last_emit_ts: float | None = None

        # Until the mirror has produced a real value we suppress state writes,
        # so the initial `unknown` (e.g. while a push source connects on a cold
        # boot) is never shown or recorded — the entity reads unavailable until
        # then. Once True, writes (incl. outage `unavailable`) go through.
        self._has_published = False

        # Emit-timer cancel handle, so a reload can restart it at a new cadence.
        self._unsub_timer: CALLBACK_TYPE | None = None

        # Source metadata copied onto the mirror. Initialise every backing attr
        # so reads stay safe even if the source's first state hasn't arrived
        # yet (these have no class-level default on SensorEntity).
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None
        self._attr_suggested_display_precision = None
        self._state_class: str | None = None
        self._suggested_precision: int | None = None

        self._attr_unique_id = target.unique_id
        # Whether this mirror is brand-new to the registry (vs. already present
        # from a prior run). Captured here, BEFORE async_add_entities registers
        # it, so display-precision seeding can run exactly once at creation.
        self._is_new = (
            er.async_get(hass).async_get_entity_id("sensor", DOMAIN, target.unique_id)
            is None
        )
        object_id = self._source.split(".", 1)[1]
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{object_id}{ENTITY_ID_SUFFIX}", hass=hass
        )
        self._attr_name = self._derive_name()
        self._source_device_id = self._derive_source_device_id()
        if (state := self.hass.states.get(self._source)) is not None:
            self._refresh_source_metadata(state.attributes)

    # -- naming / device / metadata ----------------------------------------

    def _derive_name(self) -> str:
        state = self.hass.states.get(self._source)
        base = None
        if state is not None:
            base = state.attributes.get("friendly_name")
        if not base:
            base = self._source.split(".", 1)[1].replace("_", " ").title()
        return f"{base}{NAME_SUFFIX}"

    def _derive_source_device_id(self) -> str | None:
        """The registry device_id of the source, if the source has one."""
        src = er.async_get(self.hass).async_get(self._source)
        return src.device_id if src else None

    def _source_display_precision(self) -> int | None:
        """The source's effective display precision: its display_precision
        override if the user set one, else its suggested_display_precision."""
        src = er.async_get(self.hass).async_get(self._source)
        if src is None:
            return None
        opts: Mapping[str, Any] = src.options.get("sensor", {})
        override = opts.get("display_precision")
        return (
            override
            if override is not None
            else opts.get("suggested_display_precision")
        )

    def _init_display_precision(self) -> None:
        """Apply the configured display-precision policy (never / once / track)."""
        mode = self._copy_display_precision
        if mode == COPY_DISPLAY_PRECISION_ONCE:
            self._seed_display_precision_once()
        elif mode == COPY_DISPLAY_PRECISION_TRACK:
            # Sync now, then follow the source for as long as we're loaded.
            self._sync_display_precision_from_source()
            self.async_on_remove(
                self.hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED,
                    self._on_source_registry_update,
                )
            )

    def _write_mirror_display_precision(self, value: int | None) -> None:
        """Set (or clear, when value is None) the mirror's display_precision,
        preserving the rest of its ``sensor`` options. Only writes the mirror.

        Returns early when already in the requested state, so repeated calls
        (and registry-update echoes) are no-ops.
        """
        reg = er.async_get(self.hass)
        entry = reg.async_get(self.entity_id)
        if entry is None:
            return
        sensor_opts = dict(entry.options.get("sensor", {}))
        if sensor_opts.get("display_precision") == value:
            return
        if value is None:
            sensor_opts.pop("display_precision", None)
        else:
            sensor_opts["display_precision"] = value
        reg.async_update_entity_options(self.entity_id, "sensor", sensor_opts)

    def _seed_display_precision_once(self) -> None:
        """Seed display_precision ONCE, at first creation, from the source's
        effective precision — then never re-assert it (user stays free to edit).
        """
        if not self._is_new:
            return
        value = self._source_display_precision()
        if value is None:
            return
        entry = er.async_get(self.hass).async_get(self.entity_id)
        if entry is None:
            return
        sensor_opts: Mapping[str, Any] = entry.options.get("sensor", {})
        if sensor_opts.get("display_precision") is not None:
            return  # never override an existing display_precision
        self._write_mirror_display_precision(value)

    def _sync_display_precision_from_source(self) -> None:
        """Make the mirror's display_precision match the source's effective
        precision (track mode). One-way: only the mirror is written."""
        self._write_mirror_display_precision(self._source_display_precision())

    @callback
    def _on_source_registry_update(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Re-sync when the SOURCE's registry entry changes (track mode)."""
        if event.data["action"] != "update" or event.data["entity_id"] != self._source:
            return
        self._sync_display_precision_from_source()

    def _refresh_source_metadata(self, attrs: Mapping[str, Any]) -> None:
        """Copy unit / device_class / state_class / suggested precision from
        the source's attributes onto the mirror.

        Idempotent and incremental: each field is filled in the first time it
        appears, so this works whether the source already existed at setup or
        only shows up (or gains attributes) later.
        """
        if self._attr_native_unit_of_measurement is None:
            self._attr_native_unit_of_measurement = attrs.get("unit_of_measurement")
        if self._attr_device_class is None:
            self._attr_device_class = attrs.get("device_class")
        if self._state_class is None:
            self._state_class = attrs.get("state_class")
            self._attr_state_class = self._state_class
        if self._suggested_precision is None:
            if (precision := attrs.get("suggested_display_precision")) is not None:
                self._attr_suggested_display_precision = precision
                self._suggested_precision = precision

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ndigits = resolve_precision(
            self._precision, self._state_class, self._suggested_precision
        )
        return {
            ATTR_MANAGED_BY: True,
            "source_entity_id": self._source,
            "method": resolve_method(self._method, self._state_class),
            "precision": "raw" if ndigits is None else ndigits,
            "interval_seconds": int(self._interval.total_seconds()),
            "rule": self._rule_name,
        }

    # -- lifecycle ----------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to the source and start the emit timer."""
        self._link_to_source_device()
        self._init_display_precision()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source], self._handle_source_event
            )
        )
        self._start_timer()
        self.async_on_remove(self._stop_timer)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE_TARGETS, self._on_reconfigure
            )
        )
        # Setup time anchors the first interval's time-weighting window — any
        # source updates in the first interval are weighted against this.
        self._last_emit_ts = dt_util.utcnow().timestamp()
        # Seed the buffer with the current value so the first interval has data,
        # and publish that value now so the mirror is born populated instead of
        # `unknown` (the buffer is kept, so the first interval still includes
        # this sample). If the source has no usable value yet, there's nothing
        # to publish and the mirror stays `unknown` until data arrives.
        if (state := self.hass.states.get(self._source)) is not None:
            self._ingest(state.state, state.attributes, self._last_emit_ts)
            if self._buffer:
                # Seed the carry-over too, so an interval with no source events
                # still has a value to weight (the same value, full interval).
                self._carry_value = state.state
                # Publish the seed value immediately. A single sample with any
                # positive weight collapses to that value under every method,
                # so we don't need to model dwell time here.
                self._publish_weighted_aggregate([(state.state, 1.0)])

    @callback
    def _start_timer(self) -> None:
        """(Re)start the per-interval emit timer at the current cadence."""
        self._unsub_timer = async_track_time_interval(
            self.hass, self._emit, self._interval
        )

    @callback
    def _stop_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _on_reconfigure(self, targets: list[Target]) -> None:
        """Adopt retuned params if this mirror is among the reload's changes."""
        for target in targets:
            if target.unique_id == self._attr_unique_id:
                self._method = target.method
                self._precision = target.precision
                self._rule_name = target.rule_name
                if target.interval != self._interval:
                    self._interval = target.interval
                    self._stop_timer()
                    self._start_timer()
                self.async_write_ha_state()  # refresh exposed attributes
                return

    @callback
    def _link_to_source_device(self) -> None:
        """Attach this mirror onto the source's existing device card.

        The mirror is owned by our config entry, but its device belongs to the
        source's own integration. DeviceInfo can't link
        across integrations, so we link this mirror to the source's device
        directly through the entity registry, which allows it.
        """
        if self._source_device_id is None:
            return
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(self.entity_id)
        if entry is None or entry.device_id == self._source_device_id:
            return
        ent_reg.async_update_entity(self.entity_id, device_id=self._source_device_id)

    @callback
    def _handle_source_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._ingest(
            new_state.state, new_state.attributes, new_state.last_updated.timestamp()
        )

    def _ingest(self, value: str, attrs: Mapping[str, Any], when_ts: float) -> None:
        self._refresh_source_metadata(attrs)
        if value in (STATE_UNKNOWN, STATE_UNAVAILABLE, None, ""):
            return
        self._buffer.append((value, when_ts))

    @callback
    def _publish_weighted_aggregate(self, weighted: list[tuple[str, float]]) -> bool:
        """Write the time-weighted aggregate of ``weighted`` as the mirror's value.

        Each entry is ``(raw-state-string, weight-in-seconds)``. Returns True
        if a value was written; False if the aggregation produced ``None``
        (empty input, zero total weight, or circular cancellation).
        """
        method = resolve_method(self._method, self._state_class)
        value = aggregate_weighted_samples(weighted, method)
        if value is None:
            return False
        if isinstance(value, float):
            ndigits = resolve_precision(
                self._precision, self._state_class, self._suggested_precision
            )
            if ndigits is not None:
                # ndigits == 0 records a whole number (int), not 123.0.
                value = round(value) if ndigits == 0 else round(value, ndigits)
        self._attr_available = True
        self._attr_native_value = value
        self._has_published = True
        self.async_write_ha_state()
        return True

    @callback
    def _async_write_ha_state(self) -> None:
        """Suppress state writes until the mirror has produced a real value.

        (HA's public ``async_write_ha_state`` is ``@final`` and delegates here,
        which is the documented hook for customizing state writes.) Before the
        first value — e.g. while a push source connects on a cold boot — we
        write nothing, so no `unknown` is shown or recorded; the entity reads
        unavailable. Once a value has been published, writes go through
        normally, including `unavailable` on a genuine source outage.
        """
        if not self._has_published:
            return
        super()._async_write_ha_state()

    @callback
    def _emit(self, now: datetime) -> None:
        """Write the aggregated value for the elapsed interval, then reset.

        For time-weighted methods (`mean`, `circular_mean`) each buffered
        sample's weight is the time it was the source's active state — from
        its `last_updated` to the next sample's, with the last sample weighted
        to the emit time. The previous interval's final value is prepended as
        a virtual interval-start sample so the leading gap (before the first
        in-interval update arrived) is correctly attributed to it.
        """
        now_ts = now.timestamp()
        samples = self._buffer
        self._buffer = []
        # Lower bound of the weighting window. On the very first emit this was
        # set to setup time in `async_added_to_hass`; on every subsequent emit
        # it's the previous emit's timestamp (carrying-over below).
        interval_start = (
            self._last_emit_ts if self._last_emit_ts is not None else now_ts
        )
        self._last_emit_ts = now_ts

        if samples:
            # Build the timeline `[(value, active_from_ts), …]` for this
            # interval. If we have a carry-over from the previous interval and
            # the first new sample arrived after the interval started, prepend
            # the carry-over at the interval boundary so its dwell time is
            # accounted for. (If the first sample's ts equals or precedes
            # `interval_start` — e.g. setup-seed or clock skew — skip the
            # prepend: the carry-over had no dwell in this interval.)
            timeline: list[tuple[str, float]] = []
            if self._carry_value is not None and samples[0][1] > interval_start:
                timeline.append((self._carry_value, interval_start))
            timeline.extend(samples)

            # Convert (value, active_from_ts) to (value, weight): each sample
            # is active from its ts to the next sample's ts; the last is
            # active from its ts to `now_ts`. Negative weights (clock skew /
            # out-of-order events) clamp to 0; zero-weight samples are kept
            # so unweighted methods (max/min/first/last/median) still see
            # them — only `weighted_mean` / `weighted_circular_mean` drop
            # zero-weight contributions, which is mathematically a no-op.
            weighted: list[tuple[str, float]] = []
            for i, (value, ts) in enumerate(timeline):
                end_ts = timeline[i + 1][1] if i + 1 < len(timeline) else now_ts
                weighted.append((value, max(0.0, end_ts - ts)))

            # Update carry-over for the next interval: the most recent value
            # we've observed (whether prepended carry or in-buffer sample).
            self._carry_value = samples[-1][0]

            if self._publish_weighted_aggregate(weighted):
                return

        # No samples buffered this interval (or the aggregate was degenerate,
        # e.g. circular_mean cancellation). Look at the source's CURRENT state.
        source = self.hass.states.get(self._source)
        if source is None or source.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            # Source down/gone: reflect the outage once, then hold unavailable.
            if self._attr_available:
                self._attr_available = False
                self.async_write_ha_state()
            return

        # Source is up with a value but no event arrived this interval. Normally
        # we just hold (no source update, no recorder row). But if we're stuck
        # `unavailable` — e.g. a state-change subscription was dropped while the
        # source updates slowly — self-heal by sampling the current value, so a
        # missed event can't strand the mirror indefinitely.
        if not self._attr_available:
            self._publish_weighted_aggregate([(source.state, 1.0)])
