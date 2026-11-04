"""The downloadable report: two pages at most, only model outputs and user inputs.

Page 1: the dates solved, the recommended option (the model's own best
trade-off point, results.knee_point), the Group 1 accuracy caveat, one line on
the solver and price source, and the model's four-panel figure.
Page 2: the farm inputs, each marked as entered or default.

Every number here is read from the model's results table (results.summarise)
or from the farm inputs. Nothing is calculated, and nothing is explained.
"""

import datetime as _dt
import io

import pandas as pd
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from agripower.optimiser import leap_year_note
from agripower.constraints import (
    ACCURACY_CAVEAT,
    BILL_FIELD_NOTE,
    FIELD_DEFAULTS,
    FIELD_GROUPS,
    TEMPLATE_DEFAULTED_FIELDS,
    FarmConstraints,
    format_field_value,
    group1_fields_on_default,
)

PAGE_MARGIN_MM = 16
BODY_FONT_SIZE = 9.5

# The rows of the model's results table shown for the recommended option, in the
# model's own order and with its own labels (results.summarise).
_SIZING_ROWS = ["battery capacity (kWh)", "battery inverter (kW)", "pv capacity", "generator size (kW)"]
_MONEY_AND_EMISSIONS_ROWS = ["cost over modelled period", "emissions over modelled period"]
_FINANCIAL_ROWS = [
    "Farm bill with no assets (grid only)", "Farm bill over period",
    "Third-party profit over period", "Third-party break-even rate (c/kWh)",
    "Farm max acceptable rate (c/kWh)", "Farm saving vs grid only",
]

_LABELS_FOR_FIELDS_WITHOUT_DEFAULTS = {
    "lret_charge_c_per_kwh": "LRET charge (c/kWh)",
    "sres_charge_c_per_kwh": "SRES charge (c/kWh)",
    "veec_charge_c_per_kwh": "VEEC charge (c/kWh)",
    "network_charge_c_per_kwh": "Network charge (c/kWh)",
    "gst_fraction": "GST",
    "other_charge_c_per_kwh": "Other charges (c/kWh)",
    "export_tariff_c_per_kwh": "Export tariff (c/kWh)",
}


class _ReportPDF(FPDF):
    """multi_cell's own default (new_x=RIGHT) leaves the cursor pinned at the
    right margin, so a second consecutive multi_cell(w=0, ...) computes zero
    remaining width and raises. Every call here wants "print a paragraph, then
    drop to the next line", so that is the default."""

    def multi_cell(self, w, h=None, text="", *args, **kwargs):
        kwargs.setdefault("new_x", XPos.LMARGIN)
        kwargs.setdefault("new_y", YPos.NEXT)
        return super().multi_cell(w, h, text, *args, **kwargs)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"AgriPower - page {self.page_no()}", align="C")


def _number(value) -> str:
    return f"{value:,.1f}"


def _heading(pdf: FPDF, text: str) -> None:
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(20, 20, 20)
    pdf.ln(2)
    pdf.multi_cell(0, 6, text)
    pdf.ln(0.5)


def _line(pdf: FPDF, text: str, bold: bool = False) -> None:
    pdf.set_font("helvetica", "B" if bold else "", BODY_FONT_SIZE)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 4.8, text)


def _window_text(facts: dict) -> str:
    start, end = facts["start"], facts["end"]
    text = (f"{start:%d %B %Y, %H:%M}".lstrip("0") + " to " + f"{end:%d %B %Y, %H:%M}".lstrip("0")
            + f" ({facts['hours']:,} hours, taken from the hours actually solved). Year chosen: {facts['year']}.")
    note = leap_year_note(facts["year"], end)
    return f"{text} {note}" if note else text


def _recommended_rows(summary: pd.DataFrame, index: int) -> list:
    row = summary.iloc[index]
    rows = [(label, _number(row[label])) for label in _SIZING_ROWS + _MONEY_AND_EMISSIONS_ROWS]
    rows += [(label, _number(row[label])) for label in _FINANCIAL_ROWS if label in summary.columns]
    return rows


