#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          SMART METER — ALL-IN-ONE  (Raspberry Pi 5)                        ║
║  Electricity · Water Flow · Gas Detection                                  ║
║  SQLite · MQTT · InfluxDB Cloud · Telegram Alerts · Flask Dashboard        ║
║  AI Anomaly Detection · Predictive Maintenance · Optimization              ║
║  Distributed Multi-Flat Coordination                                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

USAGE:
  pip install pyserial RPi.GPIO adafruit-circuitpython-ads1x15 paho-mqtt \
              influxdb-client python-telegram-bot flask python-dotenv
  cp env.example .env   # fill in your values
  python smart_meter_all_in_one.py

All configuration is read from .env (or environment variables).
"""

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════
import asyncio
import json
import logging
import math
import os
import signal
import sqlite3
import struct
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — CONFIGURATION  (reads from .env)
# ═══════════════════════════════════════════════════════════════════════════════
load_dotenv()

PZEM_PORT              = os.getenv("PZEM_PORT",              "/dev/ttyUSB0")
PZEM_BAUD              = int(os.getenv("PZEM_BAUD",          "9600"))
WATER_FLOW_GPIO        = int(os.getenv("WATER_FLOW_GPIO",    "17"))
WATER_PULSES_PER_LITRE = float(os.getenv("WATER_PULSES_PER_LITRE", "7.5"))
ADS1115_I2C_ADDR       = int(os.getenv("ADS1115_I2C_ADDR",  "0x48"), 16)
ADS1115_GAIN           = float(os.getenv("ADS1115_GAIN",     "1"))
GAS_CHANNEL            = int(os.getenv("GAS_CHANNEL",        "0"))
GAS_ALERT_THRESHOLD    = int(os.getenv("GAS_ALERT_THRESHOLD","20000"))
SAMPLE_INTERVAL_SEC    = int(os.getenv("SAMPLE_INTERVAL_SEC","10"))
DB_PATH                = os.getenv("DB_PATH",                "data/smart_meter.db")

MQTT_ENABLED  = os.getenv("MQTT_ENABLED",  "true").lower() == "true"
MQTT_BROKER   = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC    = os.getenv("MQTT_TOPIC",    "smart_meter/readings")

INFLUX_ENABLED = os.getenv("INFLUX_ENABLED","false").lower() == "true"
INFLUX_URL     = os.getenv("INFLUX_URL",    "https://us-east-1-1.aws.cloud2.influxdata.com")
INFLUX_TOKEN   = os.getenv("INFLUX_TOKEN",  "")
INFLUX_ORG     = os.getenv("INFLUX_ORG",    "")
INFLUX_BUCKET  = os.getenv("INFLUX_BUCKET", "smart_meter")

TELEGRAM_ENABLED   = os.getenv("TELEGRAM_ENABLED",   "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

ELECTRICITY_POWER_LIMIT_W = float(os.getenv("ELECTRICITY_POWER_LIMIT_W","3000"))
WATER_FLOW_LIMIT_LPM      = float(os.getenv("WATER_FLOW_LIMIT_LPM",     "20"))
ALERT_COOLDOWN_SEC        = int(os.getenv("ALERT_COOLDOWN_SEC",          "300"))

FLASK_HOST  = os.getenv("FLASK_HOST",  "0.0.0.0")
FLASK_PORT  = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── Multi-flat / Building ──────────────────────────────────────────────────────
FLAT_ID     = os.getenv("FLAT_ID",     "flat_1")
BUILDING_ID = os.getenv("BUILDING_ID", "building_A")

# ── Optimization (Indian tariff defaults) ─────────────────────────────────────
TARIFF_PER_KWH     = float(os.getenv("TARIFF_PER_KWH",     "6.50"))
WATER_COST_PER_KL  = float(os.getenv("WATER_COST_PER_KL",  "15.0"))
OCCUPANTS          = int(os.getenv("OCCUPANTS",             "4"))

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/smart_meter.log"),
    ],
)
logger = logging.getLogger("smart_meter")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class ElectricityReading:
    voltage_v: float = 0.0
    current_a: float = 0.0
    power_w:   float = 0.0
    energy_kwh: float = 0.0
    frequency_hz: float = 0.0
    power_factor: float = 0.0
    alarm: bool = False
    valid: bool = False

@dataclass
class WaterFlowReading:
    flow_lpm:     float = 0.0
    total_litres: float = 0.0
    pulse_count:  int   = 0
    valid:        bool  = False

@dataclass
class GasReading:
    raw_value: int   = 0
    voltage_v: float = 0.0
    pct_fsd:   float = 0.0
    alert:     bool  = False
    valid:     bool  = False

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — ELECTRICITY SENSOR  (PZEM-004T Modbus RTU over UART)
# ═══════════════════════════════════════════════════════════════════════════════
PZEM_DEFAULT_ADDR = 0xF8

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def _build_pzem_request() -> bytes:
    payload = struct.pack(">BBHH", PZEM_DEFAULT_ADDR, 0x04, 0x0000, 10)
    return payload + struct.pack("<H", _crc16(payload))

def _parse_pzem_response(data: bytes) -> ElectricityReading:
    r = ElectricityReading()
    if len(data) < 25:
        return r
    if struct.unpack_from("<H", data, 23)[0] != _crc16(data[:23]):
        return r
    regs = struct.unpack_from(">10H", data, 3)
    r.voltage_v    = regs[0] / 10.0
    r.current_a    = ((regs[2] << 16) | regs[1]) / 1000.0
    r.power_w      = ((regs[4] << 16) | regs[3]) / 10.0
    r.energy_kwh   = ((regs[6] << 16) | regs[5]) / 1000.0
    r.frequency_hz = regs[7] / 10.0
    r.power_factor = regs[8] / 100.0
    r.alarm        = bool(regs[9])
    r.valid        = True
    return r

class ElectricitySensor:
    def __init__(self):
        self._ser = None

    def init(self) -> bool:
        try:
            import serial
            self._ser = serial.Serial(
                port=PZEM_PORT, baudrate=PZEM_BAUD,
                bytesize=8, parity="N", stopbits=1, timeout=2.0)
            logger.info("PZEM-004T on %s", PZEM_PORT)
            return True
        except Exception as e:
            logger.error("PZEM init: %s", e)
            return False

    def read(self) -> ElectricityReading:
        if not self._ser or not self._ser.is_open:
            return ElectricityReading()
        try:
            self._ser.reset_input_buffer()
            self._ser.write(_build_pzem_request())
            return _parse_pzem_response(self._ser.read(25))
        except Exception as e:
            logger.error("PZEM read: %s", e)
            return ElectricityReading()

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — WATER FLOW SENSOR  (YF-S201 GPIO pulse counting)
# ═══════════════════════════════════════════════════════════════════════════════
class WaterFlowSensor:
    def __init__(self):
        self._pulse_count  = 0
        self._total_pulses = 0
        self._lock         = threading.Lock()
        self._last_time    = time.monotonic()
        self._ok           = False

    def init(self) -> bool:
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(WATER_FLOW_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(WATER_FLOW_GPIO, GPIO.FALLING,
                                  callback=self._cb)
            self._ok = True
            logger.info("YF-S201 on GPIO BCM %d", WATER_FLOW_GPIO)
            return True
        except Exception as e:
            logger.error("YF-S201 init: %s", e)
            return False

    def _cb(self, _ch):
        with self._lock:
            self._pulse_count  += 1
            self._total_pulses += 1

    def read(self) -> WaterFlowReading:
        if not self._ok:
            return WaterFlowReading()
        now     = time.monotonic()
        elapsed = now - self._last_time
        with self._lock:
            pulses           = self._pulse_count
            self._pulse_count = 0
            total            = self._total_pulses
        self._last_time = now
        flow_lpm     = (pulses / elapsed / WATER_PULSES_PER_LITRE * 60) if elapsed > 0 else 0
        total_litres = total / WATER_PULSES_PER_LITRE
        return WaterFlowReading(round(flow_lpm,3), round(total_litres,3), pulses, True)

    def close(self):
        if self._ok:
            try:
                import RPi.GPIO as GPIO
                GPIO.remove_event_detect(WATER_FLOW_GPIO)
                GPIO.cleanup(WATER_FLOW_GPIO)
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — GAS SENSOR  (MQ-4 via ADS1115 I2C ADC)
# ═══════════════════════════════════════════════════════════════════════════════
class GasSensor:
    def __init__(self):
        self._chan = None

    def init(self) -> bool:
        try:
            import board, busio
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.analog_in import AnalogIn
            i2c       = busio.I2C(board.SCL, board.SDA)
            ads       = ADS.ADS1115(i2c, address=ADS1115_I2C_ADDR, gain=ADS1115_GAIN)
            ch_map    = {0:ADS.P0, 1:ADS.P1, 2:ADS.P2, 3:ADS.P3}
            self._chan = AnalogIn(ads, ch_map[GAS_CHANNEL])
            logger.info("ADS1115/MQ-4 on I2C 0x%02X ch A%d", ADS1115_I2C_ADDR, GAS_CHANNEL)
            return True
        except Exception as e:
            logger.error("Gas sensor init: %s", e)
            return False

    def read(self) -> GasReading:
        if not self._chan:
            return GasReading()
        try:
            raw   = self._chan.value
            volts = self._chan.voltage
            pct   = raw / 32767 * 100
            alert = raw >= GAS_ALERT_THRESHOLD
            if alert:
                logger.warning("GAS ALERT raw=%d", raw)
            return GasReading(raw, round(volts,4), round(pct,2), alert, True)
        except Exception as e:
            logger.error("Gas read: %s", e)
            return GasReading()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — DATABASE  (SQLite)
# ═══════════════════════════════════════════════════════════════════════════════
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    voltage_v REAL, current_a REAL, power_w REAL, energy_kwh REAL,
    frequency_hz REAL, power_factor REAL, elec_alarm INTEGER,
    flow_lpm REAL, total_litres REAL,
    gas_raw INTEGER, gas_voltage REAL, gas_pct_fsd REAL, gas_alert INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ts ON readings(timestamp);
"""

