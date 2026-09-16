"""Constants and register map for the EVO Inverter integration.

Connection architecture (persistent asyncio TCP/Serial client, lock,
retry-with-reconnect) is ported from `hs_ms_msx_inverter` (sumry).

Register map is cross-checked against THREE sources, in order of trust:
  1. The official vendor Android/iOS app's config JSON (user-supplied,
     "0925.json") - address, scale, digits, units, enum text, all
     authoritative for this exact model/firmware (devAddrs: "5",
     funNumber 3 = read holding, matches everything else confirmed).
  2. Values already empirically confirmed against the user's real
     device (4555 charger_status, 4557 temperature_sensor - do NOT
     change these even where 0925.json's address layout suggests they
     might overlap with "Today/Month energy" 32-bit counters, since
     live data on THIS unit already validated they're right).
  3. https://github.com/odya/esphome-powmr-hybrid-inverter (community
     doc, used only for anything not covered by 1 or 2).

Corrections made against 0925.json vs. the earlier (odya-doc-based) map:
  - 4512/4513 swapped: 4512 is apparent power (VA), 4513 is active power
    (W) - the opposite of what evo.yaml/odya implied. Verify after
    flashing: VA should read >= W (physics), if not, tell me and it's
    reverted.
  - 4540 is NOT a scaled frequency value - it's an enum (0=50Hz,1=60Hz).
  - 4530 is not a plain "error code" - it's a 9-bit alarm bitmask
    (Fan Locked, Over Temp, Battery Over Charged, ...). Real error code
    is 4529. Exposed as binary_sensor entities, see ALARM_BITS below.
"""

from __future__ import annotations

from typing import NamedTuple

DOMAIN = "evo_inverter"

# --- Config keys --------------------------------------------------------
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL_PORT = "serial_port"
CONF_BAUD_RATE = "baud_rate"
CONF_DATA_BITS = "data_bits"
CONF_PARITY = "parity"
CONF_STOP_BITS = "stop_bits"
CONF_SLAVE_ID = "slave_id"

CONNECTION_TCP = "tcp"
CONNECTION_SERIAL = "serial"

# --- Defaults -------------------------------------------------------------
DEFAULT_PORT = 5000
DEFAULT_SCAN_INTERVAL = 1
DEFAULT_BAUD_RATE = 2400
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_DATA_BITS = 8
DEFAULT_PARITY = "N"
DEFAULT_STOP_BITS = 1
DEFAULT_SLAVE_ID = 0x05  # matches devAddrs: ["5"] in 0925.json

# 0912.json uses a different device profile: devAddrs ["4"] and FC04.
# This ID is used only for the experimental DC PV1 current read below;
# all existing FC03 sensors keep using the slave ID selected in config flow.
PV_CURRENT_INPUT_SLAVE_ID = 0x04
PV_CURRENT_INPUT_ADDRESS = 3052

STANDARD_BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

PARITY_OPTIONS = {"N": "None (không kiểm tra)", "E": "Even (chẵn)", "O": "Odd (lẻ)"}
DATA_BITS_OPTIONS = [7, 8]
STOP_BITS_OPTIONS = {"1": "1", "2": "2"}


class RegisterDef(NamedTuple):
    address: int
    key: str
    name: str
    unit: str | None
    device_class: str | None
    scale: float
    decimals: int
    enum_map: dict[int, str] | None = None  # nếu có: sensor trả về text thay vì số


