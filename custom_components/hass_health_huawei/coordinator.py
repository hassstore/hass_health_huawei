"""Huawei Health 集成的 coordinator。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .client import (
    HuaweiAuthError,
    HuaweiConnectionError,
    HuaweiError,
    HuaweiHealthClient,
    Tokens,
    daily_totals,
)
from .const import (
    CONF_ENABLE_HEALTH,
    CONF_ENABLE_WORKOUTS,
    CONF_HISTORY_DAYS,
    CONF_UPDATE_INTERVAL,
    CONF_WORKOUT_DAYS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WORKOUT_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class HuaweiHealthData:
    """entity 所需的一切，挂在 entry.runtime_data 上。"""

    entry: ConfigEntry
    client: HuaweiHealthClient
    tokens: Tokens
    coordinator: HuaweiHealthCoordinator

    def day(self, metric: str, lookback_days: int = 0) -> tuple[str, Any] | None:
        """某个按天指标的最新值，连同它所属的日期。

        云端把一夜标记在它结束的那天，并且随着手环同步才逐渐补齐当天数据，
        所以“今天”为空有几个小时是正常的；前一天才是诚实的读数，而不是“不可用”。
        """
        days = (self.coordinator.data or {}).get("days") or {}
        # 用 HA 配置的时区而不是容器的系统时区：云端按用户本地日期归档。
        offset = dt_util.now().date()
        for _ in range(lookback_days + 1):
            row = days.get(offset.isoformat())
            if row and row.get(metric) is not None:
                return offset.isoformat(), row[metric]
            offset -= timedelta(days=1)
        return None

    @property
    def latest_session(self) -> dict | None:
        return (self.coordinator.data or {}).get("latest_session")

    @property
    def sessions(self) -> list[dict]:
        return (self.coordinator.data or {}).get("sessions") or []


class HuaweiHealthCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """在工作线程中轮询账号的云端，token 会自动轮换。"""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 client: HuaweiHealthClient, tokens: Tokens) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=int(entry.options.get(CONF_UPDATE_INTERVAL)
                            or DEFAULT_UPDATE_INTERVAL.total_seconds() // 60)),
        )
        self.client = client
        self.tokens = tokens

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._gather)
        except HuaweiAuthError as err:
            # 只有重新走一次浏览器登录才能解决，所以向 HA 请求 reauth 卡片。
            raise ConfigEntryAuthFailed(str(err)) from err
        except HuaweiConnectionError as err:
            raise UpdateFailed(f"cannot reach the Huawei Health cloud: {err}") from err
        except HuaweiError as err:
            raise UpdateFailed(str(err)) from err

    def _gather(self) -> dict[str, Any]:
        entry = self.config_entry
        # 云端按用户本地日期归档；executor 里的系统时区通常是 UTC，因此显式传入。
        today: date = dt_util.now().date()
        tz = dt_util.DEFAULT_TIME_ZONE
        history = int(entry.options.get(CONF_HISTORY_DAYS) or DEFAULT_HISTORY_DAYS)
        days: dict[str, dict] = {}
        failures: list[str] = []

        def run(name: str, fetch, merge) -> None:
            try:
                merge(fetch())
            except HuaweiAuthError:
                raise
            except HuaweiError as err:
                # 账号设备从不产生的某一类数据，不应掩盖其余部分。
                _LOGGER.debug("Huawei Health %s read failed: %s", name, err)
                failures.append(f"{name}: {err}")

        def merge_daily(rows: list[dict]) -> None:
            for day, row in daily_totals(rows).items():
                days.setdefault(day, {}).update(row)

        def merge_health(rows: list[dict]) -> None:
            for row in rows:
                days.setdefault(row["date"], {}).update(row)

        run("activity",
            lambda: self.client.daily_summary(history, today=today, tz=tz),
            merge_daily)
        if entry.options.get(CONF_ENABLE_HEALTH, True):
            run("health",
                lambda: self.client.health_summary(history, today=today, tz=tz),
                merge_health)

        workouts: list[dict] = []
        window_days = int(entry.options.get(CONF_WORKOUT_DAYS) or DEFAULT_WORKOUT_DAYS)
        if entry.options.get(CONF_ENABLE_WORKOUTS, True):
            def fetch_sessions() -> list[dict]:
                return self.client.sessions(window_days, today=today)

            run("workouts", fetch_sessions, lambda rows: workouts.extend(rows))

        if not days and failures:
            raise UpdateFailed("; ".join(failures))
        workouts.sort(key=lambda session: session["start"])
        return {
            "days": days,
            "sessions": workouts,
            "latest_session": workouts[-1] if workouts else None,
            "window_days": window_days,
        }
