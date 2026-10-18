"""Learned models of the heat pump – pure Python, no Home Assistant imports.

Everything here is learned from the user's own data during the trial period and falls back
to conservative defaults (measured on a Nibe S1255 in October 2026) when data is missing.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Iterable

# ── Defaults (Nibe S1255, 180 l tank, measured 2026-10) ──────────────────────
DEFAULT_KWH_PER_DEG = 0.25        # heat (kWh) per °C rise on BT6
DEFAULT_CHARGE_RATE = 10.0        # °C/h BT6 rise while charging
DEFAULT_STANDBY = 0.40            # °C/h fall on BT6 without tapping
DEFAULT_LOSS_COEFF = 0.02         # extra °C/h fall per °C BT6 above LOSS_REF
LOSS_REF = 45.0
DEFAULT_HW_COP = [(44.0, 4.0), (48.0, 3.7), (51.0, 3.0), (99.0, 2.7)]  # (upper BT6 limit, COP)
DEFAULT_HEAT_COP = [(-15.0, 2.2), (-5.0, 2.8), (2.0, 3.4), (8.0, 4.0), (99.0, 4.6)]  # (upper BT1, COP)
DEFAULT_HEAT_PER_STEP = 0.05      # share of heating output per offset step (≈1.5–2 °C supply)
DEFAULT_HOUSE_CAPACITY = 15.0     # kWh heat per °C indoor temperature (thermal mass, typical villa)

MIN_DAYS_PROFILE = 3
MIN_SAMPLES_COP = 5


@dataclass
class HotWaterModel:
    kwh_per_deg: float = DEFAULT_KWH_PER_DEG
    charge_rate: float = DEFAULT_CHARGE_RATE
    loss_coeff: float = DEFAULT_LOSS_COEFF
    draw_profile: list[float] = field(default_factory=lambda: [DEFAULT_STANDBY] * 24)  # °C/h per hour of day
    cop_bands: list[tuple[float, float]] = field(default_factory=lambda: list(DEFAULT_HW_COP))
    learned: dict[str, bool] = field(default_factory=dict)

    def cop(self, bt6: float) -> float:
        for upper, cop in self.cop_bands:
            if bt6 < upper:
                return cop
        return self.cop_bands[-1][1]

    def fall_per_quarter(self, hour: int, bt6: float) -> float:
        """Expected BT6 fall during a quarter (typical tapping + standby, temperature dependent)."""
        extra = max(0.0, bt6 - LOSS_REF) * self.loss_coeff
        return (self.draw_profile[hour] + extra) / 4

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "HotWaterModel":
        if not d:
            return cls()
        m = cls()
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, [tuple(x) for x in v] if k == "cop_bands" else v)
        return m


@dataclass
class HeatingModel:
    heat_per_step: float = DEFAULT_HEAT_PER_STEP
    house_capacity: float = DEFAULT_HOUSE_CAPACITY
    cop_bands: list[tuple[float, float]] = field(default_factory=lambda: list(DEFAULT_HEAT_COP))
    learned: dict[str, bool] = field(default_factory=dict)

    def cop(self, outdoor: float | None) -> float:
        if outdoor is None:
            return 3.4
        for upper, cop in self.cop_bands:
            if outdoor < upper:
                return cop
        return self.cop_bands[-1][1]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "HeatingModel":
        if not d:
            return cls()
        m = cls()
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, [tuple(x) for x in v] if k == "cop_bands" else v)
        return m


# ── Observations ──────────────────────────────────────────────────────────────

@dataclass
class QuarterObs:
    """State sampled once per quarter."""

    t: datetime
    bt6: float | None
    bt7: float | None = None
    hw_mode: str | None = None
    offset: float | None = None
    outdoor: float | None = None
    indoor: float | None = None
    price: float | None = None
    known_until: datetime | None = None  # last price known at this moment (for honest replay)


@dataclass
class HourObs:
    """Nibe energy log for one hour (values of 'over the past hour' sensors)."""

    t: datetime                    # start of the hour the energy belongs to
    hw_used: float = 0.0           # compressor electricity for hot water, kWh
    hw_prod: float = 0.0           # heat produced for hot water, kWh
    hw_add: float = 0.0            # additional heater for hot water, kWh
    heat_used: float = 0.0
    heat_prod: float = 0.0
    heat_add: float = 0.0
    bt6_mean: float | None = None
    bt6_rise: float = 0.0          # sum of positive BT6 changes during the hour
    outdoor: float | None = None
    offset: float | None = None


def is_charging(prev: float, cur: float) -> bool:
    return cur - prev >= 0.5


def observed_draws(obs: list[QuarterObs], model: HotWaterModel) -> list[float]:
    """BT6 fall per quarter caused by tapping/standby, as observed.

    While Nibe is charging the fall is hidden by the rise, so the typical fall for that
    hour is used instead.
    """
    out: list[float] = []
    for prev, cur in zip(obs, obs[1:]):
        if prev.bt6 is None or cur.bt6 is None:
            out.append(model.draw_profile[cur.t.hour] / 4)
        elif is_charging(prev.bt6, cur.bt6):
            out.append(model.draw_profile[cur.t.hour] / 4)
        else:
            out.append(max(0.0, prev.bt6 - cur.bt6))
    return out


def learn_hot_water(obs: list[QuarterObs], hours: list[HourObs], model: HotWaterModel | None = None) -> HotWaterModel:
    """Fit the hot water model to observations. Keeps defaults where data is too thin."""
    m = model or HotWaterModel()
    m.learned = {}

    # Tapping/standby profile: median per hour of day of the summed fall, full days only.
    per_day_hour: dict[tuple, float] = {}
    rises: list[float] = []
    for prev, cur in zip(obs, obs[1:]):
        if prev.bt6 is None or cur.bt6 is None:
            continue
        if (cur.t - prev.t).total_seconds() > 1200:
            continue
        key = (cur.t.date(), cur.t.hour)
        per_day_hour.setdefault(key, 0.0)
        d = cur.bt6 - prev.bt6
        if d < 0:
            per_day_hour[key] += -d
        elif is_charging(prev.bt6, cur.bt6):
            rises.append(d * 4)
    days = sorted({k[0] for k in per_day_hour})
    full_days = days[1:-1]
    if len(full_days) >= MIN_DAYS_PROFILE:
        m.draw_profile = [
            round(max(0.05, statistics.median(per_day_hour.get((d, h), 0.0) for d in full_days)), 3)
            for h in range(24)
        ]
        m.learned["draw_profile"] = True
    # Charge rate: median of quarter rises (°C/h), ignore the first steep recovery after big taps.
    if len(rises) >= 8:
        m.charge_rate = round(min(20.0, max(4.0, statistics.median(rises))), 2)
        m.learned["charge_rate"] = True

    # kWh per °C: hours with hot water production and a clear BT6 rise.
    ratios = [h.hw_prod / h.bt6_rise for h in hours if h.hw_prod > 0.1 and h.bt6_rise >= 2.0]
    if len(ratios) >= MIN_SAMPLES_COP:
        m.kwh_per_deg = round(min(0.6, max(0.1, statistics.median(ratios))), 3)
        m.learned["kwh_per_deg"] = True

    # COP per BT6 band, weighted by energy.
    bands = [b for b, _ in m.cop_bands]
    acc = {b: [0.0, 0.0, 0] for b in bands}
    for h in hours:
        if h.hw_used <= 0.05 or h.bt6_mean is None or h.hw_prod <= 0:
            continue
        for b in bands:
            if h.bt6_mean < b:
                acc[b][0] += h.hw_prod
                acc[b][1] += h.hw_used + h.hw_add
                acc[b][2] += 1
                break
    new = []
    for b, cop in m.cop_bands:
        prod, used, n = acc[b]
        if n >= MIN_SAMPLES_COP and used > 0:
            new.append((b, round(min(6.0, max(1.0, prod / used)), 2)))
            m.learned[f"cop<{b:g}"] = True
        else:
            new.append((b, cop))
    m.cop_bands = new

    # Temperature dependent loss: regress night-time fall (00–05, little tapping) on BT6.
    xs, ys = [], []
    for prev, cur in zip(obs, obs[1:]):
        if prev.bt6 is None or cur.bt6 is None or not (0 <= cur.t.hour < 5):
            continue
        d = prev.bt6 - cur.bt6
        if 0 <= d < 0.5:
            xs.append(prev.bt6)
            ys.append(d * 4)
    if len(xs) >= 40 and statistics.pstdev(xs) > 1.0:
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        if 0.0 <= slope <= 0.1:
            m.loss_coeff = round(slope, 4)
            m.learned["loss_coeff"] = True
    return m


def learn_heating(hours: list[HourObs], model: HeatingModel | None = None) -> HeatingModel:
    m = model or HeatingModel()
    m.learned = {}
    bands = [b for b, _ in m.cop_bands]
    acc = {b: [0.0, 0.0, 0] for b in bands}
    for h in hours:
        if h.heat_used <= 0.05 or h.outdoor is None or h.heat_prod <= 0:
            continue
        for b in bands:
            if h.outdoor < b:
                acc[b][0] += h.heat_prod
                acc[b][1] += h.heat_used + h.heat_add
                acc[b][2] += 1
                break
    new = []
    for b, cop in m.cop_bands:
        prod, used, n = acc[b]
        if n >= MIN_SAMPLES_COP and used > 0:
            new.append((b, round(min(7.0, max(1.0, prod / used)), 2)))
            m.learned[f"cop<{b:g}"] = True
        else:
            new.append((b, cop))
    m.cop_bands = new

    # Heat per offset step: regress heat output on offset within outdoor-temperature bins.
    pairs: list[tuple[float, float]] = []
    by_bin: dict[int, list[HourObs]] = {}
    for h in hours:
        if h.outdoor is None or h.offset is None or h.heat_prod <= 0.2:
            continue
        by_bin.setdefault(int(h.outdoor // 3), []).append(h)
    for lst in by_bin.values():
        if len(lst) < 6:
            continue
        mean_q = statistics.fmean(x.heat_prod for x in lst)
        mean_o = statistics.fmean(x.offset for x in lst)
        for x in lst:
            pairs.append((x.offset - mean_o, (x.heat_prod - mean_q) / mean_q))
    if len(pairs) >= 48 and statistics.pstdev(p[0] for p in pairs) > 0.5:
        sxx = sum(p[0] ** 2 for p in pairs)
        slope = sum(p[0] * p[1] for p in pairs) / sxx
        if 0.01 <= slope <= 0.15:
            m.heat_per_step = round(slope, 3)
            m.learned["heat_per_step"] = True
    return m


def cop_table(hours: Iterable[HourObs]) -> dict:
    """Human readable COP summary for the report."""
    hours = list(hours)
    hw = [h for h in hours if h.hw_used > 0.05]
    ht = [h for h in hours if h.heat_used > 0.05]

    def ratio(lst, prod, used):
        u = sum(used(x) for x in lst)
        return round(sum(prod(x) for x in lst) / u, 2) if u else None

    return {
        "hot_water_cop": ratio(hw, lambda h: h.hw_prod, lambda h: h.hw_used + h.hw_add),
        "heating_cop": ratio(ht, lambda h: h.heat_prod, lambda h: h.heat_used + h.heat_add),
        "hot_water_kwh": round(sum(h.hw_used + h.hw_add for h in hours), 2),
        "heating_kwh": round(sum(h.heat_used + h.heat_add for h in hours), 2),
        "hours": len(hours),
    }
