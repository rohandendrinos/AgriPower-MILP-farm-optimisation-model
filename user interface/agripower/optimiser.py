"""Runs current_code's model for the UI and hands back exactly what it produces.

current_code (the teammate's repository, model in current_code/src/) is frozen.
This file only does three things around it, and adds no calculation:

  1. Feeds it inputs, in memory only. It sets attributes on the imported
     `config` module (the farm's values, the scenario flags, the year), writes the
     farm's meter data to the two CSV files the model reads (using the teammate's
     own input_data_processing_v3.prepare_model_inputs), and reloads the
     model's modules so each one's `from config import *` picks the values up.
  2. Runs the model's own functions unchanged: milp.pareto_front,
     results.summarise and results.plot_all.
  3. Swaps the solver. milp.solve_model calls `pulp.GUROBI_CMD` with a Mac-only
     path. For the duration of a run only, `pulp.GUROBI_CMD` is replaced in
     memory by a factory that returns the same solver with the same 1% gap
     (MIPGap 0.01) through gurobipy, plus a time ceiling and progress hooks. If
     Gurobi is not usable it returns PuLP's CBC with the same gap, and says so.

Nothing is written into current_code/. Spot prices, the solar profile, the meter
CSVs and bytecode all go under data/.

Two scenarios are supported, one per run (the model reads its scenario flags once
when it is loaded, so each run covers one):
  "farm_retail"      farmer ownership, farm pays the retail tariff in config.py
  "third_party_spot" third-party ownership, everything priced at AEMO spot prices
"""

import copy
import importlib
import math
import os
import sys
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib  # noqa: E402

matplotlib.use("Agg", force=True)

from agripower import tilt  # noqa: E402
from agripower.constraints import FarmConstraints  # noqa: E402

_PROJECT_DIR = Path(__file__).resolve().parents[1]

# Python normally writes bytecode caches next to the modules it imports, which
# would put a __pycache__ folder inside current_code/. Redirect them instead.
if sys.pycache_prefix is None:
    sys.pycache_prefix = str(_PROJECT_DIR / "data" / "pycache")

_CURRENT_CODE_SRC = _PROJECT_DIR / "current_code" / "src"
if str(_CURRENT_CODE_SRC) not in sys.path:
    sys.path.insert(0, str(_CURRENT_CODE_SRC))

# Everything this app writes while preparing or running the model.
_DEMAND_PATH = _PROJECT_DIR / "data" / "processed" / "real" / "piggery_data" / "demand_for_optimisation.csv"
_SOLAR_CACHE_ROOT = _PROJECT_DIR / "data" / "solar_cache"
_SPOT_CACHE_DIR = _PROJECT_DIR / "data" / "spot_cache"
_FORECAST_DIR = _PROJECT_DIR / "data" / "forecast"
_UPLOAD_DIR = _PROJECT_DIR / "data" / "uploads"
_FIGURE_DIR = _PROJECT_DIR / "data" / "figures"

# Guards every place this file touches current_code's modules. Kept on `sys`
# so a re-import of this file (Streamlit's file watcher can do that) shares the
# same lock instead of creating a second one that protects nothing.
if not hasattr(sys, "_agripower_current_code_lock"):
    sys._agripower_current_code_lock = threading.Lock()
_CURRENT_CODE_LOCK = sys._agripower_current_code_lock


def _divide_pulp_variable_by_a_number(variable, divisor):
    """The one operation the shim below adds: a PuLP variable divided by a
    plain number, computed as the variable times the number's reciprocal.

    It deliberately accepts nothing else. Dividing by another variable or
    expression, by zero, by a string, and so on, all raise, so that if
    current_code's model ever needs more than this, the solve fails loudly
    instead of quietly building a different model."""
    if isinstance(divisor, bool) or not isinstance(divisor, (int, float, np.integer, np.floating)):
        raise TypeError(
            f"optimiser.py's PuLP shim only covers dividing a variable by a plain number, but the "
            f"model divided {variable!r} by {type(divisor).__name__}. current_code's model needs "
            "more than the shim covers: ask the team which PuLP version they use, or widen the shim "
            "deliberately."
        )
    if divisor == 0:
        raise ZeroDivisionError("The model divided a PuLP variable by zero.")
    return variable * (1.0 / float(divisor))


