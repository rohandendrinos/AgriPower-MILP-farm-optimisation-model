"""Farm constraints: the second main input, alongside the meter file.

Turns an uploaded copy of the project's "Farm Constraints Data Import Template"
workbook (V2), or a filled-in form on the New analysis page, into one
FarmConstraints object. Nothing downstream (optimiser.py, report.py) reads the
raw workbook.

Fields the optimisation model does not read at all (discrete battery units, panel
type, mount and dimensions, a farmer-toggled generator) were dropped from the
template rather than carried over unused.

Nothing here blocks a run: a farmer can leave anything blank (or delete a row from
the template) and the model keeps its own default for that field. Results are only
specific to the farm if the Group 1 fields (FIELD_GROUPS) hold real data.
`FarmConstraints.resolve()` fills each blank field from FIELD_DEFAULTS (the model's
own values, read from current_code/src/config.py) and records exactly which fields
were defaulted in `resolved.defaulted_fields`, so every page can say which values
are the farmer's and which are the model's.
"""

from dataclasses import dataclass, field, replace
import io
from pathlib import Path
from typing import Optional

import openpyxl

from agripower import tilt

# --- Dropdown options, matching the template's "Drop Downs" sheet ---
ROOF_TILT_BUCKETS = ["Flat", "Low", "High"]


def _read_model_config() -> dict:
    """The values in current_code/src/config.py, read as plain text and run in
    a private namespace. It is never imported, so nothing is added to
    sys.modules and no bytecode is written into current_code/. These are the
    model's own defaults: a field left blank keeps exactly this value."""
    config_path = Path(__file__).resolve().parents[1] / "current_code" / "src" / "config.py"
    namespace = {"__file__": str(config_path), "__name__": "agripower_model_defaults"}
    exec(compile(config_path.read_text(encoding="utf-8"), str(config_path), "exec"), namespace)
    return namespace


MODEL_CONFIG = _read_model_config()
_MODEL_CITATION = "The model's own default (current_code/src/config.py), kept unless you enter a value."


def _nearest_roof_tilt_bucket(degrees: float) -> str:
    return min(tilt.ROOF_TILT_DEGREES_BY_BUCKET, key=lambda name: abs(tilt.ROOF_TILT_DEGREES_BY_BUCKET[name] - degrees))


# Fields the template itself leaves pre-filled as defaults: the model's own values.
DEFAULT_GRID_CONNECTION_KW = float(MODEL_CONFIG["grid_import_lim"])
DEFAULT_GROUND_AREA_M2 = float(MODEL_CONFIG["roof_area"]["Ground"])
DEFAULT_GROUND_TILT_DEGREES = float(MODEL_CONFIG["ground_tilt"])

