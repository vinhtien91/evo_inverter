"""DataUpdateCoordinator cho EVO Inverter.

Kiến trúc:
- Một kết nối TCP/Serial persistent.
- asyncio.Lock để đảm bảo bus Modbus tuần tự.
- Tự reconnect khi mất kết nối.
- Retry khi timeout / connection error.
- Đọc nhiều register trong block thay vì từng register một.
- Giữ nguyên logic byte-swap của EVO.
- Tối ưu cho scan interval = 1 giây.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    PV_CURRENT_INPUT_ADDRESS,
    PV_CURRENT_INPUT_SLAVE_ID,
    SENSORS,
    SELECTS,
)
from .protocol import (
    ModbusError,
    ModbusExceptionResponse,
    build_read_holding,
    build_read_input,
    build_write_single,
    parse_read_response_registers,
    parse_write_response,
    read_response_frame,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Communication timing
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT = 5.0

# Số lần thử một request trước khi bỏ cuộc.
MAX_ATTEMPTS = 3

# Thời gian tối đa chờ inverter trả lời một request.
PER_ATTEMPT_TIMEOUT = 1.0

# Nghỉ giữa các lần retry.
RETRY_DELAY = 0.2


# ---------------------------------------------------------------------------
# Block read configuration
# ---------------------------------------------------------------------------

# Vùng register EVO cần đọc:
#
#   4501 -> 4560
#
# Tổng cộng 60 register.
#
# Chia thành:
#
#   4501 -> 4532 = 32 register
#   4533 -> 4560 = 28 register
#
# Cách này cũng bao phủ các register readback của SELECTS:
#
#   4536
#   4537
#   4539
#   4541
#   4543
#
# mà không cần request riêng.
MAX_BLOCK_REGISTERS = 32

REGISTER_START = 4501
REGISTER_END = 4560


class _BaseInverterClient:
    """Client dùng chung cho TCP và Serial."""

    _conn_desc: str = "inverter"

    def __init__(self, slave_id: int) -> None:
        self.slave_id = slave_id

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        # Modbus RTU là request/response tuần tự.
        # Không cho phép 2 request chạy cùng lúc.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _open_connection(self):
        """Lớp con override để mở TCP hoặc Serial."""
        raise NotImplementedError

    async def _ensure_connected(self) -> None:
        """Đảm bảo connection hiện tại còn hoạt động."""

        if (
            self._writer is not None
            and not self._writer.is_closing()
            and self._reader is not None
        ):
            return

        try:
            self._reader, self._writer = await asyncio.wait_for(
                self._open_connection(),
                timeout=CONNECT_TIMEOUT,
            )

            _LOGGER.debug(
                "Đã kết nối tới %s",
                self._conn_desc,
            )

        except (
            OSError,
            asyncio.TimeoutError,
        ) as err:
            self._reader = None
            self._writer = None

            raise UpdateFailed(
                f"Không kết nối được {self._conn_desc}: {err}"
            ) from err

    def _reset_connection(self) -> None:
        """Đóng connection hiện tại.

        Request tiếp theo sẽ tự động reconnect.
        """

        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass

        self._reader = None
        self._writer = None

    # ------------------------------------------------------------------
    # Generic request
    # ------------------------------------------------------------------

    async def _send_frame(
        self,
        request: bytes,
    ) -> bytes:
        """Gửi một Modbus request và chờ response.

        Lock nằm ở đây để:
        - Coordinator không đụng select.
        - Select không đụng coordinator.
        - Không có 2 frame Modbus chạy đồng thời.
        """

        async with self._lock:
            last_err: Exception | None = None

            for attempt in range(
                1,
                MAX_ATTEMPTS + 1,
            ):
                try:
                    await self._ensure_connected()

                    if (
                        self._writer is None
                        or self._reader is None
                    ):
                        raise UpdateFailed(
                            "Modbus connection chưa sẵn sàng"
                        )

                    # Xóa dữ liệu cũ còn sót trong buffer nếu có.
                    # Không đọc thủ công ở đây vì protocol.py chịu trách
                    # nhiệm xác định đúng frame response.
                    self._writer.write(request)

                    await self._writer.drain()

                    frame = await asyncio.wait_for(
                        read_response_frame(self._reader),
                        timeout=PER_ATTEMPT_TIMEOUT,
                    )

                    return frame

                except (
                    OSError,
                    asyncio.TimeoutError,
                    asyncio.IncompleteReadError,
                    UpdateFailed,
                ) as err:

                    last_err = err

                    _LOGGER.debug(
                        "Khung %s lần %d/%d lỗi: %s",
                        request.hex(" "),
                        attempt,
                        MAX_ATTEMPTS,
                        err,
                    )

                    self._reset_connection()

                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(RETRY_DELAY)

            raise UpdateFailed(
                "Không nhận được phản hồi hợp lệ cho "
                f"khung {request.hex(' ')} sau "
                f"{MAX_ATTEMPTS} lần thử: {last_err}"
            ) from last_err

    # ------------------------------------------------------------------
    # Read one register
    # ------------------------------------------------------------------

    async def read_register(
        self,
        address: int,
        swap: bool = True,
    ) -> int:
        """Đọc một holding register."""

        request = build_read_holding(
            self.slave_id,
            address,
            count=1,
        )

        try:
            frame = await self._send_frame(request)

            values = parse_read_response_registers(
                frame=frame,
                expected_slave=self.slave_id,
                expected_address=address,
                expected_count=1,
                swap=swap,
            )

            return values[0]

        except (
            ModbusError,
            ModbusExceptionResponse,
        ) as err:

            self._reset_connection()

            raise UpdateFailed(
                f"Lỗi đọc thanh ghi {address}: {err}"
            ) from err

    async def read_input_register(
        self,
        address: int,
        slave_id: int,
        swap: bool = False,
    ) -> int:
        """Đọc một Input Register bằng Function 0x04."""

        request = build_read_input(slave_id, address, count=1)

        try:
            frame = await self._send_frame(request)

            values = parse_read_response_registers(
                frame=frame,
                expected_slave=slave_id,
                expected_address=address,
                expected_count=1,
                swap=swap,
                expected_function=0x04,
            )

            return values[0]

        except (ModbusError, ModbusExceptionResponse) as err:
            self._reset_connection()
            raise UpdateFailed(
                f"Lỗi đọc FC04 slave {slave_id}, thanh ghi {address}: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Read block
    # ------------------------------------------------------------------

    async def read_register_block(
        self,
        address: int,
        count: int,
        swap: bool = True,
    ) -> list[int]:
        """Đọc nhiều holding register bằng một request.

        Ví dụ:

            read_register_block(4501, 32)

        sẽ đọc:

            4501 ... 4532

        trong một frame Modbus.
        """

        if count < 1:
            raise ValueError(
                "count phải lớn hơn hoặc bằng 1"
            )

        if count > MAX_BLOCK_REGISTERS:
            raise ValueError(
                f"count={count} vượt MAX_BLOCK_REGISTERS="
                f"{MAX_BLOCK_REGISTERS}"
            )

        request = build_read_holding(
            self.slave_id,
            address,
            count=count,
        )

        try:
            frame = await self._send_frame(request)

            return parse_read_response_registers(
                frame=frame,
                expected_slave=self.slave_id,
                expected_address=address,
                expected_count=count,
                swap=swap,
            )

        except (
            ModbusError,
            ModbusExceptionResponse,
        ) as err:

            self._reset_connection()

            raise UpdateFailed(
                f"Lỗi đọc block "
                f"{address}-{address + count - 1}: "
                f"{err}"
            ) from err

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write_register(
        self,
        address: int,
        value: int,
    ) -> bool:
        """Ghi một holding register bằng Function 0x06."""

        request = build_write_single(
            self.slave_id,
            address,
            value,
        )

        try:
            frame = await self._send_frame(request)

            return parse_write_response(
                frame,
                self.slave_id,
                address,
                expected_value=value,
            )

        except (
            ModbusError,
            ModbusExceptionResponse,
        ) as err:

            self._reset_connection()

            _LOGGER.warning(
                "Không ghi được thanh ghi %s = %s: %s",
                address,
                value,
                err,
            )

            return False

    # ------------------------------------------------------------------
    # Build blocks
    # ------------------------------------------------------------------

    @staticmethod
    def _build_sensor_blocks() -> list[tuple[int, int]]:
        """Tạo block đọc toàn bộ vùng register EVO cần thiết.

        Vùng:

            4501 -> 4560

        Giới hạn:

            MAX_BLOCK_REGISTERS = 32

        Kết quả:

            4501 -> 4532
            4533 -> 4560

        Đọc cả các register nằm giữa những sensor là có chủ ý.
        Sau khi nhận dữ liệu chỉ map các register thực sự cần.
        """

        blocks: list[tuple[int, int]] = []

        current = REGISTER_START

        while current <= REGISTER_END:
            count = min(
                MAX_BLOCK_REGISTERS,
                REGISTER_END - current + 1,
            )

            blocks.append(
                (
                    current,
                    count,
                )
            )

            current += count

        return blocks

    # ------------------------------------------------------------------
    # Fetch all sensors
    # ------------------------------------------------------------------

    async def fetch_all(self) -> dict[str, int]:
        """Đọc toàn bộ dữ liệu cần cho Home Assistant.

        Toàn bộ vùng 4501 -> 4560 được đọc bằng 2 request:

            Request 1:
                4501 -> 4532

            Request 2:
                4533 -> 4560

        Sau đó dữ liệu được map vào:
            - SENSORS
            - SELECTS

        Như vậy không còn request riêng cho các SELECT readback.
        """

        data: dict[str, int] = {}

        # --------------------------------------------------------------
        # 1. Đọc toàn bộ vùng register theo block
        # --------------------------------------------------------------

        blocks = self._build_sensor_blocks()

        block_values: dict[int, int] = {}

        for start_address, count in blocks:
            values = await self.read_register_block(
                address=start_address,
                count=count,
                swap=True,
            )

            if len(values) != count:
                raise UpdateFailed(
                    f"Inverter trả về {len(values)} register "
                    f"nhưng yêu cầu {count} register "
                    f"từ địa chỉ {start_address}"
                )

            for index, value in enumerate(values):
                block_values[
                    start_address + index
                ] = value

        # --------------------------------------------------------------
        # 2. Map register -> sensor key
        # --------------------------------------------------------------

        for reg in SENSORS:
            value = block_values.get(reg.address)

            if value is not None:
                data[reg.key] = value

        # 0912.json: Slave ID 4, FC04, address 3052, scale x0.1 A.
        # Đây là map khác với khối FC03/Slave ID 5 của 0925.json. Nếu thiết
        # bị không hỗ trợ, chỉ sensor DÒNG PV unavailable; dữ liệu cũ vẫn chạy.
        try:
            data["pv_current"] = await self.read_input_register(
                address=PV_CURRENT_INPUT_ADDRESS,
                slave_id=PV_CURRENT_INPUT_SLAVE_ID,
                swap=False,
            )
        except UpdateFailed as err:
            _LOGGER.debug("Không đọc được DÒNG PV FC04/3052: %s", err)

        # --------------------------------------------------------------
        # 3. Map SELECT readback
        # --------------------------------------------------------------

        for (
            _write_address,
            read_address,
            key,
            _name,
            _optionsmap,
        ) in SELECTS:

            if read_address is None:
                continue

            value = block_values.get(read_address)

            if value is not None:
                data[key] = value
            else:
                # Fallback an toàn nếu sau này SELECT được chuyển
                # sang một vùng register ngoài 4501..4560.
                data[key] = await self.read_register(
                    read_address,
                    swap=True,
                )

        return data

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def async_close(self) -> None:
        """Đóng connection khi integration unload."""

        async with self._lock:
            self._reset_connection()


# =========================================================================
# TCP CLIENT
# =========================================================================


class InverterTcpClient(_BaseInverterClient):
    """TCP client.

    TCP chỉ là phương tiện truyền.
    Payload bên trong vẫn là Modbus RTU frame có CRC.
    """

    def __init__(
        self,
        host: str,
        port: int,
        slave_id: int,
    ) -> None:
        super().__init__(slave_id)

        self.host = host
        self.port = port

        self._conn_desc = (
            f"ESP32 bridge {host}:{port}"
        )

    async def _open_connection(self):
        """Mở TCP connection tới ESP32 bridge."""

        return await asyncio.open_connection(
            self.host,
            self.port,
        )


# =========================================================================
# SERIAL CLIENT
# =========================================================================


class InverterSerialClient(_BaseInverterClient):
    """Serial client.

    Dùng trực tiếp USB-RS485/RS232 trên Home Assistant.
    """

    def __init__(
        self,
        serial_port: str,
        slave_id: int,
        baud_rate: int = 2400,
        data_bits: int = 8,
        parity: str = "N",
        stop_bits: float = 1,
    ) -> None:
        super().__init__(slave_id)

        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.data_bits = data_bits
        self.parity = parity
        self.stop_bits = stop_bits

        self._conn_desc = (
            f"cổng serial {serial_port}@{baud_rate} "
            f"{data_bits}{parity}{stop_bits}"
        )

    async def _open_connection(self):
        """Mở serial connection."""

        import serial_asyncio

        return await serial_asyncio.open_serial_connection(
            url=self.serial_port,
            baudrate=self.baud_rate,
            bytesize=self.data_bits,
            parity=self.parity,
            stopbits=self.stop_bits,
        )


# =========================================================================
# DATA UPDATE COORDINATOR
# =========================================================================


class InverterDataUpdateCoordinator(
    DataUpdateCoordinator[dict[str, int]]
):
    """Home Assistant DataUpdateCoordinator cho EVO Inverter.

    scan_interval = 1:
        yêu cầu cập nhật dữ liệu mỗi 1 giây.

    Việc thực tế hoàn thành nhanh hay chậm phụ thuộc:
        - baud rate
        - thời gian inverter trả lời
        - TCP bridge
        - Modbus bus
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: _BaseInverterClient,
        scan_interval: int,
    ) -> None:
        self.client = client

        super().__init__(
            hass,
            _LOGGER,
            name="EVO Inverter",
            update_interval=timedelta(
                seconds=scan_interval
            ),
        )

    async def _async_update_data(
        self,
    ) -> dict[str, int]:
        """Cập nhật toàn bộ dữ liệu inverter."""

        return await self.client.fetch_all()
