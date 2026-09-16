"""Sensor platform for EVO Inverter."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SENSORS, RegisterDef


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="EVO Inverter",
        manufacturer="PowMr/Must-style (EVO)",
        model="Modbus RTU hybrid inverter",
    )

    async_add_entities(
        EvoInverterSensor(
            coordinator,
            entry,
            reg,
            device_info,
        )
        for reg in SENSORS
    )


class EvoInverterSensor(CoordinatorEntity, SensorEntity):
    """Một sensor tương ứng với một thanh ghi EVO."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        reg: RegisterDef,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)

        self._reg = reg

        self._attr_unique_id = f"{entry.entry_id}_{reg.key}"
        self._attr_name = reg.name
        self._attr_device_info = device_info

        if reg.enum_map is not None:
            # Sensor dạng ENUM/text.
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(reg.enum_map.values())

        else:
            self._attr_native_unit_of_measurement = reg.unit

            if reg.device_class is not None:
                self._attr_device_class = reg.device_class

            self._attr_suggested_display_precision = reg.decimals

            # Chỉ các sensor có đơn vị đo mới dùng MEASUREMENT.
            if reg.unit is not None:
                self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | int | str | None:
        """Trả giá trị đã giải mã từ coordinator."""

        raw = self.coordinator.data.get(self._reg.key)

        if raw is None:
            return None

        # Thanh ghi ENUM.
        if self._reg.enum_map is not None:
            return self._reg.enum_map.get(
                raw,
                f"Unknown ({raw})",
            )

        # Thanh ghi số.
        value = raw * self._reg.scale

        if self._reg.decimals:
            return round(value, self._reg.decimals)

        return int(value)