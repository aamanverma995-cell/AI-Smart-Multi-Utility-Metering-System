"""
sensors/gas.py — MQ-4 natural gas sensor driver via ADS1115 I2C ADC
The MQ-4 has an analog output; the ADS1115 converts it for the Pi.

Datasheet references:
  MQ-4:    https://www.pololu.com/file/0J311/MQ4.pdf
  ADS1115: https://www.ti.com/lit/ds/symlink/ads1115.pdf

Wiring (MQ-4 → ADS1115 → Raspberry Pi 5):
  MQ-4:
    VCC  →  Pi 5V    (pin 2)    ← MQ-4 heater needs 5V
    GND  →  Pi GND   (pin 6)
    AOUT →  ADS1115 A0
  ADS1115:
    VDD  →  Pi 3.3V  (pin 1)
    GND  →  Pi GND   (pin 6)
    SDA  →  Pi GPIO2  (SDA1, pin 3)
    SCL  →  Pi GPIO3  (SCL1, pin 5)
    ADDR →  GND       (sets I2C address 0x48)
    A0   →  MQ-4 AOUT

I2C must be enabled: sudo raspi-config → Interfaces → I2C → Enable

Calibration:
  1. Power on for 24–48 h in clean air to burn-in the heater coil.
  2. Note the ADC count in clean air as your baseline (R0).
  3. Adjust GAS_ALERT_THRESHOLD in .env to a value above R0 that indicates
     a meaningful gas concentration for your environment.
"""

import logging
from dataclasses import dataclass

import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# ADS1115 full-scale voltage at gain=1 (±4.096 V) → 32767 counts
ADS1115_MAX_COUNT  = 32767
ADS1115_FS_VOLTAGE = 4.096   # Volts


@dataclass
class GasReading:
    raw_value:   int   = 0      # 16-bit ADC count
    voltage_v:   float = 0.0   # Converted voltage
    pct_fsd:     float = 0.0   # Percentage of full-scale deflection
    alert:       bool  = False  # True when above threshold
    valid:       bool  = False


class GasSensor:
    """MQ-4 driver reading via ADS1115 on I2C."""

    def __init__(
        self,
        i2c_addr: int   = config.ADS1115_I2C_ADDR,
        gain:     float = config.ADS1115_GAIN,
        channel:  int   = config.GAS_CHANNEL,
        threshold: int  = config.GAS_ALERT_THRESHOLD,
    ):
        self._addr      = i2c_addr
        self._gain      = gain
        self._channel   = channel
        self._threshold = threshold
        self._ads       = None
        self._chan      = None

    def init(self) -> bool:
        """Initialise I2C bus and ADS1115. Returns True on success."""
        try:
            i2c      = busio.I2C(board.SCL, board.SDA)
            self._ads = ADS.ADS1115(i2c, address=self._addr, gain=self._gain)
            # Select single-ended channel
            ch_map = {0: ADS.P0, 1: ADS.P1, 2: ADS.P2, 3: ADS.P3}
            if self._channel not in ch_map:
                raise ValueError(f"Invalid ADS1115 channel: {self._channel}")
            self._chan = AnalogIn(self._ads, ch_map[self._channel])
            logger.info(
                "ADS1115 (0x%02X) initialised; MQ-4 on channel A%d",
                self._addr, self._channel,
            )
            return True
        except Exception as exc:
            logger.error("Failed to initialise ADS1115/MQ-4: %s", exc)
            return False

    def read(self) -> GasReading:
        """Read a single ADC sample and return a GasReading."""
        if self._chan is None:
            return GasReading()
        try:
            raw   = self._chan.value    # 16-bit signed int
            volts = self._chan.voltage  # float
            pct   = (raw / ADS1115_MAX_COUNT) * 100.0
            alert = raw >= self._threshold
            if alert:
                logger.warning("Gas alert! ADC=%d (threshold=%d)", raw, self._threshold)
            return GasReading(
                raw_value=raw,
                voltage_v=round(volts, 4),
                pct_fsd=round(pct, 2),
                alert=alert,
                valid=True,
            )
        except Exception as exc:
            logger.error("ADS1115 read error: %s", exc)
            return GasReading()

    def close(self) -> None:
        self._chan = None
        self._ads  = None
        logger.info("GasSensor closed")
