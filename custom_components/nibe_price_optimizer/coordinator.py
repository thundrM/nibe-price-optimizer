"""The optimizer engine: collects data, runs the trial, evaluates strategies, controls the pump."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_OFFSET,
    CONF_DISABLE_SPA,
    CONF_ENTITIES,
    CONF_GRID_FEE,
    CONF_INDOOR_SENSOR,
    CONF_INDOOR_TARGET,
    CONF_MAX_INDOOR_DEV,
    CONF_PRICE_SENSOR,
    CONF_PUBLISH_HOUR,
    CONF_TRIAL_DAYS,
    DEFAULT_BASE_OFFSET,
    DEFAULT_GRID_FEE,
    DEFAULT_INDOOR_TARGET,
    DEFAULT_MAX_INDOOR_DEV,
    DEFAULT_PUBLISH_HOUR,
    DEFAULT_TRIAL_DAYS,
    DOMAIN,
    EVALUATE_EVERY_HOURS,
    EVALUATE_MAX_DAYS,
    KEEP_DAYS,
    STORAGE_VERSION,
    UPDATE_MINUTES,
)
from .core import hotwater as hw
from .core import heating as ht
from .core.model import HeatingModel, HotWaterModel, HourObs, QuarterObs, cop_table, learn_heating, learn_hot_water
from .core.prices import Quarter, merge, parse_raw
from .history import async_backfill, known_until_rule

_LOGGER = logging.getLogger(__name__)

HW_BASELINE = "hw_fixed_medium"
HEAT_BASELINE = "heat_none"
DEFAULT_HW_CHOICE = "hw_forecast_70"
DEFAULT_HEAT_CHOICE = "heat_price_2"


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _obs_to_dict(o: QuarterObs) -> dict:
    return {"t": o.t.isoformat(), "bt6": o.bt6, "bt7": o.bt7, "m": o.hw_mode, "o": o.offset, "u": o.outdoor,
            "i": o.indoor, "p": o.price, "k": o.known_until.isoformat() if o.known_until else None}


def _obs_from_dict(d: dict) -> QuarterObs:
    return QuarterObs(t=datetime.fromisoformat(d["t"]), bt6=d["bt6"], bt7=d.get("bt7"), hw_mode=d.get("m"),
                      offset=d.get("o"), outdoor=d.get("u"), indoor=d.get("i"), price=d.get("p"),
                      known_until=datetime.fromisoformat(d["k"]) if d.get("k") else None)


def _hour_to_dict(h: HourObs) -> dict:
    d = dict(h.__dict__)
    d["t"] = h.t.isoformat()
    return d


def _hour_from_dict(d: dict) -> HourObs:
    return HourObs(**{**d, "t": datetime.fromisoformat(d["t"])})


class Optimizer(DataUpdateCoordinator[dict]):
    """One instance per config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        self.store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.obs: list[QuarterObs] = []
        self.hours: list[HourObs] = []
        self.prices: dict[datetime, float] = {}
        self.hw_model = HotWaterModel()
        self.heat_model = HeatingModel()
        self.results: dict[str, Any] = {}
        self.live: dict[str, Any] = {"hw": {}, "heat": {}}
        self.sim_memory: dict[str, dict] = {}
        self.trial_start: datetime | None = None
        self.report_done = False
        self.last_report: str | None = None
        self.choice = {"hw": DEFAULT_HW_CHOICE, "heat": DEFAULT_HEAT_CHOICE}
        self.active = {"hw": False, "heat": False}
        self.spa_was_on: bool | None = None
        self._hour_rise = 0.0
        self._last_bt6: float | None = None
        self._last_eval: datetime | None = None
        self._unsubs: list = []
        self.hw_strategies = {s.key: s for s in hw.search_strategies()}
        self.heat_strategies = {s.key: s for s in ht.default_heat_strategies()}

    # ── configuration ────────────────────────────────────────────────────────
    @property
    def cfg(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def ents(self) -> dict[str, str]:
        return self.entry.data.get(CONF_ENTITIES, {})

    @property
    def fee(self) -> float:
        return float(self.cfg.get(CONF_GRID_FEE, DEFAULT_GRID_FEE))

    @property
    def trial_days(self) -> int:
        return int(self.cfg.get(CONF_TRIAL_DAYS, DEFAULT_TRIAL_DAYS))

    @property
    def base_offset(self) -> int:
        return int(self.cfg.get(CONF_BASE_OFFSET, DEFAULT_BASE_OFFSET))

    @property
    def max_dev(self) -> float:
        return float(self.cfg.get(CONF_MAX_INDOOR_DEV, DEFAULT_MAX_INDOOR_DEV))

    @property
    def data_days(self) -> float:
        if len(self.obs) < 2:
            return 0.0
        return (self.obs[-1].t - self.obs[0].t).total_seconds() / 86400

    @property
    def trial_complete(self) -> bool:
        return self.data_days >= self.trial_days

    def state_of(self, key: str) -> Any:
        eid = self.ents.get(key)
        st = self.hass.states.get(eid) if eid else None
        return st.state if st else None

    def thresholds(self) -> hw.Thresholds:
        def pair(a, b, default):
            x, y = _f(self.state_of(a)), _f(self.state_of(b))
            return (x, y) if x is not None and y is not None and y > x else default

        d = hw.Thresholds()
        return hw.Thresholds(
            small=pair("hw_start_low", "hw_stop_low", d.small),
            medium=pair("hw_start_normal", "hw_stop_normal", d.medium),
            large=pair("hw_start_high", "hw_stop_high", d.large),
        )

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        await self._load()
        if not self.obs:
            obs, hours, prices = await async_backfill(
                self.hass, self.ents, self.cfg[CONF_PRICE_SENSOR], min(14, self.trial_days),
                int(self.cfg.get(CONF_PUBLISH_HOUR, DEFAULT_PUBLISH_HOUR)))
            self.obs, self.hours, self.prices = obs, hours, prices
        if self.trial_start is None:
            self.trial_start = self.obs[0].t if self.obs else dt_util.utcnow()
        self._unsubs.append(async_track_time_change(self.hass, self._on_quarter, minute=list(UPDATE_MINUTES), second=30))
        self._unsubs.append(async_track_time_change(self.hass, self._on_hour, minute=5, second=0))
        if self.ents.get("bt6"):
            self._unsubs.append(async_track_state_change_event(self.hass, [self.ents["bt6"]], self._on_bt6))
        self.hass.async_create_task(self.async_evaluate())
        await self._on_quarter(dt_util.utcnow())

    async def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        await self._save()

    async def _load(self) -> None:
        data = await self.store.async_load() or {}
        self.obs = [_obs_from_dict(d) for d in data.get("obs", [])]
        self.hours = [_hour_from_dict(d) for d in data.get("hours", [])]
        self.prices = {datetime.fromisoformat(k): v for k, v in data.get("prices", {}).items()}
        self.hw_model = HotWaterModel.from_dict(data.get("hw_model"))
        self.heat_model = HeatingModel.from_dict(data.get("heat_model"))
        self.results = data.get("results", {})
        self.sim_memory = data.get("memory", {})
        self.trial_start = datetime.fromisoformat(data["trial_start"]) if data.get("trial_start") else None
        self.report_done = data.get("report_done", False)
        self.last_report = data.get("last_report")
        self.choice.update(data.get("choice", {}))
        self.active.update(data.get("active", {}))
        self.spa_was_on = data.get("spa_was_on")

    def _data(self) -> dict:
        return {
            "obs": [_obs_to_dict(o) for o in self.obs],
            "hours": [_hour_to_dict(h) for h in self.hours],
            "prices": {k.isoformat(): v for k, v in self.prices.items()},
            "hw_model": self.hw_model.as_dict(),
            "heat_model": self.heat_model.as_dict(),
            "results": self.results,
            "memory": self.sim_memory,
            "trial_start": self.trial_start.isoformat() if self.trial_start else None,
            "report_done": self.report_done,
            "last_report": self.last_report,
            "choice": self.choice,
            "active": self.active,
            "spa_was_on": self.spa_was_on,
        }

    async def _save(self) -> None:
        await self.store.async_save(self._data())

    def _save_later(self) -> None:
        self.store.async_delay_save(self._data, 60)

    # ── data collection ──────────────────────────────────────────────────────
    @callback
    def _on_bt6(self, event: Event) -> None:
        new = event.data.get("new_state")
        v = _f(new.state) if new else None
        if v is None:
            return
        if self._last_bt6 is not None and v > self._last_bt6:
            self._hour_rise += v - self._last_bt6
        self._last_bt6 = v

    def _known_prices(self) -> list[Quarter]:
        st = self.hass.states.get(self.cfg[CONF_PRICE_SENSOR])
        if st is None:
            return []
        return merge(parse_raw(st.attributes.get("raw_today")), parse_raw(st.attributes.get("raw_tomorrow")))

    async def _on_quarter(self, now: datetime) -> None:
        now = dt_util.utcnow().replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % 15)
        known = self._known_prices()
        for q in known:
            self.prices[dt_util.as_utc(q.start)] = q.price
        known_until = dt_util.as_utc(known[-1].end) if known else known_until_rule(now, DEFAULT_PUBLISH_HOUR)
        indoor_eid = self.cfg.get(CONF_INDOOR_SENSOR)
        indoor = _f(self.hass.states.get(indoor_eid).state) if indoor_eid and self.hass.states.get(indoor_eid) else _f(self.state_of("bt50"))
        o = QuarterObs(
            t=now, bt6=_f(self.state_of("bt6")), bt7=_f(self.state_of("bt7")), hw_mode=self.state_of("hw_mode"),
            offset=_f(self.state_of("offset")), outdoor=_f(self.state_of("bt1")), indoor=indoor,
            price=self.prices.get(now), known_until=known_until,
        )
        if o.bt6 is not None and (not self.obs or self.obs[-1].t < now):
            self.obs.append(o)
        self._trim()
        upcoming = [Quarter(t, p) for t, p in sorted(self.prices.items()) if t + timedelta(minutes=15) > now and t < known_until]
        self._live_decisions(o, upcoming)
        await self._control(o)
        if self._last_eval is None or dt_util.utcnow() - self._last_eval > timedelta(hours=EVALUATE_EVERY_HOURS):
            self.hass.async_create_task(self.async_evaluate())
        if self.trial_complete and not self.report_done:
            self.report_done = True  # set before the task so it is only created once
            self.hass.async_create_task(self.async_report(notify=True))
        self._save_later()
        self.async_set_updated_data(self._snapshot())

    async def _on_hour(self, now: datetime) -> None:
        """Energy log 'over the past hour' values are final a few seconds after the full hour."""
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        q = [o for o in self.obs if start <= o.t < start + timedelta(hours=1)]
        h = HourObs(
            t=start,
            hw_used=_f(self.state_of("e_hw_used")) or 0.0, hw_prod=_f(self.state_of("e_hw_prod")) or 0.0,
            hw_add=_f(self.state_of("e_hw_add")) or 0.0, heat_used=_f(self.state_of("e_heat_used")) or 0.0,
            heat_prod=_f(self.state_of("e_heat_prod")) or 0.0, heat_add=_f(self.state_of("e_heat_add")) or 0.0,
            bt6_mean=sum(x.bt6 for x in q) / len(q) if q else None, bt6_rise=self._hour_rise,
            outdoor=q[0].outdoor if q else None, offset=q[0].offset if q else None,
        )
        self._hour_rise = 0.0
        if not self.hours or self.hours[-1].t < start:
            self.hours.append(h)
        self._save_later()

    def _trim(self) -> None:
        limit = dt_util.utcnow() - timedelta(days=KEEP_DAYS)
        self.obs = [o for o in self.obs if o.t >= limit]
        self.hours = [h for h in self.hours if h.t >= limit]
        self.prices = {t: p for t, p in self.prices.items() if t >= limit}

    # ── live decisions (shadow for every strategy) ───────────────────────────
    def _live_decisions(self, o: QuarterObs, upcoming: list[Quarter]) -> None:
        if o.bt6 is None or not upcoming:
            return
        thr = self.thresholds()
        local_q = [Quarter(dt_util.as_local(q.start), q.price) for q in upcoming]
        now_l = dt_util.as_local(o.t)
        for key, strat in self.hw_strategies.items():
            mem = self.sim_memory.setdefault(key, {"mode": hw.MEDIUM, "since": o.t.isoformat()})
            if key == self.choice["hw"] and self.active["hw"] and o.hw_mode in (hw.SMALL, hw.MEDIUM, hw.LARGE):
                real = self.hass.states.get(self.ents["hw_mode"])
                mem = {"mode": o.hw_mode, "since": (real.last_changed if real else o.t).isoformat()}
            ctx = hw.Context(now_l, o.bt6, mem["mode"], datetime.fromisoformat(mem["since"]), local_q,
                             self.hw_model, thr, self.fee)
            d = strat.decide(ctx)
            if d.mode != mem["mode"]:
                mem = {"mode": d.mode, "since": o.t.isoformat()}
            self.sim_memory[key] = mem
            self.live["hw"][key] = {"mode": d.mode, "reason": d.reason}
        target = float(self.cfg.get(CONF_INDOOR_TARGET, DEFAULT_INDOOR_TARGET))
        dev = (o.indoor - target) if o.indoor is not None else 0.0
        for key, strat in self.heat_strategies.items():
            step, why = strat.decide(ht.HeatContext(now_l, local_q, dev, self.max_dev))
            self.live["heat"][key] = {"step": step, "offset": max(-10, min(10, self.base_offset + step)), "reason": why}

    # ── control ──────────────────────────────────────────────────────────────
    async def _control(self, o: QuarterObs) -> None:
        if self.active["hw"] and self.ents.get("hw_mode"):
            want = self.live["hw"].get(self.choice["hw"], {}).get("mode")
            if want and want != o.hw_mode:
                await self.hass.services.async_call("select", "select_option",
                                                    {"entity_id": self.ents["hw_mode"], "option": want}, blocking=False)
        if self.active["heat"] and self.ents.get("offset"):
            want = self.live["heat"].get(self.choice["heat"], {}).get("offset")
            if want is not None and (o.offset is None or int(o.offset) != int(want)):
                await self.hass.services.async_call("number", "set_value",
                                                    {"entity_id": self.ents["offset"], "value": want}, blocking=False)

    async def async_set_active(self, kind: str, on: bool) -> None:
        self.active[kind] = on
        if kind == "heat" and self.cfg.get(CONF_DISABLE_SPA, True) and self.ents.get("spa"):
            if on:
                self.spa_was_on = self.state_of("spa") == "on"
                if self.spa_was_on:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": self.ents["spa"]})
            elif self.spa_was_on:
                await self.hass.services.async_call("switch", "turn_on", {"entity_id": self.ents["spa"]})
                self.spa_was_on = None
        if not on:
            # back to a neutral, safe state
            if kind == "hw" and self.ents.get("hw_mode"):
                await self.hass.services.async_call("select", "select_option",
                                                    {"entity_id": self.ents["hw_mode"], "option": hw.MEDIUM})
            if kind == "heat" and self.ents.get("offset"):
                await self.hass.services.async_call("number", "set_value",
                                                    {"entity_id": self.ents["offset"], "value": self.base_offset})
        else:
            await self._on_quarter(dt_util.utcnow())
        await self._save()
        self.async_set_updated_data(self._snapshot())

    async def async_set_choice(self, kind: str, key: str) -> None:
        self.choice[kind] = key
        await self._save()
        if self.active[kind]:
            await self._on_quarter(dt_util.utcnow())
        else:
            self.async_set_updated_data(self._snapshot())

    # ── evaluation ───────────────────────────────────────────────────────────
    def _evaluate_sync(self) -> dict:
        since = dt_util.utcnow() - timedelta(days=EVALUATE_MAX_DAYS)
        obs = [QuarterObs(**{**o.__dict__, "t": dt_util.as_local(o.t),
                             "known_until": dt_util.as_local(o.known_until) if o.known_until else None})
               for o in self.obs if o.t >= since]
        hours = [HourObs(**{**h.__dict__, "t": dt_util.as_local(h.t)}) for h in self.hours if h.t >= since]
        prices = {dt_util.as_local(t): p for t, p in self.prices.items()}
        hw_model = learn_hot_water(obs, hours)
        heat_model = learn_heating(hours)
        thr = self.thresholds()
        hw_res = {r.key: r.as_dict() for r in hw.evaluate(list(self.hw_strategies.values()), obs, prices, hw_model, thr, self.fee)}
        ku = {dt_util.as_local(o.t): dt_util.as_local(o.known_until) for o in self.obs if o.known_until and o.t.minute == 0}
        heat_res = {}
        if any(h.heat_prod > 0 for h in hours):
            for s in self.heat_strategies.values():
                heat_res[s.key] = ht.replay_heating(s, hours, prices, ku, heat_model, self.fee, self.base_offset, self.max_dev).as_dict()
        return {"hw_model": hw_model, "heat_model": heat_model, "hw": hw_res, "heat": heat_res, "cop": cop_table(hours)}

    async def async_evaluate(self) -> None:
        if len(self.obs) < 96:
            return
        try:
            out = await self.hass.async_add_executor_job(self._evaluate_sync)
        except Exception:  # noqa: BLE001 - never break data collection
            _LOGGER.exception("Evaluation failed")
            return
        self.hw_model, self.heat_model = out.pop("hw_model"), out.pop("heat_model")
        out["recommendation"] = self._recommend(out)
        out["evaluated"] = dt_util.utcnow().isoformat()
        out["data_days"] = round(self.data_days, 2)
        self.results = out
        self._last_eval = dt_util.utcnow()
        self._save_later()
        self.async_set_updated_data(self._snapshot())

    def _recommend(self, out: dict) -> dict:
        rec: dict[str, Any] = {}
        hw_res = out.get("hw", {})
        base = hw_res.get(HW_BASELINE)
        cands = [r for k, r in hw_res.items() if not k.startswith("hw_fixed_")]
        if base and cands:
            best = min(cands, key=lambda r: r["cost_per_day"])
            rec["hw"] = {"key": best["key"], "name": best["name"],
                         "saving_per_day": round(base["cost_per_day"] - best["cost_per_day"], 2),
                         "saving_per_month": round((base["cost_per_day"] - best["cost_per_day"]) * 30, 1)}
            small = hw_res.get("hw_fixed_small")
            if small and small["cost_per_day"] < best["cost_per_day"]:
                rec["hw"]["note_small"] = round(best["cost_per_day"] - small["cost_per_day"], 2)
        heat = out.get("heat", {})
        hb = heat.get(HEAT_BASELINE)
        if hb:
            ok = [r for r in heat.values() if r["indoor_max_dev"] <= self.max_dev and r["indoor_min_dev"] >= -self.max_dev]
            best = min(ok, key=lambda r: r["cost_per_day"]) if ok else hb
            rec["heat"] = {"key": best["key"], "name": best["name"],
                           "saving_per_day": round(hb["cost_per_day"] - best["cost_per_day"], 2),
                           "saving_per_month": round((hb["cost_per_day"] - best["cost_per_day"]) * 30, 1)}
        return rec

    async def async_apply_recommendation(self) -> None:
        rec = self.results.get("recommendation", {})
        for kind in ("hw", "heat"):
            if kind in rec:
                await self.async_set_choice(kind, rec[kind]["key"])

    async def async_report(self, notify: bool = False) -> str:
        from .report import build_report, write_report

        await self.async_evaluate()
        lang = "sv" if (self.hass.config.language or "").startswith("sv") else "en"
        text = build_report(self, lang)
        path = await self.hass.async_add_executor_job(write_report, self.hass.config.path(DOMAIN), text)
        self.last_report = path
        # Only the trial-end report counts as "done": a manual report during the trial
        # must not stop the automatic report and notification when the trial completes.
        if self.trial_complete:
            self.report_done = True
        if notify:
            from homeassistant.components import persistent_notification

            rec = self.results.get("recommendation", {})
            if self.trial_complete:
                title = "Nibe Price Optimizer – " + ("provperioden är klar" if lang == "sv" else "trial complete")
            else:
                title = "Nibe Price Optimizer – " + ("rapport (provperioden pågår)" if lang == "sv" else "report (trial in progress)")
            lines = []
            if "hw" in rec:
                lines.append(("Varmvatten" if lang == "sv" else "Hot water") + f": {rec['hw']['name']} "
                             f"({rec['hw']['saving_per_month']:+.1f}/" + ("mån" if lang == "sv" else "month") + ")")
            if "heat" in rec:
                lines.append(("Värme" if lang == "sv" else "Heating") + f": {rec['heat']['name']} "
                             f"({rec['heat']['saving_per_month']:+.1f}/" + ("mån" if lang == "sv" else "month") + ")")
            lines.append(("Rapport" if lang == "sv" else "Report") + f": {path}")
            persistent_notification.async_create(self.hass, "\n\n".join(lines), title, f"{DOMAIN}_{self.entry.entry_id}")
        await self._save()
        self.async_set_updated_data(self._snapshot())
        return path

    async def async_restart_trial(self) -> None:
        self.trial_start = dt_util.utcnow()
        self.obs = [o for o in self.obs if o.t >= self.trial_start]
        self.hours = [h for h in self.hours if h.t >= self.trial_start]
        self.report_done = False
        self.results = {}
        await self._save()
        self.async_set_updated_data(self._snapshot())

    def _snapshot(self) -> dict:
        return {
            "live": self.live, "results": self.results, "choice": dict(self.choice), "active": dict(self.active),
            "data_days": round(self.data_days, 2), "trial_days": self.trial_days, "trial_complete": self.trial_complete,
            "hw_model": self.hw_model.as_dict(), "heat_model": self.heat_model.as_dict(), "last_report": self.last_report,
        }
