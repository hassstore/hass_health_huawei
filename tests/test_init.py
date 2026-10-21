"""Setup, entity and diagnostics tests for the Huawei Health integration."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.icon import async_get_icons
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hass_health_huawei.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_UID,
    DOMAIN,
)
from custom_components.hass_health_huawei.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hass_health_huawei.sensor import DESCRIPTIONS

_GATHER = "custom_components.hass_health_huawei.coordinator.HuaweiHealthCoordinator._gather"

NOW = time.time()

ENTRY_DATA = {
    CONF_UID: "1001",
    CONF_ACCESS_TOKEN: "supersecret-access",
    CONF_REFRESH_TOKEN: "supersecret-refresh",
    "site_id": 1,
    "expires_at": NOW + 3600,
    CONF_REFRESH_EXPIRES_AT: NOW + 30 * 86400,
    "session_host": "https://session.example",
    CONF_REGION: "cn",
}


def _snapshot() -> dict[str, Any]:
    """A coordinator snapshot shaped like the real one."""
    session = {
        "sport_type": 4,
        "start": datetime.fromtimestamp(NOW - 3600, tz=timezone.utc),
        "end": datetime.fromtimestamp(NOW, tz=timezone.utc),
        "duration_min": 42,
        "distance_m": 7000,
        "kcal": 350.0,
        "segments": 12,
        "device_code": 7,
    }
    return {
        "days": {
            dt_util.now().date().isoformat(): {
                "steps": 1200,
                "distance_m": 8000,
                "kcal": 300,
                "duration_min": 30,
                "sleep_duration": 410,
            }
        },
        "sessions": [session],
        "latest_session": session,
        "window_days": 7,
    }


def _entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        item.unique_id.removeprefix(f"{entry.entry_id}_"): item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_setup_creates_one_entity_per_description(hass: HomeAssistant) -> None:
    """The entry loads and exposes every declared sensor on one device."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    with patch(_GATHER, return_value=_snapshot()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    entities = _entities(hass, entry)
    assert len(entities) == len(DESCRIPTIONS)
    assert set(entities) == {description.key for description in DESCRIPTIONS}

    # has_entity_name: entity_id is <device>_<translation_key>.
    assert entities["steps"].startswith("sensor.alice_")
    assert float(hass.states.get(entities["steps"]).state) == 1200
    assert float(hass.states.get(entities["distance"]).state) == 8.0
    assert hass.states.get(entities["last_activity"]).state == "running"

    # Icons come from icons.json (read by the frontend), not from the code.
    icons = await async_get_icons(hass, "entity", [DOMAIN])
    assert icons[DOMAIN]["sensor"]["steps"]["default"] == "mdi:walk"
    assert icons[DOMAIN]["sensor"]["relogin_in"]["default"] == "mdi:account-clock"

    # The "date" attribute says which day the value belongs to.
    assert hass.states.get(entities["steps"]).attributes["date"] == (
        dt_util.now().date().isoformat()
    )

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "Alice"


async def test_missing_metric_is_unknown_not_unavailable(hass: HomeAssistant) -> None:
    """A data point the cloud never produced is `unknown`; the entry stays up."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    with patch(
        _GATHER,
        return_value={
            "days": {},
            "sessions": [],
            "latest_session": None,
            "window_days": 7,
        },
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entities = _entities(hass, entry)
    assert hass.states.get(entities["sleep_spo2"]).state == "unknown"
    assert entry.state is ConfigEntryState.LOADED


async def test_unload_entry(hass: HomeAssistant) -> None:
    """The entry can be unloaded again (no leaked platforms)."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    with patch(_GATHER, return_value=_snapshot()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert getattr(entry, "runtime_data", None) is None


async def test_diagnostics_redact_tokens(hass: HomeAssistant) -> None:
    """Diagnostics are useful and carry no token material."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    with patch(_GATHER, return_value=_snapshot()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = str(diag)

    assert "supersecret-access" not in dumped
    assert "supersecret-refresh" not in dumped
    assert diag["entry"]["region"] == "cn"
    assert diag["snapshot"]["days"] == 1
    assert diag["snapshot"]["sessions"] == 1