def _allow_pulp_division_by_a_number() -> None:
    """Make `variable / number` work on PuLP 2.9, without editing current_code.

    Why: milp.py writes `x_B_to_SN[t] / battery_efficiency_discharge` in its two
    battery state-of-charge constraints (lines 118 and 124 of src/milp.py).
    PuLP 2.9.0 rejects that with "TypeError: unsupported operand type(s) for /:
    'LpVariable' and 'float'". Dividing by a number is multiplying by its
    reciprocal, so the model is mathematically unchanged.

    How narrow it is: it adds `__truediv__` to PuLP's LpElement (the base of a
    variable) only if PuLP does not already define one, so a PuLP version that
    supports this natively is left alone. The added method accepts a plain
    number only (see _divide_pulp_variable_by_a_number); anything else raises.
    Division of a number by a variable (`__rtruediv__`) is not added, so that
    still fails as PuLP does by default."""
    element = pulp.pulp.LpElement
    if not hasattr(element, "__truediv__"):
        element.__truediv__ = _divide_pulp_variable_by_a_number


with _CURRENT_CODE_LOCK:
    import config as _cc_config  # noqa: E402  (current_code/src/config.py)

    # Where the model reads and writes, redirected away from current_code/.
    _cc_config.load_data = _DEMAND_PATH
    _cc_config.raw_load_data = _DEMAND_PATH.with_name("demand_clean_raw_resolution.csv")
    _cc_config.spot_price_dir = _SPOT_CACHE_DIR
    _cc_config.forecast_dir = _FORECAST_DIR
    _allow_pulp_division_by_a_number()

    # The model's values exactly as shipped (after the folder redirects above), so
    # each run starts from them and one farm's overrides never leak into the next.
    _PRISTINE_CONFIG = {
        name: copy.deepcopy(value)
        for name, value in vars(_cc_config).items()
        if not name.startswith("__") and not isinstance(value, type(sys))
    }

# Loaded on the first run (see _reload_config_dependents / _reload_inputs_and_model).
_cc_solar = None
_cc_spot = None
_cc_inputs = None
_cc_milp = None
_cc_results = None
_idp = None  # the teammate's input_data_processing_v3

# A generous safety-net ceiling per solve, not a normal operating limit.
SOLVER_TIME_LIMIT_SECONDS = 6 * 60 * 60  # 6 hours

SCENARIOS = {
    "farm_retail": {
        "label": "Farmer ownership, retail energy",
        "settings": {"ownership": "farm", "spot_price_case": False, "use_price_forecast": False},
    },
    "third_party_spot": {
        "label": "Third-party ownership, spot market energy",
        "settings": {"ownership": "third_party", "spot_price_case": True, "use_price_forecast": False},
    },
}


class SolverInfeasible(RuntimeError):
    """Raised when the data cannot be solved (a short year, or no feasible build)."""


def scenario_label(scenario: str) -> str:
    return SCENARIOS[scenario]["label"]


# ===========================================================================
# Meter data: the teammate's own input processing
# ===========================================================================

def _input_processing():
    global _idp
    if _idp is None:
        _idp = importlib.import_module("input_data_processing_v3")
    return _idp


def prepare_meter_data(file_bytes: bytes, file_name: str) -> dict:
    """Run current_code's own prepare_model_inputs on an uploaded NEM12 file
    (.csv or .xlsx). Returns {"raw": 5-minute table, "hourly": hourly table,
    "validation": the model's data-quality summary}. The raw table is what the
    model reads to find blackouts, the hourly table is its demand."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file_name).suffix.lower() or ".csv"
    path = _UPLOAD_DIR / f"upload_{threading.get_ident()}{suffix}"
    path.write_bytes(file_bytes)
    try:
        with _CURRENT_CODE_LOCK:
            raw, hourly, validation = _input_processing().prepare_model_inputs(path, optimisation_freq="1h")
    finally:
        path.unlink(missing_ok=True)
    return {"raw": raw, "hourly": hourly, "validation": validation}


def _write_meter_files(meter: dict) -> None:
    """Write the two CSVs the model reads (config.load_data and config.raw_load_data),
    using the teammate's own save function."""
    _input_processing().save_processed_outputs(
        meter["raw"], meter["hourly"], dict(meter["validation"]), output_dir=_DEMAND_PATH.parent
    )


