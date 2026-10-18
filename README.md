# Nibe Price Optimizer

*[Svenska](README.sv.md)*

A Home Assistant integration that finds out – **with your own data** – how a Nibe heat pump
should be controlled by the Nord Pool spot price, before it is allowed to touch the pump.

1. **Trial period (default 14 days, nothing is written to the pump).** The integration learns
   how *your* pump and house behave and runs a set of hot water and heating strategies in
   parallel as dry runs ("shadow mode").
2. **Report.** Every strategy is replayed through your real hot water use and the real prices,
   including the grid fee you enter. You get kWh, cost, number of charges, COP per temperature
   band and a recommendation.
3. **Active control (optional).** Pick a strategy and flip a switch. Turning it off restores the
   pump to a neutral state.

> Status: **0.1 – beta.** Developed and verified on a Nibe S1255 (Sweden, SE3) in October 2026.

## Requirements

| | |
|---|---|
| Heat pump | Nibe **S-series** (S1155/S1255/S2125 + VVM …) connected with the core **[Nibe Heat Pump](https://www.home-assistant.io/integrations/nibe_heatpump/)** integration (Modbus). Hot water demand mode must have the options `SMALL / MEDIUM / LARGE`. |
| Prices | A Nord Pool sensor with `raw_today` / `raw_tomorrow` attributes – the HACS **[Nordpool](https://github.com/custom-components/nordpool)** integration. 15-minute and hourly prices both work. |
| Recorder | Used to import the history you already have, so the trial starts with data. |
| Home Assistant | 2025.1 or newer. |

F-series pumps use other registers and mode names; you can map the entities by hand, but they
are not tested yet.

## Installation

**HACS (custom repository):** HACS → ⋮ → *Custom repositories* → add
`https://github.com/thundrM/nibe-price-optimizer`, type *Integration* → install → restart.

**Manual:** copy `custom_components/nibe_price_optimizer` to `/config/custom_components/` and restart.

Then *Settings → Devices & services → Add integration → Nibe Price Optimizer*.

## Setup

1. Choose the Nibe heat pump and the Nord Pool price sensor.
2. **Grid fee per kWh** – everything you pay per kWh *on top of* the spot price in the price
   sensor: grid transfer fee, energy tax and supplier markup (incl. VAT if your price sensor
   includes VAT). This decides a lot: with 1 kr/kWh on top, an extra kWh is never cheap, even
   when the spot price is zero.
3. Trial length (days).
4. The entities are found automatically by Modbus register (e.g. `…-40057` = hot water demand
   mode). Check them and fill in anything missing.

Options (later, via *Configure*): grid fee, trial length, the hour tomorrow's prices are
published, base heating offset, comfort band (± °C), indoor sensor and target, and whether Nibe's
own *Smart Price Adaption* is turned off while heating control is active.

## What you get

| Entity | |
|---|---|
| `sensor.…_trial_period` | Running / complete, days of data, days left |
| `sensor.…_hot_water_recommendation`, `…_heating_recommendation` | Best strategy and saving per day/month |
| `sensor.…_hot_water_decision`, `…_heating_offset_decision` | What the selected strategy wants right now, and why |
| `sensor.…_hot_water_cop`, `…_heating_cop` | Measured COP + learned model (attributes) |
| One sensor per strategy | Its live shadow decision + trial result (cost/day, kWh/day, charges, LARGE share) |
| `select.…_hot_water_strategy`, `…_heating_strategy` | Strategy used for active control |
| `switch.…_hot_water_control_active`, `…_heating_control_active` | **Off by default** |
| Buttons | Generate report · Apply recommendation · Restart trial |

When the trial is complete you get a notification, and the report is written to
`/config/nibe_price_optimizer/report_latest.md`. Services: `nibe_price_optimizer.generate_report`
(returns the path), `apply_recommendation`, `restart_trial`.

## Hot water strategies

Nibe charges hot water when BT6 drops below the start temperature of the current mode and stops
at the stop temperature (SMALL 40→44, MEDIUM 44→48, LARGE 48→51 by default – read from your
pump). The strategies only choose the mode.

| Strategy | Idea |
|---|---|
| Always SMALL / MEDIUM / LARGE | Baselines. *Always MEDIUM* is the reference for savings. |
| Price quartiles | LARGE when the price is in the cheapest quarter of the next 24 h and ≥ 0.50 below the expensive quarter; SMALL when expensive; hysteresis. |
| LARGE only ≤ X | LARGE only below an absolute spot price. |
| **Forecast LARGE** | Avoid LARGE. Predict when Nibe would charge next in MEDIUM (using your learned tapping profile). Use LARGE only if that charge would be ≥ X more expensive than now **and** the extra heat lasts past it. Two variants of "lasts past it": the next charge must cost ≤ now + 0.30 (strict), or be ≥ X cheaper than the avoided one. Only prices that are actually known are used – before tomorrow's prices are published it never guesses. |
| … without SMALL | Same, but never SMALL. |

## Heating strategies

Price shifting of the heating curve offset around your base offset: pre-heat (+1/+2) when it is
cheap now and clearly more expensive in the next 18 h, lower (−1/−2) when it is expensive compared
with the cheap hours around. A comfort band stops it from moving the (estimated) indoor
temperature more than ± X °C.

## How the evaluation works

* **Same tapping for every strategy.** The BT6 fall that really happened (showers, standby) is
  replayed through every strategy, so day-to-day differences in use do not decide the result –
  this is what makes a 14-day trial meaningful. (Measured directly, hot water use varies by about
  ±1 kWh/day, which hides any difference between control strategies for weeks.)
* **Honest prices.** A strategy only sees the prices that were known at that moment.
* **Learned model:** kWh heat per °C on BT6, charge rate, temperature-dependent standby loss,
  hourly tapping profile and **COP per BT6 temperature band** (from Nibe's energy log). The COP
  bands matter most: on an S1255 the hot water COP was ≈3.7 below 44 °C, ≈3.0 at 44–48 °C and
  ≈2.6 at 48–51 °C – storing heat in LARGE costs ~30 % more electricity per kWh.
* **Cost** = electricity × (spot price + your grid fee). The end state of the tank/house is valued
  so that a strategy cannot "win" by ending colder.
* **Heating is an estimate:** a one-mass house model (heat loss learned from the data, heat
  capacity assumed) – treat it as a direction, not an exact amount.

## Active control and safety

* Nothing is written while the switches are off.
* Values are only written when they change (spares the pump's memory).
* Turning hot water control off sets MEDIUM; turning heating control off sets the base offset and
  restores Smart Price Adaption if it was on.
* **Disable your own automations** that write the same registers (40057 hot water mode, 40031
  offset) before you activate – otherwise they fight.
* If Home Assistant stops, the pump keeps the last mode/offset. Consider keeping a reserve plan
  (e.g. SPA) for longer outages.

## Notes from the original installation

* Nibe's "energy used over the past hour" sensors are updated ~40 s after each full hour.
  Long-term statistics of these sensors with `max` double count the previous hour – use the mean
  (this integration samples the value itself).
* Big taps drop BT6 to ~20 °C; Nibe starts charging by itself. No strategy can do better then, so
  those moments are not counted as savings.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
python scripts/generate_translations.py   # after adding strategies or fields
```

The calculation core (`core/`) is pure Python without Home Assistant imports and can be used on
exported data.

## License

MIT
