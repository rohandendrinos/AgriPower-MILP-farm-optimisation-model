from pathlib import Path

import pandas as pd

from config import selected_date, horizon


# ============================================================
# SETTINGS
# ============================================================

GRID_EF_FILE = Path(
    "data/processed/grid_ef_NSW1_2025_hourly.csv"
)

REFERENCE_YEAR = 2025


# ============================================================
# LOAD FULL 2025 GRID EF DATA
# ============================================================

def load_grid_ef():
    """
    Load the full 2025 NSW1 hourly grid emission factor dataset.

    Returns
    -------
    pandas.DataFrame

    Columns:
        timestamp
        grid_ef_kgCO2e_per_kWh

    Resolution:
        1 hour

    Emission factor unit:
        kgCO2e/kWh
    """

    df = pd.read_csv(
        GRID_EF_FILE,
        parse_dates=["timestamp"]
    )

    df = (
        df.sort_values("timestamp")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# MAP FARM DATE TO 2025 REFERENCE YEAR
# ============================================================

def map_to_reference_year(date):
    """
    Map any farm date to the same month and day
    in the 2025 reference emissions year.

    Examples
    --------
    2024-08-07 -> 2025-08-07
    2025-08-07 -> 2025-08-07
    2026-08-07 -> 2025-08-07

    Leap-year rule:
    2024-02-29 -> 2025-02-28

    The farm-data year is ignored.
    Month and day are preserved where possible.
    """

    farm_date = pd.Timestamp(date)

    month = farm_date.month
    day = farm_date.day

    # 2025 is not a leap year.
    # Map February 29 to February 28.
    if month == 2 and day == 29:
        day = 28

    reference_date = pd.Timestamp(
        year=REFERENCE_YEAR,
        month=month,
        day=day
    )

    return reference_date


# ============================================================
# GET GRID EF FOR ONE SELECTED FARM DAY
# ============================================================

def get_grid_ef_for_day(date):
    """
    Return 24 hourly grid emission factors
    for one selected farm date.

    The farm-data year is ignored.

    Month and day are mapped to the corresponding
    date in the 2025 OpenElectricity reference dataset.

    Examples
    --------
    Farm date:
        2024-08-07

    Grid EF used:
        2025-08-07 00:00
        ...
        2025-08-07 23:00

    Leap-year example:
        2024-02-29
        ->
        2025-02-28

    Parameters
    ----------
    date : str or datetime-like
        Examples:
        "2024-08-07"
        "2025-08-07"
        "2026-08-07"

    Returns
    -------
    numpy.ndarray
        24 hourly grid emission factors.

    Unit:
        kgCO2e/kWh
    """

    df = load_grid_ef()

    reference_date = map_to_reference_year(date)

    day_data = df[
        df["timestamp"].dt.date
        == reference_date.date()
    ].copy()

    day_data = (
        day_data
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if len(day_data) != 24:
        raise ValueError(
            f"Expected 24 hourly EF values for "
            f"{reference_date.date()}, "
            f"but found {len(day_data)}."
        )

    grid_ef = (
        day_data[
            "grid_ef_kgCO2e_per_kWh"
        ]
        .to_numpy()
    )

    return grid_ef


# ============================================================
# GET GRID EF FOR ONE FARM TIMESTAMP
# ============================================================

def get_grid_ef_for_timestamp(timestamp):
    """
    Return one hourly grid emission factor
    for a selected farm timestamp.

    The farm-data year is ignored.

    Month, day and hour are mapped to 2025.

    Examples
    --------
    2024-08-07 13:00
        ->
    2025-08-07 13:00

    2026-12-20 18:00
        ->
    2025-12-20 18:00

    Leap-year example:
    2024-02-29 13:00
        ->
    2025-02-28 13:00

    Parameters
    ----------
    timestamp : str or datetime-like

    Returns
    -------
    float
        Grid emission factor.

    Unit:
        kgCO2e/kWh
    """

    df = load_grid_ef()

    farm_timestamp = pd.Timestamp(timestamp)

    month = farm_timestamp.month
    day = farm_timestamp.day
    hour = farm_timestamp.hour

    # Leap-year handling
    if month == 2 and day == 29:
        day = 28

    reference_timestamp = pd.Timestamp(
        year=REFERENCE_YEAR,
        month=month,
        day=day,
        hour=hour
    )

    match = df[
        df["timestamp"]
        == reference_timestamp
    ]

    if len(match) != 1:
        raise ValueError(
            f"Expected one grid EF value for "
            f"{reference_timestamp}, "
            f"but found {len(match)}."
        )

    grid_ef = float(
        match[
            "grid_ef_kgCO2e_per_kWh"
        ].iloc[0]
    )

    return grid_ef


# ============================================================
# GET FULL 2025 YEAR AS NUMPY ARRAY
# ============================================================

def get_annual_grid_ef():
    """
    Return all 8760 hourly grid emission factors
    from the 2025 reference year.

    Returns
    -------
    numpy.ndarray
        8760 hourly grid emission factors.

    Unit:
        kgCO2e/kWh
    """

    df = load_grid_ef()

    grid_ef = (
        df[
            "grid_ef_kgCO2e_per_kWh"
        ]
        .to_numpy()
    )

    if len(grid_ef) != 8760:
        raise ValueError(
            f"Expected 8760 grid EF values, "
            f"found {len(grid_ef)}."
        )

    return grid_ef

# ============================================================
# GET SELECTED GRID EF FROM CONFIG
# ============================================================

def get_selected_grid_ef():
    """
    Return 24 hourly grid emission factors
    for the selected date defined in config.py.

    Important:
        The farm selected date is mapped to the same month and day
        in the 2025 grid emissions reference dataset.

    Example:
        selected_date = 2024-08-07
        grid EF used = 2025-08-07

        selected_date = 2025-08-07
        grid EF used = 2025-08-07

        selected_date = 2026-08-07
        grid EF used = 2025-08-07

    Unit:
        kgCO2e/kWh
    """

    grid_ef = get_grid_ef_for_day(selected_date)

    if len(grid_ef) != horizon:
        raise ValueError(
            f"Expected grid EF length equal to horizon={horizon}, "
            f"but got {len(grid_ef)}."
        )

    return grid_ef