def _year_is_fully_covered(indexed: pd.Series, year: int, horizon: int) -> bool:
    """Same test current_code/inputs.py applies: a reading for every one of
    the `horizon` hours starting at midnight on 1 January of `year`."""
    needed = pd.date_range(f"{year}-01-01", periods=horizon, freq="h")
    if not needed.isin(indexed.index).all():
        return False
    return not indexed.reindex(needed).isna().any()


def _hourly_series(meter: dict) -> pd.Series:
    return meter["hourly"].set_index("timestamp")["demand_kWh"]


def complete_calendar_years(meter: dict) -> list:
    """Calendar years the meter data covers in full, oldest first: the only
    years the model can solve."""
    indexed = _hourly_series(meter)
    horizon = _PRISTINE_CONFIG["horizon"]
    return [int(y) for y in sorted(indexed.index.year.unique()) if _year_is_fully_covered(indexed, int(y), horizon)]


def _validate_model_year(meter: dict, year: int) -> int:
    """Check that the calendar year the farmer chose can be solved, and return it
    unchanged. Never swaps in another year and never pads, repeats or scales data."""
    if _year_is_fully_covered(_hourly_series(meter), year, _PRISTINE_CONFIG["horizon"]):
        return int(year)
    raise SolverInfeasible(
        f"The full optimisation solves one whole calendar year and needs a meter reading for every "
        f"hour of it, starting 1 January. The uploaded data does not cover {year} in full, so it "
        "cannot be solved. Nothing has been padded, repeated or scaled."
    )


def solved_window(meter: dict, year: int):
    """(start, end) of the window the model will solve for this year: midnight on
    1 January to the last of the model's `horizon` hours. For a leap year that is
    30 December, because the horizon is a fixed 8,760 hours."""
    year = _validate_model_year(meter, year)
    start = pd.Timestamp(year=year, month=1, day=1)
    return start, start + pd.Timedelta(hours=_PRISTINE_CONFIG["horizon"] - 1)


# ===========================================================================
# Feeding inputs into current_code/src/config.py, in memory only
# ===========================================================================

def _reset_config() -> None:
    """Put every config value back to what the model ships with."""
    for name, value in _PRISTINE_CONFIG.items():
        setattr(_cc_config, name, copy.deepcopy(value))


def _apply_scenario(scenario: str) -> None:
    for name, value in SCENARIOS[scenario]["settings"].items():
        setattr(_cc_config, name, value)


def _is_entered(constraints: FarmConstraints, name: str) -> bool:
    """Whether the farmer gave this field a value, as opposed to leaving the
    model's own default in place. Only entered fields are passed to the model."""
    if name in constraints.defaulted_fields:
        return False
    return True


