# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Config flow for Recorder Downsampler.

The integration is configured entirely in YAML (the ``recorder_downsampler:``
block plus its rules). This flow exists solely to anchor a single config entry
so the integration — and its mirror entities — appear under Settings → Devices
& Services. ``async_setup`` triggers an import of this flow on startup; the
entry carries no data (the live config lives in ``hass.data``, keyed off the
YAML, because validated values like timedeltas aren't JSON-serializable).
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class RecorderDownsampleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance flow that anchors the YAML-configured integration."""

    VERSION = 1

    async def _create_single_entry(self) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Recorder Downsampler", data={})

    async def async_step_import(
        self, import_data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry from YAML on startup."""
        return await self._create_single_entry()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow adding from the UI; config still comes from YAML."""
        return await self._create_single_entry()
