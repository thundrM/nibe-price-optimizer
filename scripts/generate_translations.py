"""Generate strings.json + translations/{en,sv}.json (run after adding strategies or fields).

    python scripts/generate_translations.py   (needs homeassistant installed, e.g. the test venv)
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from custom_components.nibe_price_optimizer.core.heating import default_heat_strategies  # noqa: E402
from custom_components.nibe_price_optimizer.core.hotwater import search_strategies  # noqa: E402

# report.py imports Home Assistant only for dt_util; load strategy_name without it
_src = open(os.path.join(ROOT, "custom_components/nibe_price_optimizer/report.py"), encoding="utf-8").read()
_ns: dict = {}
exec("def strategy_name" + _src.split("def strategy_name")[1].split("def build_report(")[0], _ns)
strategy_name = _ns["strategy_name"]

REG = {
    "bt6": ("BT6 hot water charging", "BT6 varmvatten laddning"),
    "bt7": ("BT7 hot water top", "BT7 varmvatten topp"),
    "bt1": ("BT1 outdoor", "BT1 utetemperatur"),
    "bt50": ("BT50 room average", "BT50 rumsmedel"),
    "hw_mode": ("Hot water demand mode (40057)", "Varmvattenläge (40057)"),
    "offset": ("Heating offset climate system 1 (40031)", "Värmeförskjutning klimatsystem 1 (40031)"),
    "spa": ("Smart price adaption (40844)", "Smart price adaption (40844)"),
    "hw_start_low": ("HW start temp low/SMALL (40061)", "VV starttemp låg/SMALL (40061)"),
    "hw_stop_low": ("HW stop temp low/SMALL (40065)", "VV stopptemp låg/SMALL (40065)"),
    "hw_start_normal": ("HW start temp normal/MEDIUM (40060)", "VV starttemp normal/MEDIUM (40060)"),
    "hw_stop_normal": ("HW stop temp normal/MEDIUM (40064)", "VV stopptemp normal/MEDIUM (40064)"),
    "hw_start_high": ("HW start temp high/LARGE (40059)", "VV starttemp hög/LARGE (40059)"),
    "hw_stop_high": ("HW stop temp high/LARGE (40063)", "VV stopptemp hög/LARGE (40063)"),
    "e_heat_prod": ("Energy log: produced for heating past hour (32284)", "Energilogg: producerat värme senaste timmen (32284)"),
    "e_hw_prod": ("Energy log: produced for hot water past hour (32286)", "Energilogg: producerat varmvatten senaste timmen (32286)"),
    "e_heat_used": ("Energy log: used for heating past hour (32292)", "Energilogg: förbrukat värme senaste timmen (32292)"),
    "e_hw_used": ("Energy log: used for hot water past hour (32294)", "Energilogg: förbrukat varmvatten senaste timmen (32294)"),
    "e_heat_add": ("Energy log: add. heater heating past hour (32300)", "Energilogg: elpatron värme senaste timmen (32300)"),
    "e_hw_add": ("Energy log: add. heater hot water past hour (32302)", "Energilogg: elpatron varmvatten senaste timmen (32302)"),
}


def build(lang: str) -> dict:
    sv = lang == "sv"

    def L(en, s):
        return s if sv else en

    hw, ht = search_strategies(), default_heat_strategies()
    return {
        "config": {
            "step": {
                "user": {
                    "title": "Nibe Price Optimizer",
                    "description": L(
                        "Requires the Nibe Heat Pump (Modbus) integration and a Nord Pool price sensor with raw_today/raw_tomorrow (HACS Nordpool). Nothing is written to the pump during the trial.",
                        "Kräver integrationen Nibe Heat Pump (Modbus) och en Nord Pool-prissensor med raw_today/raw_tomorrow (HACS Nordpool). Under provperioden skrivs ingenting till pumpen."),
                    "data": {
                        "nibe_entry": L("Nibe heat pump", "Nibe-värmepump"),
                        "price_sensor": L("Nord Pool price sensor", "Nord Pool-prissensor"),
                        "grid_fee": L("Grid fee and other costs per kWh (on top of the spot price)", "Nätavgift och övriga kostnader per kWh (utöver spotpriset)"),
                        "trial_days": L("Trial period (days)", "Provperiod (dagar)"),
                    },
                    "data_description": {
                        "grid_fee": L(
                            "Everything you pay per kWh on top of the spot price in the price sensor: grid transfer fee, energy tax, supplier markup – incl. VAT if the price sensor includes VAT.",
                            "Allt du betalar per kWh utöver spotpriset i prissensorn: elöverföring, energiskatt, elbolagets påslag – inkl. moms om prissensorn har moms."),
                        "trial_days": L("History from the recorder is imported first, so the trial may finish sooner.",
                                        "Historik från recordern läses in först, så provperioden kan bli klar tidigare."),
                    },
                },
                "entities": {
                    "title": L("Heat pump entities", "Värmepumpens entiteter"),
                    "description": L(
                        "Found {found} of {total} entities automatically by Modbus register. Check them and fill in any missing ones.",
                        "Hittade {found} av {total} entiteter automatiskt via Modbus-register. Kontrollera och fyll i det som saknas."),
                    "data": {k: v[1] if sv else v[0] for k, v in REG.items()},
                },
            },
            "error": {
                "price_no_raw": L("The sensor has no raw_today attribute – use the HACS Nordpool integration.",
                                  "Sensorn saknar attributet raw_today – använd HACS-integrationen Nordpool."),
                "missing_required": L("BT6, BT1, hot water mode and the hot water energy log are required.",
                                      "BT6, BT1, varmvattenläge och varmvattnets energilogg krävs."),
            },
            "abort": {
                "no_nibe": L("No Nibe Heat Pump integration found.", "Hittade ingen Nibe Heat Pump-integration."),
                "already_configured": L("This heat pump is already configured.", "Den här värmepumpen är redan konfigurerad."),
            },
        },
        "options": {"step": {"init": {"title": L("Settings", "Inställningar"), "data": {
            "grid_fee": L("Grid fee and other costs per kWh", "Nätavgift och övriga kostnader per kWh"),
            "trial_days": L("Trial period (days)", "Provperiod (dagar)"),
            "publish_hour": L("Hour when tomorrow's prices are published", "Timme då morgondagens priser publiceras"),
            "base_offset": L("Base heating offset", "Grundförskjutning värme"),
            "max_indoor_dev": L("Max indoor deviation (± °C)", "Max avvikelse inne (± °C)"),
            "indoor_sensor": L("Indoor temperature sensor (optional, otherwise BT50)", "Innetemperaturgivare (valfri, annars BT50)"),
            "indoor_target": L("Normal indoor temperature (°C)", "Normal innetemperatur (°C)"),
            "disable_spa": L("Turn off Nibe Smart Price Adaption while heating control is active",
                             "Stäng av Nibes Smart Price Adaption när värmestyrningen är aktiv"),
        }}}},
        "entity": {
            "sensor": {
                "trial": {"name": L("Trial period", "Provperiod"),
                          "state": {"running": L("Running", "Pågår"), "complete": L("Complete", "Klar")}},
                "hw_decision": {"name": L("Hot water decision", "Varmvattenbeslut")},
                "heat_decision": {"name": L("Heating offset decision", "Beslut värmeförskjutning")},
                "hw_recommendation": {"name": L("Hot water recommendation", "Rekommendation varmvatten")},
                "heat_recommendation": {"name": L("Heating recommendation", "Rekommendation värme")},
                "hot_water_cop": {"name": L("Hot water COP", "Varmvatten-COP")},
                "heating_cop": {"name": L("Heating COP", "Värme-COP")},
            },
            "select": {
                "hw_strategy": {"name": L("Hot water strategy", "Varmvattenstrategi"),
                                "state": {s.key: strategy_name(s.key, s.name, lang) for s in hw}},
                "heat_strategy": {"name": L("Heating strategy", "Värmestrategi"),
                                  "state": {s.key: strategy_name(s.key, s.name, lang) for s in ht}},
            },
            "switch": {
                "hw_active": {"name": L("Hot water control active", "Varmvattenstyrning aktiv")},
                "heat_active": {"name": L("Heating control active", "Värmestyrning aktiv")},
            },
            "button": {
                "generate_report": {"name": L("Generate report", "Skapa rapport")},
                "apply_recommendation": {"name": L("Apply recommendation", "Använd rekommendationen")},
                "restart_trial": {"name": L("Restart trial", "Starta om provperioden")},
            },
        },
        "services": {
            "generate_report": {"name": L("Generate report", "Skapa rapport"),
                                "description": L("Evaluates all strategies and writes a Markdown report to /config/nibe_price_optimizer/.",
                                                 "Utvärderar alla strategier och skriver en Markdown-rapport till /config/nibe_price_optimizer/.")},
            "apply_recommendation": {"name": L("Apply recommendation", "Använd rekommendationen"),
                                     "description": L("Selects the recommended strategies (does not turn on active control).",
                                                      "Väljer de rekommenderade strategierna (slår inte på skarp styrning).")},
            "restart_trial": {"name": L("Restart trial", "Starta om provperioden"),
                              "description": L("Clears collected data and starts a new trial.",
                                               "Rensar insamlad data och startar en ny provperiod.")},
        },
    }


if __name__ == "__main__":
    d = os.path.join(ROOT, "custom_components/nibe_price_optimizer")
    for path, lang in (("strings.json", "en"), ("translations/en.json", "en"), ("translations/sv.json", "sv")):
        with open(os.path.join(d, path), "w", encoding="utf-8") as fh:
            json.dump(build(lang), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print("translations written")
