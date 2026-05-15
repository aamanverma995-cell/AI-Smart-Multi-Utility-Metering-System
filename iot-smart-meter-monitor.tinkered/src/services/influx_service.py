"""
services/influx_service.py — InfluxDB Cloud writer
Sends line-protocol measurements to your InfluxDB Cloud bucket.

Setup:
  1. Create a free account at https://cloud2.influxdata.com
  2. Create a bucket named 'smart_meter'
  3. Generate an All-Access API token
  4. Set INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET in .env
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


class InfluxService:
    def __init__(self):
        self._write_api = None

    def init(self) -> bool:
        if not config.INFLUX_ENABLED:
            logger.info("InfluxDB disabled in config")
            return True
        try:
            from influxdb_client import InfluxDBClient, WriteOptions
            from influxdb_client.client.write_api import SYNCHRONOUS

            client = InfluxDBClient(
                url=config.INFLUX_URL,
                token=config.INFLUX_TOKEN,
                org=config.INFLUX_ORG,
            )
            self._write_api = client.write_api(write_options=SYNCHRONOUS)
            logger.info("InfluxDB client ready → %s / %s", config.INFLUX_URL, config.INFLUX_BUCKET)
            return True
        except Exception as exc:
            logger.error("InfluxDB init error: %s", exc)
            return False

    def write(self, elec, water, gas, timestamp: Optional[datetime] = None) -> bool:
        if not config.INFLUX_ENABLED or self._write_api is None:
            return True
        ts = (timestamp or datetime.now(timezone.utc))
        try:
            from influxdb_client import Point
            points = [
                Point("electricity")
                    .field("voltage_v",    elec.voltage_v)
                    .field("current_a",    elec.current_a)
                    .field("power_w",      elec.power_w)
                    .field("energy_kwh",   elec.energy_kwh)
                    .field("frequency_hz", elec.frequency_hz)
                    .field("power_factor", elec.power_factor)
                    .field("alarm",        int(elec.alarm))
                    .time(ts),
                Point("water")
                    .field("flow_lpm",     water.flow_lpm)
                    .field("total_litres", water.total_litres)
                    .time(ts),
                Point("gas")
                    .field("raw_value",    gas.raw_value)
                    .field("voltage_v",    gas.voltage_v)
                    .field("pct_fsd",      gas.pct_fsd)
                    .field("alert",        int(gas.alert))
                    .time(ts),
            ]
            self._write_api.write(bucket=config.INFLUX_BUCKET, record=points)
            return True
        except Exception as exc:
            logger.error("InfluxDB write error: %s", exc)
            return False
