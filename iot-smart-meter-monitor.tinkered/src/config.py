"""
config.py — Smart Meter configuration
Copy .env.example to .env and fill in your values.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Hardware ───────────────────────────────────────────────────────────────────

# PZEM-004T UART port (check with: ls /dev/ttyUSB* or /dev/ttyAMA*)
PZEM_PORT         = os.getenv("PZEM_PORT", "/dev/ttyUSB0")
PZEM_BAUD         = int(os.getenv("PZEM_BAUD", "9600"))

# YF-S201 water flow sensor GPIO pin (BCM numbering)
WATER_FLOW_GPIO   = int(os.getenv("WATER_FLOW_GPIO", "17"))
# YF-S201: 7.5 pulses per litre (calibration factor)
WATER_PULSES_PER_LITRE = float(os.getenv("WATER_PULSES_PER_LITRE", "7.5"))

# ADS1115 I2C ADC (for MQ-4 gas sensor)
ADS1115_I2C_ADDR  = int(os.getenv("ADS1115_I2C_ADDR", "0x48"), 16)
ADS1115_GAIN      = float(os.getenv("ADS1115_GAIN", "1"))   # ±4.096 V range
GAS_CHANNEL       = int(os.getenv("GAS_CHANNEL", "0"))       # A0 on ADS1115
# MQ-4 threshold in raw ADC counts (tune after calibration)
GAS_ALERT_THRESHOLD = int(os.getenv("GAS_ALERT_THRESHOLD", "20000"))

# ── Sampling ───────────────────────────────────────────────────────────────────
SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "10"))

# ── SQLite ─────────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "data/smart_meter.db")

# ── MQTT ───────────────────────────────────────────────────────────────────────
MQTT_ENABLED  = os.getenv("MQTT_ENABLED", "true").lower() == "true"
MQTT_BROKER   = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC    = os.getenv("MQTT_TOPIC", "smart_meter/readings")

# ── InfluxDB Cloud ─────────────────────────────────────────────────────────────
INFLUX_ENABLED = os.getenv("INFLUX_ENABLED", "false").lower() == "true"
INFLUX_URL     = os.getenv("INFLUX_URL", "https://us-east-1-1.aws.cloud2.influxdata.com")
INFLUX_TOKEN   = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG     = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET  = os.getenv("INFLUX_BUCKET", "smart_meter")

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_ENABLED  = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Alert thresholds
ELECTRICITY_POWER_LIMIT_W  = float(os.getenv("ELECTRICITY_POWER_LIMIT_W", "3000"))
WATER_FLOW_LIMIT_LPM       = float(os.getenv("WATER_FLOW_LIMIT_LPM", "20"))
# Alert cooldown — don't repeat same alert within this many seconds
ALERT_COOLDOWN_SEC         = int(os.getenv("ALERT_COOLDOWN_SEC", "300"))

# ── Flask Dashboard ────────────────────────────────────────────────────────────
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
