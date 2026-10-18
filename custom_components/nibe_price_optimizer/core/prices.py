"""Price handling – pure Python, no Home Assistant imports.

All prices are spot prices per kWh in the currency of the price sensor (e.g. SEK/kWh,
including VAT if the sensor is configured that way). The user's grid fee is added on top
when costs are calculated, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

QUARTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Quarter:
    """One 15-minute price slot."""

    start: datetime
    price: float

    @property
    def end(self) -> datetime:
        return self.start + QUARTER


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def parse_raw(raw: Iterable[dict] | None) -> list[Quarter]:
    """Parse a Nord Pool `raw_today`/`raw_tomorrow` list into quarters.

    Hourly entries are split into four quarters with the same price, so the rest of the
    code can always work in 15-minute steps (Nord Pool moved to 15-minute MTU in 2025).
    """
    out: list[Quarter] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        start = _parse_dt(item.get("start"))
        end = _parse_dt(item.get("end"))
        value = item.get("value", item.get("price"))
        if start is None or end is None or value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        t = start
        while t < end:
            out.append(Quarter(t, price))
            t += QUARTER
    return out


def merge(*lists: Iterable[Quarter]) -> list[Quarter]:
    """Merge several quarter lists, newest value wins, sorted by start."""
    seen: dict[datetime, Quarter] = {}
    for lst in lists:
        for q in lst:
            seen[q.start] = q
    return [seen[k] for k in sorted(seen)]


def upcoming(quarters: list[Quarter], now: datetime) -> list[Quarter]:
    """Quarters from the one containing `now` and onwards."""
    return [q for q in quarters if q.end > now]


def current(quarters: list[Quarter], now: datetime) -> float | None:
    for q in quarters:
        if q.start <= now < q.end:
            return q.price
    return None


def percentile(values: list[float], p: float) -> float:
    """Same simple percentile as the original automations: sorted[int(n*p)]."""
    s = sorted(values)
    if not s:
        raise ValueError("no values")
    return s[min(int(len(s) * p), len(s) - 1)]


def window(quarters: list[Quarter], now: datetime, hours: float) -> list[Quarter]:
    end = now + timedelta(hours=hours)
    return [q for q in quarters if q.end > now and q.start < end]