def _apply_farm_overrides(constraints: FarmConstraints) -> None:
    """Overwrite config values with the fields the farmer entered. A field left
    on its default is not touched, so the model keeps its own value. Which
    fields are read, and which are not, is recorded in constraints.FIELD_GROUPS."""
    from agripower.constraints import (
        DEFAULT_GRID_CONNECTION_KW, DEFAULT_GROUND_AREA_M2, DEFAULT_GROUND_TILT_DEGREES,
    )

    c = constraints
    entered = lambda name: _is_entered(c, name)  # noqa: E731

    if entered("latitude"):
        _cc_config.lat = c.latitude
    if entered("longitude"):
        _cc_config.lon = c.longitude
    if entered("roof_tilt_bucket"):
        _cc_config.roof_tilt = tilt.tilt_degrees_for(c.roof_tilt_bucket)
    if c.ground_tilt_degrees != DEFAULT_GROUND_TILT_DEGREES:
        _cc_config.ground_tilt = c.ground_tilt_degrees

    roof_area = dict(_cc_config.roof_area)
    for orientation, field in (("North", "roof_area_north_m2"), ("South", "roof_area_south_m2"),
                               ("East", "roof_area_east_m2"), ("West", "roof_area_west_m2")):
        if entered(field):
            roof_area[orientation] = getattr(c, field)
    if c.ground_area_m2 != DEFAULT_GROUND_AREA_M2:
        roof_area["Ground"] = c.ground_area_m2
    _cc_config.roof_area = roof_area

    # The template states one grid connection size. The model has an import and
    # an export limit (200 kW and 30 kW as shipped); the farm's figure is applied
    # to the import limit only and the model's own export limit is kept.
    if c.grid_connection_kw != DEFAULT_GRID_CONNECTION_KW:
        _cc_config.grid_import_lim = c.grid_connection_kw

    if entered("battery_round_trip_efficiency"):
        _cc_config.battery_efficiency = c.battery_round_trip_efficiency
        # config.py computes these two from battery_efficiency when it is imported,
        # so they are recomputed here the same way.
        _cc_config.battery_efficiency_charge = math.sqrt(c.battery_round_trip_efficiency)
        _cc_config.battery_efficiency_discharge = math.sqrt(c.battery_round_trip_efficiency)
    if entered("battery_depth_of_discharge"):
        _cc_config.SOC_min = 1.0 - c.battery_depth_of_discharge
    if entered("discount_rate"):
        _cc_config.discount_rate = c.discount_rate  # used in the farmer ownership scenario only

    lifetime = dict(_cc_config.lifetime)
    if entered("battery_lifespan_years"):
        lifetime["battery"] = c.battery_lifespan_years
    if entered("panel_lifespan_years"):
        lifetime["pv"] = c.panel_lifespan_years
    _cc_config.lifetime = lifetime

    if entered("battery_cost_per_kwh"):
        _cc_config.battery_cost_per_kWh = c.battery_cost_per_kwh
    capex = dict(_cc_config.capex)
    if entered("pv_cost_per_kwp"):
        capex["pv"] = c.pv_cost_per_kwp
    _cc_config.capex = capex

    if entered("maintenance_fraction_of_capex"):
        maintenance = dict(_cc_config.maintinence)
        maintenance["battery"] = c.maintenance_fraction_of_capex
        maintenance["pv"] = c.maintenance_fraction_of_capex
        _cc_config.maintinence = maintenance

    # The energy bill fields (peak/off-peak rates, charges, GST, export tariff) are
    # NOT applied. The model prices electricity from its own `bill`, `peak_hours`
    # and `retail_export_price`; passing farm values to those is the team's decision.


def _set_model_year(meter: dict, chosen_year: int) -> int:
    """Check the chosen calendar year can be solved, write the meter files where
    the model reads them, and set every year setting in config. Must run BEFORE
    _reload_config_dependents(): solar_generation and spot_prices copy these year
    settings when they are reloaded."""
    year = _validate_model_year(meter, chosen_year)
    _write_meter_files(meter)
    _cc_config.model_year = year
    _cc_config.start_year = year
    _cc_config.end_year = year
    _cc_config.spot_price_start_year = year
    _cc_config.spot_price_end_year = year
    return year


def _load_or_reload(module_name: str):
    """Import a current_code module the first time, reload it afterwards."""
    module = sys.modules.get(module_name)
    if module is None:
        return importlib.import_module(module_name)
    return importlib.reload(module)