# --- Sensors (function 0x03, byte-swapped trên decode - khớp lambda evo.yaml) ---
SENSORS: list[RegisterDef] = [
    # 0912.json: FC04 Input Register 3052, raw x 0.1 A.
    # coordinator.py reads this entry separately with Slave ID 4.
    RegisterDef(3052, "pv_current", "DÒNG PV", "A", "current", 0.1, 1),
    RegisterDef(32107, "grid_active_power", "CÔNG SUẤT AC IN", "W", "power", 1, 0),
    RegisterDef(4501, "working_state", "TRẠNG THÁI LÀM VIỆC", None, None, 1, 0, {
        0: "MỞ NGUỒN", 1: "Test", 2: "CHẾ ĐỘ CHỜ", 3: "CHẠY TỪ PIN",
        4: "CHẠY ĐIỆN LƯỚI", 5: "BỎ QUA DC DÙNG LƯỚI", 6: "CHẾ ĐỘ BẢO VỆ", 7: "TẮT NGUỒN",
    }),
    RegisterDef(4502, "ac_voltage", "ĐIỆN ÁP AC VÀO", "V", "voltage", 0.1, 1),
    RegisterDef(4503, "ac_frequency", "TẦN SỐ AC VÀO", "Hz", "frequency", 0.1, 1),
    RegisterDef(4504, "pv_voltage", "ĐIỆN ÁP PV", "V", "voltage", 0.1, 1),
    RegisterDef(4505, "pv_power", "CÔNG SUẤT PV", "W", "power", 1, 0),
    RegisterDef(4506, "battery_voltage", "ĐIỆN ÁP PIN", "V", "voltage", 0.1, 1),
    RegisterDef(4507, "battery_soc", "PHẦN TRĂM PIN", "%", "battery", 1, 0),
    RegisterDef(4508, "battery_charge_current", "DÒNG SẠC PIN", "A", "current", 1, 0),
    RegisterDef(4509, "battery_discharge_current", "DÒNG XẢ PIN", "A", "current", 1, 0),
    RegisterDef(4510, "load_voltage", "ĐIỆN ÁP TẢI", "V", "voltage", 0.1, 1),
    RegisterDef(4511, "load_frequency", "TẦN SỐ ĐIỆN AC RA", "Hz", "frequency", 0.1, 1),
    # ĐÃ SỬA: 4512/4513 đảo ngược lại theo 0925.json (VA rồi mới đến W)
    RegisterDef(4512, "load_va", "Công suất biểu kiến của tải (VA)", "VA", "apparent_power", 1, 0),
    RegisterDef(4513, "load_power", "CÔNG SUẤT TẢI", "W", "power", 1, 0),
    RegisterDef(4514, "load_percent", "PHẦN TRĂM TẢI", "%", None, 1, 0),
    RegisterDef(4517, "machine_type", "LOẠI MÁY", None, None, 1, 0, {
        0: "Solar Inverter", 1: "Voltage regulator",
    }),
    RegisterDef(4518, "main_cpu_version", "PHIÊN BẢN CPU CHÍNH", None, None, 1, 0),
    RegisterDef(4519, "secondary_cpu_version", "PHIÊN BẢN CPU PHỤ", None, None, 1, 0),
    RegisterDef(4520, "battery_piece", "HỆ PIN", None, None, 1, 0, {
        0: "no battery", 1: "24V (3KW)", 2: "48V (5KW)",
    }),
    RegisterDef(4521, "nominal_output_va", "CÔNG SUẤT TỐI ĐA", "VA", "apparent_power", 1, 0),
    RegisterDef(4522, "nominal_output_w", "CÔNG SUẤT HOẠT ĐỘNG ỔN ĐINH", "W", "power", 1, 0),
    RegisterDef(4523, "nominal_ac_voltage", "ĐIỆN ÁP CHUẨN AC", "V", "voltage", 1, 0),
    RegisterDef(4524, "nominal_ac_current", "DÒNG TIÊU CHUẨN ĐỊNH DANH", "A", "current", 1, 0),
    RegisterDef(4525, "rated_battery_voltage", "ĐIỆN ÁP ĐỊNH MỨC CỦA PIN", "V", "voltage", 0.1, 1),
    RegisterDef(4526, "nominal_output_voltage", "ĐIỆN ÁP AC RA ĐỊNH MỨC", "V", "voltage", 1, 0),
    RegisterDef(4527, "nominal_output_frequency", "TẦN SỐ RA ĐỊNH MỨC", "Hz", "frequency", 0.1, 1),
    RegisterDef(4528, "nominal_output_current", "DÒNG RA ĐỊNH MỨC", "A", "current", 1, 0),
    RegisterDef(4529, "error_code", "MÃ LỖI", None, None, 1, 0),
    # 4530 KHÔNG còn là sensor số đơn - là bitmask 9 cờ alarm, xem ALARM_BITS
    # + binary_sensor.py. Vẫn giữ giá trị raw ở đây cho mục đích debug.
    RegisterDef(4530, "alarm_flags_raw", "BÁO ĐỘNG DỮ LIỆU THÔ", None, None, 1, 0),
    RegisterDef(4531, "device_id_1", "Device ID 1", None, None, 1, 0),
    RegisterDef(4532, "device_id_2", "Device ID 2", None, None, 1, 0),
    RegisterDef(4533, "device_id_3", "Device ID 3", None, None, 1, 0),
    RegisterDef(4534, "device_id_4", "Device ID 4", None, None, 1, 0),
    # 4535 cũng là bitmask 11 cờ setting-state (buzzer/backlight/...), phần lớn
    # đã có select riêng (buzzer_alarm, beep_on_primary_source_fail,
    # overload_bypass); giữ raw để debug các bit còn lại chưa có select.
    RegisterDef(4535, "settings_flags_raw", "Settings Flags (raw)", None, None, 1, 0),
    # 4536/4537/4541/4543 KHÔNG khai báo riêng ở đây - đã là read_address
    # của 4 select bên dưới (SELECTS), tránh đọc trùng 2 lần/chu kỳ với 2
    # giả định swap khác nhau. Trạng thái hiện tại của chúng hiển thị ngay
    # trên chính entity select (current_option), không cần sensor riêng.
    RegisterDef(4538, "ac_input_range", "DẢI ĐIỆN ÁP ĐẦU VÀO AC", None, None, 1, 0, {
        0: "Appliances (default)", 1: "UPS",
    }),
    # battery_type CHUYỂN xuống SELECTS (có thanh ghi ghi 5020) - không còn
    # là sensor read-only ở đây nữa.
    # ĐÃ SỬA: 4540 KHÔNG phải Hz*0.1 liên tục - là enum 0/1 (bug kiểu "temperature")
    RegisterDef(4540, "output_frequency_setting", "Output Frequency Setting", None, None, 1, 0, {
        0: "50Hz (default)", 1: "60Hz",
    }),
    RegisterDef(4542, "target_output_voltage", "ĐIỆN ÁP HIỆU CHUẨN ĐẦU RA", "V", "voltage", 1, 0),
    RegisterDef(4544, "back_to_utility_voltage", "ĐIỆN ÁP CHUYỂN ĐIỆN LƯỚI", "V", "voltage", 0.1, 1),
    RegisterDef(4545, "back_to_battery_voltage", "ĐIỆN ÁP CHUYỂN VỀ DÙNG PIN", "V", "voltage", 0.1, 1),
    RegisterDef(4546, "bulk_charging_voltage", "ĐIỆN ÁP SẠC NỔI", "V", "voltage", 0.1, 1),
    RegisterDef(4547, "floating_charging_voltage", "MỨC ĐIỆN ÁP SẠC DUY TRÌ", "V", "voltage", 0.1, 1),
    RegisterDef(4548, "low_cutoff_voltage", "ĐIỆN ÁP NGẮT KHI PIN YẾU", "V", "voltage", 0.1, 1),
    RegisterDef(4549, "battery_equalization_voltage", "ĐIỆN ÁP CÂN BẰNG", "V", "voltage", 0.1, 1),
    RegisterDef(4550, "battery_equalized_time", "THỜI GIAN CÂN BẰNG PIN", "min", None, 1, 0),
    RegisterDef(4551, "battery_equalized_timeout", "THỜI GIAN CHỜ CÂN BẰNG PIN", "min", None, 1, 0),
    RegisterDef(4552, "equalization_interval", "KHOẢNG THỜI GIAN CÂN BẰNG", "d", None, 1, 0),
    # 4553 "APP show" - không rõ ý nghĩa bit rõ ràng (dữ liệu thực 1137 không
    # khớp gọn với bảng bit cộng đồng), để raw tham khảo.
    RegisterDef(4553, "diag_app_show_4553", "Diag APP Show 4553 (raw)", None, None, 1, 0),
    # Đã xác nhận bằng dữ liệu thực (raw=7 -> 0.07 kWh, hợp lý) - theo 0925.json.
    RegisterDef(4554, "today_energy", "NĂNG LƯỢNG HÔM NAYy", "kWh", "energy", 0.01, 2),
    RegisterDef(4556, "month_energy", "NĂNG LƯỢNG THÁNG NÀY", "kWh", "energy", 0.01, 2),
    RegisterDef(4558, "year_energy", "NĂNG LƯỢNG NĂM NÀY", "kWh", "energy", 0.01, 2),
    RegisterDef(4560, "all_energy", "TỔNG NĂNG LƯỢNG", "kWh", "energy", 0.01, 2),
    # KHÔNG đổi 2 thanh ghi dưới đây - đã xác nhận đúng trên thiết bị thật,
    # dù 0925.json gợi ý khu vực này có thể là bộ đếm năng lượng 32-bit.
    RegisterDef(4555, "charger_status", "TRẠNG THÁI SẠC", None, None, 1, 0),
    RegisterDef(4557, "temperature_sensor", "NHIỆT ĐỘ", "°C", "temperature", 1, 0),
]

