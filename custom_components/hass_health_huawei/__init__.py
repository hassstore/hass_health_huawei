"""Huawei Health 集成。

为一个（或多个）HUAWEI ID 账号轮询 Health 应用自己的云端。
每个 config entry 携带自己独立轮换的 token 对，因此各账号完全互不影响。

本项目基于 and7ey 的 huawei_health 逆向工程成果二次开发，感谢原作者。
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError

from .client import HuaweiHealthClient, Tokens
from .const import (
    APP_VERSION,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_CODE,
    CONF_EXPIRES_AT,
    CONF_REFRESH_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SESSION_HOST,
    CONF_SITE_ID,
    CONF_TOKEN_TYPE,
    CONF_UID,
    DOMAIN,
    PLATFORMS,
    TOKEN_TYPE,
)
from .coordinator import HuaweiHealthCoordinator, HuaweiHealthData

_LOGGER = logging.getLogger(__name__)

type HuaweiHealthConfigEntry = ConfigEntry[HuaweiHealthData]


async def async_setup_entry(
        hass: HomeAssistant, entry: HuaweiHealthConfigEntry) -> bool:
    """构建 client，验证凭据可用，然后交给各 platform。"""
    if not entry.data.get(CONF_REFRESH_TOKEN):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="no_refresh_token",
        )

    data = _build(hass, entry)
    await data.coordinator.async_config_entry_first_refresh()

    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _build(hass: HomeAssistant,
           entry: HuaweiHealthConfigEntry) -> HuaweiHealthData:
    tokens = Tokens(
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        uid=entry.data.get(CONF_UID),
        site_id=entry.data.get(CONF_SITE_ID),
        expires_at=float(entry.data.get(CONF_EXPIRES_AT) or 0),
        refresh_expires_at=float(entry.data.get(CONF_REFRESH_EXPIRES_AT) or 0),
        session_host=entry.data.get(CONF_SESSION_HOST),
        on_change=lambda current: _schedule_save(hass, entry, current),
    )
    # device_code 是用户可改的选项，所以只能从 options 里取——data 里没有它。
    device_code = entry.options.get(CONF_DEVICE_CODE) or entry.data.get(CONF_DEVICE_CODE)
    client = HuaweiHealthClient(
        tokens,
        region=entry.data.get(CONF_REGION),
        device_code=int(device_code) if device_code else None,
        version=APP_VERSION,
        token_type=int(entry.data.get(CONF_TOKEN_TYPE) or TOKEN_TYPE),
    )
    coordinator = HuaweiHealthCoordinator(hass, entry, client, tokens)
    return HuaweiHealthData(entry=entry, client=client, tokens=tokens,
                            coordinator=coordinator)


def _schedule_save(hass: HomeAssistant, entry: HuaweiHealthConfigEntry,
                   tokens: Tokens) -> None:
    """从执行轮换的任意线程持久化新的 token 对。

    轮换会在服务端使旧的 access token 失效，所以丢失这次写入就意味着
    会话丢失、只能重新登录 —— 因此需要用线程安全的方式调度。
    """
    hass.loop.call_soon_threadsafe(_save_tokens, hass, entry, tokens)


@callback
def _save_tokens(hass: HomeAssistant, entry: HuaweiHealthConfigEntry,
                 tokens: Tokens) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_ACCESS_TOKEN: tokens.access_token,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_UID: tokens.uid,
            CONF_SITE_ID: tokens.site_id,
            CONF_EXPIRES_AT: tokens.expires_at,
            CONF_REFRESH_EXPIRES_AT: tokens.refresh_expires_at,
            CONF_SESSION_HOST: tokens.session_host,
        },
    )
    _LOGGER.debug("Stored the rotated Huawei Health token for %s", entry.title)


async def async_unload_entry(
        hass: HomeAssistant, entry: HuaweiHealthConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.runtime_data = None
    return True
