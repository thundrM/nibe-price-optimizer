"""Hot water strategies and tank simulation – pure Python.

The tank is modelled on BT6 (charging sensor), which is what Nibe uses to start and stop
hot water charging. Nibe charges when BT6 falls below the start temperature of the
current demand mode and stops at the stop temperature. Strategies only choose the mode
(SMALL / MEDIUM / LARGE) – exactly what the integration writes to the pump when active.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

from .model import HotWaterModel, QuarterObs, is_charging, observed_draws
from .prices import QUARTER, Quarter, percentile

SMALL, MEDIUM, LARGE = "SMALL", "MEDIUM", "LARGE"


@dataclass(frozen=True)
class Thresholds:
    """Start/stop temperatures per mode, read from the pump (registers 40059–40065)."""

    small: tuple[float, float] = (40.0, 44.0)
    medium: tuple[float, float] = (44.0, 48.0)
    large: tuple[float, float] = (48.0, 51.0)

    def of(self, mode: str) -> tuple[float, float]:
        return {SMALL: self.small, MEDIUM: self.medium, LARGE: self.large}.get(mode, self.medium)


@dataclass
class Context:
    now: datetime
    bt6: float
    mode: str                      # mode currently set (real or simulated)
    mode_since: datetime
    quarters: list[Quarter]        # known prices from the current quarter onwards
    model: HotWaterModel
    thr: Thresholds
    fee: float
    memory: dict = field(default_factory=dict)


@dataclass
class Decision:
    mode: str
    reason: str


class Strategy(Protocol):
    key: str
    name: str

    def decide(self, ctx: Context) -> Decision: ...


def _price_stats(ctx: Context) -> dict | None:
    win = [q.price for q in ctx.quarters if q.start < ctx.now + timedelta(hours=24)]
    if not ctx.quarters or len(win) < 8:
        return None
    return {"now": ctx.quarters[0].price, "p25": percentile(win, 0.25), "p75": percentile(win, 0.75)}


def _expensive(st: dict, diff: float = 0.5) -> bool:
    return st["now"] >= st["p75"] and st["now"] - st["p25"] >= diff


# ── Forecast of Nibe's own behaviour ────────────────────────────────────────

def forecast_charges(bt6: float, quarters: list[Quarter], model: HotWaterModel, thr: Thresholds,
                     large_first: bool, base_mode: str = MEDIUM, max_charges: int = 2) -> list[tuple[int, float]]:
    """Simulate typical use: returns [(quarter index where a charge starts, average price)]."""
    t, charging, cur, out = bt6, False, None, []
    mode = LARGE if large_first else base_mode
    for k, q in enumerate(quarters):
        start, stop = thr.of(mode)
        f = model.fall_per_quarter(q.start.hour, t)
        if not charging and t - f < start:
            charging, cur = True, [k, []]
        if charging:
            t = min(stop, t - f + model.charge_rate / 4)
            cur[1].append(q.price)
            if t >= stop - 0.01:
                out.append((cur[0], sum(cur[1]) / len(cur[1])))
                charging, cur = False, None
                mode = base_mode
                if len(out) >= max_charges:
                    break
        else:
            t -= f
    return out


def large_assessment(ctx: Context, diff: float, after: float, base_mode: str = MEDIUM,
                     bridge: str = "now") -> tuple[bool, str]:
    """Is LARGE worth it now? See README: avoid LARGE unless it bridges an expensive charge.

    bridge="now":   the charge after LARGE must cost ≤ price now + `after` (strict).
    bridge="later": the charge after LARGE must be ≥ `diff` cheaper than the avoided one.
    """
    l_start, _ = ctx.thr.large
    if ctx.bt6 >= l_start:
        return False, f"no – BT6 {ctx.bt6:.1f} °C, LARGE would not start a charge"
    if ctx.bt6 < ctx.thr.small[0]:
        return False, "no – Nibe is already charging after tapping"
    without = forecast_charges(ctx.bt6, ctx.quarters, ctx.model, ctx.thr, False, base_mode, 1)
    with_l = forecast_charges(ctx.bt6, ctx.quarters, ctx.model, ctx.thr, True, base_mode, 2)
    if not without or len(with_l) < 2:
        return False, "no – next charge is beyond known prices"
    now_p = with_l[0][1]
    later_t, later_p = ctx.quarters[without[0][0]].start, without[0][1]
    next_t, next_p = ctx.quarters[with_l[1][0]].start, with_l[1][1]
    info = (f"now {now_p:.2f} · without LARGE charge {later_t:%H:%M} at {later_p:.2f} · "
            f"with LARGE next {next_t:%H:%M} at {next_p:.2f}")
    if later_p - now_p < diff:
        return False, f"no – later charge only {later_p - now_p:+.2f} more expensive · {info}"
    limit = now_p + after if bridge == "now" else later_p - diff
    if next_p > limit:
        return False, f"no – extra heat does not bridge the expensive hours · {info}"
    return True, f"YES – avoids a charge {later_p - now_p:.2f} more expensive · {info}"


# ── Strategies ──────────────────────────────────────────────────────────────

@dataclass
class Fixed:
    mode: str

    @property
    def key(self) -> str:
        return f"hw_fixed_{self.mode.lower()}"

    @property
    def name(self) -> str:
        return f"Always {self.mode}"

    def decide(self, ctx: Context) -> Decision:
        return Decision(self.mode, "fixed")


@dataclass
class Quartile:
    """LARGE when cheap (≤ p25 and ≥ diff below p75), SMALL when expensive, with hysteresis."""

    diff: float = 0.5
    hold: float = 0.10

    @property
    def key(self) -> str:
        return f"hw_quartile_{int(self.diff * 100)}"

    @property
    def name(self) -> str:
        return f"Price quartiles (LARGE ≤ p25, spread ≥ {self.diff:.2f})"

    def decide(self, ctx: Context) -> Decision:
        st = _price_stats(ctx)
        if st is None:
            return Decision(MEDIUM, "prices missing")
        if st["now"] <= st["p25"] and st["p75"] - st["now"] >= self.diff:
            return Decision(LARGE, "cheap now")
        if ctx.mode == LARGE and st["now"] <= st["p25"] + self.hold:
            return Decision(LARGE, "holding LARGE (still cheap)")
        if _expensive(st):
            return Decision(SMALL, "expensive now")
        return Decision(MEDIUM, "normal price")


@dataclass
class Absolute:
    """LARGE only below an absolute spot price (and ≤ p25)."""

    limit: float = 0.5

    @property
    def key(self) -> str:
        return f"hw_absolute_{int(self.limit * 100)}"

    @property
    def name(self) -> str:
        return f"LARGE only ≤ {self.limit:.2f} spot"

    def decide(self, ctx: Context) -> Decision:
        st = _price_stats(ctx)
        if st is None:
            return Decision(MEDIUM, "prices missing")
        if st["now"] <= self.limit and st["now"] <= st["p25"]:
            return Decision(LARGE, "very cheap")
        if ctx.mode == LARGE and st["now"] <= self.limit + 0.1 and st["now"] <= st["p25"] + 0.1:
            return Decision(LARGE, "holding LARGE")
        if _expensive(st):
            return Decision(SMALL, "expensive now")
        return Decision(MEDIUM, "normal price")


@dataclass
class Forecast:
    """Avoid LARGE unless it bridges a charge that would be ≥ diff more expensive."""

    diff: float = 0.7
    after: float = 0.3
    use_small: bool = True
    bridge: str = "now"
    max_hold: timedelta = timedelta(hours=2)

    @property
    def key(self) -> str:
        return (f"hw_forecast_{int(self.diff * 100)}" + ("_later" if self.bridge == "later" else "")
                + ("" if self.use_small else "_nosmall"))

    @property
    def name(self) -> str:
        return (f"Forecast LARGE (≥ {self.diff:.2f} saved)" + (", vs avoided charge" if self.bridge == "later" else "")
                + ("" if self.use_small else ", no SMALL"))

    def decide(self, ctx: Context) -> Decision:
        ok, why = large_assessment(ctx, self.diff, self.after, bridge=self.bridge)
        if ok:
            return Decision(LARGE, why)
        _, l_stop = ctx.thr.large
        if ctx.mode == LARGE and ctx.bt6 < l_stop - 0.5 and ctx.now - ctx.mode_since < self.max_hold:
            return Decision(LARGE, "holding LARGE until the charge is done")
        st = _price_stats(ctx)
        if st is None:
            return Decision(MEDIUM, "prices missing")
        if self.use_small and _expensive(st):
            return Decision(SMALL, "expensive now")
        return Decision(MEDIUM, why)


def default_strategies() -> list:
    return [
        Fixed(MEDIUM),
        Fixed(SMALL),
        Fixed(LARGE),
        Quartile(0.5),
        Absolute(0.5),
        Forecast(0.5),
        Forecast(0.7),
        Forecast(1.0),
        Forecast(0.7, use_small=False),
        Forecast(0.7, bridge="later"),
    ]


def search_strategies() -> list:
    """Wider grid used for the recommendation."""
    out = default_strategies()
    for d in (0.3, 0.4, 0.6, 0.8, 1.2, 1.5):
        out.append(Forecast(d))
    for d in (0.5, 1.0):
        out.append(Forecast(d, bridge="later"))
    for a in (0.3, 0.7):
        out.append(Absolute(a))
    out.append(Quartile(0.3))
    return out


# ── Replay ───────────────────────────────────────────────────────────────────

@dataclass
class SimResult:
    key: str
    name: str
    cost: float = 0.0           # spot + grid fee, currency
    spot_cost: float = 0.0
    el_kwh: float = 0.0
    heat_kwh: float = 0.0
    charges: int = 0
    mode_quarters: Counter = field(default_factory=Counter)
    days: float = 0.0
    end_temp: float = 0.0

    def per_day(self, v: float) -> float:
        return v / self.days if self.days else 0.0

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "days": round(self.days, 2),
            "cost": round(self.cost, 2), "cost_per_day": round(self.per_day(self.cost), 2),
            "el_kwh": round(self.el_kwh, 2), "kwh_per_day": round(self.per_day(self.el_kwh), 2),
            "heat_kwh": round(self.heat_kwh, 2), "charges": self.charges,
            "charges_per_day": round(self.per_day(self.charges), 2),
            "large_share": round(self.mode_quarters[LARGE] / max(1, sum(self.mode_quarters.values())), 3),
            "end_temp": round(self.end_temp, 1),
        }


def known_quarters(prices: dict[datetime, float], start: datetime, known_until: datetime | None) -> list[Quarter]:
    out = []
    t = start
    limit = known_until or start + timedelta(hours=12)
    while t < limit and t in prices:
        out.append(Quarter(t, prices[t]))
        t += QUARTER
    return out


def replay(strategy, obs: list[QuarterObs], prices: dict[datetime, float], model: HotWaterModel,
           thr: Thresholds, fee: float, decide_every: int = 1) -> SimResult:
    """Run `strategy` through the observed tapping and the real prices.

    Tapping (BT6 fall) is taken from what really happened; standby losses are corrected for
    the simulated temperature. Only prices that were known at each moment are shown to the
    strategy, so the result is what it would really have achieved.
    """
    res = SimResult(strategy.key, strategy.name)
    if len(obs) < 2:
        return res
    draws = observed_draws(obs, model)
    t = obs[0].bt6 if obs[0].bt6 is not None else thr.medium[1]
    mode, since, charging, memory = MEDIUM, obs[0].t, False, {}
    for k, (o, draw) in enumerate(zip(obs[1:], draws)):
        price = prices.get(o.t.replace(second=0, microsecond=0))
        if price is None:
            price = o.price
        if price is None:
            continue
        if k % decide_every == 0:
            ctx = Context(o.t, t, mode, since, known_quarters(prices, o.t, o.known_until), model, thr, fee, memory)
            if ctx.quarters:
                new = strategy.decide(ctx).mode
                if new != mode:
                    mode, since = new, o.t
        real = o.bt6 if o.bt6 is not None else t
        fall = draw + max(0.0, t - real) * model.loss_coeff / 4
        start, stop = thr.of(mode)
        if not charging and t - fall < start:
            charging = True
            res.charges += 1
        if charging:
            new_t = min(stop, t - fall + model.charge_rate / 4)
            heat = max(0.0, new_t - (t - fall)) * model.kwh_per_deg
            el = heat / model.cop(t)
            res.heat_kwh += heat
            res.el_kwh += el
            res.cost += el * (price + fee)
            res.spot_cost += el * price
            t = new_t
            if t >= stop - 0.01:
                charging = False
        else:
            t -= fall
        res.mode_quarters[mode] += 1
    res.days = (obs[-1].t - obs[0].t).total_seconds() / 86400
    # value the end state so strategies finishing hotter/colder compare fairly
    ref = thr.medium[1]
    avg = sum(prices.values()) / len(prices) if prices else 1.0
    adj = (t - ref) * model.kwh_per_deg / model.cop(46) * (avg + fee)
    res.cost -= adj
    res.end_temp = t
    return res


def evaluate(strategies: list, obs, prices, model, thr, fee) -> list[SimResult]:
    return [replay(s, obs, prices, model, thr, fee) for s in strategies]