def _solar_cache_dir() -> Path:
    """A folder named for the location and tilts the solar profile is built from.
    solar_generation.py names its cache file by year only, so without this a farm
    at a different location would silently reuse the first farm's profile.
    Pointing config.REPO_ROOT here moves that cache outside current_code/."""
    key = (f"lat{_cc_config.lat:.4f}_lon{_cc_config.lon:.4f}"
           f"_roof{_cc_config.roof_tilt:g}_ground{_cc_config.ground_tilt:g}")
    folder = _SOLAR_CACHE_ROOT / key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _reload_config_dependents() -> None:
    """(Re)load solar_generation, and spot_prices when the scenario needs spot
    prices, against the just-set config."""
    global _cc_solar, _cc_spot
    _cc_config.REPO_ROOT = _solar_cache_dir()
    _cc_solar = _load_or_reload("solar_generation")
    if _cc_config.spot_price_case or _cc_config.ownership == "third_party":
        _cc_spot = _load_or_reload("spot_prices")


def _reload_inputs_and_model() -> None:
    """(Re)load inputs (which reads the meter CSVs, fetches weather and prices, and
    reads the grid emissions table, all itself) and everything downstream."""
    global _cc_inputs, _cc_milp, _cc_results
    _cc_inputs = _load_or_reload("inputs")
    _cc_milp = _load_or_reload("milp")
    _cc_results = _load_or_reload("results")


def _check_model_used_this_run(scenario: str, year: int) -> None:
    """Fail loudly, rather than solve the wrong farm, if the model did not pick up
    this run's settings.

    The model's modules read their inputs with `from config import *`. If something
    unloads the config module between this file setting the values and the model
    importing them, the model would quietly re-read config.py's own defaults (a
    different farm) and still return a result. This compares what the model's
    modules actually hold with what was set."""
    problems = []
    if sys.modules.get("config") is not _cc_config:
        problems.append("the model's config module was unloaded and reloaded")
    for name in ("lat", "lon", "grid_import_lim", "ownership", "spot_price_case", "model_year"):
        if getattr(_cc_inputs, name) != getattr(_cc_config, name):
            problems.append(name)
    if dict(_cc_inputs.roof_area) != dict(_cc_config.roof_area):
        problems.append("roof areas")
    for name, value in SCENARIOS[scenario]["settings"].items():
        if getattr(_cc_inputs, name, value) != value:
            problems.append(name)
    if int(_cc_inputs.model_year) != int(year):
        problems.append("year")
    if problems:
        raise RuntimeError(
            "The optimiser did not pick up this run's settings (" + ", ".join(problems) + "), so it was "
            "stopped rather than solve the wrong farm. This can happen if the app's files were edited "
            "or reloaded while an analysis was starting. Start the analysis again."
        )


# ===========================================================================
# The solver swap
# ===========================================================================

_GUROBI_UNKNOWN_SENTINEL = 1e99  # Gurobi reports MIP_OBJBST/MIP_OBJBND as +-1e100 for "not yet known"


def _make_gurobi_progress_callback(on_progress, *, min_interval_seconds: float = 0.5):
    """Wraps a plain `on_progress(best, bound)` callback as a native gurobipy MIP
    callback. Only reports once both the best feasible objective and the best bound
    are genuinely known (not Gurobi's own "not yet known" sentinel), and at most
    every `min_interval_seconds`."""
    from gurobipy import GRB

    last_reported_at = 0.0

    def _callback(model, where):
        nonlocal last_reported_at
        if where != GRB.Callback.MIP:
            return
        now = time.monotonic()
        if now - last_reported_at < min_interval_seconds:
            return
        best = model.cbGet(GRB.Callback.MIP_OBJBST)
        bound = model.cbGet(GRB.Callback.MIP_OBJBND)
        if abs(best) < _GUROBI_UNKNOWN_SENTINEL and abs(bound) < _GUROBI_UNKNOWN_SENTINEL:
            last_reported_at = now
            on_progress(best, bound)

    return _callback


def _gurobi_is_usable() -> bool:
    try:
        return bool(pulp.GUROBI(msg=0).available())
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False


def _gurobi_version_text() -> str:
    try:
        import gurobipy
        return ".".join(str(part) for part in gurobipy.gurobi.version())
    except Exception:  # noqa: BLE001 - description only
        return "unknown"


