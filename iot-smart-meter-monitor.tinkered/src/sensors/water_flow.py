"""
sensors/water_flow.py — YF-S201 Hall-effect water flow sensor driver
Uses GPIO interrupt-based pulse counting for accuracy.

Datasheet reference:
  https://www.seeedstudio.com/Water-Flow-Sensor-YF-S201C-p-2878.html

Wiring (YF-S201 → Raspberry Pi 5):
  Red   (VCC)    →  Pi 5V   (pin 2 or 4)
  Black (GND)    →  Pi GND  (pin 6)
  Yellow (Signal) →  Pi GPIO17 (BCM 17, pin 11)  ← set WATER_FLOW_GPIO in .env
  Add a 10kΩ pull-up resistor between Signal and 5V (or use Pi internal pull-up).

Calibration:
  YF-S201 outputs ~7.5 pulses per litre (may vary ±10%).
  Adjust WATER_PULSES_PER_LITRE in .env after bench-testing with a known volume.
"""

import logging
import threading
import time
from dataclasses import dataclass

import RPi.GPIO as GPIO

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


@dataclass
class WaterFlowReading:
    flow_lpm:        float = 0.0   # Litres per minute (instantaneous)
    total_litres:    float = 0.0   # Cumulative since init
    pulse_count:     int   = 0     # Raw pulse count since last reset
    valid:           bool  = False


class WaterFlowSensor:
    """
    YF-S201 interrupt-driven driver.
    Thread-safe: pulse counting happens in an ISR, reads happen from main thread.
    """

    def __init__(
        self,
        gpio_pin: int = config.WATER_FLOW_GPIO,
        pulses_per_litre: float = config.WATER_PULSES_PER_LITRE,
    ):
        self._pin              = gpio_pin
        self._pulses_per_litre = pulses_per_litre
        self._pulse_count      = 0
        self._total_pulses     = 0
        self._lock             = threading.Lock()
        self._last_read_time   = time.monotonic()
        self._initialised      = False

    def init(self) -> bool:
        """Set up GPIO and attach falling-edge interrupt. Returns True on success."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self._pin,
                GPIO.FALLING,
                callback=self._pulse_callback,
            )
            self._last_read_time = time.monotonic()
            self._initialised    = True
            logger.info("YF-S201 initialised on GPIO BCM %d", self._pin)
            return True
        except RuntimeError as exc:
            logger.error("Failed to initialise YF-S201: %s", exc)
            return False

    def _pulse_callback(self, channel: int) -> None:  # noqa: ARG002
        """Called by GPIO ISR on every falling edge."""
        with self._lock:
            self._pulse_count  += 1
            self._total_pulses += 1

    def read(self) -> WaterFlowReading:
        """
        Return instantaneous flow rate (L/min) and cumulative volume.
        Resets the interval pulse counter after each read.
        """
        if not self._initialised:
            return WaterFlowReading()

        now = time.monotonic()
        elapsed = now - self._last_read_time

        with self._lock:
            interval_pulses    = self._pulse_count
            self._pulse_count  = 0
            total_pulses       = self._total_pulses

        self._last_read_time = now

        if elapsed > 0:
            pulses_per_sec = interval_pulses / elapsed
            flow_lpm       = (pulses_per_sec / self._pulses_per_litre) * 60.0
        else:
            flow_lpm = 0.0

        total_litres = total_pulses / self._pulses_per_litre

        return WaterFlowReading(
            flow_lpm=round(flow_lpm, 3),
            total_litres=round(total_litres, 3),
            pulse_count=interval_pulses,
            valid=True,
        )

    def reset_total(self) -> None:
        """Reset the cumulative litre counter."""
        with self._lock:
            self._total_pulses = 0
        logger.info("Water flow cumulative counter reset")

    def close(self) -> None:
        if self._initialised:
            GPIO.remove_event_detect(self._pin)
            GPIO.cleanup(self._pin)
            self._initialised = False
            logger.info("YF-S201 GPIO cleaned up")
