"""Tests of the pure calculation core (no Home Assistant needed)."""

from datetime import timedelta

from custom_components.nibe_price_optimizer.core import hotwater as hw
from custom_components.nibe_price_optimizer.core import heating as ht
from custom_components.nibe_price_optimizer.core.model import HotWaterModel, HeatingModel, learn_hot_water
from custom_components.nibe_price_optimizer.core.prices import parse_raw, percentile

from .synthetic import PROFILE, START, make, price


def test_parse_raw_splits_hours_into_quarters():
    raw = [{"start": "2026-10-01T00:00:00+02:00", "end": "2026-10-01T01:00:00+02:00", "value": 1.5}]
    q = parse_raw(raw)
    assert len(q) == 4 and all(x.price == 1.5 for x in q)


def test_parse_raw_skips_missing_values():
    raw = [{"start": "2026-10-01T00:00:00+02:00", "end": "2026-10-01T00:15:00+02:00", "value": None}]
    assert parse_raw(raw) == []


def test_percentile_matches_automation_definition():
    assert percentile([5, 1, 3, 2, 4], 0.25) == 2
    assert percentile([1, 2], 0.99) == 2


def test_learn_profile_from_observations():
    obs, hours, _ = make(7)
    m = learn_hot_water(obs, hours)
    assert m.learned.get("draw_profile")
    assert m.draw_profile[20] > m.draw_profile[3]


def test_forecast_large_before_expensive_morning():
    """At 04:00 (0.30) with BT6 just above MEDIUM start, LARGE avoids the 2.50 morning charge."""
    model = HotWaterModel(draw_profile=PROFILE, loss_coeff=0.0)
    now = START.replace(hour=4)
    quarters = [hw.Quarter(now + timedelta(minutes=15 * i), price(now + timedelta(minutes=15 * i))) for i in range(80)]
    ctx = hw.Context(now, 46.0, hw.MEDIUM, now, quarters, model, hw.Thresholds(), 1.0)
    # strict rule: next charge after LARGE (12:45 at 1.00) is not ≤ now + 0.30
    ok, why = hw.large_assessment(ctx, 0.7, 0.3)
    assert not ok and "bridge" in why
    # "later" rule: 1.00 is ≥ 0.70 cheaper than the avoided 2.50 charge
    ok, why = hw.large_assessment(ctx, 0.7, 0.3, bridge="later")
    assert ok, why


def test_forecast_no_large_when_beyond_known_prices():
    model = HotWaterModel(draw_profile=[0.1] * 24, loss_coeff=0.0)
    now = START.replace(hour=10)
    quarters = [hw.Quarter(now + timedelta(minutes=15 * i), 1.0) for i in range(8)]
    ctx = hw.Context(now, 46.0, hw.MEDIUM, now, quarters, model, hw.Thresholds(), 1.0)
    ok, why = hw.large_assessment(ctx, 0.7, 0.3)
    assert not ok and "beyond" in why


def test_replay_strategies_and_fee():
    obs, hours, prices = make(7)
    model = learn_hot_water(obs, hours, HotWaterModel(draw_profile=PROFILE))
    res = {r.key: r for r in hw.evaluate(hw.default_strategies(), obs, prices, model, hw.Thresholds(), 1.0)}
    base = res["hw_fixed_medium"]
    assert base.charges > 0 and base.el_kwh > 0
    # with a strong two-peak price curve, forecast LARGE must beat plain MEDIUM
    assert res["hw_forecast_70"].cost < base.cost
    # always LARGE stores the most heat and uses the most electricity
    assert res["hw_fixed_large"].el_kwh > base.el_kwh
    # a higher grid fee never makes anything cheaper
    res2 = {r.key: r for r in hw.evaluate([hw.Fixed(hw.MEDIUM)], obs, prices, model, hw.Thresholds(), 2.0)}
    assert res2["hw_fixed_medium"].cost > base.cost


def test_strategy_only_sees_known_prices():
    obs, hours, prices = make(2)
    seen = []

    class Spy:
        key, name = "spy", "spy"

        def decide(self, ctx):
            seen.append((ctx.now, ctx.quarters[-1].start if ctx.quarters else None))
            return hw.Decision(hw.MEDIUM, "")

    hw.replay(Spy(), obs, prices, HotWaterModel(), hw.Thresholds(), 1.0)
    for now, last in seen:
        if last is None:
            continue
        limit = now.replace(hour=0, minute=0) + timedelta(days=1 if now.hour < 13 else 2)
        assert last < limit


def test_heating_strategies_respect_comfort_band():
    obs, hours, prices = make(5)
    ku = {o.t: o.known_until for o in obs if o.t.minute == 0}
    for s in ht.default_heat_strategies():
        r = ht.replay_heating(s, hours, prices, ku, HeatingModel(), 1.0, 0, max_dev=0.5)
        assert r.max_dev <= 0.6 and r.min_dev >= -0.6
    none = ht.replay_heating(ht.HeatNone(), hours, prices, ku, HeatingModel(), 1.0, 0)
    assert none.max_dev == 0 and none.min_dev == 0
