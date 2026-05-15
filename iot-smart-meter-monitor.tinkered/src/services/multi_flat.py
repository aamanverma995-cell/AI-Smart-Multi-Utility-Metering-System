"""
services/multi_flat.py — Distributed Multi-Flat Coordination
Enables multiple smart meter units in an apartment building to share data
over MQTT, compute building-wide totals, and coordinate load balancing.

Architecture:
  - Each flat runs this service as a NODE
  - One flat is elected COORDINATOR (lowest flat_id wins)
  - Every node publishes its readings to:  smart_meter/flat/<flat_id>/readings
  - Coordinator subscribes to all flats, aggregates, publishes to:
      smart_meter/building/summary
  - Any node can query the building summary

Configuration (.env):
  FLAT_ID=flat_1          (unique ID for this unit, e.g. flat_1, flat_2 ...)
  BUILDING_ID=building_A  (shared across all units in the building)
  MQTT_BROKER=<shared broker IP>
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

import os
FLAT_ID     = os.getenv("FLAT_ID",     "flat_1")
BUILDING_ID = os.getenv("BUILDING_ID", "building_A")


@dataclass
class FlatReading:
    flat_id:      str   = ""
    timestamp:    str   = ""
    power_w:      float = 0.0
    energy_kwh:   float = 0.0
    flow_lpm:     float = 0.0
    total_litres: float = 0.0
    gas_alert:    bool  = False
    online:       bool  = True
    last_seen:    float = field(default_factory=time.monotonic)


@dataclass
class BuildingSummary:
    building_id:       str        = ""
    timestamp:         str        = ""
    total_power_w:     float      = 0.0
    total_energy_kwh:  float      = 0.0
    total_flow_lpm:    float      = 0.0
    total_litres:      float      = 0.0
    active_flats:      int        = 0
    gas_alert_flats:   List[str]  = field(default_factory=list)
    peak_flat:         str        = ""    # flat consuming the most power
    coordinator_id:    str        = ""


class MultiFlatCoordinator:
    """
    MQTT-based multi-flat coordination service.
    Each instance represents one flat in the building.
    """

    OFFLINE_TIMEOUT_SEC = 60   # mark flat offline if no update for this long

    def __init__(self):
        self._client        = None
        self._flats:  Dict[str, FlatReading] = {}
        self._lock          = threading.Lock()
        self._is_coordinator = False
        self._summary:  Optional[BuildingSummary] = None
        self._callbacks = []   # list of callables(summary) for local consumers

    # ── MQTT topic helpers ────────────────────────────────────────────────────
    @staticmethod
    def _my_topic() -> str:
        return f"smart_meter/flat/{FLAT_ID}/readings"

    @staticmethod
    def _all_flats_topic() -> str:
        return f"smart_meter/flat/+/readings"

    @staticmethod
    def _building_topic() -> str:
        return f"smart_meter/{BUILDING_ID}/summary"

    @staticmethod
    def _coordinator_topic() -> str:
        return f"smart_meter/{BUILDING_ID}/coordinator"

    # ── Init ─────────────────────────────────────────────────────────────────
    def init(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"smart_meter_{FLAT_ID}",
            )
            self._client.on_connect    = self._on_connect
            self._client.on_message    = self._on_message
            self._client.on_disconnect = self._on_disconnect

            broker = os.getenv("MQTT_BROKER", "localhost")
            port   = int(os.getenv("MQTT_PORT", "1883"))
            user   = os.getenv("MQTT_USERNAME", "")
            pwd    = os.getenv("MQTT_PASSWORD", "")
            if user:
                self._client.username_pw_set(user, pwd)

            self._client.connect_async(broker, port, keepalive=30)
            self._client.loop_start()

            # Announce presence and attempt coordinator election
            threading.Thread(
                target=self._elect_coordinator,
                daemon=True, name="coordinator-election",
            ).start()

            logger.info(
                "Multi-flat service started: flat=%s building=%s broker=%s",
                FLAT_ID, BUILDING_ID, broker,
            )
            return True
        except Exception as exc:
            logger.error("Multi-flat init error: %s", exc)
            return False

    # ── MQTT callbacks ────────────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.warning("Multi-flat MQTT connect failed rc=%d", rc)
            return
        # Subscribe to all flat readings and building summary
        client.subscribe(self._all_flats_topic(),   qos=1)
        client.subscribe(self._building_topic(),    qos=1)
        client.subscribe(self._coordinator_topic(), qos=1)
        logger.info("Multi-flat MQTT connected, flat=%s", FLAT_ID)

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, properties=None):
        logger.info("Multi-flat MQTT disconnected rc=%d", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic   = msg.topic

            if "/readings" in topic:
                # Extract flat_id from topic: smart_meter/flat/<id>/readings
                parts   = topic.split("/")
                flat_id = parts[2] if len(parts) >= 3 else "unknown"
                self._update_flat(flat_id, payload)

                # If we are coordinator, recompute building summary
                if self._is_coordinator:
                    self._publish_building_summary()

            elif "/summary" in topic:
                self._summary = BuildingSummary(**{
                    k: v for k, v in payload.items()
                    if k in BuildingSummary.__dataclass_fields__
                })
                for cb in self._callbacks:
                    try:
                        cb(self._summary)
                    except Exception:
                        pass

            elif "/coordinator" in topic:
                coord_id = payload.get("coordinator_id", "")
                self._is_coordinator = (coord_id == FLAT_ID)
                logger.info(
                    "Coordinator: %s (me=%s, is_coord=%s)",
                    coord_id, FLAT_ID, self._is_coordinator,
                )
        except Exception as exc:
            logger.debug("Multi-flat message parse error: %s", exc)

    # ── Coordinator election (lowest flat_id string wins) ────────────────────
    def _elect_coordinator(self):
        time.sleep(5)   # wait for other nodes to announce
        # Publish own candidacy
        if self._client:
            self._client.publish(
                self._coordinator_topic(),
                json.dumps({"candidate": FLAT_ID, "ts": datetime.utcnow().isoformat()}),
                qos=1, retain=True,
            )
        time.sleep(3)
        # If no other flat has a lexicographically lower ID, we become coordinator
        with self._lock:
            known = list(self._flats.keys()) + [FLAT_ID]
        elected = min(known)
        self._is_coordinator = (elected == FLAT_ID)
        if self._is_coordinator:
            self._client.publish(
                self._coordinator_topic(),
                json.dumps({
                    "coordinator_id": FLAT_ID,
                    "ts": datetime.utcnow().isoformat(),
                }),
                qos=1, retain=True,
            )
            logger.info("This node (%s) is now COORDINATOR", FLAT_ID)

    # ── Flat state management ─────────────────────────────────────────────────
    def _update_flat(self, flat_id: str, payload: dict):
        with self._lock:
            self._flats[flat_id] = FlatReading(
                flat_id=flat_id,
                timestamp=payload.get("ts", ""),
                power_w=float(payload.get("power_w", 0)),
                energy_kwh=float(payload.get("energy_kwh", 0)),
                flow_lpm=float(payload.get("flow_lpm", 0)),
                total_litres=float(payload.get("total_litres", 0)),
                gas_alert=bool(payload.get("gas_alert", False)),
                online=True,
                last_seen=time.monotonic(),
            )

    def _mark_offline(self):
        cutoff = time.monotonic() - self.OFFLINE_TIMEOUT_SEC
        with self._lock:
            for fr in self._flats.values():
                if fr.last_seen < cutoff:
                    fr.online = False

    # ── Building-level aggregation ────────────────────────────────────────────
    def _publish_building_summary(self):
        self._mark_offline()
        with self._lock:
            active = [f for f in self._flats.values() if f.online]

        if not active:
            return

        total_power  = sum(f.power_w      for f in active)
        total_energy = sum(f.energy_kwh   for f in active)
        total_flow   = sum(f.flow_lpm     for f in active)
        total_litres = sum(f.total_litres for f in active)
        gas_flats    = [f.flat_id for f in active if f.gas_alert]
        peak_flat    = max(active, key=lambda f: f.power_w).flat_id if active else ""

        summary = {
            "building_id":      BUILDING_ID,
            "timestamp":        datetime.utcnow().isoformat(),
            "total_power_w":    round(total_power,  1),
            "total_energy_kwh": round(total_energy, 3),
            "total_flow_lpm":   round(total_flow,   3),
            "total_litres":     round(total_litres, 1),
            "active_flats":     len(active),
            "gas_alert_flats":  gas_flats,
            "peak_flat":        peak_flat,
            "coordinator_id":   FLAT_ID,
        }
        self._client.publish(
            self._building_topic(),
            json.dumps(summary),
            qos=1, retain=True,
        )
        if gas_flats:
            logger.warning("GAS ALERT in flats: %s", gas_flats)

    # ── Public API ────────────────────────────────────────────────────────────
    def publish_reading(self, elec, water, gas):
        """Publish this flat's reading to the MQTT mesh."""
        if not self._client:
            return
        payload = json.dumps({
            "ts":          datetime.utcnow().isoformat(),
            "flat_id":     FLAT_ID,
            "power_w":     elec.power_w,
            "energy_kwh":  elec.energy_kwh,
            "flow_lpm":    water.flow_lpm,
            "total_litres": water.total_litres,
            "gas_alert":   gas.alert,
        })
        self._client.publish(self._my_topic(), payload, qos=1)

    def get_building_summary(self) -> Optional[dict]:
        """Return the latest building-wide summary."""
        if self._summary is None:
            return None
        s = self._summary
        return {
            "building_id":      s.building_id,
            "timestamp":        s.timestamp,
            "total_power_w":    s.total_power_w,
            "total_energy_kwh": s.total_energy_kwh,
            "total_flow_lpm":   s.total_flow_lpm,
            "total_litres":     s.total_litres,
            "active_flats":     s.active_flats,
            "gas_alert_flats":  s.gas_alert_flats,
            "peak_flat":        s.peak_flat,
            "coordinator_id":   s.coordinator_id,
            "is_coordinator":   self._is_coordinator,
        }

    def get_all_flats(self) -> List[dict]:
        """Return health snapshot for every known flat."""
        self._mark_offline()
        with self._lock:
            return [
                {
                    "flat_id":      f.flat_id,
                    "power_w":      f.power_w,
                    "flow_lpm":     f.flow_lpm,
                    "gas_alert":    f.gas_alert,
                    "online":       f.online,
                    "last_seen_s":  round(time.monotonic() - f.last_seen, 0),
                }
                for f in self._flats.values()
            ]

    def on_summary_update(self, callback):
        """Register a callback(BuildingSummary) called on every new summary."""
        self._callbacks.append(callback)

    def close(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
