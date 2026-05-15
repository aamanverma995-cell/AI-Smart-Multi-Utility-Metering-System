"""
sensors/electricity.py — PZEM-004T AC power sensor driver
Communicates over UART (RS-485 TTL adapter) using Modbus RTU protocol.

Datasheet / protocol reference:
  https://innovatorsguru.com/wp-content/uploads/2019/06/PZEM-004T-V3.0-Datasheet-User-Manual.pdf

Wiring (PZEM-004T V3.0 → Raspberry Pi 5):
  PZEM TX  →  Pi RX  (GPIO15 / /dev/ttyAMA0 pin 10, or USB-UART adapter)
  PZEM RX  →  Pi TX  (GPIO14 / /dev/ttyAMA0 pin 8)
  PZEM 5V  →  Pi 5V  (pin 2 or 4)
  PZEM GND →  Pi GND (pin 6)
  ⚠ Live AC wires must be connected by a qualified electrician.
"""

import struct
import logging
from dataclasses import dataclass, field
from typing import Optional

import serial

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# PZEM-004T default Modbus slave address
PZEM_DEFAULT_ADDR = 0xF8
# Read input registers function code
PZEM_READ_CMD     = 0x04
# Register start address and count (voltage → PF = 10 registers)
REG_START         = 0x0000
REG_COUNT         = 10


@dataclass
class ElectricityReading:
    voltage_v:     float = 0.0   # Volts
    current_a:     float = 0.0   # Amperes
    power_w:       float = 0.0   # Watts
    energy_kwh:    float = 0.0   # kWh (cumulative)
    frequency_hz:  float = 0.0   # Hz
    power_factor:  float = 0.0   # 0.00 – 1.00
    alarm:         bool  = False
    valid:         bool  = False


def _crc16(data: bytes) -> int:
    """CRC-16/Modbus calculation."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_request(addr: int = PZEM_DEFAULT_ADDR) -> bytes:
    payload = struct.pack(">BBHH", addr, PZEM_READ_CMD, REG_START, REG_COUNT)
    crc = _crc16(payload)
    return payload + struct.pack("<H", crc)


def _parse_response(data: bytes) -> ElectricityReading:
    reading = ElectricityReading()
    # Expected: addr(1) + fc(1) + byte_count(1) + 20 data bytes + CRC(2) = 25 bytes
    if len(data) < 25:
        logger.warning("PZEM response too short: %d bytes", len(data))
        return reading

    # Verify CRC
    received_crc = struct.unpack_from("<H", data, 23)[0]
    calc_crc     = _crc16(data[:23])
    if received_crc != calc_crc:
        logger.warning("PZEM CRC mismatch: got 0x%04X, expected 0x%04X", received_crc, calc_crc)
        return reading

    regs = struct.unpack_from(">10H", data, 3)
    reading.voltage_v    = regs[0] / 10.0
    reading.current_a    = ((regs[2] << 16) | regs[1]) / 1000.0
    reading.power_w      = ((regs[4] << 16) | regs[3]) / 10.0
    reading.energy_kwh   = ((regs[6] << 16) | regs[5]) / 1000.0
    reading.frequency_hz = regs[7] / 10.0
    reading.power_factor = regs[8] / 100.0
    reading.alarm        = bool(regs[9])
    reading.valid        = True
    return reading


class ElectricitySensor:
    """PZEM-004T driver. Thread-safe for single-reader use."""

    def __init__(self, port: str = config.PZEM_PORT, baud: int = config.PZEM_BAUD):
        self._port = port
        self._baud = baud
        self._ser: Optional[serial.Serial] = None

    def init(self) -> bool:
        """Open the serial port. Returns True on success."""
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=2.0,
            )
            logger.info("PZEM-004T opened on %s @ %d baud", self._port, self._baud)
            return True
        except serial.SerialException as exc:
            logger.error("Failed to open PZEM port %s: %s", self._port, exc)
            return False

    def read(self) -> ElectricityReading:
        """Send a Modbus read request and return parsed data."""
        if self._ser is None or not self._ser.is_open:
            logger.error("PZEM serial port not open")
            return ElectricityReading()
        try:
            self._ser.reset_input_buffer()
            self._ser.write(_build_request())
            response = self._ser.read(25)
            return _parse_response(response)
        except serial.SerialException as exc:
            logger.error("PZEM read error: %s", exc)
            return ElectricityReading()

    def reset_energy(self) -> bool:
        """Reset the cumulative kWh counter on the PZEM."""
        RESET_CMD = bytes([PZEM_DEFAULT_ADDR, 0x42])
        crc = _crc16(RESET_CMD)
        packet = RESET_CMD + struct.pack("<H", crc)
        try:
            self._ser.write(packet)
            return True
        except Exception as exc:
            logger.error("PZEM reset error: %s", exc)
            return False

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info("PZEM-004T port closed")
