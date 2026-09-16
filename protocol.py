"""Modbus RTU protocol helpers for EVO Inverter."""

from __future__ import annotations


class ModbusError(Exception):
    """Lỗi giao thức Modbus."""


class ModbusExceptionResponse(ModbusError):
    """Inverter trả về Modbus exception."""


# ---------------------------------------------------------------------------
# CRC-16/MODBUS
# ---------------------------------------------------------------------------


def crc16_modbus(data: bytes) -> int:
    """Tính CRC-16/MODBUS."""

    crc = 0xFFFF

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1

    return crc & 0xFFFF


def append_crc(data: bytes) -> bytes:
    """Thêm CRC Modbus vào cuối frame."""

    crc = crc16_modbus(data)

    return data + bytes(
        (
            crc & 0xFF,
            (crc >> 8) & 0xFF,
        )
    )


def _check_crc(frame: bytes) -> None:
    """Kiểm tra CRC của frame."""

    if len(frame) < 4:
        raise ModbusError(
            f"Frame quá ngắn: {frame.hex(' ')}"
        )

    data = frame[:-2]

    received_crc = (
        frame[-2]
        | (frame[-1] << 8)
    )

    calculated_crc = crc16_modbus(data)

    if received_crc != calculated_crc:
        raise ModbusError(
            "CRC không đúng: "
            f"received=0x{received_crc:04X}, "
            f"calculated=0x{calculated_crc:04X}, "
            f"frame={frame.hex(' ')}"
        )


# ---------------------------------------------------------------------------
# Byte swap
# ---------------------------------------------------------------------------


def _swap_u16(value: int) -> int:
    """Đảo 2 byte của một unsigned 16-bit register.

    Ví dụ:

        0x3412 -> 0x1234
    """

    return (
        ((value & 0x00FF) << 8)
        | ((value & 0xFF00) >> 8)
    )


# ---------------------------------------------------------------------------
# Build request
# ---------------------------------------------------------------------------


def build_read_holding(
    slave_id: int,
    address: int,
    count: int = 1,
) -> bytes:
    """Tạo Modbus Function 03 Read Holding Registers."""

    if not 0 <= slave_id <= 0xFF:
        raise ValueError(
            f"slave_id không hợp lệ: {slave_id}"
        )

    if not 0 <= address <= 0xFFFF:
        raise ValueError(
            f"address không hợp lệ: {address}"
        )

    if not 1 <= count <= 125:
        raise ValueError(
            f"count phải nằm trong 1..125: {count}"
        )

    frame = bytes(
        (
            slave_id,
            0x03,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        )
    )

    return append_crc(frame)


def build_read_input(
    slave_id: int,
    address: int,
    count: int = 1,
) -> bytes:
    """Tạo Modbus Function 04 Read Input Registers."""

    if not 0 <= slave_id <= 0xFF:
        raise ValueError(f"slave_id không hợp lệ: {slave_id}")

    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"address không hợp lệ: {address}")

    if not 1 <= count <= 125:
        raise ValueError(f"count phải nằm trong 1..125: {count}")

    frame = bytes(
        (
            slave_id,
            0x04,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        )
    )

    return append_crc(frame)


def build_write_single(
    slave_id: int,
    address: int,
    value: int,
) -> bytes:
    """Tạo Modbus Function 06 Write Single Register."""

    if not 0 <= slave_id <= 0xFF:
        raise ValueError(
            f"slave_id không hợp lệ: {slave_id}"
        )

    if not 0 <= address <= 0xFFFF:
        raise ValueError(
            f"address không hợp lệ: {address}"
        )

    if not 0 <= value <= 0xFFFF:
        raise ValueError(
            f"value không hợp lệ: {value}"
        )

    frame = bytes(
        (
            slave_id,
            0x06,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        )
    )

    return append_crc(frame)


# ---------------------------------------------------------------------------
# Read response frame
# ---------------------------------------------------------------------------


async def read_response_frame(
    reader,
) -> bytes:
    """Đọc một Modbus response từ StreamReader.

    Hỗ trợ:
        Function 03
        Function 04
        Function 06
        Exception response
    """

    # Header:
    #   slave
    #   function
    #   byte count / data high
    #
    # Đọc trước 2 byte.
    header = await reader.readexactly(2)

    if len(header) != 2:
        raise ModbusError(
            "Không đọc đủ Modbus header"
        )

    function = header[1]

    # --------------------------------------------------------------
    # Modbus exception
    # --------------------------------------------------------------

    if function & 0x80:
        rest = await reader.readexactly(3)

        frame = header + rest

        _check_crc(frame)

        exception_code = frame[2]

        raise ModbusExceptionResponse(
            f"Modbus exception "
            f"function=0x{function:02X}, "
            f"code=0x{exception_code:02X}"
        )

    # --------------------------------------------------------------
    # Function 03/04 - Read Holding/Input Registers
    # --------------------------------------------------------------

    if function in (0x03, 0x04):
        byte_count_raw = await reader.readexactly(1)

        byte_count = byte_count_raw[0]

        if byte_count == 0:
            raise ModbusError(
                f"Modbus Function {function:02X} trả byte_count = 0"
            )

        if byte_count % 2 != 0:
            raise ModbusError(
                f"Byte count Function {function:02X} phải là số chẵn: "
                f"{byte_count}"
            )

        data_and_crc = await reader.readexactly(
            byte_count + 2
        )

        frame = (
            header
            + byte_count_raw
            + data_and_crc
        )

        _check_crc(frame)

        return frame

    # --------------------------------------------------------------
    # Function 06 - Write Single Register
    # --------------------------------------------------------------

    if function == 0x06:
        rest = await reader.readexactly(6)

        frame = header + rest

        _check_crc(frame)

        return frame

    # --------------------------------------------------------------
    # Function không hỗ trợ
    # --------------------------------------------------------------

    raise ModbusError(
        f"Function Modbus không hỗ trợ: "
        f"0x{function:02X}"
    )


