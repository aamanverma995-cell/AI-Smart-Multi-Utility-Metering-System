"""
services/mqtt_service.py — Paho MQTT publish service
Publishes a JSON payload to the configured topic on every reading cycle.

Payload example:
{
  "ts": "2024-01-15T10:30:00",
  "voltage_v": 230.5, "current_a": 2.1, "power_w": 480.0,
  "energy_kwh": 12.34, "frequency_hz": 50.0, "power_factor": 0.98,
  "flow_lpm": 3.2, "total_litres": 156.7,
  "gas_raw": 4800, "gas_voltage": 0.596, "gas_pct_fsd": 14.65,
  "gas_alert": false
}
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


class MqttService:
    def __init__(self):
        self._client: Optional[mqtt.Client] = None
        self._connected = False

    def init(self) -> bool:
        if not config.MQTT_ENABLED:
            logger.info("MQTT disabled in config")
            return True
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self._client.on_connect    = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            if config.MQTT_USERNAME:
                self._client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
            self._client.connect_async(config.MQTT_BROKER, config.MQTT_PORT, keepalive=60)
            self._client.loop_start()
            # Wait up to 5 s for connection
            deadline = time.monotonic() + 5
            while not self._connected and time.monotonic() < deadline:
                time.sleep(0.1)
            if not self._connected:
                logger.warning("MQTT broker not reachable within 5 s — will retry on publish")
            return True
        except Exception as exc:
            logger.error("MQTT init error: %s", exc)
            return False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            logger.info("MQTT connected to %s:%d", config.MQTT_BROKER, config.MQTT_PORT)
        else:
            logger.warning("MQTT connect failed, rc=%d", rc)

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, properties=None):
        self._connected = False
        logger.info("MQTT disconnected (rc=%d)", rc)

    def publish(self, elec, water, gas, timestamp: Optional[datetime] = None) -> bool:
        if not config.MQTT_ENABLED or self._client is None:
            return True
        ts = (timestamp or datetime.utcnow()).isoformat()
        payload = json.dumps({
            "ts":           ts,
            "voltage_v":    elec.voltage_v,
            "current_a":    elec.current_a,
            "power_w":      elec.power_w,
            "energy_kwh":   elec.energy_kwh,
            "frequency_hz": elec.frequency_hz,
            "power_factor": elec.power_factor,
            "elec_alarm":   elec.alarm,
            "flow_lpm":     water.flow_lpm,
            "total_litres": water.total_litres,
            "gas_raw":      gas.raw_value,
            "gas_voltage":  gas.voltage_v,
            "gas_pct_fsd":  gas.pct_fsd,
            "gas_alert":    gas.alert,
        })
        result = self._client.publish(config.MQTT_TOPIC, payload, qos=1)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("MQTT publish failed, rc=%d", result.rc)
            return False
        return True

    def close(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