# ----------------------------------------------------------------------------
# What a blank field shows, and keeps. These are the model's own values, read
# from current_code's config.py, so leaving a field blank runs the model exactly
# as its authors set it. Nothing here is a number invented by this app.
# views/about.py renders this same registry.
# ----------------------------------------------------------------------------
FIELD_DEFAULTS = {
    # name: (default_value, display_label, citation)
    "roof_area_north_m2": (float(MODEL_CONFIG["roof_area"]["North"]), "North facing roof area", _MODEL_CITATION),
    "roof_area_south_m2": (float(MODEL_CONFIG["roof_area"]["South"]), "South facing roof area", _MODEL_CITATION),
    "roof_area_east_m2": (float(MODEL_CONFIG["roof_area"]["East"]), "East facing roof area", _MODEL_CITATION),
    "roof_area_west_m2": (float(MODEL_CONFIG["roof_area"]["West"]), "West facing roof area", _MODEL_CITATION),
    "roof_tilt_bucket": (_nearest_roof_tilt_bucket(MODEL_CONFIG["roof_tilt"]), "Roof tilt",
                         f"The model uses {MODEL_CONFIG['roof_tilt']:g} degrees (current_code/src/config.py), which is this bucket."),
    "latitude": (float(MODEL_CONFIG["lat"]), "Farm location (latitude)", _MODEL_CITATION),
    "longitude": (float(MODEL_CONFIG["lon"]), "Farm location (longitude)", _MODEL_CITATION),
    "battery_cost_per_kwh": (float(MODEL_CONFIG["battery_cost_per_kWh"]), "Cost of battery ($/kWh)", _MODEL_CITATION),
    "pv_cost_per_kwp": (float(MODEL_CONFIG["capex"]["pv"]), "Cost of solar panels ($/kWp)", _MODEL_CITATION),
    "maintenance_fraction_of_capex": (
        float(MODEL_CONFIG["maintinence"]["pv"]), "Maintenance cost (% of CAPEX)",
        f"The model uses {MODEL_CONFIG['maintinence']['pv'] * 100:g}% for solar and {MODEL_CONFIG['maintinence']['battery'] * 100:g}% for "
        "the battery (current_code/src/config.py). A value you enter is applied to both.",
    ),
    "discount_rate": (float(MODEL_CONFIG["discount_rate"]), "Discount rate", _MODEL_CITATION),
    "battery_round_trip_efficiency": (float(MODEL_CONFIG["battery_efficiency"]), "Battery round trip efficiency", _MODEL_CITATION),
    "battery_depth_of_discharge": (1.0 - float(MODEL_CONFIG["SOC_min"]), "Battery depth of discharge",
                                   f"The model keeps the battery at least {MODEL_CONFIG['SOC_min'] * 100:g}% charged (current_code/src/config.py)."),
    "battery_lifespan_years": (int(MODEL_CONFIG["lifetime"]["battery"]), "Battery lifespan (years)", _MODEL_CITATION),
    "panel_lifespan_years": (int(MODEL_CONFIG["lifetime"]["pv"]), "Solar panel lifespan (years)", _MODEL_CITATION),
    "peak_hour_start": (int(MODEL_CONFIG["peak_hours"][0][0]), "Peak hours start", _MODEL_CITATION),
    "peak_hour_end": (int(MODEL_CONFIG["peak_hours"][0][1]), "Peak hours end", _MODEL_CITATION),
    "off_peak_rate_c_per_kwh": (float(MODEL_CONFIG["bill"]["off_peak"]), "Off-peak rate (c/kWh)", _MODEL_CITATION),
    "peak_rate_c_per_kwh": (float(MODEL_CONFIG["bill"]["peak"]), "Peak rate (c/kWh)", _MODEL_CITATION),
}


# ----------------------------------------------------------------------------
# Which fields actually change the full optimisation. Traced through
# current_code/src on 20 October 2026 (config.py -> inputs.py /
# solar_generation.py / milp.py), not assumed:
#   1 = read by current_code and used in the optimisation
#   2 = never read from the farm by current_code; only changes the instant
#       estimate or which option is pre-selected on Results. (The energy bill
#       fields are in this group because the app does not pass them on: the
#       model prices electricity from a tariff held in its own config.py,
#       which has a counterpart for each one. Whether to pass them on is the
#       host's decision.)
#   3 = not used by any calculation at all
# ----------------------------------------------------------------------------
FIELD_GROUPS = {
    "roof_area_north_m2": 1, "roof_area_south_m2": 1, "roof_area_east_m2": 1, "roof_area_west_m2": 1,
    "roof_tilt_bucket": 1, "latitude": 1, "longitude": 1, "grid_connection_kw": 1,
    "battery_cost_per_kwh": 1, "pv_cost_per_kwp": 1,
    "ground_area_m2": 1, "ground_tilt_degrees": 1,
    "maintenance_fraction_of_capex": 1, "discount_rate": 1,
    "battery_round_trip_efficiency": 1, "battery_depth_of_discharge": 1,
    "battery_lifespan_years": 1, "panel_lifespan_years": 1,
    "peak_hour_start": 2, "peak_hour_end": 2, "off_peak_rate_c_per_kwh": 2, "peak_rate_c_per_kwh": 2,
    "lret_charge_c_per_kwh": 2, "sres_charge_c_per_kwh": 2, "veec_charge_c_per_kwh": 2,
    "network_charge_c_per_kwh": 2, "gst_fraction": 2, "other_charge_c_per_kwh": 2,
    "export_tariff_c_per_kwh": 2,
    "cost_priority": 3, "emissions_priority": 3, "start_year": 3,
}

# Plain-language names for the three groups, shown on New analysis, Results and
# in the report, so the wording is only ever written here.
GROUP_TITLES = {
    1: "Changes the optimisation result",
    2: "Energy bill fields, not yet passed to the model",
    3: "Not used",
}
GROUP_EXPLANATIONS = {
    1: "These are read by the optimisation model. Real values here are what make the result "
       "specific to your farm.",
    2: "The model has its own tariff settings for these, but the app does not pass your values "
       "to it yet, so they do not change the result.",
    3: "These are not used by the model.",
}

