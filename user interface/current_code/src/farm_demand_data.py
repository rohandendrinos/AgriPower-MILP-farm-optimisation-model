from pathlib import Path

import pandas as pd

from config import selected_date, horizon


# ============================================================
# DATA FILE
# ============================================================

REPO_ROOT = Path(__file__).resolve().parents[1]

DEMAND_FILE = (
    REPO_ROOT
    / "data"
    / "processed"
    / "real"
    / "piggery_data"
    / "demand_for_optimisation.csv"
)


# ============================================================
# LOAD FULL FARM DEMAND DATA
# ============================================================

def load_farm_demand():
    """
    Load full hourly piggery demand dataset.

    Expected columns:
        timestamp
        demand_kWh
        demand_kW_avg

    Unit:
        demand_kWh is kWh per hourly timestep.
    """

    df = pd.read_csv(
        DEMAND_FILE,
        parse_dates=["timestamp"]
    )

    df = (
        df.sort_values("timestamp")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# GET FARM DEMAND FOR ONE SELECTED DAY
# ============================================================

def get_farm_demand_for_day(date):
    """
    Return 24 hourly farm demand values for a selected date.

    Important:
        Farm demand uses the real selected date directly.
        It does NOT map the year to 2025.

    Example:
        selected date = 2024-08-07
        farm demand = real demand on 2024-08-07

    Unit:
        kWh per hourly timestep
    """

    df = load_farm_demand()

    selected_day = pd.Timestamp(date).date()

    day_data = df[
        df["timestamp"].dt.date == selected_day
    ].copy()

    day_data = (
        day_data
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    load_kwh = day_data["demand_kWh"].to_numpy()

    if len(load_kwh) != 24:
        raise ValueError(
            f"Expected 24 hourly demand values for {selected_day}, "
            f"but found {len(load_kwh)}. "
            f"Please check whether this date exists in the farm demand dataset."
        )

    return load_kwh


# ============================================================
# GET SELECTED FARM DEMAND FROM CONFIG
# ============================================================

def get_selected_farm_demand():
    """
    Return farm demand for the selected date defined in config.py.

    This is the function that inputs.py should call.
    """

    load_kwh = get_farm_demand_for_day(selected_date)

    if len(load_kwh) != horizon:
        raise ValueError(
            f"Expected load length equal to horizon={horizon}, "
            f"but got {len(load_kwh)}."
        )

    return load_kwh


# ============================================================
# OPTIONAL: GET SELECTED DAY DATAFRAME
# ============================================================

def get_selected_farm_demand_dataframe():
    """
    Return selected day's farm demand as a DataFrame.
    Useful for checking, plotting or debugging.
    """

    df = load_farm_demand()

    selected_day = pd.Timestamp(selected_date).date()

    day_data = df[
        df["timestamp"].dt.date == selected_day
    ].copy()

    day_data = (
        day_data
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if len(day_data) != 24:
        raise ValueError(
            f"Expected 24 hourly rows for {selected_day}, "
            f"but found {len(day_data)}."
        )

    return day_data