"""Huawei Health 的诊断数据。

设备页 → ⋯ → 「下载诊断信息」会走到这里：脱敏后的条目数据 + 当前快照概况。
排障（resultCode、区域、token 到期日、有没有取到数据）足够，但绝不会带出令牌。
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HuaweiHealthConfigEntry
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SITE_ID,
    CONF_UID,
)

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_UID,
    CONF_SITE_ID,
}


async def async_get_config_entry_diagnostics(
        hass: HomeAssistant, entry: HuaweiHealthConfigEntry) -> dict[str, Any]:
    """返回该条目的诊断信息（令牌已脱敏）。"""
    data = getattr(entry, "runtime_data", None)
    snapshot = (data.coordinator.data if data is not None else None) or {}
    sessions = snapshot.get("sessions") or []
    days = snapshot.get("days") or {}

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "title": entry.title,
            "unique_id": entry.unique_id,
            "region": entry.data.get(CONF_REGION),
        },
        "tokens": {
            "refresh_expires_at": entry.data.get(CONF_REFRESH_EXPIRES_AT),
            "refresh_token_present": bool(entry.data.get(CONF_REFRESH_TOKEN)),
            "access_token_present": bool(entry.data.get(CONF_ACCESS_TOKEN)),
        },
        "snapshot": {
            "days": len(days),
            "latest_day": max(days) if days else None,
            "sessions": len(sessions),
            "window_days": snapshot.get("window_days"),
            "metrics": sorted({key for row in days.values() for key in row}),
        },
    }