# The energy bill fields. The app does not pass them to the model, so they do not
# change the result. This wording is stored once so it can be changed in one place.
BILL_FIELD_NOTE = (
    "Not yet used by the optimiser. A tariff scaling factor is being developed by the team."
)
BILL_FIELDS = {
    "peak_hour_start", "peak_hour_end", "off_peak_rate_c_per_kwh", "peak_rate_c_per_kwh",
    "lret_charge_c_per_kwh", "sres_charge_c_per_kwh", "veec_charge_c_per_kwh",
    "network_charge_c_per_kwh", "gst_fraction", "other_charge_c_per_kwh", "export_tariff_c_per_kwh",
}
# Fields that only apply to one of the two ownership scenarios.
FARM_OWNERSHIP_ONLY_FIELDS = {"discount_rate"}

RECOMMENDED_NOTE = "Recommended for most accurate results."


def field_group_note(name: str) -> str:
    """One short line stating which group a field is in and what that means.
    Group 1 fields carry the "Recommended for most accurate results" marker."""
    group = FIELD_GROUPS[name]
    if group == 1:
        extra = " Used in the farmer ownership scenario only." if name in FARM_OWNERSHIP_ONLY_FIELDS else ""
        return f"Group 1, changes the result.{extra} {RECOMMENDED_NOTE}"
    if name in BILL_FIELDS:
        return f"Group 2. {BILL_FIELD_NOTE}"
    return "Group 3, not used."


# Template-defaulted fields have no entry in FIELD_DEFAULTS (they always hold a
# value), so they need their own labels and built-in defaults.
TEMPLATE_DEFAULTED_FIELDS = {
    "grid_connection_kw": ("Grid connection (kW)", DEFAULT_GRID_CONNECTION_KW),
    "ground_area_m2": ("Ground array area (m2)", DEFAULT_GROUND_AREA_M2),
    "ground_tilt_degrees": ("Ground array tilt (degrees)", DEFAULT_GROUND_TILT_DEGREES),
}

# Stored as a fraction, entered as a percentage.
PERCENT_FIELDS = {
    "maintenance_fraction_of_capex", "discount_rate",
    "battery_round_trip_efficiency", "battery_depth_of_discharge",
}
FIELD_UNITS = {
    "roof_area_north_m2": " m2", "roof_area_south_m2": " m2",
    "roof_area_east_m2": " m2", "roof_area_west_m2": " m2",
    "battery_cost_per_kwh": " $/kWh", "pv_cost_per_kwp": " $/kWp",
    "battery_lifespan_years": " years", "panel_lifespan_years": " years",
    "off_peak_rate_c_per_kwh": " c/kWh", "peak_rate_c_per_kwh": " c/kWh",
    "peak_hour_start": ":00", "peak_hour_end": ":00",
    "grid_connection_kw": " kW", "ground_area_m2": " m2", "ground_tilt_degrees": " degrees",
}


def format_field_value(name: str, value) -> str:
    """A field's value the way the form shows it: percentages as percentages,
    with its unit."""
    if name in PERCENT_FIELDS:
        return f"{value * 100:g}%"
    shown = f"{value:g}" if isinstance(value, float) else str(value)
    return shown + FIELD_UNITS.get(name, "")


def group1_fields_on_default(constraints: "FarmConstraints") -> list:
    """The fields that change the optimisation result but are still on their
    default values, as (label, shown value) pairs.

    Uses the existing entered-versus-default tracking: a field counts if it
    is in `defaulted_fields` (left blank, filled from FIELD_DEFAULTS). The
    three template-defaulted fields are not tracked that way, so those count
    when they still equal the template's built-in default.
    """
    # An already-resolved copy has no blanks left, so resolving it again would
    # erase the record of which fields were defaulted; use it as it is.
    already_resolved = all(getattr(constraints, name) is not None for name in FIELD_DEFAULTS)
    resolved = constraints if already_resolved else constraints.resolve()
    on_default = []
    for name, group in FIELD_GROUPS.items():
        if group != 1:
            continue
        if name in FIELD_DEFAULTS:
            if name in resolved.defaulted_fields:
                label = FIELD_DEFAULTS[name][1]
                on_default.append((label, format_field_value(name, getattr(resolved, name))))
        elif name in TEMPLATE_DEFAULTED_FIELDS:
            label, built_in_default = TEMPLATE_DEFAULTED_FIELDS[name]
            value = getattr(resolved, name)
            if value == built_in_default:
                on_default.append((label, format_field_value(name, value)))
    return on_default


