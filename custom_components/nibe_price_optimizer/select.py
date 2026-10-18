"""Select which strategy is used when active control is on."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import OptimizerEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    opt = entry.runtime_data
    async_add_entities([StrategySelect(opt, "hw"), StrategySelect(opt, "heat")])


class StrategySelect(OptimizerEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, opt, kind: str) -> None:
        super().__init__(opt, f"{kind}_strategy")
        self.kind = kind
        self._attr_translation_key = f"{kind}_strategy"
        self._attr_icon = "mdi:tune-variant"
        strategies = opt.hw_strategies if kind == "hw" else opt.heat_strategies
        self._attr_options = list(strategies)

    @property
    def current_option(self) -> str | None:
        return self.opt.choice[self.kind]

    async def async_select_option(self, option: str) -> None:
        await self.opt.async_set_choice(self.kind, option)
