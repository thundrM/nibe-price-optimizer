"""Bootstrap observations from the recorder so the trial does not start from zero."""

from __future__ import annotations

import bisect
import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .core.model import HourObs, QuarterObs

_LOGGER = logging.getLogger(__name__)


def _series(states) -> tuple[list[float], list]:
    ts, vals = [], []
    for s in states or []:
        ts.append(s.last_updated.timestamp())
        try:
            vals.append(float(s.state))
        except (TypeError, ValueError):
            vals.append(s.state if s.state not in ("unknown", "unavailable") else None)
    return ts, vals


def _at(series, t: float):
    ts, vals = series
    i = bisect.bisect_right(ts, t) - 1
    return vals[i] if i >= 0 else None


def _num(v):
    return v if isinstance(v, (int, float)) else None


def known_until_rule(t: datetime, publish_hour: int) -> datetime:
    """Prices for tomorrow are published around `publish_hour` local time."""
    local = dt_util.as_local(t)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=1 if local.hour < publish_hour else 2)


def _fetch(hass: HomeAssistant, start: datetime, end: datetime, entity_ids: list[str]):
    from homeassistant.components.recorder import history

    return history.get_significant_states(
        hass, start, end, entity_ids, significant_changes_only=False, minimal_response=False, no_attributes=True
    )


async def async_backfill(hass: HomeAssistant, entities: dict[str, str], price_sensor: str, days: int,
                         publish_hour: int) -> tuple[list[QuarterObs], list[HourObs], dict[datetime, float]]:
    """Read up to `days` of history and convert it to quarter/hour observations."""
    try:
        from homeassistant.components.recorder import get_instance
    except ImportError:  # recorder not loaded
        return [], [], {}
    end = dt_util.utcnow().replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 15)
    start = end - timedelta(days=days)
    wanted = {k: v for k, v in entities.items() if k in (
        "bt6", "bt7", "bt1", "bt50", "hw_mode", "offset",
        "e_hw_used", "e_hw_prod", "e_hw_add", "e_heat_used", "e_heat_prod", "e_heat_add")}
    ids = list(wanted.values()) + [price_sensor]
    try:
        raw = await get_instance(hass).async_add_executor_job(_fetch, hass, start, end, ids)
    except Exception as err:  # noqa: BLE001 - history is a bonus, never fatal
        _LOGGER.warning("Could not read history for bootstrap: %s", err)
        return [], [], {}
    ser = {k: _series(raw.get(eid)) for k, eid in wanted.items()}
    price_ser = _series(raw.get(price_sensor))
    obs: list[QuarterObs] = []
    prices: dict[datetime, float] = {}
    t = start
    while t < end:
        ts = t.timestamp()
        p = _num(_at(price_ser, ts + 60))
        if p is not None:
            prices[t] = p
        bt6 = _num(_at(ser["bt6"], ts)) if "bt6" in ser else None
        if bt6 is not None:
            obs.append(QuarterObs(
                t=t, bt6=bt6,
                bt7=_num(_at(ser["bt7"], ts)) if "bt7" in ser else None,
                hw_mode=_at(ser["hw_mode"], ts) if "hw_mode" in ser else None,
                offset=_num(_at(ser["offset"], ts)) if "offset" in ser else None,
                outdoor=_num(_at(ser["bt1"], ts)) if "bt1" in ser else None,
                indoor=_num(_at(ser["bt50"], ts)) if "bt50" in ser else None,
                price=p, known_until=known_until_rule(t, publish_hour),
            ))
        t += timedelta(minutes=15)
    # hours: the 'past hour' value appears just after the next full hour
    hours: list[HourObs] = []
    t = start.replace(minute=0)
    bt6_ts, bt6_vals = ser.get("bt6", ([], []))
    while t < end - timedelta(hours=1):
        nxt = (t + timedelta(hours=1, minutes=5)).timestamp()
        h = HourObs(t=t)
        for key, attr in (("e_hw_used", "hw_used"), ("e_hw_prod", "hw_prod"), ("e_hw_add", "hw_add"),
                          ("e_heat_used", "heat_used"), ("e_heat_prod", "heat_prod"), ("e_heat_add", "heat_add")):
            if key in ser:
                setattr(h, attr, _num(_at(ser[key], nxt)) or 0.0)
        a, b = t.timestamp(), (t + timedelta(hours=1)).timestamp()
        i0, i1 = bisect.bisect_left(bt6_ts, a), bisect.bisect_left(bt6_ts, b)
        vals = [v for v in bt6_vals[i0:i1] if isinstance(v, (int, float))]
        prev = _num(_at(ser["bt6"], a - 1)) if "bt6" in ser else None
        rise = 0.0
        for v in vals:
            if prev is not None and v > prev:
                rise += v - prev
            prev = v
        h.bt6_rise = rise
        h.bt6_mean = sum(vals) / len(vals) if vals else _num(_at(ser["bt6"], a)) if "bt6" in ser else None
        h.outdoor = _num(_at(ser["bt1"], a)) if "bt1" in ser else None
        h.offset = _num(_at(ser["offset"], a)) if "offset" in ser else None
        hours.append(h)
        t += timedelta(hours=1)
    _LOGGER.info("Bootstrapped %d quarters and %d hours from history", len(obs), len(hours))
    return obs, hours, prices
