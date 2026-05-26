# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.
"""Unit tests for the (anchor-only) config flow."""

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.recorder_downsampler.const import DOMAIN


async def test_import_creates_single_entry(
    recorder_mock: object, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """Importing from YAML creates the one entry; a second import aborts."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={}
    )
    assert first["type"] == FlowResultType.CREATE_ENTRY
    assert first["title"] == "Recorder Downsampler"

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={}
    )
    assert second["type"] == FlowResultType.ABORT


async def test_user_step_is_single_instance(
    recorder_mock: object, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """A UI-initiated add creates the entry once, then is single-instance."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert first["type"] == FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert second["type"] == FlowResultType.ABORT
