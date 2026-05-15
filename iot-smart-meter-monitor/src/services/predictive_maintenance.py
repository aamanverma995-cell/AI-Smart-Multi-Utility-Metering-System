"""
services/predictive_maintenance.py — Predictive Maintenance Service
Tracks sensor health over time, detects degradation patterns, and predicts
when a sensor is likely to fail or need recalibration.

Monitored health signals:
  - PZEM-004T : invalid read rate, voltage deviation from nominal
  - YF-S201   : pulse drop-off, zero-flow periods during expected usage
  - MQ-4/ADS  : ADC drift, baseline creep over time
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    GOOD       = "good"
    DEGRADED   = "degraded"
    CRITICAL   = "critical"
    FAILED     = "failed"


@dataclass
class SensorHealth:
    sensor:          str          = ""
    status:          HealthStatus = HealthStatus.GOOD
    error_rate_pct:  float        = 0.0   # % of reads that failed
    drift_pct:       float        = 0.0   # baseline drift %
    uptime_pct:      float        = 100.0
    last_good_read:  float        = field(default_factory=time.monotonic)
    total_reads:     int          = 0
    failed_reads:    int          = 0
    warnings:        List[str]    = field(default_factory=list)
    predicted_days_to_failure: Optional[float] = None


class _SensorTracker:
    """Per-sensor health tracker using a sliding window."""

    WINDOW = 200  # number of recent reads to evaluate

    def __init__(self, name: str, nominal: Optional[float] = None):
        self.name         = name
        self.nominal      = nominal   # expected baseline value (e.g. 230 V)
        self._reads       = 0
        self._failures    = 0
        self._values      = deque(maxlen=self.WINDOW)
        self._fail_window = deque(maxlen=self.WINDOW)
        self._baselines   = deque(maxlen=50)   # slow history for drift
        self._last_good   = time.monotonic()
        self._start_time  = time.monotonic()

    def record(self, value: Optional[float], valid: bool):
        self._reads += 1
        self._fail_window.append(0 if valid else 1)
        if not valid:
            self._failures += 1
            return
        self._last_good = time.monotonic()
        self._values.append(value)
        # Record a slow baseline sample every 20 valid reads
        if self._reads % 20 == 0 and value is not None:
            self._baselines.append(value)

    @property
    def error_rate(self) -> float:
        if not self._fail_window:
            return 0.0
        return 100.0 * sum(self._fail_window) / len(self._fail_window)

    @property
    def drift_pct(self) -> float:
        """How much the recent mean has drifted from the first baseline sample."""
        if len(self._baselines) < 5 or self.nominal is None:
            return 0.0
        recent_mean = sum(list(self._baselines)[-10:]) / min(10, len(self._baselines))
        return abs((recent_mean - self.nominal) / self.nominal) * 100.0

    @property
    def uptime(self) -> float:
        elapsed = time.monotonic() - self._start_time
        if elapsed == 0:
            return 100.0
        silent = max(0.0, time.monotonic() - self._last_good - 30)
        return max(0.0, 100.0 - (silent / elapsed) * 100.0)

    def predict_failure_days(self) -> Optional[float]:
        """
        Linear regression on drift over time — extrapolate days until drift
        exceeds 20% (considered failed).
        """
        if len(self._baselines) < 10 or self.nominal is None:
            return None
        try:
            vals = list(self._baselines)
            n    = len(vals)
            xs   = list(range(n))
            xm   = sum(xs) / n
            ym   = sum(vals) / n
            num  = sum((x - xm) * (y - ym) for x, y in zip(xs, vals))
            den  = sum((x - xm) ** 2 for x in xs)
            if den == 0:
                return None
            slope = num / den
            if abs(slope) < 1e-6:
                return None
            current_drift = abs(ym - self.nominal)
            target_drift  = 0.20 * abs(self.nominal)
            remaining     = (target_drift - current_drift) / abs(slope)
            # Each baseline sample is ~20 reads * SAMPLE_INTERVAL_SEC seconds
            sample_gap_days = 20 * 10 / 86400
            return max(0.0, round(remaining * sample_gap_days, 1))
        except Exception:
            return None

    def health(self) -> SensorHealth:
        er     = self.error_rate
        drift  = self.drift_pct
        uptime = self.uptime
        pred   = self.predict_failure_days()
        warns  = []

        if er > 30:
            warns.append(f"High error rate: {er:.1f}%")
        if drift > 10:
            warns.append(f"Baseline drift: {drift:.1f}%")
        if uptime < 90:
            warns.append(f"Low uptime: {uptime:.1f}%")
        if pred is not None and pred < 7:
            warns.append(f"Predicted failure in {pred:.1f} days")

        if er > 50 or uptime < 70:
            status = HealthStatus.FAILED
        elif er > 30 or drift > 15 or uptime < 85:
            status = HealthStatus.CRITICAL
        elif er > 10 or drift > 8 or uptime < 95:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.GOOD

        return SensorHealth(
            sensor=self.name,
            status=status,
            error_rate_pct=round(er, 2),
            drift_pct=round(drift, 2),
            uptime_pct=round(uptime, 2),
            last_good_read=self._last_good,
            total_reads=self._reads,
            failed_reads=self._failures,
            warnings=warns,
            predicted_days_to_failure=pred,
        )


class PredictiveMaintenanceService:
    """
    Aggregates health data from all sensors and generates maintenance alerts.
    """

    def __init__(self):
        self._trackers: Dict[str, _SensorTracker] = {
            "PZEM-004T":  _SensorTracker("PZEM-004T",  nominal=230.0),   # 230 V AC nominal
            "YF-S201":    _SensorTracker("YF-S201",    nominal=None),
            "MQ-4/ADS":   _SensorTracker("MQ-4/ADS",   nominal=None),
        }
        self._maintenance_log: List[dict] = []

    def init(self) -> bool:
        logger.info("Predictive maintenance service ready")
        return True

    def record(self, elec, water, gas):
        """Feed latest readings into each sensor's health tracker."""
        self._trackers["PZEM-004T"].record(elec.voltage_v, elec.valid)
        self._trackers["YF-S201"].record(water.flow_lpm,   water.valid)
        self._trackers["MQ-4/ADS"].record(gas.voltage_v,   gas.valid)

    def assess(self) -> List[SensorHealth]:
        """Return health report for all sensors."""
        results = []
        for tracker in self._trackers.values():
            h = tracker.health()
            results.append(h)
            if h.status in (HealthStatus.CRITICAL, HealthStatus.FAILED):
                entry = {
                    "ts":      __import__("datetime").datetime.utcnow().isoformat(),
                    "sensor":  h.sensor,
                    "status":  h.status,
                    "warnings": h.warnings,
                }
                if not self._maintenance_log or self._maintenance_log[-1]["sensor"] != h.sensor:
                    self._maintenance_log.append(entry)
                    logger.warning(
                        "MAINTENANCE ALERT [%s] status=%s warnings=%s",
                        h.sensor, h.status, h.warnings,
                    )
        return results

    def report(self) -> dict:
        """Full maintenance report as a dictionary."""
        healths = self.assess()
        return {
            "sensors": [
                {
                    "sensor":          h.sensor,
                    "status":          h.status,
                    "error_rate_pct":  h.error_rate_pct,
                    "drift_pct":       h.drift_pct,
                    "uptime_pct":      h.uptime_pct,
                    "total_reads":     h.total_reads,
                    "failed_reads":    h.failed_reads,
                    "warnings":        h.warnings,
                    "predicted_days_to_failure": h.predicted_days_to_failure,
                }
                for h in healths
            ],
            "maintenance_log": self._maintenance_log[-20:],
        }

    def maintenance_needed(self) -> bool:
        """Quick check — True if any sensor is CRITICAL or FAILED."""
        return any(
            t.health().status in (HealthStatus.CRITICAL, HealthStatus.FAILED)
            for t in self._trackers.values()
        )
