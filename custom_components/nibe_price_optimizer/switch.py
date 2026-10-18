"""Active control switches – off by default, nothing is written to the pump while off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import OptimizerEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    opt = entry.runtime_data
    async_add_entities([ActiveSwitch(opt, "hw"), ActiveSwitch(opt, "heat")])


class ActiveSwitch(OptimizerEntity, SwitchEntity):
    def __init__(self, opt, kind: str) -> None:
        super().__init__(opt, f"{kind}_active")
        self.kind = kind
        self._attr_translation_key = f"{kind}_active"
        self._attr_icon = "mdi:water-boiler-alert" if kind == "hw" else "mdi:radiator"

    @property
    def is_on(self) -> bool:
        return self.opt.active[self.kind]

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.opt.async_set_active(self.kind, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.opt.async_set_active(self.kind, False)
