"""
services/anomaly_detection.py — AI Anomaly Detection
Uses Z-score for real-time detection and IsolationForest for batch analysis.
Detects anomalies in electricity, water flow, and gas readings.
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Rolling window size for Z-score baseline
WINDOW_SIZE = 100
# Z-score threshold — readings beyond this are anomalies
Z_THRESHOLD = 3.0
# Minimum samples before anomaly detection kicks in
MIN_SAMPLES = 20


@dataclass
class AnomalyResult:
    metric:      str   = ""
    value:       float = 0.0
    z_score:     float = 0.0
    mean:        float = 0.0
    std:         float = 0.0
    is_anomaly:  bool  = False
    severity:    str   = "normal"   # normal / warning / critical
    message:     str   = ""


class RollingStats:
    """Welford online algorithm for mean and variance."""

    def __init__(self, maxlen: int = WINDOW_SIZE):
        self._buf    = deque(maxlen=maxlen)
        self._mean   = 0.0
        self._M2     = 0.0
        self._count  = 0

    def push(self, x: float):
        self._buf.append(x)
        self._count += 1
        delta       = x - self._mean
        self._mean += delta / self._count
        self._M2   += delta * (x - self._mean)

    @property
    def mean(self) -> float:
        return self._mean

    @property
    def std(self) -> float:
        if self._count < 2:
            return 0.0
        return math.sqrt(self._M2 / (self._count - 1))

    @property
    def count(self) -> int:
        return self._count

    def z_score(self, x: float) -> float:
        s = self.std
        if s == 0:
            return 0.0
        return abs((x - self.mean) / s)


class AnomalyDetectionService:
    """
    Real-time Z-score anomaly detection for all three utility metrics.
    Optionally runs IsolationForest on accumulated history every N samples.
    """

    def __init__(self, z_threshold: float = Z_THRESHOLD):
        self._z_threshold = z_threshold
        self._stats: Dict[str, RollingStats] = {
            "voltage_v":    RollingStats(),
            "power_w":      RollingStats(),
            "current_a":    RollingStats(),
            "flow_lpm":     RollingStats(),
            "gas_pct_fsd":  RollingStats(),
        }
        self._history: Dict[str, List[float]] = {k: [] for k in self._stats}
        self._anomaly_count = 0
        self._forest_ready  = False
        self._forest        = None
        self._sample_count  = 0

    def init(self) -> bool:
        logger.info("Anomaly detection service ready (Z-threshold=%.1f)", self._z_threshold)
        try:
            from sklearn.ensemble import IsolationForest  # noqa: F401
            logger.info("IsolationForest available — batch analysis enabled")
        except ImportError:
            logger.warning(
                "scikit-learn not installed — IsolationForest disabled. "
                "Run: pip install scikit-learn"
            )
        return True

    def _classify(self, z: float) -> str:
        if z < self._z_threshold:
            return "normal"
        if z < self._z_threshold * 1.5:
            return "warning"
        return "critical"

    def _check(self, key: str, value: float) -> Optional[AnomalyResult]:
        stats = self._stats[key]
        self._history[key].append(value)
        if len(self._history[key]) > 500:
            self._history[key] = self._history[key][-500:]

        if stats.count < MIN_SAMPLES:
            stats.push(value)
            return None

        z        = stats.z_score(value)
        severity = self._classify(z)
        result   = AnomalyResult(
            metric=key, value=value, z_score=round(z, 3),
            mean=round(stats.mean, 3), std=round(stats.std, 3),
            is_anomaly=(severity != "normal"), severity=severity,
            message=(
                f"{key} anomaly: value={value:.2f}, "
                f"expected={stats.mean:.2f}±{stats.std:.2f}, Z={z:.2f}"
                if severity != "normal" else ""
            ),
        )
        stats.push(value)
        if result.is_anomaly:
            self._anomaly_count += 1
            logger.warning("ANOMALY [%s] Z=%.2f severity=%s value=%.3f",
                           key, z, severity, value)
        return result

    def analyse(self, elec, water, gas) -> List[AnomalyResult]:
        """
        Run Z-score check on every metric.
        Returns list of anomalies found (empty list = all normal).
        """
        self._sample_count += 1
        checks = [
            ("voltage_v",   elec.voltage_v),
            ("power_w",     elec.power_w),
            ("current_a",   elec.current_a),
            ("flow_lpm",    water.flow_lpm),
            ("gas_pct_fsd", gas.pct_fsd),
        ]
        anomalies = []
        for key, val in checks:
            r = self._check(key, val)
            if r and r.is_anomaly:
                anomalies.append(r)

        # Run IsolationForest every 200 samples once we have enough data
        if self._sample_count % 200 == 0 and self._sample_count > 200:
            self._run_isolation_forest()

        return anomalies

    def _run_isolation_forest(self):
        """Batch analysis using IsolationForest on recent history."""
        try:
            from sklearn.ensemble import IsolationForest
            import numpy as np

            # Build feature matrix: [power_w, flow_lpm, gas_pct_fsd]
            pw  = self._history.get("power_w",    [])
            fl  = self._history.get("flow_lpm",   [])
            gs  = self._history.get("gas_pct_fsd",[])
            n   = min(len(pw), len(fl), len(gs), 200)
            if n < 50:
                return

            X = np.column_stack([pw[-n:], fl[-n:], gs[-n:]])
            clf = IsolationForest(contamination=0.05, random_state=42)
            labels = clf.fit_predict(X)
            n_outliers = int((labels == -1).sum())
            logger.info(
                "IsolationForest batch: %d samples, %d outliers (%.1f%%)",
                n, n_outliers, 100 * n_outliers / n,
            )
            self._forest = clf
            self._forest_ready = True
        except Exception as exc:
            logger.debug("IsolationForest skipped: %s", exc)

    def predict_next_anomaly(self, elec, water, gas) -> bool:
        """
        Use the trained IsolationForest to predict if current reading is an outlier.
        Returns True if the model flags it as anomalous.
        """
        if not self._forest_ready or self._forest is None:
            return False
        try:
            import numpy as np
            X = np.array([[elec.power_w, water.flow_lpm, gas.pct_fsd]])
            return bool(self._forest.predict(X)[0] == -1)
        except Exception:
            return False

    @property
    def total_anomalies(self) -> int:
        return self._anomaly_count

    def summary(self) -> dict:
        return {
            "total_anomalies": self._anomaly_count,
            "samples_analysed": self._sample_count,
            "forest_ready": self._forest_ready,
            "metrics": {
                k: {"mean": round(v.mean, 3), "std": round(v.std, 3), "n": v.count}
                for k, v in self._stats.items()
            },
        }
