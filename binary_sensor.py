"""Binary sensor platform for EVO Inverter - giải mã 2 thanh ghi bitmask:
- 4530 (alarm_flags_raw): 9 cờ cảnh báo (AlarmCode1) - device_class PROBLEM.
- 4535 (settings_flags_raw): 11 cờ trạng thái cài đặt (SystemState1) - đã
  xác nhận decode đúng với dữ liệu thực (raw=489 khớp cấu hình mặc định).
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ALARM_BITS, SETTINGS_BITS


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="EVO Inverter",
        manufacturer="PowMr/Must-style (EVO)",
        model="Modbus RTU hybrid inverter",
    )

    entities = [
        EvoInverterBitSensor(
            coordinator, entry, "alarm_flags_raw", bit, key, name, device_info, BinarySensorDeviceClass.PROBLEM
        )
        for bit, key, name in ALARM_BITS
    ] + [
        EvoInverterBitSensor(
            coordinator, entry, "settings_flags_raw", bit, key, name, device_info, None
        )
        for bit, key, name in SETTINGS_BITS
    ]
    async_add_entities(entities)


class EvoInverterBitSensor(CoordinatorEntity, BinarySensorEntity):
    """1 cờ nhị phân đọc từ 1 bit cụ thể trong 1 thanh ghi bitmask đã có
    sẵn trong coordinator.data (đọc chung với các sensor khác, không tốn
    thêm request Modbus)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry, source_key: str, bit: int, key: str,
                 name: str, device_info: DeviceInfo, device_class: BinarySensorDeviceClass | None) -> None:
        super().__init__(coordinator)
        self._source_key = source_key
        self._bit = bit
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = device_info
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self._source_key)
        if raw is None:
            return None
        return bool(raw & (1 << self._bit))