class _ModelGurobi(pulp.GUROBI):
    """pulp.GUROBI with a step counter and live progress, so a run can report which
    of the model's solves it is on. The solve itself is unchanged."""

    def __init__(self, hooks: dict, **kwargs):
        super().__init__(**kwargs)
        self._hooks = hooks
        self._gap = kwargs.get("gapRel")

    def actualSolve(self, lp, callback=None):
        hooks = self._hooks
        minimise = "emis" if hooks["solve_index"] == 1 else "cost"  # milp.pareto_front: cost, emissions, then the sweep
        on_progress = hooks.get("on_progress")
        if on_progress is not None:
            on_progress(None, None, minimise)
            callback = _make_gurobi_progress_callback(lambda best, bound: on_progress(best, bound, minimise))
        try:
            status = super().actualSolve(lp, callback=callback)
        except Exception as error:  # noqa: BLE001
            if type(error).__module__.split(".")[0] != "gurobipy":
                _step_done(hooks)
                raise
            # Gurobi could not solve this model here (for example a size-limited
            # licence). Solve this and the remaining problems with CBC at the same
            # gap, and say so in the run's facts.
            hooks["gurobi_failed"] = str(error)
            return _ModelCbc(hooks, msg=0, timeLimit=SOLVER_TIME_LIMIT_SECONDS, gapRel=self._gap).actualSolve(lp)
        _step_done(hooks)
        return status


class _ModelCbc(pulp.PULP_CBC_CMD):
    """The CBC fallback, with the same step counter."""

    def __init__(self, hooks: dict, **kwargs):
        super().__init__(**kwargs)
        self._hooks = hooks

    def actualSolve(self, lp, **kwargs):
        try:
            return super().actualSolve(lp, **kwargs)
        finally:
            _step_done(self._hooks)


def _step_done(hooks: dict) -> None:
    hooks["solve_index"] += 1
    if hooks.get("on_step") is not None:
        hooks["on_step"](hooks["solve_index"], hooks["total"])


class _SolverSwap:
    """For the length of a run, replace pulp.GUROBI_CMD (which milp.solve_model
    calls with a Mac-only path) with a factory returning the same solver
    configuration through gurobipy, or CBC if Gurobi is not usable."""

    def __init__(self, hooks: dict):
        self.hooks = hooks
        self.use_gurobi = _gurobi_is_usable()
        self.gap = None
        self.description = ""

    def _factory(self, path=None, msg=True, options=None, **_ignored):
        # milp.py asks for: pulp.GUROBI_CMD(path=..., msg=1, options=[("MIPGap", 0.01)])
        self.gap = dict(options or []).get("MIPGap", 0.01)
        if self.use_gurobi and not self.hooks.get("gurobi_failed"):
            return _ModelGurobi(self.hooks, msg=0, timeLimit=SOLVER_TIME_LIMIT_SECONDS, gapRel=self.gap)
        return _ModelCbc(self.hooks, msg=0, timeLimit=SOLVER_TIME_LIMIT_SECONDS, gapRel=self.gap)

    def __enter__(self):
        self._original = pulp.GUROBI_CMD
        pulp.GUROBI_CMD = self._factory
        return self

    def __exit__(self, *exc_info):
        pulp.GUROBI_CMD = self._original

    def describe(self) -> str:
        gap = f"{(self.gap if self.gap is not None else 0.01) * 100:g}% gap"
        if self.hooks.get("gurobi_failed"):
            return f"CBC through PuLP ({gap}); Gurobi could not solve this model here ({self.hooks['gurobi_failed']})"
        if self.use_gurobi:
            return f"Gurobi {_gurobi_version_text()} (MIPGap {gap})"
        return f"CBC through PuLP ({gap}); Gurobi was not available on this computer"


# ===========================================================================
# Running the model
# ===========================================================================

def default_pareto_points() -> int:
    return int(_PRISTINE_CONFIG["n_epsilon_steps"])


