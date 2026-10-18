"""Helpers to fake a Nibe heat pump and a Nord Pool sensor in a test Home Assistant."""

from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nibe_price_optimizer.const import REGISTERS

STATES = {
    "bt6": "46.0", "bt7": "50.0", "bt1": "5.0", "bt50": "21.0", "hw_mode": "MEDIUM", "offset": "0", "spa": "on",
    "hw_start_low": "40", "hw_stop_low": "44", "hw_start_normal": "44", "hw_stop_normal": "48",
    "hw_start_high": "48", "hw_stop_high": "51", "e_heat_prod": "1.5", "e_hw_prod": "0.3", "e_heat_used": "0.4",
    "e_hw_used": "0.1", "e_heat_add": "0", "e_hw_add": "0",
}


def add_nibe(hass) -> tuple[MockConfigEntry, dict[str, str]]:
    entry = MockConfigEntry(domain="nibe_heatpump", title="S1255 test", entry_id="nibe1")
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    ids = {}
    for key, (register, domain, _req) in REGISTERS.items():
        ent = reg.async_get_or_create(domain, "nibe_heatpump", f"abc-{register}", config_entry=entry,
                                      suggested_object_id=f"{key}_{register}")
        ids[key] = ent.entity_id
        attrs = {"options": ["SMALL", "MEDIUM", "LARGE", "SMART CONTROL"]} if domain == "select" else {}
        hass.states.async_set(ent.entity_id, STATES[key], attrs)
    return entry, ids


def set_prices(hass, cheap_now: bool = True) -> str:
    now = dt_util.now().replace(minute=0, second=0, microsecond=0)
    start = now.replace(hour=0)
    raw_today, raw_tomorrow = [], []
    for i in range(96 * 2):
        t = start + timedelta(minutes=15 * i)
        h = t.hour
        v = 0.3 if h < 5 else 2.5 if 6 <= h < 9 else 3.0 if 17 <= h < 21 else 1.0
        item = {"start": t.isoformat(), "end": (t + timedelta(minutes=15)).isoformat(), "value": v}
        (raw_today if i < 96 else raw_tomorrow).append(item)
    hass.states.async_set("sensor.nordpool_test", "1.0", {"raw_today": raw_today, "raw_tomorrow": raw_tomorrow})
    return "sensor.nordpool_test"