def _input_rows(constraints: FarmConstraints) -> list:
    """(field, value, entered or default, group) for every field the app collects.
    A field counts as default if it is blank (filled from the model's own value) or,
    for the three template fields, still equal to the model's value."""
    rows = []
    for name, group in FIELD_GROUPS.items():
        if name in ("cost_priority", "emissions_priority", "start_year"):
            continue
        value = getattr(constraints, name)
        if name in FIELD_DEFAULTS:
            label = FIELD_DEFAULTS[name][1]
            source = "Default" if name in constraints.defaulted_fields else "Entered"
        elif name in TEMPLATE_DEFAULTED_FIELDS:
            label, built_in_default = TEMPLATE_DEFAULTED_FIELDS[name]
            source = "Default" if value == built_in_default else "Entered"
        else:
            label = _LABELS_FOR_FIELDS_WITHOUT_DEFAULTS[name]
            source = "Default" if value == 0 else "Entered"
        rows.append((label, format_field_value(name, value), source, f"Group {group}"))
    return rows


def build_report(facts: dict, summary: pd.DataFrame, constraints: FarmConstraints, figure_png: bytes) -> bytes:
    """`facts` and `summary` are what optimiser.run_model returned; `constraints`
    is the resolved farm the run used."""
    best = int(facts["best_trade_off_index"])
    pdf = _ReportPDF(format="A4")
    pdf.set_margins(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM)
    pdf.set_auto_page_break(False)

    # ---- page 1
    pdf.add_page()
    pdf.set_font("helvetica", "B", 16)
    pdf.set_text_color(11, 95, 255)
    pdf.multi_cell(0, 8, f"AgriPower results: {facts['scenario_label']}")
    pdf.set_font("helvetica", "", 8.5)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 4.5, f"Generated {_dt.datetime.now():%d %B %Y, %H:%M}")

    _heading(pdf, "Dates solved")
    _line(pdf, _window_text(facts))

    _heading(pdf, f"Recommended option: best trade-off (option {best + 1} of {facts['options_returned']})")
    pdf.set_font("helvetica", "", BODY_FONT_SIZE)
    pdf.set_text_color(40, 40, 40)
    for label, value in _recommended_rows(summary, best):
        pdf.cell(105, 4.8, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(0, 4.8, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(105, 4.8, "Solver status", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(0, 4.8, "Optimal (within the solver's gap)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    on_default = group1_fields_on_default(constraints)
    if on_default:
        _heading(pdf, "Accuracy")
        pdf.set_font("helvetica", "B", BODY_FONT_SIZE)
        pdf.set_text_color(170, 90, 0)
        pdf.multi_cell(0, 4.8, ACCURACY_CAVEAT)
        pdf.set_font("helvetica", "", BODY_FONT_SIZE - 0.5)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 4.4, "; ".join(f"{label}: {value}" for label, value in on_default) + ".")

    pdf.ln(2)
    _line(pdf, f"Solver: {facts['solver']}. Prices: {facts['price_source']}.")

    pdf.ln(2)
    pdf.image(io.BytesIO(figure_png), w=pdf.epw)

    # ---- page 2
    pdf.add_page()
    _heading(pdf, "Farm inputs")
    widths = (78, 40, 24, 22)
    pdf.set_font("helvetica", "B", BODY_FONT_SIZE - 0.5)
    for title, width in zip(("Field", "Value", "Entered or default", "Group"), widths):
        pdf.cell(width, 5, title, border="B", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(5.5)
    pdf.set_font("helvetica", "", BODY_FONT_SIZE - 0.5)
    for label, value, source, group in _input_rows(constraints):
        for text, width in zip((label, value, source, group), widths):
            pdf.cell(width, 4.8, text, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(4.8)
    pdf.ln(2)
    _line(pdf, f"Group 2 fields: {BILL_FIELD_NOTE}")

    return bytes(pdf.output())
