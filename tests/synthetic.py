"""Synthetic but realistic data: a household tapping pattern and a two-peak price curve."""

from datetime import datetime, timedelta, timezone

from custom_components.nibe_price_optimizer.core.model import HourObs, QuarterObs

TZ = timezone(timedelta(hours=2))
START = datetime(2026, 10, 1, tzinfo=TZ)
# °C/h BT6 fall per hour of day: showers 06–07 and 19–21
PROFILE = [0.4] * 24
for h, v in ((6, 3.0), (7, 1.5), (19, 2.0), (20, 3.0), (21, 1.0)):
    PROFILE[h] = v


def price(t: datetime) -> float:
    h = t.hour
    if 0 <= h < 5:
        return 0.30
    if 6 <= h < 9:
        return 2.50
    if 17 <= h < 21:
        return 3.00
    return 1.00


def make(days: int = 7):
    """Simulate Nibe in MEDIUM (44→48) and return quarter/hour observations + prices."""
    obs, hours, prices = [], [], {}
    t, bt6, charging = START, 46.0, False
    hour_rise = 0.0
    for k in range(days * 96 + 1):
        if t.hour < 13:
            known = t.replace(hour=0, minute=0) + timedelta(days=1)
        else:
            known = t.replace(hour=0, minute=0) + timedelta(days=2)
        obs.append(QuarterObs(t=t, bt6=round(bt6, 2), hw_mode="MEDIUM", offset=0, outdoor=5.0, indoor=21.0,
                              price=price(t), known_until=known))
        fall = PROFILE[t.hour] / 4
        if not charging and bt6 - fall < 44:
            charging = True
        if charging:
            new = min(48.0, bt6 - fall + 2.5)
            hour_rise += max(0, new - bt6)
            bt6 = new
            charging = bt6 < 47.99
        else:
            bt6 -= fall
        t += timedelta(minutes=15)
    t = START
    while t < START + timedelta(days=days + 3):
        prices[t] = price(t)
        t += timedelta(minutes=15)
    for h in range(days * 24):
        ht = START + timedelta(hours=h)
        hours.append(HourObs(t=ht, hw_used=0.1, hw_prod=0.3, heat_used=0.4, heat_prod=1.6, outdoor=5.0,
                             offset=0, bt6_mean=46.0, bt6_rise=1.0))
    return obs, hours, prices
