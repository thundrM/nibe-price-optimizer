"""Heating offset strategies and a simple house simulation – pure Python.

The strategies change Nibe's heating curve offset (register 40031) by a few steps around a
base offset: pre-heat when it is cheap now and clearly more expensive later, lower the
offset when it is expensive. The house is modelled as one thermal mass:

    ΔT_in(t+1) = ΔT_in(t) + (Q_strategy − Q_base − UA·ΔT_in(t)) / C

where Q_base is the heat Nibe really delivered (normalised to the base offset), UA is
learned from the data (heat demand per °C indoor–outdoor difference) and C is the house's
heat capacity. The comfort band limits how far a strategy may move the indoor temperature.
This is an estimate – the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import HeatingModel, HourObs
from .prices import Quarter

COP_PER_STEP = 0.02   # relative COP loss per offset step (≈1.7 °C higher supply)


@dataclass
class HeatContext:
    now: datetime
    quarters: list[Quarter]   # known prices from now
    indoor_dev: float         # estimated deviation from normal indoor temperature (°C)
    max_dev: float            # comfort band (± °C)


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@dataclass
class HeatNone:
    key: str = "heat_none"
    name: str = "No price control (base offset)"

    def decide(self, ctx: HeatContext) -> tuple[int, str]:
        return 0, "base offset"


@dataclass
class HeatPrice:
    """Price shifting like the original automation: up to ±max_step around the base."""

    max_step: int = 2
    gain2: float = 0.75     # pre-heat +2 when now ≤ p25 and the expensive hours ≥ gain2 dearer
    gain1: float = 0.40     # pre-heat +1 when now ≤ p40 and ≥ gain1 dearer
    exp2: float = 1.50      # −2 when now ≥ p80 and ≥ exp2 above the cheap hours around
    exp1: float = 0.75

    @property
    def key(self) -> str:
        return f"heat_price_{self.max_step}"

    @property
    def name(self) -> str:
        return f"Price shifting ±{self.max_step}"

    def decide(self, ctx: HeatContext) -> tuple[int, str]:
        q = ctx.quarters
        if len(q) < 8:
            return 0, "prices missing"
        now = q[0].price
        w24 = sorted(x.price for x in q if x.start < ctx.now + timedelta(hours=24))
        n = len(w24)
        p25, p40, p80 = w24[int(n * 0.25)], w24[int(n * 0.40)], w24[min(int(n * 0.80), n - 1)]
        later = sorted((x.price for x in q[1:] if x.start < ctx.now + timedelta(hours=18)), reverse=True)
        expensive_later = _avg(later[:16]) if later else now
        around = sorted(x.price for x in q if x.start < ctx.now + timedelta(hours=12))
        cheap_around = _avg(around[:16])
        gain = expensive_later - now
        step, why = 0, "normal"
        if now <= p25 and gain >= self.gain2:
            step, why = 2, f"pre-heat, {gain:.2f} dearer later"
        elif now <= p40 and gain >= self.gain1:
            step, why = 1, f"pre-heat, {gain:.2f} dearer later"
        elif now >= p80 and now - cheap_around >= self.exp2:
            step, why = -2, "expensive now"
        elif now >= p80 and now - cheap_around >= self.exp1:
            step, why = -1, "expensive now"
        step = max(-self.max_step, min(self.max_step, step))
        # comfort band
        if step > 0 and ctx.indoor_dev >= ctx.max_dev - 0.25:
            step, why = 0, "comfort: already warm"
        if step < 0 and ctx.indoor_dev <= -ctx.max_dev + 0.25:
            step, why = 0, "comfort: already cool"
        return step, why


def default_heat_strategies() -> list:
    return [HeatNone(), HeatPrice(1), HeatPrice(2), HeatPrice(3, gain2=1.0, exp2=2.0)]


@dataclass
class HeatResult:
    key: str
    name: str
    cost: float = 0.0
    el_kwh: float = 0.0
    heat_kwh: float = 0.0
    max_dev: float = 0.0
    min_dev: float = 0.0
    hours: int = 0
    step_hours: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = self.hours / 24 if self.hours else 0
        return {
            "key": self.key, "name": self.name, "days": round(d, 2),
            "cost": round(self.cost, 2), "cost_per_day": round(self.cost / d, 2) if d else 0,
            "el_kwh": round(self.el_kwh, 2), "kwh_per_day": round(self.el_kwh / d, 2) if d else 0,
            "indoor_max_dev": round(self.max_dev, 2), "indoor_min_dev": round(self.min_dev, 2),
            "step_hours": self.step_hours,
        }


def estimate_ua(hours: list[HourObs], indoor_ref: float = 20.5) -> float:
    """Heat loss coefficient (kWh/h per °C) from delivered heat and outdoor temperature."""
    q = [h.heat_prod for h in hours if h.outdoor is not None and h.outdoor < indoor_ref - 3]
    dt = [indoor_ref - h.outdoor for h in hours if h.outdoor is not None and h.outdoor < indoor_ref - 3]
    if len(q) < 24 or sum(dt) <= 0:
        return 0.15
    return max(0.03, min(1.0, sum(q) / sum(dt)))


def replay_heating(strategy, hours: list[HourObs], prices: dict[datetime, float],
                   known_until: dict[datetime, datetime], model: HeatingModel, fee: float,
                   base_offset: float, max_dev: float = 1.0) -> HeatResult:
    res = HeatResult(strategy.key, strategy.name)
    ua = estimate_ua(hours)
    cap = model.house_capacity
    dev = 0.0
    for h in hours:
        if h.heat_prod <= 0 and h.heat_used <= 0:
            continue
        hp = [prices[h.t + timedelta(minutes=15 * i)] for i in range(4) if h.t + timedelta(minutes=15 * i) in prices]
        if not hp:
            continue
        price = sum(hp) / len(hp)
        ku = known_until.get(h.t) or h.t + timedelta(hours=12)
        qs, t = [], h.t
        while t < ku and t in prices:
            qs.append(Quarter(t, prices[t]))
            t += timedelta(minutes=15)
        step, _ = strategy.decide(HeatContext(h.t, qs, dev, max_dev))
        real_step = (h.offset if h.offset is not None else base_offset) - base_offset
        q_base = h.heat_prod / max(0.2, 1 + model.heat_per_step * real_step)
        q = q_base * (1 + model.heat_per_step * step)
        dev += (q - q_base - ua * dev) / cap
        cop = model.cop(h.outdoor) * (1 - COP_PER_STEP * step)
        el = q / cop
        res.el_kwh += el
        res.heat_kwh += q
        res.cost += el * (price + fee)
        res.max_dev = max(res.max_dev, dev)
        res.min_dev = min(res.min_dev, dev)
        res.hours += 1
        res.step_hours[str(step)] = res.step_hours.get(str(step), 0) + 1
    # value the stored heat at the end at the average price
    if prices:
        avg = sum(prices.values()) / len(prices)
        cop_avg = res.heat_kwh / res.el_kwh if res.el_kwh else 3.0
        res.cost -= dev * cap / cop_avg * (avg + fee)
    return res