# 9 cờ alarm trong thanh ghi 4530 (bit 0 = LSB). Theo 0925.json/AlarmCode1.
ALARM_BITS: list[tuple[int, str, str]] = [
    (0, "alarm_fan_locked", "QUẠT BỊ KHOÁ"),
    (1, "alarm_over_temperature", "QUÁ NHIỆT"),
    (2, "alarm_battery_over_charged", "PIN BỊ SẠC QUÁ MỨC"),
    (3, "alarm_battery_voltage_low", "ĐIỆN ÁP PIN THẤP"),
    (4, "alarm_overload", "QUÁ TẢI"),
    (5, "alarm_output_power_derating", "GIẢM CÔNG SUẤT ĐẦU RA"),
    (6, "alarm_pv_energy_weak", "NGUỒN PV YẾU"),
    (7, "alarm_ac_voltage_high", "ĐIỆN AC QUÁ CAO"),
    (8, "alarm_no_battery", "KHÔNG CÓ PIN"),
]

# 11 cờ setting-state trong thanh ghi 4535 (bit 0 = LSB). Theo
# 0925.json/SystemState1 - đã xác nhận decode đúng với dữ liệu thực
# (raw=489 -> Buzzer/Overload auto restart/Beep primary fail/Auto return/
# Transfer to bypass/Record fault code đều ON, khớp cấu hình mặc định).
# Đây là cờ TRẠNG THÁI đọc-only (device_class None, không phải "problem"
# như ALARM_BITS) - buzzer_alarm/beep_on_primary_source_fail/
# overload_bypass đã có select riêng để ghi, các bit này chỉ để xem.
SETTINGS_BITS: list[tuple[int, str, str]] = [
    (0, "setting_buzzer_alarm", "CÒI BÁO ĐỘNG"),
    (1, "setting_power_saving_mode", "CHẾ ĐỘ TIẾT KIỆM ĐIỆN"),
    (2, "setting_lcd_backlight", "ĐÈN NỀN MÀN HÌNH"),
    (3, "setting_overload_auto_restart", "TỰ KHỞI ĐỘNG SAU KHI QUÁ TẢI"),
    (4, "setting_over_temp_auto_restart", "TỰ KHỞI ĐỘNG SAU KHI QUÁ NHIỆT"),
    (5, "setting_beep_primary_source_fail", "PHÁT ÂM THANH KHI NGUỒN CHÍNH BỊ GIÁN ĐOẠN"),
    (6, "setting_auto_return_display", "TỰ ĐỘNG QUAY VỀ MÀN HÌNH MẶC ĐỊNH"),
    (7, "setting_transfer_bypass_overload", "CHUYỂN SANG CHẾ ĐỘ BYPASS KHI QUÁ TẢI"),
    (8, "setting_record_fault_code", "GHI MÃ LỖI"),
    (9, "setting_battery_equalization", "CÂN BẰNG ĐIỆN ÁP PIN"),
    (10, "setting_battery_equalization_immediate", "KÍCH HOẠT CẦN BẰNG LẬP TỨC"),
]

