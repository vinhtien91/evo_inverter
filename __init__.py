"""EVO Inverter (PowMr/Must-style Modbus RTU) integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BAUD_RATE,
    CONF_CONNECTION_TYPE,
    CONF_DATA_BITS,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ID,
    CONF_STOP_BITS,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_BAUD_RATE,
    DEFAULT_DATA_BITS,
    DEFAULT_PARITY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STOP_BITS,
    DOMAIN,
)
from .coordinator import InverterDataUpdateCoordinator, InverterSerialClient, InverterTcpClient

PLATFORMS = ["sensor", "select", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TCP)
    slave_id = entry.data[CONF_SLAVE_ID]

    if connection_type == CONNECTION_SERIAL:
        # Ưu tiên giá trị đã chỉnh trong Options, fallback về lúc setup ban đầu.
        cfg = {**entry.data, **entry.options}
        client = InverterSerialClient(
            cfg[CONF_SERIAL_PORT],
            slave_id,
            cfg.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE),
            cfg.get(CONF_DATA_BITS, DEFAULT_DATA_BITS),
            cfg.get(CONF_PARITY, DEFAULT_PARITY),
            cfg.get(CONF_STOP_BITS, DEFAULT_STOP_BITS),
        )
    else:
        client = InverterTcpClient(entry.data[CONF_HOST], entry.data[CONF_PORT], slave_id)

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    coordinator = InverterDataUpdateCoordinator(hass, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Đổi Options (scan_interval, baud_rate...) -> tự reload, không cần restart HA.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: InverterDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_close()
    return unload_ok
