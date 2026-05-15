"""
services/optimization.py — Smart Energy & Water Optimization Algorithms

Algorithms included:
  1. Peak Load Shifting     — detect peak hours, suggest off-peak scheduling
  2. Usage Pattern Mining   — daily/weekly usage profiles via time-bucketed averages
  3. Waste Detection        — identify standby waste and always-on loads
  4. Water Efficiency Score — benchmark daily water usage
  5. Cost Estimator         — calculate electricity bill from kWh and tariff rate
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Indian electricity tariff defaults (₹/kWh) — override in .env
DEFAULT_TARIFF_PER_KWH = 6.50   # ₹ per kWh (average domestic, India)
DEFAULT_WATER_COST_PER_KL = 15.0  # ₹ per 1000 litres

# Peak hours (24h format) — typical India domestic peak
PEAK_HOURS = list(range(6, 10)) + list(range(18, 23))   # 6–9 AM, 6–11 PM

# Minimum standby power threshold (W)
STANDBY_THRESHOLD_W = 50.0

# Daily water benchmark (litres per person per day — WHO guideline)
WHO_WATER_PER_PERSON_L = 50.0


@dataclass
class HourlyBucket:
    hour:        int   = 0
    avg_power_w: float = 0.0
    avg_flow_lpm: float = 0.0
    sample_count: int  = 0
    total_kwh:    float = 0.0


@dataclass
class OptimizationReport:
    peak_hours_detected:     List[int]  = field(default_factory=list)
    off_peak_recommendation: str        = ""
    standby_waste_w:         float      = 0.0
    standby_cost_daily_inr:  float      = 0.0
    water_efficiency_score:  float      = 0.0    # 0–100
    estimated_monthly_bill_inr: float   = 0.0
    estimated_monthly_water_inr: float  = 0.0
    usage_profile:           List[dict] = field(default_factory=list)
    savings_tips:            List[str]  = field(default_factory=list)


class OptimizationService:
    """
    Continuously builds hourly usage profiles and applies optimization
    algorithms to generate actionable savings recommendations.
    """

    def __init__(
        self,
        tariff: float = DEFAULT_TARIFF_PER_KWH,
        water_cost: float = DEFAULT_WATER_COST_PER_KL,
        occupants: int = 4,
    ):
        self._tariff    = tariff
        self._water_cost = water_cost
        self._occupants = occupants

        # Hourly buckets: key = hour (0–23)
        self._power_buckets: Dict[int, List[float]] = defaultdict(list)
        self._flow_buckets:  Dict[int, List[float]] = defaultdict(list)

        # Running totals for bill estimation
        self._total_kwh:    float = 0.0
        self._total_litres: float = 0.0
        self._first_kwh:    Optional[float] = None
        self._first_litres: Optional[float] = None
        self._sample_count: int = 0

        # Nightly low-power baseline (watts) for standby detection
        self._night_powers: List[float] = []

    def init(self) -> bool:
        logger.info(
            "Optimization service ready (tariff=₹%.2f/kWh, occupants=%d)",
            self._tariff, self._occupants,
        )
        return True

    # ── Data ingestion ────────────────────────────────────────────────────────
    def record(self, elec, water):
        if not elec.valid:
            return
        now  = datetime.utcnow()
        hour = now.hour

        self._power_buckets[hour].append(elec.power_w)
        if water.valid:
            self._flow_buckets[hour].append(water.flow_lpm)

        # Track cumulative energy/water for billing
        if self._first_kwh is None:
            self._first_kwh    = elec.energy_kwh
            self._first_litres = water.total_litres if water.valid else 0.0
        self._total_kwh    = elec.energy_kwh    - self._first_kwh
        self._total_litres = (water.total_litres - self._first_litres) if water.valid else 0.0

        # Collect night baseline (midnight–5 AM)
        if hour < 5:
            self._night_powers.append(elec.power_w)
            if len(self._night_powers) > 500:
                self._night_powers = self._night_powers[-500:]

        self._sample_count += 1

    # ── Algorithm 1: Peak Load Shifting ───────────────────────────────────────
    def _peak_hours_detected(self) -> List[int]:
        if len(self._power_buckets) < 3:
            return []
        avg_by_hour = {
            h: sum(v) / len(v)
            for h, v in self._power_buckets.items() if v
        }
        if not avg_by_hour:
            return []
        overall_avg = sum(avg_by_hour.values()) / len(avg_by_hour)
        return sorted(
            h for h, avg in avg_by_hour.items() if avg > overall_avg * 1.3
        )

    def _off_peak_suggestion(self, peaks: List[int]) -> str:
        if not peaks:
            return "No significant peak periods detected."
        off_peak = [h for h in range(24) if h not in peaks and h not in PEAK_HOURS]
        if not off_peak:
            return "Run heavy appliances (washing machine, dishwasher) at off-peak hours."
        best = off_peak[:3]
        times = ", ".join(f"{h:02d}:00" for h in best)
        return (
            f"Run heavy loads (washing machine, water heater, EV charging) "
            f"during off-peak hours: {times}"
        )

    # ── Algorithm 2: Standby Waste Detection ──────────────────────────────────
    def _standby_waste(self) -> Tuple[float, float]:
        if len(self._night_powers) < 10:
            return 0.0, 0.0
        # Minimum overnight power = standby baseline
        sorted_vals  = sorted(self._night_powers)
        p10_baseline = sorted_vals[len(sorted_vals) // 10]  # 10th percentile
        waste_w      = max(0.0, p10_baseline - STANDBY_THRESHOLD_W)
        # Cost: waste_w × 24h × 365d / 1000 / 12 months
        cost_per_day = waste_w * 24 / 1000 * self._tariff
        return round(waste_w, 1), round(cost_per_day, 2)

    # ── Algorithm 3: Water Efficiency Score ───────────────────────────────────
    def _water_score(self) -> float:
        if self._total_litres <= 0:
            return 100.0
        daily_per_person = self._total_litres / max(1, self._occupants)
        # Score: 100 at WHO guideline (50 L), 0 at 300 L/person/day
        score = max(0.0, 100.0 - (daily_per_person - WHO_WATER_PER_PERSON_L) / 2.5)
        return round(min(100.0, score), 1)

    # ── Algorithm 4: Usage Profile ────────────────────────────────────────────
    def _usage_profile(self) -> List[dict]:
        profile = []
        for h in range(24):
            pw  = self._power_buckets.get(h, [])
            fl  = self._flow_buckets.get(h,  [])
            profile.append({
                "hour":         h,
                "label":        f"{h:02d}:00",
                "avg_power_w":  round(sum(pw) / len(pw), 1) if pw else 0.0,
                "avg_flow_lpm": round(sum(fl) / len(fl), 3) if fl else 0.0,
                "is_peak":      h in PEAK_HOURS,
            })
        return profile

    # ── Algorithm 5: Bill Estimation ─────────────────────────────────────────
    def _monthly_bill(self) -> Tuple[float, float]:
        # Extrapolate from tracked kWh to a 30-day month
        days_tracked = self._sample_count * 10 / 86400   # 10s intervals
        if days_tracked < 0.01:
            return 0.0, 0.0
        daily_kwh    = self._total_kwh    / days_tracked
        daily_litres = self._total_litres / days_tracked
        monthly_elec_inr  = daily_kwh    * 30 * self._tariff
        monthly_water_inr = daily_litres * 30 / 1000 * self._water_cost
        return round(monthly_elec_inr, 2), round(monthly_water_inr, 2)

    # ── Savings Tips Generator ────────────────────────────────────────────────
    def _tips(self, waste_w, score, peaks) -> List[str]:
        tips = []
        if waste_w > 100:
            tips.append(f"Switch off standby devices — wasting ~{waste_w:.0f} W continuously.")
        if score < 60:
            tips.append("Water usage is above WHO guideline — check for running taps or leaks.")
        if peaks:
            tips.append("Shift heavy loads to off-peak hours to reduce peak demand charges.")
        if self._total_kwh > 10:
            tips.append("Consider a solar panel system to offset daytime load.")
        if not tips:
            tips.append("Usage looks efficient. Keep it up!")
        return tips

    # ── Public API ────────────────────────────────────────────────────────────
    def generate_report(self) -> OptimizationReport:
        peaks       = self._peak_hours_detected()
        waste_w, waste_cost = self._standby_waste()
        score       = self._water_score()
        elec_bill, water_bill = self._monthly_bill()
        profile     = self._usage_profile()
        tips        = self._tips(waste_w, score, peaks)

        report = OptimizationReport(
            peak_hours_detected=peaks,
            off_peak_recommendation=self._off_peak_suggestion(peaks),
            standby_waste_w=waste_w,
            standby_cost_daily_inr=waste_cost,
            water_efficiency_score=score,
            estimated_monthly_bill_inr=elec_bill,
            estimated_monthly_water_inr=water_bill,
            usage_profile=profile,
            savings_tips=tips,
        )
        logger.info(
            "Optimization report: peaks=%s waste=%.0fW score=%.0f "
            "elec=₹%.0f/mo water=₹%.0f/mo",
            peaks, waste_w, score, elec_bill, water_bill,
        )
        return report

    def report_dict(self) -> dict:
        r = self.generate_report()
        return {
            "peak_hours_detected":       r.peak_hours_detected,
            "off_peak_recommendation":   r.off_peak_recommendation,
            "standby_waste_w":           r.standby_waste_w,
            "standby_cost_daily_inr":    r.standby_cost_daily_inr,
            "water_efficiency_score":    r.water_efficiency_score,
            "estimated_monthly_bill_inr":  r.estimated_monthly_bill_inr,
            "estimated_monthly_water_inr": r.estimated_monthly_water_inr,
            "usage_profile":             r.usage_profile,
            "savings_tips":              r.savings_tips,
        }
