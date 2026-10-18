"""Nibe Price Optimizer – trial period, strategy comparison and price control for Nibe heat pumps."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import DOMAIN, PLATFORMS, SERVICE_APPLY, SERVICE_REPORT, SERVICE_RESTART_TRIAL
from .coordinator import Optimizer

type NibePriceOptimizerConfigEntry = ConfigEntry[Optimizer]


async def async_setup_entry(hass: HomeAssistant, entry: NibePriceOptimizerConfigEntry) -> bool:
    opt = Optimizer(hass, entry)
    entry.runtime_data = opt
    await opt.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NibePriceOptimizerConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _optimizers(hass: HomeAssistant) -> list[Optimizer]:
    return [e.runtime_data for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REPORT):
        return

    async def report(call: ServiceCall):
        paths = [await o.async_report(notify=False) for o in _optimizers(hass)]
        return {"reports": paths}

    async def apply(call: ServiceCall) -> None:
        for o in _optimizers(hass):
            await o.async_apply_recommendation()

    async def restart(call: ServiceCall) -> None:
        for o in _optimizers(hass):
            await o.async_restart_trial()

    hass.services.async_register(DOMAIN, SERVICE_REPORT, report, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_APPLY, apply)
    hass.services.async_register(DOMAIN, SERVICE_RESTART_TRIAL, restart)