# ---------------------------------------------------------------------------
# Parse Function 03
# ---------------------------------------------------------------------------


def parse_read_response_registers(
    frame: bytes,
    expected_slave: int,
    expected_address: int,
    expected_count: int,
    swap: bool = True,
    expected_function: int = 0x03,
) -> list[int]:
    """Parse Modbus Function 03 hoặc 04 response.

    Trả về danh sách register theo thứ tự:

        [register_0, register_1, ...]

    Nếu swap=True:
        mỗi register sẽ được đảo byte theo giao thức EVO.
    """

    if expected_count < 1:
        raise ValueError(
            "expected_count phải >= 1"
        )

    if len(frame) < 5:
        raise ModbusError(
            "Frame response quá ngắn"
        )

    _check_crc(frame)

    slave = frame[0]
    function = frame[1]

    if slave != expected_slave:
        raise ModbusError(
            "Sai Slave ID: "
            f"expected={expected_slave}, "
            f"received={slave}"
        )

    exception_function = expected_function | 0x80

    if function == exception_function:
        exception_code = frame[2]

        raise ModbusExceptionResponse(
            f"Modbus exception code="
            f"0x{exception_code:02X}"
        )

    if function != expected_function:
        raise ModbusError(
            "Sai function: "
            f"expected=0x{expected_function:02X}, "
            f"received=0x{function:02X}"
        )

    byte_count = frame[2]

    expected_byte_count = expected_count * 2

    if byte_count != expected_byte_count:
        raise ModbusError(
            "Sai byte count: "
            f"expected={expected_byte_count}, "
            f"received={byte_count}"
        )

    expected_length = (
        3
        + byte_count
        + 2
    )

    if len(frame) != expected_length:
        raise ModbusError(
            "Độ dài frame không đúng: "
            f"expected={expected_length}, "
            f"received={len(frame)}"
        )

    values: list[int] = []

    data_start = 3

    for index in range(expected_count):
        offset = data_start + index * 2

        raw = (
            (frame[offset] << 8)
            | frame[offset + 1]
        )

        if swap:
            raw = _swap_u16(raw)

        values.append(raw)

    return values


def parse_read_response(
    frame: bytes,
    expected_slave: int,
    expected_address: int,
    swap: bool = True,
) -> int:
    """Parse response đọc một register.

    Giữ hàm này để tương thích với code cũ.
    """

    values = parse_read_response_registers(
        frame=frame,
        expected_slave=expected_slave,
        expected_address=expected_address,
        expected_count=1,
        swap=swap,
    )

    return values[0]


# ---------------------------------------------------------------------------
# Parse Function 06
# ---------------------------------------------------------------------------


def parse_write_response(
    frame: bytes,
    expected_slave: int,
    expected_address: int,
    expected_value: int | None = None,
) -> bool:
    """Parse Modbus Function 06 response."""

    if len(frame) != 8:
        raise ModbusError(
            "Function 06 phải có 8 byte: "
            f"received={len(frame)}"
        )

    _check_crc(frame)

    slave = frame[0]
    function = frame[1]

    if slave != expected_slave:
        raise ModbusError(
            "Sai Slave ID khi ghi: "
            f"expected={expected_slave}, "
            f"received={slave}"
        )

    if function == 0x86:
        exception_code = frame[2]

        raise ModbusExceptionResponse(
            f"Modbus write exception "
            f"code=0x{exception_code:02X}"
        )

    if function != 0x06:
        raise ModbusError(
            "Sai function khi ghi: "
            f"expected=0x06, "
            f"received=0x{function:02X}"
        )

    address = (
        (frame[2] << 8)
        | frame[3]
    )

    value = (
        (frame[4] << 8)
        | frame[5]
    )

    if address != expected_address:
        raise ModbusError(
            "Sai địa chỉ phản hồi ghi: "
            f"expected={expected_address}, "
            f"received={address}"
        )

    if (
        expected_value is not None
        and value != expected_value
    ):
        raise ModbusError(
            "Sai giá trị phản hồi ghi: "
            f"expected={expected_value}, "
            f"received={value}"
        )

    return True
