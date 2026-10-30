"""Setup, shadow decisions and active control."""

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.nibe_price_optimizer.const import (
    CONF_ENTITIES, CONF_GRID_FEE, CONF_NIBE_ENTRY, CONF_PRICE_SENSOR, CONF_TRIAL_DAYS, DOMAIN)

from .common import add_nibe, set_prices


async def _setup(hass):
    _, ids = add_nibe(hass)
    price = set_prices(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="nibe1", data={
        CONF_NIBE_ENTRY: "nibe1", CONF_PRICE_SENSOR: price, CONF_GRID_FEE: 1.0, CONF_TRIAL_DAYS: 14,
        CONF_ENTITIES: ids})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, ids


async def test_setup_creates_entities_and_never_writes(hass):
    entry, _ = await _setup(hass)
    selects = async_mock_service(hass, "select", "select_option")
    numbers = async_mock_service(hass, "number", "set_value")
    opt = entry.runtime_data
    await opt._on_quarter(None)
    await hass.async_block_till_done()
    assert opt.live["hw"], "shadow decisions for hot water"
    assert opt.live["heat"], "shadow decisions for heating"
    st = hass.states.get("sensor.nibe_price_optimizer_trial_period")
    assert st is not None and st.state == "running"
    assert hass.states.get("switch.nibe_price_optimizer_hot_water_control_active").state == "off"
    assert not selects and not numbers, "nothing may be written to the pump during the trial"


async def test_active_hot_water_control_writes_mode(hass):
    entry, ids = await _setup(hass)
    # mock after setup: our select/switch platforms load the real select/switch services
    selects = async_mock_service(hass, "select", "select_option")
    opt = entry.runtime_data
    await opt.async_set_choice("hw", "hw_fixed_small")
    await hass.services.async_call("switch", "turn_on",
                                   {"entity_id": "switch.nibe_price_optimizer_hot_water_control_active"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.nibe_price_optimizer_hot_water_control_active").state == "on"
    assert any(c.data["option"] == "SMALL" and c.data["entity_id"] == ids["hw_mode"] for c in selects)
    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": "switch.nibe_price_optimizer_hot_water_control_active"}, blocking=True)
    assert selects[-1].data["option"] == "MEDIUM", "turning off restores MEDIUM"


async def test_active_heating_turns_off_spa_and_restores(hass):
    entry, ids = await _setup(hass)
    numbers = async_mock_service(hass, "number", "set_value")
    sw_off = async_mock_service(hass, "switch", "turn_off")
    sw_on = async_mock_service(hass, "switch", "turn_on")
    opt = entry.runtime_data
    await opt.async_set_active("heat", True)
    assert any(c.data["entity_id"] == ids["spa"] for c in sw_off)
    await opt.async_set_active("heat", False)
    assert any(c.data["entity_id"] == ids["spa"] for c in sw_on)
    assert numbers[-1].data["value"] == 0


async def test_report_service(hass, tmp_path):
    entry, _ = await _setup(hass)
    hass.config.config_dir = str(tmp_path)
    res = await hass.services.async_call(DOMAIN, "generate_report", {}, blocking=True, return_response=True)
    assert res["reports"] and (tmp_path / DOMAIN / "report_latest.md").exists()


async def test_unload(hass):
    entry, _ = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_evaluation_and_recommendation(hass, tmp_path):
    from homeassistant.util import dt as dt_util

    from .synthetic import make

    entry, _ = await _setup(hass)
    opt = entry.runtime_data
    obs, hours, prices = make(7)
    opt.obs = [type(o)(**{**o.__dict__, "t": dt_util.as_utc(o.t), "known_until": dt_util.as_utc(o.known_until)}) for o in obs]
    opt.hours = [type(h)(**{**h.__dict__, "t": dt_util.as_utc(h.t)}) for h in hours]
    opt.prices = {dt_util.as_utc(t): p for t, p in prices.items()}
    # evaluation window is relative to now – move the data to end now
    shift = dt_util.utcnow() - opt.obs[-1].t
    opt.obs = [type(o)(**{**o.__dict__, "t": o.t + shift, "known_until": o.known_until + shift}) for o in opt.obs]
    opt.hours = [type(h)(**{**h.__dict__, "t": h.t + shift}) for h in opt.hours]
    opt.prices = {t + shift: p for t, p in opt.prices.items()}
    await opt.async_evaluate()
    rec = opt.results["recommendation"]
    assert rec["hw"]["key"].startswith("hw_forecast") or rec["hw"]["key"].startswith("hw_")
    assert rec["hw"]["saving_per_day"] > 0
    assert "heat" in rec
    hass.config.config_dir = str(tmp_path)
    path = await opt.async_report(notify=True)
    # 7 days of data < 14-day trial: a manual report must not mark the trial-end report as done
    assert not opt.trial_complete
    assert opt.report_done is False
    text = open(path, encoding="utf-8").read()
    assert "Recommendation" in text or "Rekommendation" in text
    st = hass.states.get("sensor.nibe_price_optimizer_hot_water_recommendation")
    assert st.state not in (None, "unknown")


async def test_evaluation_survives_prices_changing_mid_run(hass):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from .synthetic import make

    entry, _ = await _setup(hass)
    opt = entry.runtime_data
    obs, hours, prices = make(2)
    shift = dt_util.utcnow() - dt_util.as_utc(obs[-1].t)
    opt.obs = [type(o)(**{**o.__dict__, "t": dt_util.as_utc(o.t) + shift,
                          "known_until": dt_util.as_utc(o.known_until) + shift}) for o in obs]
    opt.hours = [type(h)(**{**h.__dict__, "t": dt_util.as_utc(h.t) + shift}) for h in hours]
    opt.prices = {dt_util.as_utc(t) + shift: p for t, p in prices.items()}
    real = type(opt)._evaluate_sync

    def mutating(raw_obs, raw_hours, raw_prices, *args):
        # simulate _on_quarter adding a price while the executor iterates
        for i, _ in enumerate(raw_prices.items()):
            if i == 0:
                opt.prices[dt_util.utcnow() + timedelta(days=30)] = 1.0
        return real(raw_obs, raw_hours, raw_prices, *args)

    opt._evaluate_sync = mutating
    await opt.async_evaluate()
    assert opt.results.get("hw")
