"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Optimizer


class OptimizerEntity(CoordinatorEntity[Optimizer]):
    _attr_has_entity_name = True

    def __init__(self, opt: Optimizer, key: str) -> None:
        super().__init__(opt)
        self.opt = opt
        self._attr_unique_id = f"{opt.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, opt.entry.entry_id)},
            name="Nibe Price Optimizer",
            manufacturer="nibe-price-optimizer",
            model="Trial & price control",
        )