def _price_source_text(year: int) -> str:
    cfg = _cc_config
    if cfg.spot_price_case or cfg.ownership == "third_party":
        return (f"AEMO spot prices (RRP) for {year}, region {cfg.nem_region}, averaged to hourly "
                f"(current_code/src/spot_prices.py)")
    peak = ", ".join(f"{start}:00 to {end}:00" for start, end in cfg.peak_hours)
    return (f"the retail tariff in current_code/src/config.py (peak {cfg.bill['peak']:g} c/kWh in {peak}, "
            f"off-peak {cfg.bill['off_peak']:g} c/kWh, plus its charges, exports {cfg.retail_export_price * 100:g} c/kWh)")


def run_model(meter: dict, constraints: FarmConstraints, year: int, scenario: str, *,
              on_step=None, on_progress=None) -> dict:
    """Run current_code's model for one scenario and return what it produces:
    {"summary": the results table (results.summarise), "figure_png": the
    four-panel figure (results.plot_all), "facts": ...}.

    `on_step(done, total)` is called as each of the model's solves finishes, and
    `on_progress(best, bound, minimise)` with Gurobi's live best-feasible and
    best-bound values."""
    started = time.time()
    with _CURRENT_CODE_LOCK:
        _reset_config()
        _apply_scenario(scenario)
        _apply_farm_overrides(constraints)
        year = _set_model_year(meter, year)
        _reload_config_dependents()
        _reload_inputs_and_model()
        _check_model_used_this_run(scenario, year)
        pv_profile = _cc_inputs.pv_profile
        times = _cc_inputs.times
        expected_options = int(_cc_config.n_epsilon_steps)
        price_source = _price_source_text(year)
        ownership = _cc_config.ownership

    hooks = {"solve_index": 0, "total": expected_options + 2, "on_step": on_step, "on_progress": on_progress}
    with _SolverSwap(hooks) as swap:
        try:
            results = _cc_milp.pareto_front(pv_profile)
        except RuntimeError as error:
            raise SolverInfeasible(
                f"{error}. The model found no feasible build for this farm and year; check that the "
                "meter data and the grid connection size are realistic."
            ) from error
    if not results:
        raise SolverInfeasible("The model solved its two anchor cases but no point on the trade-off curve came back optimal.")

    summary = _cc_results.summarise(results, times)
    figure_png = _draw_four_panel_figure(summary, results, times, ownership)
    best_trade_off = int(_cc_results.knee_point(summary))

    facts = {
        "year": int(year),
        "hours": int(len(times)),
        "start": times[0],
        "end": times[-1],
        "scenario": scenario,
        "scenario_label": scenario_label(scenario),
        "ownership": ownership,
        "solver": swap.describe(),
        "price_source": price_source,
        "options_returned": int(len(results)),
        "options_expected": expected_options,
        "best_trade_off_index": best_trade_off,
        "solve_seconds": time.time() - started,
    }
    return {"summary": summary, "figure_png": figure_png, "facts": facts}


_PLOT_LOCK = threading.Lock()


def _draw_four_panel_figure(summary: pd.DataFrame, results: list, times, ownership: str) -> bytes:
    """Call current_code's own results.plot_all with a non-interactive backend and
    return the PNG it saves (outside current_code/). Nothing is restyled.

    The PNG is what is stored with the job and shown on Results. A pickled Figure
    was tried and rejected: the model's figure uses subfigures, and a Figure
    unpickled from the job record draws blank."""
    import matplotlib.pyplot as plt

    _FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = _FIGURE_DIR / f"results_{ownership}_{threading.get_ident()}.png"
    with _PLOT_LOCK, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # plot_all ends with plt.show(), a no-op under Agg
        try:
            _cc_results.plot_all(summary, results, times, fname=str(path))
            png = path.read_bytes()
        finally:
            plt.close("all")
            path.unlink(missing_ok=True)
    return png


def leap_year_note(year: int, end) -> str:
    """The model's horizon is a fixed 8,760 hours, so in a leap year it stops on
    30 December. Returns a sentence saying so, or "" when the window ends on
    31 December."""
    if end.month == 12 and end.day == 31:
        return ""
    return (f"{year} has 366 days, but the model always solves 8,760 hours from 1 January, "
            "so 31 December is not included.")


def format_duration(seconds: float) -> str:
    """A short, human duration string ("6h 0m", "42m 3s", "8s")."""
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
