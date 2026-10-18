"""Constants for Nibe Price Optimizer."""

from __future__ import annotations

DOMAIN = "nibe_price_optimizer"
NIBE_DOMAIN = "nibe_heatpump"

# Config entry keys
CONF_NIBE_ENTRY = "nibe_entry"
CONF_PRICE_SENSOR = "price_sensor"
CONF_GRID_FEE = "grid_fee"
CONF_TRIAL_DAYS = "trial_days"
CONF_PUBLISH_HOUR = "publish_hour"
CONF_BASE_OFFSET = "base_offset"
CONF_MAX_INDOOR_DEV = "max_indoor_dev"
CONF_INDOOR_SENSOR = "indoor_sensor"
CONF_INDOOR_TARGET = "indoor_target"
CONF_DISABLE_SPA = "disable_spa"
CONF_ENTITIES = "entities"

DEFAULT_GRID_FEE = 1.0
DEFAULT_TRIAL_DAYS = 14
DEFAULT_PUBLISH_HOUR = 13
DEFAULT_BASE_OFFSET = 0
DEFAULT_MAX_INDOOR_DEV = 1.0
DEFAULT_INDOOR_TARGET = 21.0

# Nibe S-series Modbus registers (nibe_heatpump unique_id = "<entry>-<register>")
# key: (register, domain, required)
REGISTERS: dict[str, tuple[int, str, bool]] = {
    "bt6": (30010, "sensor", True),            # hot water charging
    "bt7": (30009, "sensor", False),           # hot water top
    "bt1": (30002, "sensor", True),            # outdoor
    "bt50": (30117, "sensor", False),          # room average
    "hw_mode": (40057, "select", True),        # hot water demand mode SMALL/MEDIUM/LARGE
    "offset": (40031, "number", False),        # heating offset climate system 1
    "spa": (40844, "switch", False),           # smart price adaption
    "hw_start_low": (40061, "number", False),
    "hw_stop_low": (40065, "number", False),
    "hw_start_normal": (40060, "number", False),
    "hw_stop_normal": (40064, "number", False),
    "hw_start_high": (40059, "number", False),
    "hw_stop_high": (40063, "number", False),
    "e_heat_prod": (32284, "sensor", False),   # energy log, produced for heating past hour
    "e_hw_prod": (32286, "sensor", True),
    "e_heat_used": (32292, "sensor", False),
    "e_hw_used": (32294, "sensor", True),
    "e_heat_add": (32300, "sensor", False),
    "e_hw_add": (32302, "sensor", False),
}

UPDATE_MINUTES = (0, 15, 30, 45)
EVALUATE_EVERY_HOURS = 6
EVALUATE_MAX_DAYS = 30
KEEP_DAYS = 60
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "select", "switch", "button"]

SERVICE_REPORT = "generate_report"
SERVICE_APPLY = "apply_recommendation"
SERVICE_RESTART_TRIAL = "restart_trial"