ACCURACY_CAVEAT = (
    "These fields are still on default values, so the optimisation result will not be fully "
    "accurate for your farm."
)


class MissingConstraintsError(ValueError):
    """Kept for any old call site; nothing in the app calls validate() any more.

    Every field now has a default (see FIELD_DEFAULTS above) so a farm can
    always be resolved. validate() is retained only as an opt-in strict
    check, not something the app calls in its normal flow.
    """


@dataclass
class FarmConstraints:
    # --- Input Data sheet ---
    roof_area_north_m2: Optional[float] = None
    roof_area_south_m2: Optional[float] = None
    roof_area_east_m2: Optional[float] = None
    roof_area_west_m2: Optional[float] = None
    roof_tilt_bucket: Optional[str] = None  # "Flat" / "Low" / "High"
    emissions_priority: int = 0  # 0-5, 0 = no importance
    cost_priority: int = 0  # 0-5
    grid_connection_kw: float = DEFAULT_GRID_CONNECTION_KW
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    start_year: Optional[int] = None  # informational only; NEM12 already carries real dates

    # --- Technical Specs sheet ---
    battery_cost_per_kwh: Optional[float] = None
    pv_cost_per_kwp: Optional[float] = None
    maintenance_fraction_of_capex: Optional[float] = None  # stored as a fraction, e.g. 0.015
    discount_rate: Optional[float] = None  # stored as a fraction, e.g. 0.07
    battery_round_trip_efficiency: Optional[float] = None  # fraction, e.g. 0.88
    battery_depth_of_discharge: Optional[float] = None  # fraction, e.g. 0.90
    battery_lifespan_years: Optional[int] = None
    panel_lifespan_years: Optional[int] = None

    # --- Energy Bill sheet ---
    peak_hour_start: Optional[int] = None  # 24h clock, inclusive
    peak_hour_end: Optional[int] = None  # 24h clock, exclusive
    off_peak_rate_c_per_kwh: Optional[float] = None
    peak_rate_c_per_kwh: Optional[float] = None
    lret_charge_c_per_kwh: float = 0.0
    sres_charge_c_per_kwh: float = 0.0
    veec_charge_c_per_kwh: float = 0.0
    network_charge_c_per_kwh: float = 0.0
    gst_fraction: float = 0.0
    other_charge_c_per_kwh: float = 0.0

    # --- Fields not in the host's template ---
    export_tariff_c_per_kwh: float = 0.0  # no feed-in revenue unless stated
    ground_area_m2: float = DEFAULT_GROUND_AREA_M2
    ground_tilt_degrees: float = DEFAULT_GROUND_TILT_DEGREES

    # Which fields on *this* object were filled in by resolve() rather than
    # supplied by the farmer. Empty until resolve() is called; a raw,
    # just-parsed/just-uploaded FarmConstraints has no opinion on this yet.
    defaulted_fields: frozenset = field(default_factory=frozenset)

    def resolve(self) -> "FarmConstraints":
        """Return a copy with every missing field in FIELD_DEFAULTS filled in.

        Nothing about this ever raises: a completely empty FarmConstraints()
        resolves to a fully-populated, runnable one, entirely from
        placeholders. `resolved.defaulted_fields` names exactly which values
        came from FIELD_DEFAULTS rather than the farmer, so pages can label
        them - see views/new_analysis.py, views/about.py, report.py.
        """
        overrides = {}
        defaulted = set()
        for name, (default_value, _label, _citation) in FIELD_DEFAULTS.items():
            if getattr(self, name) is None:
                overrides[name] = default_value
                defaulted.add(name)
        overrides["defaulted_fields"] = frozenset(defaulted)
        return replace(self, **overrides)

    def field_source(self, name: str) -> str:
        """"farmer" or "default", for labelling a single resolved value."""
        return "default" if name in self.defaulted_fields else "farmer"

    def roof_area_by_orientation(self) -> dict:
        return {
            "North": self.roof_area_north_m2,
            "South": self.roof_area_south_m2,
            "East": self.roof_area_east_m2,
            "West": self.roof_area_west_m2,
            "Ground": self.ground_area_m2,
        }

    def validate(self) -> None:
        """Raise MissingConstraintsError naming every required field still missing."""
        required = {
            "North facing roof area": self.roof_area_north_m2,
            "South facing roof area": self.roof_area_south_m2,
            "East facing roof area": self.roof_area_east_m2,
            "West facing roof area": self.roof_area_west_m2,
            "Roof tilt": self.roof_tilt_bucket,
            "Farm location (latitude)": self.latitude,
            "Farm location (longitude)": self.longitude,
            "Cost of battery ($/kWh)": self.battery_cost_per_kwh,
            "Cost of solar panels ($/kWp)": self.pv_cost_per_kwp,
            "Maintenance cost (% of CAPEX)": self.maintenance_fraction_of_capex,
            "Discount rate": self.discount_rate,
            "Battery round trip efficiency": self.battery_round_trip_efficiency,
            "Battery depth of discharge": self.battery_depth_of_discharge,
            "Battery lifespan (years)": self.battery_lifespan_years,
            "Solar panel lifespan (years)": self.panel_lifespan_years,
            "Peak hours start": self.peak_hour_start,
            "Peak hours end": self.peak_hour_end,
            "Off-peak rate (c/kWh)": self.off_peak_rate_c_per_kwh,
            "Peak rate (c/kWh)": self.peak_rate_c_per_kwh,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise MissingConstraintsError(
                "These farm constraints are still missing: " + ", ".join(missing)
            )


# ----------------------------------------------------------------------------
# Reading the host's template workbook
# ----------------------------------------------------------------------------

def parse_constraints_workbook(uploaded_file) -> FarmConstraints:
    """Read a workbook laid out like "Farm Constraints Data Import Template" (V2).

    Expects three sheets named exactly "Input Data", "Energy Bill" and
    "Technical Specs", each one header row plus one data row. Optional bill
    line items left blank become 0; everything else left blank is simply
    left as None on the returned FarmConstraints (validate() is what turns
    "still missing" into an error, once the farmer has had a chance to fill
    gaps in on the New analysis page).
    """
    file_bytes = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

    constraints = FarmConstraints()
    _read_input_data_sheet(workbook, constraints)
    _read_technical_specs_sheet(workbook, constraints)
    _read_energy_bill_sheet(workbook, constraints)
    return constraints


def _sheet_row_as_dict(workbook, sheet_name: str) -> dict:
    """Header row -> data row, as {header_text: cell_value}, for a template sheet."""
    if sheet_name not in workbook.sheetnames:
        return {}
    sheet = workbook[sheet_name]
    headers = [cell.value for cell in sheet[1]]
    values = [cell.value for cell in sheet[2]] if sheet.max_row >= 2 else []
    return {
        str(header).strip(): value
        for header, value in zip(headers, values)
        if header is not None
    }


def _read_input_data_sheet(workbook, constraints: FarmConstraints) -> None:
    row = _sheet_row_as_dict(workbook, "Input Data")
    constraints.roof_area_north_m2 = _as_float(row.get("Available North Facing Roof Area for Panels (m2)"))
    constraints.roof_area_south_m2 = _as_float(row.get("Available South Facing Roof Area for Panels (m2)"))
    constraints.roof_area_east_m2 = _as_float(row.get("Available East Facing Roof Area for Panels (m2)"))
    constraints.roof_area_west_m2 = _as_float(row.get("Available West Facing Roof Area for Panels (m2)"))
    constraints.roof_tilt_bucket = _as_text(row.get("Roof Tilt"))
    constraints.emissions_priority = _or_default(_as_int(row.get("Emissions Reduction Priority")), 0)
    constraints.cost_priority = _or_default(_as_int(row.get("Costs Savings Priority")), 0)
    constraints.grid_connection_kw = _or_default(
        _as_float(row.get("Grid Connection (kW)")), DEFAULT_GRID_CONNECTION_KW
    )
    constraints.latitude, constraints.longitude = _parse_location(row.get("Location (x,y)"))
    constraints.start_year = _as_int(row.get("Start Year of Energy Demand Data (YYYY)"))
    constraints.ground_area_m2 = _or_default(
        _as_float(row.get("Available Ground Area for Panels (m2)")), DEFAULT_GROUND_AREA_M2
    )
    constraints.ground_tilt_degrees = _or_default(
        _as_float(row.get("Ground Array Tilt (degrees)")), DEFAULT_GROUND_TILT_DEGREES
    )


def _read_technical_specs_sheet(workbook, constraints: FarmConstraints) -> None:
    row = _sheet_row_as_dict(workbook, "Technical Specs")
    constraints.battery_cost_per_kwh = _as_float(row.get("Cost of Battery ($/kWh)"))
    constraints.pv_cost_per_kwp = _as_float(row.get("Cost of Solar Panels ($/kWp)"))
    maintenance_pct = _as_float(row.get("Maintenance Cost (% of asset CAPEX)"))
    constraints.maintenance_fraction_of_capex = maintenance_pct / 100.0 if maintenance_pct is not None else None
    discount_pct = _as_float(row.get("Discount Rate (%)"))
    constraints.discount_rate = discount_pct / 100.0 if discount_pct is not None else None
    round_trip_pct = _as_float(row.get("Battery round trip efficiency (%)"))
    constraints.battery_round_trip_efficiency = round_trip_pct / 100.0 if round_trip_pct is not None else None
    dod_pct = _as_float(row.get("Battery depth of discharge (%)"))
    constraints.battery_depth_of_discharge = dod_pct / 100.0 if dod_pct is not None else None
    constraints.battery_lifespan_years = _as_int(row.get("Battery lifespan (years)"))
    constraints.panel_lifespan_years = _as_int(row.get("Solar panel lifespan (years)"))


def _read_energy_bill_sheet(workbook, constraints: FarmConstraints) -> None:
    row = _sheet_row_as_dict(workbook, "Energy Bill")
    start, end = _parse_peak_hours(row.get("Peak Hours (24 hour time)"))
    constraints.peak_hour_start = start
    constraints.peak_hour_end = end
    constraints.off_peak_rate_c_per_kwh = _as_float(row.get("Off peak Rate (c/kWh)"))
    constraints.peak_rate_c_per_kwh = _as_float(row.get("Peak period rate (c/kWh)"))
    constraints.lret_charge_c_per_kwh = _as_float(row.get("LRET Environmental Charge (c/kWh)")) or 0.0
    constraints.sres_charge_c_per_kwh = _as_float(row.get("SRES Environmental Charge (c/kWh)")) or 0.0
    constraints.veec_charge_c_per_kwh = _as_float(
        row.get("VEEC Environmental Charge (Victoria Only) (c/kWh)")
    ) or 0.0
    constraints.network_charge_c_per_kwh = _as_float(row.get("Network Charge Rate (c/kWh)")) or 0.0
    gst_pct = _as_float(row.get("GST (%)"))
    constraints.gst_fraction = (gst_pct / 100.0) if gst_pct else 0.0
    constraints.other_charge_c_per_kwh = _as_float(row.get("Other (c/kWh)")) or 0.0
    constraints.export_tariff_c_per_kwh = _as_float(row.get("Feed-in / Export Tariff (c/kWh)")) or 0.0


# ----------------------------------------------------------------------------
# Small parsing helpers
# ----------------------------------------------------------------------------

def _or_default(value, default):
    """Like `value or default`, but a real 0 doesn't get replaced by `default`."""
    return value if value is not None else default


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _as_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(float(value))


def _as_text(value) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _parse_location(value) -> tuple:
    """"lat,lon" (the template's own format, e.g. "-34.28,146.04") -> (lat, lon)."""
    if value is None or str(value).strip() == "":
        return None, None
    text = str(value).strip().lstrip("'")
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Location must be 'latitude,longitude', got {value!r}")
    return float(parts[0]), float(parts[1])


def _parse_peak_hours(value) -> tuple:
    """"15, 21" -> (15, 21), a 24-hour start/end pair."""
    if value is None or str(value).strip() == "":
        return None, None
    parts = [p.strip() for p in str(value).split(",")]
    if len(parts) != 2:
        raise ValueError(f"Peak hours must be 'start,end' in 24h time, got {value!r}")
    return int(float(parts[0])), int(float(parts[1]))


# ----------------------------------------------------------------------------
# Building the real hourly import tariff from the Energy Bill fields
# ----------------------------------------------------------------------------
