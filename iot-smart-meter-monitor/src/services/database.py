"""
services/database.py — SQLite persistence service
Stores every sensor reading in a local database.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,

    -- Electricity
    voltage_v     REAL,
    current_a     REAL,
    power_w       REAL,
    energy_kwh    REAL,
    frequency_hz  REAL,
    power_factor  REAL,
    elec_alarm    INTEGER,

    -- Water flow
    flow_lpm      REAL,
    total_litres  REAL,

    -- Gas
    gas_raw       INTEGER,
    gas_voltage   REAL,
    gas_pct_fsd   REAL,
    gas_alert     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_timestamp ON readings(timestamp);
"""


class DatabaseService:
    def __init__(self, db_path: str = config.DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    def init(self) -> bool:
        try:
            with self._connect() as conn:
                conn.executescript(CREATE_TABLE_SQL)
            logger.info("SQLite database ready at %s", self._db_path)
            return True
        except sqlite3.Error as exc:
            logger.error("DB init failed: %s", exc)
            return False

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_reading(
        self,
        elec,
        water,
        gas,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """Insert one row with all sensor values."""
        ts = (timestamp or datetime.utcnow()).isoformat()
        sql = """
        INSERT INTO readings (
            timestamp, voltage_v, current_a, power_w, energy_kwh,
            frequency_hz, power_factor, elec_alarm,
            flow_lpm, total_litres,
            gas_raw, gas_voltage, gas_pct_fsd, gas_alert
        ) VALUES (
            :ts, :voltage_v, :current_a, :power_w, :energy_kwh,
            :frequency_hz, :power_factor, :elec_alarm,
            :flow_lpm, :total_litres,
            :gas_raw, :gas_voltage, :gas_pct_fsd, :gas_alert
        )
        """
        params = dict(
            ts=ts,
            voltage_v=elec.voltage_v,
            current_a=elec.current_a,
            power_w=elec.power_w,
            energy_kwh=elec.energy_kwh,
            frequency_hz=elec.frequency_hz,
            power_factor=elec.power_factor,
            elec_alarm=int(elec.alarm),
            flow_lpm=water.flow_lpm,
            total_litres=water.total_litres,
            gas_raw=gas.raw_value,
            gas_voltage=gas.voltage_v,
            gas_pct_fsd=gas.pct_fsd,
            gas_alert=int(gas.alert),
        )
        try:
            with self._connect() as conn:
                conn.execute(sql, params)
            return True
        except sqlite3.Error as exc:
            logger.error("DB write error: %s", exc)
            return False

    def get_latest(self, limit: int = 50) -> List[dict]:
        """Return the N most recent readings as a list of dicts."""
        sql = "SELECT * FROM readings ORDER BY id DESC LIMIT ?"
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            logger.error("DB read error: %s", exc)
            return []

    def get_energy_today(self) -> float:
        """Return the kWh consumed since midnight UTC."""
        sql = """
        SELECT MAX(energy_kwh) - MIN(energy_kwh) AS delta
        FROM readings
        WHERE timestamp >= date('now')
        """
        try:
            with self._connect() as conn:
                row = conn.execute(sql).fetchone()
            delta = row["delta"]
            return round(delta, 4) if delta is not None else 0.0
        except sqlite3.Error as exc:
            logger.error("DB energy_today error: %s", exc)
            return 0.0

    def get_water_today(self) -> float:
        """Return litres consumed since midnight UTC."""
        sql = """
        SELECT MAX(total_litres) - MIN(total_litres) AS delta
        FROM readings
        WHERE timestamp >= date('now')
        """
        try:
            with self._connect() as conn:
                row = conn.execute(sql).fetchone()
            delta = row["delta"]
            return round(delta, 3) if delta is not None else 0.0
        except sqlite3.Error as exc:
            logger.error("DB water_today error: %s", exc)
            return 0.0
