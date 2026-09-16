"""Select platform for EVO Inverter (writable config registers).

Reads the current value back from the inverter every poll cycle (not just
optimistic-after-write), so state survives a Home Assistant restart and
reflects changes made on the inverter's own front panel too.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SELECTS, SELECT_BIT_READBACK

_LOGGER = logging.getLogger(__name__)


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

    async_add_entities(
        EvoInverterSelect(coordinator, entry, write_address, read_address, key, name, optionsmap, device_info)
        for write_address, read_address, key, name, optionsmap in SELECTS
    )


class EvoInverterSelect(CoordinatorEntity, SelectEntity):
    """Đọc giá trị thật mỗi chu kỳ poll (ở read_address, có thể khác
    write_address - xem const.py); ghi thì gửi lệnh tới write_address rồi
    cập nhật cache ngay, không đợi vòng poll kế tiếp."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, write_address: int, read_address: int, key: str,
                 name: str, optionsmap: dict[str, int], device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._write_address = write_address
        self._read_address = read_address
        self._key = key
        self._optionsmap = optionsmap
        self._value_to_option = {v: k for k, v in optionsmap.items()}
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_options = list(optionsmap.keys())
        self._attr_device_info = device_info

    @property
    def current_option(self) -> str | None:
        bit_source = SELECT_BIT_READBACK.get(self._key)
        if bit_source is not None:
            source_key, bit = bit_source
            bitmask = self.coordinator.data.get(source_key)
            if bitmask is None:
                return None
            raw = (bitmask >> bit) & 1
        else:
            raw = self.coordinator.data.get(self._key)
            if raw is None:
                return None
        option = self._value_to_option.get(raw)
        if option is None:
            _LOGGER.debug(
                "Thanh ghi %s (%s) trả về giá trị %s không khớp optionsmap nào",
                self._read_address, self._key, raw,
            )
        return option

    async def async_select_option(self, option: str) -> None:
        value = self._optionsmap[option]
        ok = await self.coordinator.client.write_register(self._write_address, value)
        if not ok:
            _LOGGER.warning(
                "Inverter không xác nhận ghi %s = %s (thanh ghi %s)", option, value, self._write_address
            )
            return
        # Cập nhật ngay trong cache để UI phản hồi tức thời. Với select đọc
        # qua bit (settings_flags_raw), cập nhật đúng bit đó trong bitmask
        # cache thay vì ghi đè cả thanh ghi.
        bit_source = SELECT_BIT_READBACK.get(self._key)
        if bit_source is not None:
            source_key, bit = bit_source
            bitmask = self.coordinator.data.get(source_key, 0)
            if value:
                bitmask |= (1 << bit)
            else:
                bitmask &= ~(1 << bit)
            self.coordinator.data[source_key] = bitmask
        else:
            self.coordinator.data[self._key] = value
        self.async_write_ha_state()
