"""Huawei Health 集成的基础 entity。"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HuaweiHealthCoordinator, HuaweiHealthData

# 设备页“在网页上打开”按钮的目标。集成读取的是 App 自有云的 API，
# 那里没有给人看的页面，所以指向华为运动健康官网。
CLOUD_URL = "https://consumer.huawei.com/en/mobileservices/health/"


class HuaweiHealthEntity(CoordinatorEntity[HuaweiHealthCoordinator]):
    """一个账号对应一个 service 设备；它的所有 sensor 都挂在该设备下。"""

    # 新式命名：entity_id 变为 <设备名>_<sensor 的 translation_key>。
    _attr_has_entity_name = True

    def __init__(self, data: HuaweiHealthData) -> None:
        super().__init__(data.coordinator)
        self._data = data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.entry.entry_id)},
            name=data.entry.title or "Huawei Health",
            manufacturer="Huawei",
            model="Health Cloud API",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CLOUD_URL,
        )

    @property
    def available(self) -> bool:
        return super().available and self._data.coordinator.data is not None
