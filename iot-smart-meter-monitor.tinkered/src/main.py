"""
main.py — Smart Meter entry point
Starts the sensor loop, all services, and the Flask dashboard in parallel threads.

Usage:
  python src/main.py

The sensor reading loop runs every SAMPLE_INTERVAL_SEC seconds (default 10 s).
The Flask dashboard runs on port 5000 (change in .env).
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime

# ── Bootstrap ──────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(ROOT, "..", "smart_meter.log")),
    ],
)
logger = logging.getLogger("main")

# ── Sensor imports ─────────────────────────────────────────────────────────────
from sensors.electricity import ElectricitySensor, ElectricityReading
from sensors.water_flow  import WaterFlowSensor,  WaterFlowReading
from sensors.gas         import GasSensor,         GasReading

# ── Service imports ────────────────────────────────────────────────────────────
from services.database      import DatabaseService
from services.mqtt_service  import MqttService
from services.influx_service import InfluxService
from services.alert_service import AlertService
from web.app                import run_dashboard


# ── Global stop event ─────────────────────────────────────────────────────────
_stop_event = threading.Event()


def _handle_signal(signum, frame):  # noqa: ARG001
    logger.info("Shutdown signal received (%d)", signum)
    _stop_event.set()


def sensor_loop(elec_sensor, water_sensor, gas_sensor, db, mqtt, influx, alerts):
    """Main reading loop — runs in its own thread."""
    logger.info(
        "Sensor loop started (interval = %d s)", config.SAMPLE_INTERVAL_SEC
    )
    while not _stop_event.is_set():
        loop_start = time.monotonic()
        now        = datetime.utcnow()

        # Read sensors
        elec  = elec_sensor.read()
        water = water_sensor.read()
        gas   = gas_sensor.read()

        if not elec.valid:
            logger.warning("Electricity reading invalid — check PZEM wiring/port")
        if not water.valid:
            logger.warning("Water flow reading invalid")
        if not gas.valid:
            logger.warning("Gas reading invalid — check ADS1115/I2C")

        logger.info(
            "Elec: %.1f V | %.0f W | %.3f kWh  "
            "Water: %.2f L/min | %.1f L total  "
            "Gas: %d raw (%.1f%%FSD) alert=%s",
            elec.voltage_v, elec.power_w, elec.energy_kwh,
            water.flow_lpm, water.total_litres,
            gas.raw_value, gas.pct_fsd, gas.alert,
        )

        # Persist & publish
        db.save_reading(elec, water, gas, timestamp=now)
        mqtt.publish(elec, water, gas, timestamp=now)
        influx.write(elec, water, gas, timestamp=now)
        alerts.check_and_alert(elec, water, gas)

        # Sleep for the remainder of the interval
        elapsed = time.monotonic() - loop_start
        sleep_s = max(0.0, config.SAMPLE_INTERVAL_SEC - elapsed)
        _stop_event.wait(timeout=sleep_s)

    logger.info("Sensor loop stopped")


def main():
    logger.info("=== Smart Meter starting up ===")

    # ── Register SIGINT / SIGTERM ──────────────────────────────────────────────
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Initialise sensors ─────────────────────────────────────────────────────
    elec_sensor  = ElectricitySensor()
    water_sensor = WaterFlowSensor()
    gas_sensor   = GasSensor()

    if not elec_sensor.init():
        logger.error("Electricity sensor failed to init — continuing without it")
    if not water_sensor.init():
        logger.error("Water flow sensor failed to init — continuing without it")
    if not gas_sensor.init():
        logger.error("Gas sensor failed to init — continuing without it")

    # ── Initialise services ────────────────────────────────────────────────────
    db     = DatabaseService()
    mqtt   = MqttService()
    influx = InfluxService()
    alerts = AlertService()

    for svc in (db, mqtt, influx, alerts):
        svc.init()

    # ── Start sensor loop in background thread ─────────────────────────────────
    loop_thread = threading.Thread(
        target=sensor_loop,
        args=(elec_sensor, water_sensor, gas_sensor, db, mqtt, influx, alerts),
        daemon=True,
        name="sensor-loop",
    )
    loop_thread.start()

    # ── Start Flask dashboard in background thread ────────────────────────────
    dash_thread = threading.Thread(
        target=run_dashboard,
        args=(db,),
        daemon=True,
        name="flask-dashboard",
    )
    dash_thread.start()
    logger.info("Dashboard available at http://<Pi-IP>:%d", config.FLASK_PORT)

    # ── Wait for shutdown ─────────────────────────────────────────────────────
    _stop_event.wait()
    logger.info("Shutting down…")

    elec_sensor.close()
    water_sensor.close()
    gas_sensor.close()
    mqtt.close()

    logger.info("Smart Meter stopped cleanly")


if __name__ == "__main__":
    main()