# --- Selects (function 0x06, ghi RAW không đảo byte tại địa chỉ 5000-series;
# đọc lại trạng thái ở read_address riêng theo 0925.json - cùng khối
# "System Info" đã xác nhận đảo byte thật (4502), nên đọc read_address
# CŨNG đảo byte (swap=True), khác với việc GHI (luôn RAW, xem write_register). ---
SELECT_OPTIONS_ONOFF = {"TẮT": 0, "MỞ": 1}

# 3 select KHÔNG có địa chỉ readback dạng thanh ghi độc lập hoạt động được
# (đọc thẳng 5002/5007/5009 trả về "unknown" trên thiết bị thật) - trạng
# thái thật của chúng nằm trong CÙNG bit đã giải mã ở settings_flags_raw
# (4535, xem SETTINGS_BITS) nên không cần đọc thêm - lấy lại đúng bit đó.
# key -> (sensor_key chứa bitmask, số bit)
SELECT_BIT_READBACK: dict[str, tuple[str, int]] = {
    "buzzer_alarm": ("settings_flags_raw", 0),
    "beep_on_primary_source_fail": ("settings_flags_raw", 5),
    "overload_bypass": ("settings_flags_raw", 7),
}

# (write_address, read_address, key, name, optionsmap)
# read_address = None cho 3 select ở trên (đọc qua SELECT_BIT_READBACK thay
# vì tự poll riêng - vừa tránh request Modbus vô ích vừa cho kết quả đúng).
SELECTS: list[tuple[int, int | None, str, str, dict[str, int]]] = [
    (5002, None, "buzzer_alarm", "BÁO ÂM THANH", SELECT_OPTIONS_ONOFF),
    (5007, None, "beep_on_primary_source_fail", "PHÁT ÂM THANH NGUỒN CHÍNH SỰ CỐ", SELECT_OPTIONS_ONOFF),
    (5009, None, "overload_bypass", "Overload Bypass", SELECT_OPTIONS_ONOFF),
    (5017, 4536, "charger_source_priority", "NGUỒN SẠC ƯU TIÊN", {
        "LƯỚI": 0,
        "Solar": 1,
        "LƯỚI or Solar (default)": 2,
        "CHỈ SOLAR": 3,
    }),
    (5018, 4537, "output_source_priority", "ƯU TIÊN NGUỒN ĐẦU RA", {
        "LƯỚI (default)": 0,
        "Solar": 1,
        "SBU": 2,
    }),
    (5022, 4541, "max_total_charge_current", "DÒNG SẠC TỐI DA", {
        str(v): v for v in (10, 15, 18, 20, 23, 25, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120)
    }),
    (5024, 4543, "utility_charge_current", "Utility Charge Current", {
        str(v): v for v in (2, 10, 20, 30, 40, 50, 60)
    }),
    (5020, 4539, "battery_type", "LOẠI LƯU TRỮ", {
        "ẮC QUY KHÔ": 0,
        "ẮC QUY NƯỚC": 1,
        "KHÁC": 2,
    }),
]


def swap_bytes(raw: int) -> int:
    """Đảo 2 byte của thanh ghi 16-bit, khớp lambda ESPHome trong evo.yaml."""
    raw &= 0xFFFF
    return ((raw >> 8) | (raw << 8)) & 0xFFFF
