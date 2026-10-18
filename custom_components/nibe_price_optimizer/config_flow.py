"""Config flow for Nibe Price Optimizer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_BASE_OFFSET,
    CONF_DISABLE_SPA,
    CONF_ENTITIES,
    CONF_GRID_FEE,
    CONF_INDOOR_SENSOR,
    CONF_INDOOR_TARGET,
    CONF_MAX_INDOOR_DEV,
    CONF_NIBE_ENTRY,
    CONF_PRICE_SENSOR,
    CONF_PUBLISH_HOUR,
    CONF_TRIAL_DAYS,
    DEFAULT_BASE_OFFSET,
    DEFAULT_GRID_FEE,
    DEFAULT_INDOOR_TARGET,
    DEFAULT_MAX_INDOOR_DEV,
    DEFAULT_PUBLISH_HOUR,
    DEFAULT_TRIAL_DAYS,
    DOMAIN,
    NIBE_DOMAIN,
    REGISTERS,
)


def detect_entities(hass: HomeAssistant, nibe_entry_id: str) -> dict[str, str]:
    """Find the Nibe entities by Modbus register (unique_id ends with '-<register>')."""
    reg = er.async_get(hass)
    found: dict[str, str] = {}
    entries = er.async_entries_for_config_entry(reg, nibe_entry_id)
    for key, (register, domain, _req) in REGISTERS.items():
        for ent in entries:
            if ent.domain == domain and str(ent.unique_id).endswith(f"-{register}"):
                found[key] = ent.entity_id
                break
    return found


def find_price_sensors(hass: HomeAssistant) -> list[str]:
    return [s.entity_id for s in hass.states.async_all("sensor") if "raw_today" in s.attributes]


def _entities_schema(defaults: dict[str, str]) -> vol.Schema:
    fields: dict = {}
    for key, (_register, domain, required) in REGISTERS.items():
        marker = vol.Required if required else vol.Optional
        opts = {"description": {"suggested_value": defaults.get(key)}}
        fields[marker(key, **opts)] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
    return vol.Schema(fields)


def _settings_fields(d: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_GRID_FEE, default=d.get(CONF_GRID_FEE, DEFAULT_GRID_FEE)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_TRIAL_DAYS, default=d.get(CONF_TRIAL_DAYS, DEFAULT_TRIAL_DAYS)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=3, max=60, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
    }


class NibePriceOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        nibe = self.hass.config_entries.async_entries(NIBE_DOMAIN)
        if not nibe:
            return self.async_abort(reason="no_nibe")
        prices = find_price_sensors(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_PRICE_SENSOR])
            if state is None or "raw_today" not in state.attributes:
                errors[CONF_PRICE_SENSOR] = "price_no_raw"
            else:
                await self.async_set_unique_id(user_input[CONF_NIBE_ENTRY])
                self._abort_if_unique_id_configured()
                self._data = {
                    CONF_NIBE_ENTRY: user_input[CONF_NIBE_ENTRY],
                    CONF_PRICE_SENSOR: user_input[CONF_PRICE_SENSOR],
                    CONF_GRID_FEE: float(user_input[CONF_GRID_FEE]),
                    CONF_TRIAL_DAYS: int(user_input[CONF_TRIAL_DAYS]),
                }
                return await self.async_step_entities()

        schema = vol.Schema(
            {
                vol.Required(CONF_NIBE_ENTRY, default=nibe[0].entry_id): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=e.entry_id, label=e.title) for e in nibe]
                    )
                ),
                vol.Required(
                    CONF_PRICE_SENSOR, **({"default": prices[0]} if prices else {})
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                **_settings_fields({}),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            missing = [k for k, (_r, _d, req) in REGISTERS.items() if req and not user_input.get(k)]
            if missing:
                errors["base"] = "missing_required"
            else:
                self._data[CONF_ENTITIES] = {k: v for k, v in user_input.items() if v}
                title = self.hass.config_entries.async_get_entry(self._data[CONF_NIBE_ENTRY])
                return self.async_create_entry(
                    title=f"Price Optimizer – {title.title if title else 'Nibe'}", data=self._data
                )
        found = detect_entities(self.hass, self._data[CONF_NIBE_ENTRY])
        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(found),
            errors=errors,
            description_placeholders={"found": str(len(found)), "total": str(len(REGISTERS))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NibePriceOptimizerOptionsFlow()


class NibePriceOptimizerOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        d = {**self.config_entry.data, **self.config_entry.options}
        indoor = d.get(CONF_INDOOR_SENSOR)
        schema = vol.Schema(
            {
                **_settings_fields(d),
                vol.Required(CONF_PUBLISH_HOUR, default=d.get(CONF_PUBLISH_HOUR, DEFAULT_PUBLISH_HOUR)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=16, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_BASE_OFFSET, default=d.get(CONF_BASE_OFFSET, DEFAULT_BASE_OFFSET)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-10, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_MAX_INDOOR_DEV, default=d.get(CONF_MAX_INDOOR_DEV, DEFAULT_MAX_INDOOR_DEV)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.2, max=3, step=0.1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_INDOOR_SENSOR, description={"suggested_value": indoor}): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_INDOOR_TARGET, default=d.get(CONF_INDOOR_TARGET, DEFAULT_INDOOR_TARGET)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=15, max=26, step=0.5, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_DISABLE_SPA, default=d.get(CONF_DISABLE_SPA, True)): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
