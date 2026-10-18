"""Config flow tests."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nibe_price_optimizer.const import CONF_ENTITIES, CONF_GRID_FEE, DOMAIN

from .common import add_nibe, set_prices


async def test_flow_detects_entities(hass):
    _, ids = add_nibe(hass)
    price = set_prices(hass)
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert res["type"] is FlowResultType.FORM and res["step_id"] == "user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"nibe_entry": "nibe1", "price_sensor": price, "grid_fee": 0.95, "trial_days": 14})
    assert res["step_id"] == "entities"
    assert res["description_placeholders"]["found"] == "19"
    # accept the suggestions
    res = await hass.config_entries.flow.async_configure(res["flow_id"], ids)
    assert res["type"] is FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_GRID_FEE] == 0.95
    assert res["data"][CONF_ENTITIES]["hw_mode"] == ids["hw_mode"]


async def test_flow_rejects_sensor_without_raw(hass):
    add_nibe(hass)
    hass.states.async_set("sensor.plain_price", "1.0")
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"nibe_entry": "nibe1", "price_sensor": "sensor.plain_price", "grid_fee": 1, "trial_days": 14})
    assert res["errors"] == {"price_sensor": "price_no_raw"}


async def test_flow_aborts_without_nibe(hass):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert res["type"] is FlowResultType.ABORT and res["reason"] == "no_nibe"
