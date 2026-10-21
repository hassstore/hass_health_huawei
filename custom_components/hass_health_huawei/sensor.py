"""Huawei Health 集成的 sensor。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HuaweiHealthConfigEntry
from .const import SPORT_TYPES
from .coordinator import HuaweiHealthData
from .entity import HuaweiHealthEntity

# 只读平台：所有数据来自同一个 coordinator，不需要并发请求配额。
PARALLEL_UPDATES = 0

UNIT_STEPS: str = "steps"
UNIT_BPM: str = "bpm"
UNIT_SCORE: str = "score"


@dataclass(kw_only=True)
class HuaweiSensorDescription(SensorEntityDescription):
    """一个 sensor，加上如何从 coordinator 快照中取出它的数值。

    icon 不写在这里：按规范放进 icons.json（entity.sensor.<translation_key>），
    这样不需要在每个 description 里硬编码图标。
    """

    value: Callable[[HuaweiHealthData], Any] = lambda data: None
    attributes: Callable[[HuaweiHealthData], dict[str, Any]] = lambda data: {}


def day_value(metric: str, lookback: int = 0, scale: float = 1.0
              ) -> Callable[[HuaweiHealthData], Any]:
    """读取一个按天指标，必要时回退到之前的日期。"""

    def get(data: HuaweiHealthData) -> Any:
        found = data.day(metric, lookback)
        if found is None:
            return None
        # 步数、分钟数这类整数指标不要因为乘 1.0 变成 "1200.0"。
        return found[1] if scale == 1.0 else found[1] * scale

    return get


def day_date(metric: str, lookback: int = 0) -> Callable[[HuaweiHealthData], dict]:
    def attributes(data: HuaweiHealthData) -> dict[str, Any]:
        found = data.day(metric, lookback)
        return {} if found is None else {"date": found[0]}

    return attributes


def session_value(key: str, scale: float = 1.0
                  ) -> Callable[[HuaweiHealthData], Any]:
    def get(data: HuaweiHealthData) -> Any:
        session = data.latest_session
        value = None if session is None else session.get(key)
        if value is None:
            return None
        return value if scale == 1.0 else value * scale

    return get


def _local_stamp(stamp: datetime | None) -> str:
    """把云端时间戳换成 HA 配置时区里的可读时间。"""
    if stamp is None:
        return ""
    return dt_util.as_local(stamp).strftime("%Y-%m-%d %H:%M")


def session_attributes(data: HuaweiHealthData) -> dict[str, Any]:
    session = data.latest_session
    if not session:
        return {}
    return {
        "start": _local_stamp(session["start"]),
        "end": _local_stamp(session["end"]),
        "duration_min": session["duration_min"],
        "distance_m": session["distance_m"],
        "calories_kcal": session["kcal"],
        "segments": session["segments"],
        "sport_type": session["sport_type"],
        "device_code": session["device_code"],
    }


def activity_name(data: HuaweiHealthData) -> str | None:
    session = data.latest_session
    if not session:
        return None
    # 超出已声明 options 的状态会让 HA 记录警告，
    # 所以来自更新版应用的未知 id 都归并为同一个值。
    return SPORT_TYPES.get(session["sport_type"], "unknown")


def _days_left(data: HuaweiHealthData) -> float | None:
    deadline = data.tokens.refresh_expires_at
    return None if not deadline else round((deadline - time.time()) / 86400, 1)


def _token_dates(data: HuaweiHealthData) -> dict[str, Any]:
    def stamp(seconds: float) -> str:
        return dt_util.as_local(
            dt_util.utc_from_timestamp(seconds)).strftime("%Y-%m-%d %H:%M")

    return {
        "access_token_expires": stamp(data.tokens.expires_at),
        "refresh_token_expires": stamp(data.tokens.refresh_expires_at),
    }


DESCRIPTIONS: tuple[HuaweiSensorDescription, ...] = (
    # ── 当天合计，对应 Health 应用的活动三环 ──────────────────────────────
    HuaweiSensorDescription(
        key="steps", translation_key="steps", native_unit_of_measurement=UNIT_STEPS,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("steps"), attributes=day_date("steps")),
    HuaweiSensorDescription(
        key="distance", translation_key="distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("distance_m", scale=1 / 1000), attributes=day_date("distance_m")),
    HuaweiSensorDescription(
        key="calories", translation_key="calories",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        suggested_display_precision=0, state_class=SensorStateClass.TOTAL,
        value=day_value("kcal"), attributes=day_date("kcal")),
    HuaweiSensorDescription(
        key="activity_duration", translation_key="activity_duration",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("duration_min"), attributes=day_date("duration_min")),
    HuaweiSensorDescription(
        key="walking_duration", translation_key="walking_duration",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("walk_min"), attributes=day_date("walk_min")),
    HuaweiSensorDescription(
        key="active_hours", translation_key="active_hours",
        native_unit_of_measurement=UnitOfTime.HOURS, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("active_hours"), attributes=day_date("active_hours")),
    HuaweiSensorDescription(
        key="moderate_high_intensity_minutes",
        translation_key="moderate_high_intensity_minutes",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("moderate_high_intensity_minutes"),
        attributes=day_date("moderate_high_intensity_minutes")),
    HuaweiSensorDescription(
        key="step_goal", translation_key="step_goal", native_unit_of_measurement=UNIT_STEPS,
        entity_registry_enabled_default=False,
        value=day_value("step_goal"), attributes=day_date("step_goal")),
    # ── 心率 ─────────────────────────────────────────────────────────────
    HuaweiSensorDescription(
        key="resting_heart_rate", translation_key="resting_heart_rate",
        native_unit_of_measurement=UNIT_BPM, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("resting_heart_rate"),
        attributes=day_date("resting_heart_rate")),
    HuaweiSensorDescription(
        key="heart_rate", translation_key="heart_rate",
        native_unit_of_measurement=UNIT_BPM, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("heart_rate"), attributes=day_date("heart_rate")),
    HuaweiSensorDescription(
        key="max_heart_rate", translation_key="max_heart_rate",
        native_unit_of_measurement=UNIT_BPM, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("max_heart_rate"), attributes=day_date("max_heart_rate")),
    HuaweiSensorDescription(
        key="min_heart_rate", translation_key="min_heart_rate",
        native_unit_of_measurement=UNIT_BPM, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("min_heart_rate"), attributes=day_date("min_heart_rate")),
    # ── 睡眠：刚结束的一夜会被标记在醒来的那一天 ──────────────────────────
    HuaweiSensorDescription(
        key="sleep_duration", translation_key="sleep_duration",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_duration", lookback=1),
        attributes=day_date("sleep_duration", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_deep", translation_key="sleep_deep",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_deep", lookback=1),
        attributes=day_date("sleep_deep", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_rem", translation_key="sleep_rem",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_rem", lookback=1),
        attributes=day_date("sleep_rem", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_light", translation_key="sleep_light",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("sleep_light", lookback=1),
        attributes=day_date("sleep_light", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_awake", translation_key="sleep_awake",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("sleep_awake", lookback=1),
        attributes=day_date("sleep_awake", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_score", translation_key="sleep_score",
        native_unit_of_measurement=UNIT_SCORE, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_score", lookback=1),
        attributes=day_date("sleep_score", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_efficiency", translation_key="sleep_efficiency",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("sleep_efficiency", lookback=1),
        attributes=day_date("sleep_efficiency", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_hrv", translation_key="sleep_hrv",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_hrv", lookback=1),
        attributes=day_date("sleep_hrv", lookback=1)),
    HuaweiSensorDescription(
        key="sleep_spo2", translation_key="sleep_spo2",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=day_value("sleep_spo2", lookback=1),
        attributes=day_date("sleep_spo2", lookback=1)),
    # ── 压力 ─────────────────────────────────────────────────────────────
    HuaweiSensorDescription(
        key="stress", translation_key="stress", native_unit_of_measurement=UNIT_SCORE,
        state_class=SensorStateClass.MEASUREMENT,
        value=day_value("stress_average"), attributes=day_date("stress_average")),
    HuaweiSensorDescription(
        key="stress_last", translation_key="stress_last",
        native_unit_of_measurement=UNIT_SCORE, state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=day_value("stress_last"), attributes=day_date("stress_last")),
    # ── 锻炼，由逐分钟片段重建 ────────────────────────────────────────────
    HuaweiSensorDescription(
        key="last_activity", translation_key="last_activity",
        device_class=SensorDeviceClass.ENUM,
        options=list(SPORT_TYPES.values()),
        value=activity_name, attributes=session_attributes),
    HuaweiSensorDescription(
        key="last_activity_duration", translation_key="last_activity_duration",
        device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value=session_value("duration_min"), attributes=session_attributes),
    HuaweiSensorDescription(
        key="last_activity_distance", translation_key="last_activity_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2, state_class=SensorStateClass.MEASUREMENT,
        value=session_value("distance_m", scale=1 / 1000),
        attributes=session_attributes),
    HuaweiSensorDescription(
        key="last_activity_calories", translation_key="last_activity_calories",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        suggested_display_precision=0, state_class=SensorStateClass.TOTAL,
        value=session_value("kcal"), attributes=session_attributes),
    HuaweiSensorDescription(
        key="workouts", translation_key="workouts", native_unit_of_measurement="workouts",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda data: len(data.sessions),
        attributes=lambda data: {
            "window_days": (data.coordinator.data or {}).get("window_days")}),
    # ── 会话健康度：那个告诉你何时需要重新登录的数字 ──────────────────────
    HuaweiSensorDescription(
        key="relogin_in", translation_key="relogin_in",
        native_unit_of_measurement=UnitOfTime.DAYS, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value=_days_left, attributes=_token_dates),
)


async def async_setup_entry(hass: HomeAssistant,
                            entry: HuaweiHealthConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    data: HuaweiHealthData = entry.runtime_data
    async_add_entities(HuaweiHealthSensor(description, data)
                       for description in DESCRIPTIONS)


class HuaweiHealthSensor(HuaweiHealthEntity, SensorEntity):
    entity_description: HuaweiSensorDescription

    # 运动类属性里的 segments 是逐分钟口径的原始计数，重算频繁且体积大，
    # 不需要进 recorder；实体的其余属性仍然照常记录。
    _unrecorded_attributes = frozenset({"segments"})

    def __init__(self, description: HuaweiSensorDescription,
                 data: HuaweiHealthData) -> None:
        super().__init__(data)
        self.entity_description = description
        self._attr_unique_id = f"{data.entry.entry_id}_{description.key}"
        # translation_key 来自 description，所以名称放在 translations/ 中

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self._data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attributes(self._data)
