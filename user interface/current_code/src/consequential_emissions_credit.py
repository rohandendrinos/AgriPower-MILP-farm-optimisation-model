import numpy as np

from src.grid_emission_data import (
    get_grid_ef_for_day,
    get_annual_grid_ef,
)


# ============================================================
# CONSEQUENTIAL EMISSIONS CREDIT MODEL
# ============================================================
#
# Core equation:
#
# CEC_t = GridExport_t × GridEF_t
#
# Units:
#
# GridExport_t : kWh
# GridEF_t     : kgCO2e / kWh
# CEC_t        : kgCO2e
#
# Total credit:
#
# CEC_total = sum(CEC_t)
#
# In teammate optimisation model:
#
# GridExport_t = x_SN_G[t]
#
# IMPORTANT:
# This credit must replace the existing export-emissions-credit
# term in the final emissions model.
# It must NOT be subtracted twice.
#
# ============================================================


def calculate_export_credit(
    grid_export_kwh,
    grid_ef_kgCO2e_per_kWh
):
    """
    Calculate consequential emissions credit from electricity
    exported to the grid.

    Parameters
    ----------
    grid_export_kwh : array-like
        Grid export electricity at each timestep.
        Unit: kWh

    grid_ef_kgCO2e_per_kWh : array-like
        Time-dependent NSW grid emission factor.
        Unit: kgCO2e/kWh

    Returns
    -------
    hourly_credit : numpy.ndarray
        Consequential emissions credit for each timestep.
        Unit: kgCO2e

    total_credit : float
        Total consequential emissions credit.
        Unit: kgCO2e
    """

    # Convert inputs to NumPy arrays
    grid_export_kwh = np.asarray(
        grid_export_kwh,
        dtype=float
    )

    grid_ef = np.asarray(
        grid_ef_kgCO2e_per_kWh,
        dtype=float
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    # Both arrays must have the same length
    if len(grid_export_kwh) != len(grid_ef):
        raise ValueError(
            "Grid export and grid emission factor must have "
            "the same number of timesteps."
        )

    # Export cannot be negative
    if np.any(grid_export_kwh < 0):
        raise ValueError(
            "Grid export contains negative values."
        )

    # Emission factor cannot be negative
    if np.any(grid_ef < 0):
        raise ValueError(
            "Grid emission factor contains negative values."
        )

    # Check missing / infinite export values
    if not np.all(np.isfinite(grid_export_kwh)):
        raise ValueError(
            "Grid export contains missing or invalid values."
        )

    # Check missing / infinite EF values
    if not np.all(np.isfinite(grid_ef)):
        raise ValueError(
            "Grid emission factor contains missing or invalid values."
        )

    # ========================================================
    # CALCULATE HOURLY CREDIT
    # ========================================================

    hourly_credit = (
        grid_export_kwh
        * grid_ef
    )

    # ========================================================
    # CALCULATE TOTAL CREDIT
    # ========================================================

    total_credit = float(
        np.sum(hourly_credit)
    )

    return hourly_credit, total_credit


# ============================================================
# 24-HOUR VERSION
# ============================================================

def calculate_daily_export_credit(
    date,
    grid_export_kwh
):
    """
    Calculate consequential emissions credit for one selected
    24-hour day.

    The function automatically loads the corresponding
    OpenElectricity hourly grid emission factors.

    Parameters
    ----------
    date : str
        Selected date.
        Example:
        "2025-01-15"

    grid_export_kwh : array-like
        24 hourly grid export values.
        Unit: kWh

    Returns
    -------
    hourly_credit : numpy.ndarray
        24 hourly consequential emissions credits.
        Unit: kgCO2e

    total_credit : float
        Total daily credit.
        Unit: kgCO2e
    """

    grid_export_kwh = np.asarray(
        grid_export_kwh,
        dtype=float
    )

    if len(grid_export_kwh) != 24:
        raise ValueError(
            f"Expected 24 hourly export values, "
            f"but received {len(grid_export_kwh)}."
        )

    # Load the 24 OpenElectricity emission factors
    # corresponding to the selected date
    grid_ef = get_grid_ef_for_day(date)

    return calculate_export_credit(
        grid_export_kwh,
        grid_ef
    )


# ============================================================
# FULL-YEAR VERSION
# ============================================================

def calculate_annual_export_credit(
    grid_export_kwh
):
    """
    Calculate consequential emissions credit for a full
    8760-hour year.

    Parameters
    ----------
    grid_export_kwh : array-like
        8760 hourly grid export values.
        Unit: kWh

    Returns
    -------
    hourly_credit : numpy.ndarray
        Hourly consequential emissions credits.
        Unit: kgCO2e

    total_credit : float
        Annual consequential emissions credit.
        Unit: kgCO2e
    """

    grid_export_kwh = np.asarray(
        grid_export_kwh,
        dtype=float
    )

    if len(grid_export_kwh) != 8760:
        raise ValueError(
            f"Expected 8760 hourly export values, "
            f"but received {len(grid_export_kwh)}."
        )

    grid_ef = get_annual_grid_ef()

    return calculate_export_credit(
        grid_export_kwh,
        grid_ef
    )