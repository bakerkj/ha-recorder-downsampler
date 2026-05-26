# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Repairs flow for Recorder Downsampler.

Raised when a mirror's source has been disabled (e.g. an unwired hardware channel
its integration disabled), leaving the mirror with no data. The user can delete
that mirror, delete every flagged orphan at once, keep this one (ignore), or
keep all of them — rather than the integration silently deleting entities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DATA_MANAGER, DOMAIN

if TYPE_CHECKING:
    from . import RecorderDownsampleManager


class OrphanedMirrorRepairFlow(RepairsFlow):
    """Menu-driven fix flow for a mirror whose source is disabled."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any] | None) -> None:
        self._hass = hass
        self._data = data or {}

    def _manager(self) -> RecorderDownsampleManager | None:
        return cast(
            "RecorderDownsampleManager | None",
            self._hass.data.get(DOMAIN, {}).get(DATA_MANAGER),
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["delete_this", "delete_all", "ignore_this", "ignore_all"],
        )

    async def async_step_delete_this(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        mgr = self._manager()
        unique_id = self._data.get("unique_id")
        if mgr is not None and isinstance(unique_id, str):
            await mgr.async_delete_orphan(unique_id)
        return self.async_create_entry(title="", data={})

    async def async_step_delete_all(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        mgr = self._manager()
        if mgr is not None:
            await mgr.async_delete_all_orphans()
        return self.async_create_entry(title="", data={})

    async def async_step_ignore_this(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        mgr = self._manager()
        unique_id = self._data.get("unique_id")
        if mgr is not None and isinstance(unique_id, str):
            await mgr.async_ignore_orphan(unique_id)
        return self.async_create_entry(title="", data={})

    async def async_step_ignore_all(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        mgr = self._manager()
        if mgr is not None:
            await mgr.async_ignore_all_orphans()
        return self.async_create_entry(title="", data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    return OrphanedMirrorRepairFlow(hass, data)
