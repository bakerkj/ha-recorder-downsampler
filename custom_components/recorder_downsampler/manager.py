# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""The manager: resolves rules into Targets and drives platform (re)loads,
orphan Repairs, device co-ownership, and statistics backfill."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .backfill import build_backfill_rows
from .const import (
    CONF_BACKFILL_HISTORY,
    CONF_COPY_DISPLAY_PRECISION,
    CONF_DRY_RUN,
    CONF_ENABLED,
    CONF_INTERVAL,
    CONF_METHOD,
    CONF_PRECISION,
    CONF_RULE_NAME,
    CONF_RULES,
    CONF_WARN_UNEXCLUDED,
    DEFAULT_COPY_DISPLAY_PRECISION,
    DOMAIN,
    ENTITY_ID_SUFFIX,
    EVENT_BACKFILL_COMPLETED,
    SIGNAL_ADD_TARGETS,
    SIGNAL_UPDATE_TARGETS,
)
from .resolve import _all_known_entity_ids, resolve_rule_entities

_LOGGER = logging.getLogger(__name__)


# Repairs issue id prefix for an orphaned mirror (one issue per mirror).
ISSUE_ORPHAN_PREFIX = "orphaned_mirror_"


def _orphan_issue_id(unique_id: str) -> str:
    return f"{ISSUE_ORPHAN_PREFIX}{unique_id}"


@dataclass(frozen=True)
class Target:
    """One source entity to mirror, with its resolved interval/method/precision."""

    source_entity_id: str
    interval: timedelta
    method: str
    precision: str | int
    rule_name: str
    dry_run: bool = False
    # Whether this mirror is eligible for the opt-in auto-backfill at setup.
    # Resolved per-rule (rule override, else the global default). The manual
    # backfill_history service ignores this — it acts on whatever it's given.
    backfill_history: bool = False
    # Frontend display-precision seeding policy ("never" / "once"); global, not
    # per-rule. Carried on the target so the sensor stays decoupled from config.
    copy_display_precision: str = DEFAULT_COPY_DISPLAY_PRECISION

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.source_entity_id}{ENTITY_ID_SUFFIX}"


def is_recorded(hass: HomeAssistant, entity_id: str) -> bool | None:
    """Best-effort: True if the recorder would record ``entity_id``.

    Delegates to HA's public ``recorder.is_entity_recorded`` helper, which
    correctly reports ``True`` when no recorder filter is configured (i.e. the
    recorder records everything — the common case where the warning matters
    most). Returns ``None`` only if the recorder isn't set up / the API isn't
    available, which callers treat as "can't tell, skip the warning". The exact
    API is pinned by tests/test_ha_signature_compat.py.
    """
    try:
        from homeassistant.components.recorder import (
            is_entity_recorded,
        )

        return bool(is_entity_recorded(hass, entity_id))
    except Exception:  # noqa: BLE001 - best-effort diagnostic only
        return None


