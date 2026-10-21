"""Config flow tests for the Huawei Health integration.

Covers the browser-assisted sign-in (region -> sign-in page -> pasted code),
every error the flow can surface, and the reauth flow.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hass_health_huawei.client import (
    HuaweiAuthError,
    HuaweiConnectionError,
    Tokens,
)
from custom_components.hass_health_huawei.const import (
    CONF_ACCESS_TOKEN,
    CONF_CODE,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_UID,
    DOMAIN,
)

CODE_ADDRESS = "hms://redirect_url?code=AB%2B"

EXCHANGE = (
    "custom_components.hass_health_huawei.config_flow.exchange_authorization_code"
)
PROFILE_NAME = "custom_components.hass_health_huawei.config_flow._profile_name"
_GATHER = "custom_components.hass_health_huawei.coordinator.HuaweiHealthCoordinator._gather"

ENTRY_DATA = {
    CONF_UID: "1001",
    CONF_ACCESS_TOKEN: "old-access",
    CONF_REFRESH_TOKEN: "old-refresh",
    "site_id": 1,
    "expires_at": 0.0,
    "refresh_expires_at": 0.0,
    "session_host": "https://session.example",
    CONF_REGION: "cn",
}


def _tokens(uid: str = "1001") -> Tokens:
    now = time.time()
    return Tokens(
        access_token="new-access",
        refresh_token="new-refresh",
        uid=uid,
        expires_at=now + 3600,
        refresh_expires_at=now + 86400,
        session_host="https://session.example",
    )


async def _start_user_flow(hass, region: str = "cn"):
    """Drive the flow up to the code step and return (flow_id, result)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGION: region}
    )
    assert result["step_id"] == "login"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "code"
    return result["flow_id"], result


async def test_user_step_shows_region_form(hass) -> None:
    """The first step only asks which cloud the account belongs to."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"] is None


async def test_full_flow_creates_entry(hass) -> None:
    """A valid pasted code creates an entry keyed by the HUAWEI ID."""
    with (
        patch(EXCHANGE, return_value=_tokens()),
        patch(PROFILE_NAME, return_value="Alice"),
    ):
        flow_id, _ = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_CODE: CODE_ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alice"
    assert result["data"][CONF_ACCESS_TOKEN] == "new-access"
    assert result["data"][CONF_REGION] == "cn"
    assert result["result"].unique_id == "1001"


async def test_pasted_text_without_code(hass) -> None:
    """Anything without a `code=` parameter is rejected field-level."""
    flow_id, _ = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_CODE: "nothing useful here"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "no_code"}


async def test_invalid_code(hass) -> None:
    """A refused code keeps the user on the same step and refreshes the URL."""
    with patch(EXCHANGE, side_effect=HuaweiAuthError("refused")):
        flow_id, _ = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_CODE: CODE_ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "invalid_code"}


async def test_cannot_connect(hass) -> None:
    """Network failures are reported as such, not as an invalid code."""
    with patch(EXCHANGE, side_effect=HuaweiConnectionError("offline")):
        flow_id, _ = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_CODE: CODE_ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "cannot_connect"}


async def test_flow_aborts_for_a_known_account(hass) -> None:
    """Adding the same HUAWEI ID twice aborts instead of creating a duplicate."""
    MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    ).add_to_hass(hass)

    with (
        patch(EXCHANGE, return_value=_tokens()),
        patch(PROFILE_NAME, return_value="Alice"),
    ):
        flow_id, _ = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_CODE: CODE_ADDRESS}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_same_entry(hass) -> None:
    """Reauth writes the fresh token pair into the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "code"

    with (
        patch(EXCHANGE, return_value=_tokens()),
        patch(PROFILE_NAME, return_value="Alice"),
        patch(_GATHER, return_value={"days": {}, "sessions": [], "window_days": 7}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: CODE_ADDRESS}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == "new-access"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert entry.data[CONF_UID] == "1001"


async def test_reauth_with_another_account_aborts(hass) -> None:
    """Signing in with a different HUAWEI ID must not take over the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    with (
        patch(EXCHANGE, return_value=_tokens(uid="2002")),
        patch(PROFILE_NAME, return_value="Bob"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: CODE_ADDRESS}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_ACCESS_TOKEN] == "old-access"


async def test_options_flow_reloads_and_stores_options(hass) -> None:
    """Options are validated, stored, and the entry is reloaded by HA."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1001", data=ENTRY_DATA, title="Alice"
    )
    entry.add_to_hass(hass)

    with patch(_GATHER, return_value={"days": {}, "sessions": [], "window_days": 7}):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "update_interval": 30,
                "history_days": 5,
                "workout_days": 10,
                "enable_workouts": False,
                "enable_health_metrics": True,
                "device_code": 0,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["update_interval"] == 30
    assert entry.options["history_days"] == 5
    assert entry.options["enable_workouts"] is False
    # 0 means "whole account", so it is not persisted as a mask.
    assert "device_code" not in entry.options
