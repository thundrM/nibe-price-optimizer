"""Buttons: generate report, apply the recommendation, restart the trial."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import OptimizerEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    opt = entry.runtime_data
    async_add_entities([
        OptButton(opt, "generate_report", "mdi:file-chart-outline"),
        OptButton(opt, "apply_recommendation", "mdi:check-decagram-outline"),
        OptButton(opt, "restart_trial", "mdi:restart", EntityCategory.CONFIG),
    ])


class OptButton(OptimizerEntity, ButtonEntity):
    def __init__(self, opt, key: str, icon: str, category: EntityCategory | None = None) -> None:
        super().__init__(opt, key)
        self.key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_entity_category = category

    async def async_press(self) -> None:
        if self.key == "generate_report":
            await self.opt.async_report(notify=True)
        elif self.key == "apply_recommendation":
            await self.opt.async_apply_recommendation()
        elif self.key == "restart_trial":
            await self.opt.async_restart_trial()