class RecorderDownsampleManager:
    """Resolves rules into targets and drives platform (re)loads."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        entry_id: str | None = None,
    ) -> None:
        self.hass = hass
        self.config = config
        # The config entry that owns our mirrors; None until set up via an entry
        # (enables device co-ownership). Set by async_setup_entry.
        self.entry_id = entry_id
        # Targets we've already handed to the sensor platform, keyed by
        # unique_id, so a reload can tell new / changed / orphaned apart.
        self._created: dict[str, Target] = {}
        # Source devices we've added our config entry to, so we can drop the
        # ones we no longer mirror on a reload.
        self._coowned_devices: set[str] = set()
        # Mirror unique_ids the user chose to keep despite their source being
        # disabled (a Repairs "ignore"). Persisted so we don't re-raise on
        # restart; cleaned when the source comes back. Loaded by async_load.
        self._ignored: set[str] = set()
        # Mirror unique_ids whose source history has already been grafted, so a
        # repeat backfill doesn't double-import. Persisted alongside _ignored.
        self._backfilled: set[str] = set()
        # Summary of the most recent (non-dry-run) backfill: completion time +
        # counts. A durable, queryable record that it ran and what it did.
        self._last_backfill: dict[str, Any] = {}
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.state")

    # -- resolution ---------------------------------------------------------

    def resolve_targets(self, *, log: bool = False) -> list[Target]:
        """Resolve every enabled rule to a deduped list of Targets.

        If two rules match the same source, the first rule wins (so put more
        specific rules first). ``log=True`` (the once-per-cycle rollout pass)
        emits each rule's skipped-disabled-source diagnostics.
        """
        default_interval: timedelta = self.config[CONF_INTERVAL]
        default_method: str = self.config[CONF_METHOD]
        default_precision: str | int = self.config[CONF_PRECISION]
        default_dry_run: bool = self.config[CONF_DRY_RUN]
        default_backfill: bool = self.config[CONF_BACKFILL_HISTORY]
        copy_display = self.config[CONF_COPY_DISPLAY_PRECISION]
        seen: set[str] = set()
        targets: list[Target] = []
        for rule in self.config.get(CONF_RULES, []):
            if not rule.get(CONF_ENABLED, True):
                continue
            interval = rule.get(CONF_INTERVAL) or default_interval
            method = rule.get(CONF_METHOD) or default_method
            # `precision: 0` is valid (round to integer) and falsy, so inherit
            # only on an explicit None.
            rule_precision = rule.get(CONF_PRECISION)
            precision = default_precision if rule_precision is None else rule_precision
            # dry_run None => inherit the global; true/false override per rule.
            rule_dry_run = rule.get(CONF_DRY_RUN)
            dry_run = default_dry_run if rule_dry_run is None else rule_dry_run
            # backfill_history None => inherit the global; true/false override.
            rule_backfill = rule.get(CONF_BACKFILL_HISTORY)
            backfill = default_backfill if rule_backfill is None else rule_backfill
            entities = resolve_rule_entities(self.hass, rule, log=log)
            for eid in sorted(entities):
                if eid in seen:
                    continue
                seen.add(eid)
                targets.append(
                    Target(
                        source_entity_id=eid,
                        interval=interval,
                        method=method,
                        precision=precision,
                        rule_name=rule[CONF_RULE_NAME],
                        dry_run=dry_run,
                        backfill_history=backfill,
                        copy_display_precision=copy_display,
                    )
                )
        return targets

    # -- diagnostics --------------------------------------------------------

    def log_rollout(self, targets: list[Target]) -> None:
        """Log the resolved rollout.

        INFO carries the rule/source counts; a single WARNING reports how many
        sources are still being recorded (and so wasting the downsample). The
        full per-source breakdown (``[LIVE]`` / ``[DRY RUN]`` status, effective
        interval / method / precision, still-recorded flag) goes out at DEBUG —
        one record per line, so every line is individually prefixed. Because
        it's DEBUG, the default log level stays at the few-line summary and
        doesn't trip HA's "logging too frequently" guard at fleet scale.
        """
        rules = self.config.get(CONF_RULES, [])
        live = [t for t in targets if not t.dry_run]
        dry = [t for t in targets if t.dry_run]

        _LOGGER.info(
            "loaded %d rule(s) — interval %s, method %s, precision %s",
            len(rules),
            self.config[CONF_INTERVAL],
            self.config[CONF_METHOD],
            self.config[CONF_PRECISION],
        )
        if dry and not live:
            _LOGGER.info(
                "DRY RUN — would mirror %d source sensor(s); no mirrors created",
                len(dry),
            )
        elif dry:
            _LOGGER.info(
                "mirroring %d source sensor(s); %d more in DRY RUN (not created)",
                len(live),
                len(dry),
            )
        else:
            _LOGGER.info("mirroring %d source sensor(s)", len(live))

        warn = bool(self.config.get(CONF_WARN_UNEXCLUDED))
        still_recorded = {
            t.source_entity_id
            for t in targets
            if warn and is_recorded(self.hass, t.source_entity_id) is True
        }

        # Per-source breakdown: one DEBUG record per line, each individually
        # prefixed. Skipped entirely unless debug logging is on.
        if _LOGGER.isEnabledFor(logging.DEBUG):
            by_rule: dict[str, list[Target]] = {}
            for t in targets:
                by_rule.setdefault(t.rule_name, []).append(t)
            for rule in rules:
                name = rule[CONF_RULE_NAME]
                if not rule.get(CONF_ENABLED, True):
                    _LOGGER.debug('rule "%s" — DISABLED', name)
                    continue
                rule_targets = sorted(
                    by_rule.get(name, []), key=lambda t: t.source_entity_id
                )
                _LOGGER.debug('rule "%s" — %d source(s)', name, len(rule_targets))
                for t in rule_targets:
                    flag = "DRY RUN" if t.dry_run else "LIVE"
                    rec = (
                        " — STILL being recorded"
                        if t.source_entity_id in still_recorded
                        else ""
                    )
                    _LOGGER.debug(
                        "  [%s] %s — interval %s, method %s, precision %s%s",
                        flag,
                        t.source_entity_id,
                        t.interval,
                        t.method,
                        t.precision,
                        rec,
                    )

        if still_recorded:
            _LOGGER.warning(
                "%d mirrored source(s) are STILL being recorded — exclude them in "
                "recorder.yaml or the downsample saves nothing (enable debug "
                "logging for the full list)",
                len(still_recorded),
            )

    # -- reload -------------------------------------------------------------

    def update_config(self, config: dict[str, Any]) -> None:
        """Swap in a new validated domain config; reconcile mirrors live.

        Three cases, all without a restart:

        - **New** sources get a mirror via the dispatcher.
        - **Changed** sources (a still-matched source whose interval / method /
          precision / rule changed) have their live mirror reconfigured in
          place. Remove + re-add would race the entity registry on the shared
          unique_id, so we mutate the existing entity instead.
        - **Orphaned** sources (no longer matched, or flipped to dry_run) are
          surfaced as a Repairs issue, not deleted — a mirror is never
          auto-removed (it may carry history); the user deletes it via Repairs.

        Only LIVE targets become entities; dry-run targets are logged but never
        created — so flipping a rule's ``dry_run`` true->false adds its mirrors,
        and false->true raises a Repair for them (they aren't auto-removed).
        """
        self.config = config
        targets = self.resolve_targets(log=True)
        self.log_rollout(targets)
        live = [t for t in targets if not t.dry_run]
        by_uid = {t.unique_id: t for t in live}

        new = [t for t in live if t.unique_id not in self._created]
        if new:
            async_dispatcher_send(self.hass, SIGNAL_ADD_TARGETS, new)

        changed = [
            t
            for uid, t in by_uid.items()
            if uid in self._created and self._created[uid] != t
        ]
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_UPDATE_TARGETS, changed)
            self._created.update((t.unique_id, t) for t in changed)

        # Orphans (matched by no rule, disabled, or flipped to dry_run) are
        # reconciled: a dry_run flip tears its mirror down, the rest are raised
        # as a Repairs issue (never auto-deleted) — see reconcile_orphans.
        self.reconcile_orphans()

        # Co-own newly-mirrored source devices; drop ones no longer mirrored.
        self.reconcile_device_ownership()

        # Reload is like startup for backfill too: graft any newly-eligible
        # mirror (a rule that just turned backfill_history on). Background +
        # idempotent, so a routine reload with nothing new is a no-op.
        if self.wants_auto_backfill():
            self.hass.async_create_background_task(
                self.async_backfill_new(), "recorder_downsampler_backfill"
            )

    # -- orphaned mirrors (Repairs) -----------------------------------------

    async def async_load(self) -> None:
        """Load persisted state (ignored orphans + already-backfilled mirrors)."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._ignored = set(data.get("ignored", []))
            self._backfilled = set(data.get("backfilled", []))
            self._last_backfill = data.get("last_backfill", {})

    async def _async_save_state(self) -> None:
        await self._store.async_save(
            {
                "ignored": sorted(self._ignored),
                "backfilled": sorted(self._backfilled),
                "last_backfill": self._last_backfill,
            }
        )

    def _orphan_entries(self) -> list[er.RegistryEntry]:
        """Our mirror registry entries that aren't a current live target."""
        keep = {t.unique_id for t in self.resolve_targets() if not t.dry_run}
        ent_reg = er.async_get(self.hass)
        return [
            e
            for e in list(ent_reg.entities.values())
            if e.platform == DOMAIN and e.unique_id not in keep
        ]

    def reconcile_orphans(self) -> None:
        """Reconcile mirrors that aren't a current live target.

        A real orphan is **never auto-deleted** — it may carry long-term
        history, and deleting it over a transient is exactly what lost data:

        - Mirror for a rule deliberately staged to **dry_run**: torn down (an
          explicit "simulate" directive, not an orphan; its statistics persist
          by ``statistic_id`` if it goes live again).
        - Source still **known** (registry or state machine) but matched by no
          rule — disabled, or removed from config: raise a Repairs issue so the
          user decides (delete / ignore, each / all), unless already ignored.
        - Source **not known at all** (neither registered nor in the state
          machine): PRESERVED, untouched — its integration may simply not have
          loaded yet; treating that as "removed" is what destroyed history.

        So a real mirror is only ever removed via the Repairs flow
        (user-initiated) or an explicit dry_run. Clears issues and ignored
        entries whose source has come back. Called after HA start (deferred, so
        the registry/state machine are complete) and on reload; the only async
        side-effect (persisting ``_ignored``) is scheduled so this stays
        callable from sync ``update_config``.
        """
        orphans = self._orphan_entries()
        orphan_uids = {e.unique_id for e in orphans}
        known = _all_known_entity_ids(self.hass)
        dry_run_uids = {t.unique_id for t in self.resolve_targets() if t.dry_run}
        ent_reg = er.async_get(self.hass)

        flagged: set[str] = set()
        for e in orphans:
            source = e.entity_id[: -len(ENTITY_ID_SUFFIX)]
            if e.unique_id in dry_run_uids:
                # Deliberately staged to dry_run — tear the live mirror down.
                # Its statistics persist by statistic_id if it goes live again.
                ent_reg.async_remove(e.entity_id)
                self._created.pop(e.unique_id, None)
            elif source in known:
                # Source still exists but matched by no rule (disabled, or
                # removed from config). Never auto-delete — surface a Repairs
                # issue and let the user delete / ignore.
                flagged.add(e.unique_id)
                if e.unique_id not in self._ignored:
                    self._raise_orphan_issue(e)
            # else: source unknown (not registered, no state) — most likely a
            # not-yet-loaded integration. Preserve the mirror and its history.

        # Drop issues for mirrors no longer flagged (source back, or ignored).
        issue_reg = ir.async_get(self.hass)
        for domain, issue_id in list(issue_reg.issues):
            if domain == DOMAIN and issue_id.startswith(ISSUE_ORPHAN_PREFIX):
                uid = issue_id[len(ISSUE_ORPHAN_PREFIX) :]
                if uid not in flagged or uid in self._ignored:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        # Forget ignores whose source has come back (so it can re-flag later).
        stale = self._ignored - orphan_uids
        if stale:
            self._ignored -= stale
            self.hass.async_create_task(self._async_save_state())

    def _raise_orphan_issue(self, entry: er.RegistryEntry) -> None:
        source = entry.entity_id[: -len(ENTITY_ID_SUFFIX)]
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            _orphan_issue_id(entry.unique_id),
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="orphaned_mirror",
            translation_placeholders={"mirror": entry.entity_id, "source": source},
            data={
                "entry_id": self.entry_id,
                "unique_id": entry.unique_id,
                "entity_id": entry.entity_id,
            },
        )

    def _flagged_orphans(self) -> dict[str, str]:
        """unique_id -> entity_id for orphans whose source still exists.

        These are the Repairs issue subjects (the orphans the user can delete /
        ignore). An orphan whose source isn't known is preserved silently and
        never flagged, so it isn't included here.
        """
        known = _all_known_entity_ids(self.hass)
        return {
            e.unique_id: e.entity_id
            for e in self._orphan_entries()
            if e.entity_id[: -len(ENTITY_ID_SUFFIX)] in known
        }

    async def async_delete_orphan(self, unique_id: str) -> None:
        """Repairs fix: delete one orphaned mirror entity."""
        ent_reg = er.async_get(self.hass)
        entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is not None:
            ent_reg.async_remove(entity_id)
        self._created.pop(unique_id, None)
        self._ignored.discard(unique_id)
        ir.async_delete_issue(self.hass, DOMAIN, _orphan_issue_id(unique_id))
        await self._async_save_state()

    async def async_delete_all_orphans(self) -> None:
        """Repairs fix: delete every currently-flagged orphaned mirror."""
        for unique_id in list(self._flagged_orphans()):
            await self.async_delete_orphan(unique_id)

    async def async_ignore_orphan(self, unique_id: str) -> None:
        """Repairs fix: keep one orphaned mirror; stop nagging about it."""
        self._ignored.add(unique_id)
        ir.async_delete_issue(self.hass, DOMAIN, _orphan_issue_id(unique_id))
        await self._async_save_state()

    async def async_ignore_all_orphans(self) -> None:
        """Repairs fix: keep every flagged orphan; stop nagging about all."""
        for unique_id in list(self._flagged_orphans()):
            self._ignored.add(unique_id)
            ir.async_delete_issue(self.hass, DOMAIN, _orphan_issue_id(unique_id))
        await self._async_save_state()

    # -- statistics backfill ------------------------------------------------

    async def async_backfill(
        self, entity_ids: list[str] | None = None, *, dry_run: bool = False
    ) -> list[str]:
        """Graft each mirror's pre-creation history from its source's stats.

        Without arguments, backfills every mirror; otherwise the given mirror
        entity_ids. Returns a per-mirror status line. Idempotent: a mirror whose
        history was already grafted is skipped. With ``dry_run`` it reports what
        it *would* graft and writes nothing (no import, no state change).
        """
        started = time.monotonic()
        ent_reg = er.async_get(self.hass)
        if entity_ids:
            mirrors = list(entity_ids)
        else:
            mirrors = [
                e.entity_id for e in ent_reg.entities.values() if e.platform == DOMAIN
            ]

        if mirrors:
            _LOGGER.info(
                "backfill_history: %s %d mirror(s)%s",
                "checking" if dry_run else "starting graft of",
                len(mirrors),
                " (dry run)" if dry_run else "",
            )

        results: list[str] = []
        failed: list[str] = []
        for mirror in sorted(mirrors):
            try:
                line = await self._async_backfill_one(mirror, dry_run=dry_run)
            except Exception as err:
                failed.append(mirror)
                line = f"{mirror}: FAILED — {err}"
                _LOGGER.exception("backfill failed for %s", mirror)
            results.append(line)
            # Log each mirror AS IT COMPLETES (actions/failures at INFO, no-op
            # skips at DEBUG) so a long fleet run shows live progress instead of
            # one silent burst at the very end.
            if "row(s) from" in line or "FAILED" in line:
                _LOGGER.info("backfill_history: %s", line)
            else:
                _LOGGER.debug("backfill_history: %s", line)

        acted = sum(1 for r in results if "row(s) from" in r)
        rows = sum(int(n) for r in results for n in re.findall(r"(\d+) row\(s\)", r))
        skipped = len(results) - acted - len(failed)
        duration = round(time.monotonic() - started, 1)
        _LOGGER.info(
            "backfill_history complete: %s %d mirror(s) (%d rows), %d skipped, "
            "%d failed in %.1fs",
            "would graft" if dry_run else "grafted",
            acted,
            rows,
            skipped,
            len(failed),
            duration,
        )

        if not dry_run:
            self._last_backfill = {
                "completed": dt_util.utcnow().isoformat(),
                "grafted": acted,
                "rows": rows,
                "skipped": skipped,
                "failed": len(failed),
                "duration_s": duration,
            }
            await self._async_save_state()
            self._announce_backfill(acted, rows, skipped, failed, duration)
        return results

    def _announce_backfill(
        self,
        grafted: int,
        rows: int,
        skipped: int,
        failed: list[str],
        duration: float,
    ) -> None:
        """Fire the completion event and raise/clear persistent notifications."""
        from homeassistant.components import persistent_notification

        self.hass.bus.async_fire(
            EVENT_BACKFILL_COMPLETED,
            {
                "grafted": grafted,
                "rows": rows,
                "skipped": skipped,
                "failed": len(failed),
                "duration_s": duration,
            },
        )
        if failed:
            persistent_notification.async_create(
                self.hass,
                f"{len(failed)} mirror(s) failed to backfill: "
                f"{', '.join(sorted(failed)[:10])}. See the log for details.",
                title="Recorder Downsampler: backfill failed",
                notification_id=f"{DOMAIN}_backfill_failed",
            )
        else:
            persistent_notification.async_dismiss(
                self.hass, f"{DOMAIN}_backfill_failed"
            )
        if grafted:
            persistent_notification.async_create(
                self.hass,
                f"Grafted history onto {grafted} mirror(s) ({rows} rows) in "
                f"{duration:.0f}s; {skipped} already current.",
                title="Recorder Downsampler: backfill complete",
                notification_id=f"{DOMAIN}_backfill",
            )

    def wants_auto_backfill(self) -> bool:
        """True if the global flag or any enabled rule opts into auto-backfill.

        Used to decide whether to schedule the background task at all — if
        nothing opts in, there's no work to do.
        """
        if self.config.get(CONF_BACKFILL_HISTORY):
            return True
        return any(
            rule.get(CONF_BACKFILL_HISTORY) and rule.get(CONF_ENABLED, True)
            for rule in self.config.get(CONF_RULES, [])
        )

    async def async_backfill_new(self) -> None:
        """Background entry point for the opt-in auto-backfill (setup + reload).

        Backfills only mirrors whose resolved target opts in (per-rule
        ``backfill_history``, else the global default) and that haven't been
        grafted yet — so enabling it on one rule grafts just that rule's
        mirrors, and a routine reload with nothing new is a clean no-op (no
        re-graft, no completion event). Dry-run targets have no mirror entity
        and are skipped.

        Best-effort: a failure is logged (and surfaced via the failure
        notification inside async_backfill), never propagated to the caller.
        """
        try:
            ent_reg = er.async_get(self.hass)
            mirrors: list[str] = []
            for target in self.resolve_targets():
                if target.dry_run or not target.backfill_history:
                    continue
                if target.unique_id in self._backfilled:
                    continue  # already grafted — keep a reload a clean no-op
                mirror_id = ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, target.unique_id
                )
                if mirror_id is not None:
                    mirrors.append(mirror_id)
            if not mirrors:
                return
            await self.async_backfill(mirrors)
        except Exception:
            _LOGGER.exception("auto-backfill failed")

    async def _async_backfill_one(
        self, mirror_entity_id: str, *, dry_run: bool = False
    ) -> str:
        from homeassistant.components.recorder.models import (
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
            get_metadata,
            statistics_during_period,
        )
        from homeassistant.helpers.recorder import get_instance

        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(mirror_entity_id)
        if entry is None or entry.platform != DOMAIN:
            return f"{mirror_entity_id}: not a downsampler mirror"
        if entry.unique_id in self._backfilled:
            return f"{mirror_entity_id}: already backfilled"
        source = mirror_entity_id[: -len(ENTITY_ID_SUFFIX)]

        inst = get_instance(self.hass)
        meta = await inst.async_add_executor_job(
            partial(get_metadata, self.hass, statistic_ids={source})
        )
        if source not in meta:
            return f"{mirror_entity_id}: source {source} has no statistics"
        src_meta = meta[source][1]
        has_sum = bool(src_meta.get("has_sum"))
        mean_type = src_meta.get("mean_type")
        has_mean = bool(src_meta.get("has_mean")) or (
            mean_type is not None and mean_type != StatisticMeanType.NONE
        )
        if not has_sum and not has_mean:
            return f"{mirror_entity_id}: {source} has no numeric statistics"

        types = {"state", "sum"} if has_sum else {"mean", "min", "max"}
        data = await inst.async_add_executor_job(
            statistics_during_period,
            self.hass,
            dt_util.utc_from_timestamp(0),
            dt_util.utcnow(),
            {source, mirror_entity_id},
            "hour",
            None,
            types,
        )
        mirror_rows = data.get(mirror_entity_id, [])
        # Anchor the graft where the mirror's own series begins: its first
        # recorded row, or "now" for a brand-new mirror with no stats yet.
        cutover = (
            mirror_rows[0]["start"] if mirror_rows else dt_util.utcnow().timestamp()
        )
        rows = build_backfill_rows(
            cast("list[dict[str, Any]]", data.get(source, [])),
            cutover=cutover,
            has_sum=has_sum,
        )
        if not rows:
            return f"{mirror_entity_id}: no source history before mirror start"

        if dry_run:
            return f"{mirror_entity_id}: would graft {len(rows)} row(s) from {source} (dry run)"

        metadata = cast(
            StatisticMetaData,
            {**src_meta, "statistic_id": mirror_entity_id, "name": None},
        )
        async_import_statistics(self.hass, metadata, cast("list[StatisticData]", rows))
        # Wait for the queued import to commit before recording the mirror as
        # backfilled, so a crash can't leave the flag ahead of the data. (A flag
        # lost *after* the commit is harmless — a re-run is an idempotent no-op
        # once the rows exist.)
        await inst.async_block_till_done()
        self._backfilled.add(entry.unique_id)
        return f"{mirror_entity_id}: grafted {len(rows)} row(s) from {source}"

    def mark_created(self, targets: list[Target]) -> None:
        self._created.update((t.unique_id, t) for t in targets)

    def reconcile_device_ownership(self) -> None:
        """Co-own every source device our live mirrors attach to.

        Adds our config entry to each distinct source device (so that device
        also appears under the Recorder Downsampler card), and drops our entry
        from devices we no longer mirror. One ``async_update_device`` per device
        added/removed; a no-op until the integration has a config entry. (When
        the entry is fully removed, HA clears us from all devices itself.)
        """
        if self.entry_id is None:
            return
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        wanted: set[str] = set()
        for target in self._created.values():
            src = ent_reg.async_get(target.source_entity_id)
            if src is not None and src.device_id is not None:
                wanted.add(src.device_id)

        for device_id in wanted - self._coowned_devices:
            if dev_reg.async_get(device_id) is not None:
                dev_reg.async_update_device(
                    device_id, add_config_entry_id=self.entry_id
                )
        for device_id in self._coowned_devices - wanted:
            if dev_reg.async_get(device_id) is not None:
                dev_reg.async_update_device(
                    device_id, remove_config_entry_id=self.entry_id
                )
        self._coowned_devices = wanted