class DatabaseService:
    def init(self) -> bool:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with self._conn() as c:
            c.executescript(_CREATE_SQL)
        logger.info("SQLite ready at %s", DB_PATH)
        return True

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            yield con; con.commit()
        except Exception:
            con.rollback(); raise
        finally:
            con.close()

    def save(self, e: ElectricityReading, w: WaterFlowReading, g: GasReading,
             ts: Optional[datetime] = None):
        with self._conn() as c:
            c.execute("""
                INSERT INTO readings VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                (ts or datetime.utcnow()).isoformat(),
                e.voltage_v, e.current_a, e.power_w, e.energy_kwh,
                e.frequency_hz, e.power_factor, int(e.alarm),
                w.flow_lpm, w.total_litres,
                g.raw_value, g.voltage_v, g.pct_fsd, int(g.alert),
            ))

    def latest(self, n=50) -> List[dict]:
        with self._conn() as c:
            return [dict(r) for r in
                    c.execute("SELECT * FROM readings ORDER BY id DESC LIMIT ?", (n,))]

    def energy_today(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(energy_kwh)-MIN(energy_kwh) d FROM readings WHERE timestamp>=date('now')"
            ).fetchone()
        return round(row["d"] or 0, 4)

    def water_today(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(total_litres)-MIN(total_litres) d FROM readings WHERE timestamp>=date('now')"
            ).fetchone()
        return round(row["d"] or 0, 3)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — MQTT SERVICE
# ═══════════════════════════════════════════════════════════════════════════════
class MqttService:
    def __init__(self):
        self._client = None

    def init(self) -> bool:
        if not MQTT_ENABLED:
            return True
        try:
            import paho.mqtt.client as mqtt
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            if MQTT_USERNAME:
                self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self._client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
            self._client.loop_start()
            logger.info("MQTT → %s:%d", MQTT_BROKER, MQTT_PORT)
            return True
        except Exception as e:
            logger.error("MQTT init: %s", e); return False

    def publish(self, e, w, g, ts=None):
        if not MQTT_ENABLED or not self._client:
            return
        self._client.publish(MQTT_TOPIC, json.dumps({
            "ts": (ts or datetime.utcnow()).isoformat(),
            "voltage_v": e.voltage_v, "current_a": e.current_a,
            "power_w": e.power_w, "energy_kwh": e.energy_kwh,
            "frequency_hz": e.frequency_hz, "power_factor": e.power_factor,
            "flow_lpm": w.flow_lpm, "total_litres": w.total_litres,
            "gas_raw": g.raw_value, "gas_pct_fsd": g.pct_fsd, "gas_alert": g.alert,
        }), qos=1)

    def close(self):
        if self._client:
            self._client.loop_stop(); self._client.disconnect()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — INFLUXDB CLOUD SERVICE
# ═══════════════════════════════════════════════════════════════════════════════
class InfluxService:
    def __init__(self):
        self._api = None

    def init(self) -> bool:
        if not INFLUX_ENABLED:
            return True
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS
            self._api = InfluxDBClient(
                url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG
            ).write_api(write_options=SYNCHRONOUS)
            logger.info("InfluxDB ready → %s", INFLUX_URL)
            return True
        except Exception as e:
            logger.error("InfluxDB init: %s", e); return False

    def write(self, e, w, g, ts=None):
        if not INFLUX_ENABLED or not self._api:
            return
        try:
            from influxdb_client import Point
            t = ts or datetime.now(timezone.utc)
            self._api.write(bucket=INFLUX_BUCKET, record=[
                Point("electricity").field("voltage_v", e.voltage_v)
                    .field("current_a", e.current_a).field("power_w", e.power_w)
                    .field("energy_kwh", e.energy_kwh).field("frequency_hz", e.frequency_hz)
                    .field("power_factor", e.power_factor).time(t),
                Point("water").field("flow_lpm", w.flow_lpm)
                    .field("total_litres", w.total_litres).time(t),
                Point("gas").field("raw", g.raw_value)
                    .field("pct_fsd", g.pct_fsd).field("alert", int(g.alert)).time(t),
            ])
        except Exception as e:
            logger.error("InfluxDB write: %s", e)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — TELEGRAM ALERT SERVICE
# ═══════════════════════════════════════════════════════════════════════════════
class AlertService:
    def __init__(self):
        self._bot  = None
        self._last: Dict[str, float] = {}

    def init(self) -> bool:
        if not TELEGRAM_ENABLED:
            return True
        try:
            import telegram
            self._bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
            logger.info("Telegram bot ready")
            return True
        except Exception as e:
            logger.error("Telegram init: %s", e); return False

    def _send(self, key: str, msg: str):
        if time.monotonic() - self._last.get(key, 0) < ALERT_COOLDOWN_SEC:
            return
        self._last[key] = time.monotonic()
        try:
            asyncio.run(self._bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="HTML"))
        except Exception as e:
            logger.error("Telegram send [%s]: %s", key, e)

    def check(self, e, w, g):
        if not TELEGRAM_ENABLED or not self._bot:
            return
        if e.valid and e.power_w > ELECTRICITY_POWER_LIMIT_W:
            self._send("elec", f"⚡ <b>Power Overload</b>\n{e.power_w:.0f} W (limit {ELECTRICITY_POWER_LIMIT_W:.0f} W)")
        if e.valid and e.alarm:
            self._send("elec_alarm", "⚡ <b>PZEM Alarm Active</b>\nCheck your power setup.")
        if w.valid and w.flow_lpm > WATER_FLOW_LIMIT_LPM:
            self._send("water", f"💧 <b>High Water Flow</b>\n{w.flow_lpm:.1f} L/min (limit {WATER_FLOW_LIMIT_LPM:.1f})")
        if g.valid and g.alert:
            self._send("gas", f"🔥 <b>GAS DETECTED!</b>\n{g.raw_value} ({g.pct_fsd:.1f}% FSD)\nCheck for leaks immediately.")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — AI ANOMALY DETECTION  (Z-score + IsolationForest)
# ═══════════════════════════════════════════════════════════════════════════════
_Z_THRESHOLD = 3.0
_MIN_SAMPLES = 20
_WINDOW_SIZE = 100

@dataclass
class AnomalyResult:
    metric: str = ""; value: float = 0.0; z_score: float = 0.0
    mean: float = 0.0; std: float = 0.0; is_anomaly: bool = False
    severity: str = "normal"; message: str = ""

class _RollingStats:
    def __init__(self):
        self._mean = 0.0; self._M2 = 0.0; self._count = 0
        self._buf = deque(maxlen=_WINDOW_SIZE)
    def push(self, x):
        self._buf.append(x); self._count += 1
        d = x - self._mean; self._mean += d / self._count
        self._M2 += d * (x - self._mean)
    @property
    def mean(self): return self._mean
    @property
    def std(self): return math.sqrt(self._M2 / (self._count-1)) if self._count > 1 else 0.0
    @property
    def count(self): return self._count
    def z_score(self, x):
        s = self.std; return abs((x - self.mean) / s) if s else 0.0

class AnomalyDetectionService:
    def __init__(self):
        self._stats = {k: _RollingStats() for k in
                       ("voltage_v","power_w","current_a","flow_lpm","gas_pct_fsd")}
        self._history = {k: [] for k in self._stats}
        self._anomaly_count = 0; self._sample_count = 0
        self._forest = None; self._forest_ready = False

    def init(self):
        logger.info("Anomaly detection ready (Z=%.1f)", _Z_THRESHOLD)
        try:
            from sklearn.ensemble import IsolationForest  # noqa
            logger.info("IsolationForest available")
        except ImportError:
            logger.warning("scikit-learn not installed — pip install scikit-learn")
        return True

    def _check(self, key, value):
        st = self._stats[key]
        self._history[key].append(value)
        if len(self._history[key]) > 500: self._history[key] = self._history[key][-500:]
        if st.count < _MIN_SAMPLES: st.push(value); return None
        z = st.z_score(value)
        sev = "critical" if z >= _Z_THRESHOLD*1.5 else "warning" if z >= _Z_THRESHOLD else "normal"
        r = AnomalyResult(metric=key, value=value, z_score=round(z,3),
                          mean=round(st.mean,3), std=round(st.std,3),
                          is_anomaly=(sev!="normal"), severity=sev,
                          message=f"{key}: val={value:.2f} Z={z:.2f}" if sev!="normal" else "")
        st.push(value)
        if r.is_anomaly:
            self._anomaly_count += 1
            logger.warning("ANOMALY [%s] Z=%.2f sev=%s", key, z, sev)
        return r

    def analyse(self, elec, water, gas):
        self._sample_count += 1
        anomalies = []
        for key, val in [("voltage_v",elec.voltage_v),("power_w",elec.power_w),
                         ("current_a",elec.current_a),("flow_lpm",water.flow_lpm),
                         ("gas_pct_fsd",gas.pct_fsd)]:
            r = self._check(key, val)
            if r and r.is_anomaly: anomalies.append(r)
        if self._sample_count % 200 == 0 and self._sample_count > 200:
            self._run_forest()
        return anomalies

    def _run_forest(self):
        try:
            from sklearn.ensemble import IsolationForest
            import numpy as np
            pw = self._history["power_w"]; fl = self._history["flow_lpm"]
            gs = self._history["gas_pct_fsd"]
            n = min(len(pw), len(fl), len(gs), 200)
            if n < 50: return
            X = np.column_stack([pw[-n:], fl[-n:], gs[-n:]])
            clf = IsolationForest(contamination=0.05, random_state=42)
            labels = clf.fit_predict(X)
            out = int((labels==-1).sum())
            logger.info("IsolationForest: %d samples, %d outliers (%.1f%%)", n, out, 100*out/n)
            self._forest = clf; self._forest_ready = True
        except Exception as e: logger.debug("IsolationForest: %s", e)

    def predict_anomaly(self, elec, water, gas):
        if not self._forest_ready: return False
        try:
            import numpy as np
            return bool(self._forest.predict(
                np.array([[elec.power_w, water.flow_lpm, gas.pct_fsd]]))[0] == -1)
        except Exception: return False

    def summary(self):
        return {"total_anomalies": self._anomaly_count,
                "samples_analysed": self._sample_count,
                "forest_ready": self._forest_ready,
                "metrics": {k: {"mean": round(v.mean,3), "std": round(v.std,3)}
                            for k,v in self._stats.items()}}

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — PREDICTIVE MAINTENANCE
# ═══════════════════════════════════════════════════════════════════════════════
class HealthStatus(str, Enum):
    GOOD="good"; DEGRADED="degraded"; CRITICAL="critical"; FAILED="failed"

@dataclass
class SensorHealth:
    sensor: str = ""; status: HealthStatus = HealthStatus.GOOD
    error_rate_pct: float = 0.0; drift_pct: float = 0.0; uptime_pct: float = 100.0
    total_reads: int = 0; failed_reads: int = 0
    warnings: List[str] = field(default_factory=list)
    predicted_days_to_failure: Optional[float] = None

class _SensorTracker:
    WINDOW = 200
    def __init__(self, name, nominal=None):
        self.name=name; self.nominal=nominal
        self._reads=0; self._failures=0
        self._fail_w=deque(maxlen=self.WINDOW)
        self._values=deque(maxlen=self.WINDOW)
        self._baselines=deque(maxlen=50)
        self._last_good=time.monotonic(); self._t0=time.monotonic()

    def record(self, value, valid):
        self._reads += 1; self._fail_w.append(0 if valid else 1)
        if not valid: self._failures += 1; return
        self._last_good = time.monotonic(); self._values.append(value)
        if self._reads % 20 == 0 and value is not None: self._baselines.append(value)

    @property
    def error_rate(self):
        return 100.0*sum(self._fail_w)/len(self._fail_w) if self._fail_w else 0.0

    @property
    def drift_pct(self):
        if len(self._baselines) < 5 or self.nominal is None: return 0.0
        rm = sum(list(self._baselines)[-10:]) / min(10, len(self._baselines))
        return abs((rm - self.nominal) / self.nominal) * 100.0

    @property
    def uptime(self):
        el = time.monotonic()-self._t0
        if el == 0: return 100.0
        silent = max(0.0, time.monotonic()-self._last_good-30)
        return max(0.0, 100.0-(silent/el)*100.0)

    def predict_failure_days(self):
        if len(self._baselines) < 10 or self.nominal is None: return None
        try:
            vals=list(self._baselines); n=len(vals); xs=list(range(n))
            xm=sum(xs)/n; ym=sum(vals)/n
            num=sum((x-xm)*(y-ym) for x,y in zip(xs,vals))
            den=sum((x-xm)**2 for x in xs)
            if den==0: return None
            slope=num/den
            if abs(slope)<1e-6: return None
            rem=(0.20*abs(self.nominal)-abs(ym-self.nominal))/abs(slope)
            return max(0.0, round(rem*20*10/86400, 1))
        except Exception: return None

    def health(self):
        er=self.error_rate; dr=self.drift_pct; up=self.uptime; pr=self.predict_failure_days()
        warns=[]
        if er>30: warns.append(f"High error rate: {er:.1f}%")
        if dr>10: warns.append(f"Baseline drift: {dr:.1f}%")
        if up<90: warns.append(f"Low uptime: {up:.1f}%")
        if pr is not None and pr<7: warns.append(f"Predicted failure in {pr:.1f} days")
        if er>50 or up<70: st=HealthStatus.FAILED
        elif er>30 or dr>15 or up<85: st=HealthStatus.CRITICAL
        elif er>10 or dr>8 or up<95: st=HealthStatus.DEGRADED
        else: st=HealthStatus.GOOD
        return SensorHealth(sensor=self.name, status=st,
                            error_rate_pct=round(er,2), drift_pct=round(dr,2),
                            uptime_pct=round(up,2), total_reads=self._reads,
                            failed_reads=self._failures, warnings=warns,
                            predicted_days_to_failure=pr)

class PredictiveMaintenanceService:
    def __init__(self):
        self._trackers = {"PZEM-004T": _SensorTracker("PZEM-004T", 230.0),
                          "YF-S201":   _SensorTracker("YF-S201"),
                          "MQ-4/ADS":  _SensorTracker("MQ-4/ADS")}
        self._log: List[dict] = []

    def init(self):
        logger.info("Predictive maintenance ready"); return True

    def record(self, elec, water, gas):
        self._trackers["PZEM-004T"].record(elec.voltage_v, elec.valid)
        self._trackers["YF-S201"].record(water.flow_lpm,  water.valid)
        self._trackers["MQ-4/ADS"].record(gas.voltage_v,  gas.valid)

    def report(self):
        healths = [t.health() for t in self._trackers.values()]
        for h in healths:
            if h.status in (HealthStatus.CRITICAL, HealthStatus.FAILED):
                entry = {"ts": datetime.utcnow().isoformat(), "sensor": h.sensor,
                         "status": h.status, "warnings": h.warnings}
                if not self._log or self._log[-1]["sensor"] != h.sensor:
                    self._log.append(entry)
                    logger.warning("MAINTENANCE [%s] %s %s", h.sensor, h.status, h.warnings)
        return {"sensors": [{"sensor":h.sensor,"status":h.status,
                              "error_rate_pct":h.error_rate_pct,"drift_pct":h.drift_pct,
                              "uptime_pct":h.uptime_pct,"total_reads":h.total_reads,
                              "warnings":h.warnings,
                              "predicted_days_to_failure":h.predicted_days_to_failure}
                             for h in healths],
                "maintenance_log": self._log[-20:]}

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 14 — OPTIMIZATION ALGORITHMS
# ═══════════════════════════════════════════════════════════════════════════════
_PEAK_HOURS = list(range(6,10)) + list(range(18,23))
_STANDBY_W  = 50.0
_WHO_L      = 50.0

class OptimizationService:
    def __init__(self):
        self._power_b: Dict[int,List[float]] = defaultdict(list)
        self._flow_b:  Dict[int,List[float]] = defaultdict(list)
        self._night_p: List[float] = []
        self._first_kwh = self._first_litres = None
        self._total_kwh = self._total_litres = 0.0
        self._n = 0

    def init(self):
        logger.info("Optimization ready (tariff=₹%.2f/kWh)", TARIFF_PER_KWH); return True

    def record(self, elec, water):
        if not elec.valid: return
        h = datetime.utcnow().hour
        self._power_b[h].append(elec.power_w)
        if water.valid: self._flow_b[h].append(water.flow_lpm)
        if self._first_kwh is None:
            self._first_kwh    = elec.energy_kwh
            self._first_litres = water.total_litres if water.valid else 0.0
        self._total_kwh    = elec.energy_kwh - self._first_kwh
        self._total_litres = (water.total_litres - self._first_litres) if water.valid else 0.0
        if h < 5: self._night_p.append(elec.power_w)
        if len(self._night_p) > 500: self._night_p = self._night_p[-500:]
        self._n += 1

    def _peaks(self):
        if len(self._power_b) < 3: return []
        avg = {h: sum(v)/len(v) for h,v in self._power_b.items() if v}
        if not avg: return []
        oa = sum(avg.values())/len(avg)
        return sorted(h for h,a in avg.items() if a > oa*1.3)

    def _standby(self) -> Tuple[float,float]:
        if len(self._night_p) < 10: return 0.0, 0.0
        p10 = sorted(self._night_p)[len(self._night_p)//10]
        w   = max(0.0, p10-_STANDBY_W)
        return round(w,1), round(w*24/1000*TARIFF_PER_KWH, 2)

    def _water_score(self):
        if self._total_litres <= 0: return 100.0
        return round(min(100.0, max(0.0,
            100.0-(self._total_litres/max(1,OCCUPANTS)-_WHO_L)/2.5)), 1)

    def _bill(self) -> Tuple[float,float]:
        days = self._n*10/86400
        if days < 0.01: return 0.0, 0.0
        return (round(self._total_kwh/days*30*TARIFF_PER_KWH, 2),
                round(self._total_litres/days*30/1000*WATER_COST_PER_KL, 2))

    def report_dict(self):
        peaks = self._peaks(); waste_w, waste_cost = self._standby()
        score = self._water_score(); eb, wb = self._bill()
        off = ([h for h in range(24) if h not in peaks and h not in _PEAK_HOURS] or [0,1,2])[:3]
        tips = []
        if waste_w > 100: tips.append(f"Standby waste ~{waste_w:.0f} W — switch off idle devices.")
        if score < 60:    tips.append("Water usage above WHO guideline — check for leaks.")
        if peaks:         tips.append("Shift heavy loads to off-peak to cut demand charges.")
        if self._total_kwh > 10: tips.append("Consider rooftop solar to offset daytime load.")
        if not tips:      tips.append("Usage looks efficient. Keep it up!")
        profile = [{"hour":h,"label":f"{h:02d}:00",
                    "avg_power_w": round(sum(self._power_b.get(h,[]))/len(self._power_b[h]),1)
                                   if self._power_b.get(h) else 0.0,
                    "is_peak": h in _PEAK_HOURS} for h in range(24)]
        return {"peak_hours_detected": peaks,
                "off_peak_recommendation":
                    f"Run heavy loads at {', '.join(f'{h:02d}:00' for h in off)}",
                "standby_waste_w": waste_w,
                "standby_cost_daily_inr": waste_cost,
                "water_efficiency_score": score,
                "estimated_monthly_bill_inr": eb,
                "estimated_monthly_water_inr": wb,
                "usage_profile": profile,
                "savings_tips": tips}

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 15 — DISTRIBUTED MULTI-FLAT COORDINATION
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class FlatReading:
    flat_id: str=""; timestamp: str=""; power_w: float=0.0; energy_kwh: float=0.0
    flow_lpm: float=0.0; total_litres: float=0.0; gas_alert: bool=False
    online: bool=True; last_seen: float=field(default_factory=time.monotonic)

class MultiFlatCoordinator:
    OFFLINE_SEC = 60
    def __init__(self):
        self._client=None; self._flats: Dict[str,FlatReading]={}
        self._lock=threading.Lock(); self._is_coord=False
        self._summary: Optional[dict]=None

    def init(self):
        try:
            import paho.mqtt.client as mqtt
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                       client_id=f"sm_{FLAT_ID}")
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            if MQTT_USERNAME: self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self._client.connect_async(MQTT_BROKER, MQTT_PORT, 30)
            self._client.loop_start()
            threading.Thread(target=self._elect, daemon=True, name="coord-elect").start()
            logger.info("Multi-flat ready: flat=%s building=%s", FLAT_ID, BUILDING_ID)
            return True
        except Exception as e:
            logger.error("Multi-flat init: %s", e); return False

    def _on_connect(self, client, *_):
        client.subscribe(f"smart_meter/flat/+/readings", qos=1)
        client.subscribe(f"smart_meter/{BUILDING_ID}/summary", qos=1)
        client.subscribe(f"smart_meter/{BUILDING_ID}/coordinator", qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            p = json.loads(msg.payload.decode()); t = msg.topic
            if "/readings" in t:
                fid = t.split("/")[2]
                with self._lock:
                    self._flats[fid] = FlatReading(
                        flat_id=fid, timestamp=p.get("ts",""),
                        power_w=float(p.get("power_w",0)),
                        energy_kwh=float(p.get("energy_kwh",0)),
                        flow_lpm=float(p.get("flow_lpm",0)),
                        total_litres=float(p.get("total_litres",0)),
                        gas_alert=bool(p.get("gas_alert",False)),
                        online=True, last_seen=time.monotonic())
                if self._is_coord: self._publish_summary()
            elif "/summary" in t:
                self._summary = p
            elif "/coordinator" in t:
                self._is_coord = (p.get("coordinator_id","") == FLAT_ID)
        except Exception as e: logger.debug("Multi-flat msg: %s", e)

    def _elect(self):
        time.sleep(5)
        if self._client:
            self._client.publish(f"smart_meter/{BUILDING_ID}/coordinator",
                json.dumps({"candidate":FLAT_ID,"ts":datetime.utcnow().isoformat()}),
                qos=1, retain=True)
        time.sleep(3)
        with self._lock: known = list(self._flats.keys()) + [FLAT_ID]
        elected = min(known); self._is_coord = (elected == FLAT_ID)
        if self._is_coord:
            self._client.publish(f"smart_meter/{BUILDING_ID}/coordinator",
                json.dumps({"coordinator_id":FLAT_ID,"ts":datetime.utcnow().isoformat()}),
                qos=1, retain=True)
            logger.info("This node (%s) is COORDINATOR", FLAT_ID)

    def _publish_summary(self):
        cutoff = time.monotonic()-self.OFFLINE_SEC
        with self._lock:
            for f in self._flats.values():
                if f.last_seen < cutoff: f.online = False
            active = [f for f in self._flats.values() if f.online]
        if not active: return
        s = {"building_id":BUILDING_ID,"timestamp":datetime.utcnow().isoformat(),
             "total_power_w":  round(sum(f.power_w for f in active),1),
             "total_energy_kwh":round(sum(f.energy_kwh for f in active),3),
             "total_flow_lpm": round(sum(f.flow_lpm for f in active),3),
             "active_flats":   len(active),
             "gas_alert_flats":[f.flat_id for f in active if f.gas_alert],
             "peak_flat":      max(active,key=lambda f:f.power_w).flat_id,
             "coordinator_id": FLAT_ID}
        self._client.publish(f"smart_meter/{BUILDING_ID}/summary",
                             json.dumps(s), qos=1, retain=True)

    def publish_reading(self, elec, water, gas):
        if not self._client: return
        self._client.publish(f"smart_meter/flat/{FLAT_ID}/readings",
            json.dumps({"ts":datetime.utcnow().isoformat(),"flat_id":FLAT_ID,
                        "power_w":elec.power_w,"energy_kwh":elec.energy_kwh,
                        "flow_lpm":water.flow_lpm,"total_litres":water.total_litres,
                        "gas_alert":gas.alert}), qos=1)

    def get_summary(self): return self._summary
    def get_flats(self):
        with self._lock:
            return [{"flat_id":f.flat_id,"power_w":f.power_w,
                     "flow_lpm":f.flow_lpm,"gas_alert":f.gas_alert,
                     "online":f.online} for f in self._flats.values()]
    def close(self):
        if self._client: self._client.loop_stop(); self._client.disconnect()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 16 — FLASK WEB DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Smart Meter</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0}
    header{background:#1a1d27;padding:1rem 2rem;border-bottom:1px solid #2d3045}
    header h1{font-size:1.4rem;color:#7eb6ff}
    header small{color:#888;font-size:.75rem}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;padding:1.5rem 2rem}
    .card{background:#1a1d27;border-radius:10px;padding:1.2rem;border:1px solid #2d3045}
    .card .label{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:.4rem}
    .card .value{font-size:2rem;font-weight:700}
    .card .unit{font-size:.9rem;color:#888;margin-left:.25rem}
    .card.alert-on{border-color:#ff4d4d}
    .badge{display:inline-block;font-size:.68rem;padding:2px 8px;border-radius:20px;margin-top:.4rem}
    .ok{background:#1e4d2b;color:#4caf50}.warn{background:#4d2a1e;color:#ff6b35}
    .charts{display:grid;grid-template-columns:1fr 1fr;gap:1rem;padding:0 2rem 2rem}
    @media(max-width:700px){.charts{grid-template-columns:1fr}}
    .chart-box{background:#1a1d27;border-radius:10px;padding:1.2rem;border:1px solid #2d3045}
    .chart-box h3{font-size:.85rem;color:#aaa;margin-bottom:.8rem}
    .status-bar{text-align:center;padding:.5rem;font-size:.75rem;color:#555}
  </style>
</head>
<body>
  <header>
    <h1>Smart Meter Dashboard</h1>
    <small id="last-update">Connecting…</small>
  </header>
  <div class="grid">
    <div class="card"><div class="label">Voltage</div><div><span class="value" id="v-voltage">--</span><span class="unit">V</span></div></div>
    <div class="card"><div class="label">Active Power</div><div><span class="value" id="v-power">--</span><span class="unit">W</span></div></div>
    <div class="card"><div class="label">Energy Today</div><div><span class="value" id="v-energy">--</span><span class="unit">kWh</span></div></div>
    <div class="card"><div class="label">Power Factor</div><div><span class="value" id="v-pf">--</span></div></div>
    <div class="card"><div class="label">Water Flow</div><div><span class="value" id="v-flow">--</span><span class="unit">L/min</span></div></div>
    <div class="card"><div class="label">Water Today</div><div><span class="value" id="v-litres">--</span><span class="unit">L</span></div></div>
    <div class="card" id="card-gas"><div class="label">Gas Sensor</div><div><span class="value" id="v-gas">--</span><span class="unit">%FSD</span></div><span class="badge ok" id="badge-gas">OK</span></div>
    <div class="card"><div class="label">Frequency</div><div><span class="value" id="v-freq">--</span><span class="unit">Hz</span></div></div>
  </div>
  <div class="charts">
    <div class="chart-box"><h3>Power (W)</h3><canvas id="cp" height="120"></canvas></div>
    <div class="chart-box"><h3>Water Flow (L/min)</h3><canvas id="cw" height="120"></canvas></div>
    <div class="chart-box"><h3>Voltage (V)</h3><canvas id="cv" height="120"></canvas></div>
    <div class="chart-box"><h3>Gas (%FSD)</h3><canvas id="cg" height="120"></canvas></div>
  </div>
  <div class="status-bar" id="sb">Auto-refreshes every 10 s</div>
  <script>
    function mk(id,col){const c=document.getElementById(id).getContext('2d');return new Chart(c,{type:'line',data:{labels:[],datasets:[{data:[],borderColor:col,backgroundColor:col+'22',borderWidth:2,pointRadius:2,tension:.3}]},options:{animation:false,responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#666',maxTicksLimit:8},grid:{color:'#2d3045'}},y:{ticks:{color:'#666'},grid:{color:'#2d3045'}}}}})}
    const ch={p:mk('cp','#7eb6ff'),w:mk('cw','#4fc3f7'),v:mk('cv','#aed581'),g:mk('cg','#ff8a65')};
    function push(c,l,v){c.data.labels.push(l);c.data.datasets[0].data.push(v);if(c.data.labels.length>50){c.data.labels.shift();c.data.datasets[0].data.shift();}c.update('none')}
    function set(id,v){const e=document.getElementById(id);if(e)e.textContent=v}
    async function refresh(){
      try{
        const[lat,sum]=await Promise.all([fetch('/api/latest').then(r=>r.json()),fetch('/api/summary').then(r=>r.json())]);
        if(!lat.length)return;const r=lat[0];const ts=(r.timestamp||'').substring(11,19);
        set('v-voltage',r.voltage_v?.toFixed(1)??'--');set('v-power',r.power_w?.toFixed(0)??'--');
        set('v-energy',sum.energy_today_kwh?.toFixed(3)??'--');set('v-pf',r.power_factor?.toFixed(2)??'--');
        set('v-flow',r.flow_lpm?.toFixed(2)??'--');set('v-litres',sum.water_today_litres?.toFixed(1)??'--');
        set('v-gas',r.gas_pct_fsd?.toFixed(1)??'--');set('v-freq',r.frequency_hz?.toFixed(1)??'--');
        set('last-update','Last update: '+ts+' UTC');
        const gc=document.getElementById('card-gas'),gb=document.getElementById('badge-gas');
        if(r.gas_alert){gc.classList.add('alert-on');gb.textContent='ALERT';gb.className='badge warn';}
        else{gc.classList.remove('alert-on');gb.textContent='OK';gb.className='badge ok';}
        lat.reverse().forEach(row=>{const t=(row.timestamp||'').substring(11,19);push(ch.p,t,row.power_w);push(ch.w,t,row.flow_lpm);push(ch.v,t,row.voltage_v);push(ch.g,t,row.gas_pct_fsd);});
        document.getElementById('sb').textContent='Last refresh: '+new Date().toLocaleTimeString();
      }catch(e){document.getElementById('sb').textContent='Connection error — retrying…';}
    }
    refresh();setInterval(refresh,10000);
  </script>
</body>
</html>"""

flask_app = Flask(__name__)
_db_ref:        Optional["DatabaseService"]            = None
_anomaly_ref:   Optional["AnomalyDetectionService"]    = None
_maint_ref:     Optional["PredictiveMaintenanceService"] = None
_optim_ref:     Optional["OptimizationService"]        = None
_multiflat_ref: Optional["MultiFlatCoordinator"]       = None

@flask_app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@flask_app.route("/api/latest")
def api_latest():
    return jsonify(_db_ref.latest(50) if _db_ref else [])

@flask_app.route("/api/summary")
def api_summary():
    return jsonify({
        "energy_today_kwh":    _db_ref.energy_today() if _db_ref else 0,
        "water_today_litres":  _db_ref.water_today()  if _db_ref else 0,
    })

@flask_app.route("/api/anomalies")
def api_anomalies():
    return jsonify(_anomaly_ref.summary() if _anomaly_ref else {})

@flask_app.route("/api/maintenance")
def api_maintenance():
    return jsonify(_maint_ref.report() if _maint_ref else {})

@flask_app.route("/api/optimization")
def api_optimization():
    return jsonify(_optim_ref.report_dict() if _optim_ref else {})

@flask_app.route("/api/building")
def api_building():
    return jsonify(_multiflat_ref.get_summary() or {} if _multiflat_ref else {})

@flask_app.route("/api/flats")
def api_flats():
    return jsonify(_multiflat_ref.get_flats() if _multiflat_ref else [])

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
_stop = threading.Event()

def _on_signal(sig, _):
    logger.info("Signal %d received — shutting down", sig)
    _stop.set()

def sensor_loop(elec, water, gas, db, mqtt, influx, alerts,
                anomaly, maint, optim, multiflat):
    logger.info("Sensor loop started (interval=%ds)", SAMPLE_INTERVAL_SEC)
    while not _stop.is_set():
        t0  = time.monotonic()
        now = datetime.utcnow()
        e, w, g = elec.read(), water.read(), gas.read()
        logger.info(
            "Elec: %.1fV %.0fW %.3fkWh | Water: %.2fL/min %.1fL | Gas: %d(%.1f%%)",
            e.voltage_v, e.power_w, e.energy_kwh,
            w.flow_lpm, w.total_litres, g.raw_value, g.pct_fsd,
        )
        # Core services
        db.save(e, w, g, now)
        mqtt.publish(e, w, g, now)
        influx.write(e, w, g, now)
        alerts.check(e, w, g)
        # AI anomaly detection
        found = anomaly.analyse(e, w, g)
        if found:
            logger.warning("%d anomal%s detected: %s",
                len(found), "y" if len(found)==1 else "ies",
                [(r.metric, r.severity) for r in found])
        # Predictive maintenance
        maint.record(e, w, g)
        # Optimization
        optim.record(e, w)
        # Multi-flat mesh
        multiflat.publish_reading(e, w, g)
        _stop.wait(max(0.0, SAMPLE_INTERVAL_SEC - (time.monotonic() - t0)))

def main():
    global _db_ref, _anomaly_ref, _maint_ref, _optim_ref, _multiflat_ref
    logger.info("=== Smart Meter starting ===")
    signal.signal(signal.SIGINT,  _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # Sensors
    elec   = ElectricitySensor()
    water  = WaterFlowSensor()
    gas    = GasSensor()

    # Core services
    db     = DatabaseService()
    mqtt   = MqttService()
    influx = InfluxService()
    alerts = AlertService()

    # AI / analytics services
    anomaly    = AnomalyDetectionService()
    maint      = PredictiveMaintenanceService()
    optim      = OptimizationService()
    multiflat  = MultiFlatCoordinator()

    # Expose to Flask
    _db_ref        = db
    _anomaly_ref   = anomaly
    _maint_ref     = maint
    _optim_ref     = optim
    _multiflat_ref = multiflat

    for s in (elec, water, gas, db, mqtt, influx, alerts,
              anomaly, maint, optim, multiflat):
        s.init()

    threading.Thread(
        target=sensor_loop,
        args=(elec, water, gas, db, mqtt, influx, alerts,
              anomaly, maint, optim, multiflat),
        daemon=True, name="sensor-loop",
    ).start()

    threading.Thread(
        target=lambda: flask_app.run(
            host=FLASK_HOST, port=FLASK_PORT,
            debug=False, use_reloader=False),
        daemon=True, name="dashboard",
    ).start()

    logger.info("Dashboard → http://<Pi-IP>:%d", FLASK_PORT)
    logger.info("New endpoints: /api/anomalies /api/maintenance /api/optimization /api/building /api/flats")
    _stop.wait()

    logger.info("Shutting down…")
    elec.close(); water.close(); mqtt.close(); multiflat.close()
    logger.info("Done.")

if __name__ == "__main__":
    main()
