"""Markdown report of the trial (Swedish or English)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import Optimizer

T = {
    "sv": {
        "title": "Nibe Price Optimizer – rapport",
        "data": "Underlag", "days": "dygn data", "fee": "Nätkostnad (din inställning)", "per_kwh": "per kWh",
        "learned": "Inlärd modell", "hw": "Varmvatten", "heat": "Värme",
        "kwh_per_deg": "kWh värme per °C på BT6", "charge_rate": "Laddtakt (°C/h)", "loss": "Extra förlust per °C över 45",
        "cop_band": "COP per temperaturband (BT6 under …)", "cop_out": "Värme-COP per utetemperatur (under …)",
        "measured": "Uppmätt (Nibes energilogg)", "cop_hw": "Varmvatten-COP", "cop_heat": "Värme-COP",
        "kwh_hw": "kWh el varmvatten", "kwh_heat": "kWh el värme",
        "strategies": "Strategier (samma tappning och priser för alla)", "strategy": "Strategi",
        "cost_day": "Kostnad/dygn", "kwh_day": "kWh/dygn", "charges_day": "Laddningar/dygn", "large": "Andel LARGE",
        "vs_base": "mot grund", "indoor": "Inne ±°C (uppskattat)",
        "rec": "Rekommendation", "saving": "besparing", "month": "mån",
        "note_small": "Alltid SMALL vore {x} billigare per dygn, men ger mindre varmvatten i tanken – välj det bara om SMALL räcker för er.",
        "how": "Så räknas det",
        "how_text": (
            "Varmvattnet simuleras på BT6: Nibe laddar när BT6 faller under läget start-temperatur och slutar vid "
            "stopp-temperaturen (läses från pumpen). Tappningen tas från det som verkligen hände, så alla strategier "
            "möter samma förbrukning. Varje strategi får bara se de priser som var kända vid tillfället "
            "(morgondagens priser efter publicering). Kostnad = el × (spotpris + nätkostnad). "
            "Värmen är en uppskattning med en enkel husmodell (värmeförlust inlärd ur datan, värmekapacitet antagen) – "
            "se den som riktning, inte exakt belopp."
        ),
        "learned_yes": "inlärt", "learned_no": "standardvärde",
    },
    "en": {
        "title": "Nibe Price Optimizer – report",
        "data": "Data", "days": "days of data", "fee": "Grid fee (your setting)", "per_kwh": "per kWh",
        "learned": "Learned model", "hw": "Hot water", "heat": "Heating",
        "kwh_per_deg": "kWh heat per °C on BT6", "charge_rate": "Charge rate (°C/h)", "loss": "Extra loss per °C above 45",
        "cop_band": "COP per temperature band (BT6 below …)", "cop_out": "Heating COP per outdoor temperature (below …)",
        "measured": "Measured (Nibe energy log)", "cop_hw": "Hot water COP", "cop_heat": "Heating COP",
        "kwh_hw": "kWh electricity hot water", "kwh_heat": "kWh electricity heating",
        "strategies": "Strategies (same tapping and prices for all)", "strategy": "Strategy",
        "cost_day": "Cost/day", "kwh_day": "kWh/day", "charges_day": "Charges/day", "large": "LARGE share",
        "vs_base": "vs base", "indoor": "Indoor ±°C (estimate)",
        "rec": "Recommendation", "saving": "saving", "month": "month",
        "note_small": "Always SMALL would be {x} cheaper per day but stores less hot water – only choose it if SMALL is enough for you.",
        "how": "How it is calculated",
        "how_text": (
            "Hot water is simulated on BT6: Nibe charges when BT6 drops below the start temperature of the mode and stops "
            "at the stop temperature (read from the pump). Tapping is taken from what really happened, so every strategy "
            "faces the same consumption. Each strategy only sees the prices that were known at that moment. "
            "Cost = electricity × (spot price + grid fee). Heating is an estimate using a simple house model "
            "(heat loss learned from data, heat capacity assumed) – treat it as a direction, not an exact amount."
        ),
        "learned_yes": "learned", "learned_no": "default",
    },
}


def strategy_name(key: str, name: str, lang: str) -> str:
    """Swedish names for the strategies (the core uses English)."""
    if lang != "sv":
        return name
    parts = key.split("_")
    try:
        if key.startswith("hw_fixed_"):
            return f"Alltid {parts[2].upper()}"
        if key.startswith("hw_quartile_"):
            return f"Priskvartiler (LARGE ≤ p25, spridning ≥ {int(parts[2]) / 100:.2f})"
        if key.startswith("hw_absolute_"):
            return f"LARGE bara ≤ {int(parts[2]) / 100:.2f} spot"
        if key.startswith("hw_forecast_"):
            return (f"Prognos-LARGE (≥ {int(parts[2]) / 100:.2f} sparat)"
                    + (", mot undviken laddning" if "_later" in key else "")
                    + (", utan SMALL" if key.endswith("_nosmall") else ""))
        if key == "heat_none":
            return "Ingen prisstyrning (grundförskjutning)"
        if key.startswith("heat_price_"):
            return f"Prisförskjutning ±{parts[2]}"
    except (IndexError, ValueError):
        pass
    return name


def build_report(opt: "Optimizer", lang: str = "en") -> str:
    t = T.get(lang, T["en"])
    r = opt.results or {}
    m, hm = opt.hw_model, opt.heat_model
    out = [f"# {t['title']}", "", f"_{dt_util.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    out += [f"## {t['data']}", "", f"- {r.get('data_days', round(opt.data_days, 1))} {t['days']}",
            f"- {t['fee']}: {opt.fee:.2f} {t['per_kwh']}", ""]

    def lv(key):
        return t["learned_yes"] if m.learned.get(key) else t["learned_no"]

    out += [f"## {t['learned']} – {t['hw']}", "",
            f"- {t['kwh_per_deg']}: {m.kwh_per_deg} ({lv('kwh_per_deg')})",
            f"- {t['charge_rate']}: {m.charge_rate} ({lv('charge_rate')})",
            f"- {t['loss']}: {m.loss_coeff} °C/h ({lv('loss_coeff')})",
            f"- {t['cop_band']}: " + ", ".join(f"{b:g} °C → {c}" for b, c in m.cop_bands), "",
            f"## {t['learned']} – {t['heat']}", "",
            f"- {t['cop_out']}: " + ", ".join(f"{b:g} °C → {c}" for b, c in hm.cop_bands), ""]
    cop = r.get("cop", {})
    if cop:
        out += [f"## {t['measured']}", "",
                f"- {t['cop_hw']}: {cop.get('hot_water_cop')} · {t['kwh_hw']}: {cop.get('hot_water_kwh')}",
                f"- {t['cop_heat']}: {cop.get('heating_cop')} · {t['kwh_heat']}: {cop.get('heating_kwh')}", ""]

    hw_res = r.get("hw", {})
    if hw_res:
        base = hw_res.get("hw_fixed_medium", {}).get("cost_per_day")
        out += [f"## {t['strategies']} – {t['hw']}", "",
                f"| {t['strategy']} | {t['cost_day']} | {t['vs_base']} | {t['kwh_day']} | {t['charges_day']} | {t['large']} |",
                "|---|---:|---:|---:|---:|---:|"]
        for x in sorted(hw_res.values(), key=lambda x: x["cost_per_day"]):
            diff = f"{x['cost_per_day'] - base:+.2f}" if base is not None else ""
            out.append(f"| {strategy_name(x['key'], x['name'], lang)} | {x['cost_per_day']:.2f} | {diff} | {x['kwh_per_day']:.2f} | "
                       f"{x['charges_per_day']:.1f} | {x['large_share'] * 100:.0f} % |")
        out.append("")
    heat = r.get("heat", {})
    if heat:
        base = heat.get("heat_none", {}).get("cost_per_day")
        out += [f"## {t['strategies']} – {t['heat']}", "",
                f"| {t['strategy']} | {t['cost_day']} | {t['vs_base']} | {t['kwh_day']} | {t['indoor']} |",
                "|---|---:|---:|---:|---:|"]
        for x in sorted(heat.values(), key=lambda x: x["cost_per_day"]):
            diff = f"{x['cost_per_day'] - base:+.2f}" if base is not None else ""
            out.append(f"| {strategy_name(x['key'], x['name'], lang)} | {x['cost_per_day']:.2f} | {diff} | {x['kwh_per_day']:.2f} | "
                       f"{x['indoor_min_dev']:+.1f}…{x['indoor_max_dev']:+.1f} |")
        out.append("")
    rec = r.get("recommendation", {})
    if rec:
        out += [f"## {t['rec']}", ""]
        for kind in ("hw", "heat"):
            if kind in rec:
                x = rec[kind]
                nm = strategy_name(x["key"], x["name"], lang)
                unit = "dygn" if lang == "sv" else "day"
                out.append(f"- **{t[kind]}: {nm}** – {t['saving']} {x['saving_per_day']:+.2f}/{unit}, "
                           f"{x['saving_per_month']:+.1f}/{t['month']}")
                if x.get("note_small"):
                    out.append(f"  - {t['note_small'].format(x=x['note_small'])}")
        out.append("")
    out += [f"## {t['how']}", "", t["how_text"], ""]
    return "\n".join(out)


def write_report(folder: str, text: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"report_{dt_util.now().strftime('%Y%m%d_%H%M')}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(folder, "report_latest.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
