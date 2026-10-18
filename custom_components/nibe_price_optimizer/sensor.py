"""Sensors: trial status, decisions, recommendation, COP and one sensor per strategy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core.hotwater import default_strategies
from .entity import OptimizerEntity
from .report import strategy_name

DEFAULT_HW_KEYS = {s.key for s in default_strategies()}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    opt = entry.runtime_data
    ents: list[SensorEntity] = [
        TrialSensor(opt), DecisionSensor(opt, "hw"), DecisionSensor(opt, "heat"),
        RecommendationSensor(opt, "hw"), RecommendationSensor(opt, "heat"),
        CopSensor(opt, "hot_water_cop"), CopSensor(opt, "heating_cop"),
    ]
    ents += [StrategySensor(opt, "hw", k) for k in opt.hw_strategies]
    ents += [StrategySensor(opt, "heat", k) for k in opt.heat_strategies]
    async_add_entities(ents)


def _lang(hass) -> str:
    return "sv" if (hass.config.language or "").startswith("sv") else "en"


class TrialSensor(OptimizerEntity, SensorEntity):
    _attr_translation_key = "trial"
    _attr_icon = "mdi:flask-outline"

    def __init__(self, opt) -> None:
        super().__init__(opt, "trial")

    @property
    def native_value(self) -> str:
        return "complete" if self.opt.trial_complete else "running"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {
            "data_days": d.get("data_days"), "trial_days": d.get("trial_days"),
            "days_left": max(0.0, round(self.opt.trial_days - self.opt.data_days, 1)),
            "grid_fee": self.opt.fee, "last_report": d.get("last_report"),
            "evaluated": (self.opt.results or {}).get("evaluated"),
        }


class DecisionSensor(OptimizerEntity, SensorEntity):
    """What the chosen strategy wants right now (written to the pump only when active)."""

    def __init__(self, opt, kind: str) -> None:
        super().__init__(opt, f"{kind}_decision")
        self.kind = kind
        self._attr_translation_key = f"{kind}_decision"
        self._attr_icon = "mdi:water-boiler" if kind == "hw" else "mdi:radiator"

    @property
    def native_value(self):
        live = self.opt.live[self.kind].get(self.opt.choice[self.kind], {})
        return live.get("mode") if self.kind == "hw" else live.get("offset")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        key = self.opt.choice[self.kind]
        live = self.opt.live[self.kind].get(key, {})
        strat = (self.opt.hw_strategies if self.kind == "hw" else self.opt.heat_strategies).get(key)
        return {"strategy": key, "strategy_name": strategy_name(key, strat.name, _lang(self.hass)) if strat else key,
                "reason": live.get("reason"), "active": self.opt.active[self.kind], "step": live.get("step")}


class RecommendationSensor(OptimizerEntity, SensorEntity):
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, opt, kind: str) -> None:
        super().__init__(opt, f"{kind}_recommendation")
        self.kind = kind
        self._attr_translation_key = f"{kind}_recommendation"

    @property
    def _rec(self) -> dict:
        return (self.opt.results or {}).get("recommendation", {}).get(self.kind, {})

    @property
    def native_value(self):
        r = self._rec
        return strategy_name(r["key"], r["name"], _lang(self.hass)) if r else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._rec)


class CopSensor(OptimizerEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, opt, key: str) -> None:
        super().__init__(opt, key)
        self.key = key
        self._attr_translation_key = key

    @property
    def native_value(self):
        return (self.opt.results or {}).get("cop", {}).get(self.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.key == "hot_water_cop":
            return {"bands": [list(x) for x in self.opt.hw_model.cop_bands], "model": self.opt.hw_model.as_dict()}
        return {"bands": [list(x) for x in self.opt.heat_model.cop_bands], "model": self.opt.heat_model.as_dict()}


class StrategySensor(OptimizerEntity, SensorEntity):
    """Shadow decision and trial result for one strategy."""

    def __init__(self, opt, kind: str, key: str) -> None:
        super().__init__(opt, f"strategy_{key}")
        self.kind, self.key = kind, key
        strat = (opt.hw_strategies if kind == "hw" else opt.heat_strategies)[key]
        self._strategy_name = strat.name
        self._attr_icon = "mdi:water-boiler-auto" if kind == "hw" else "mdi:home-thermometer-outline"
        self._attr_entity_registry_enabled_default = kind == "heat" or key in DEFAULT_HW_KEYS

    @property
    def name(self) -> str:
        return strategy_name(self.key, self._strategy_name, _lang(self.hass))

    @property
    def native_value(self):
        live = self.opt.live[self.kind].get(self.key, {})
        return live.get("mode") if self.kind == "hw" else live.get("offset")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        res = (self.opt.results or {}).get(self.kind, {}).get(self.key, {})
        live = self.opt.live[self.kind].get(self.key, {})
        return {"reason": live.get("reason"), **{k: v for k, v in res.items() if k not in ("key", "name")}}
