import numpy as np
import pandas as pd

from config import horizon
from input_data_processing_v3 import prepare_model_inputs


# ============================================================
# BLACKOUT DATA MODULE
# ============================================================
# 1 = grid available
# 0 = blackout / grid unavailable
#
# Default:
#     Use a 6-hour blackout scenario based on historical piggery data analysis.
#
# Uploaded NEM data:
#     If a NEM file path is provided, this module calls
#     input_data_processing_v3.py to detect long zero-demand periods
#     and convert the selected event into a 24-hour grid availability array.
# ============================================================


# Options:
# "none"    -> no blackout, all grid available
# "default" -> use default 6-hour blackout if no uploaded NEM data is provided
BLACKOUT_MODE = "default"

DEFAULT_BLACKOUT_START_HOUR = 2
DEFAULT_BLACKOUT_DURATION_HOURS = 6

LONG_ZERO_MIN_DURATION_MINUTES = 60
BLACKOUT_EVENT_RULE = "longest"


# ============================================================
# BASIC HELPERS
# ============================================================

def create_grid_available_from_event(start_hour, duration_hours):
    """
    Create a 24-hour grid availability array.

    1 = grid available
    0 = blackout / grid unavailable

    If the blackout crosses midnight, it wraps around.
    """

    grid_available = np.ones(horizon, dtype=int)

    start_hour = int(start_hour)
    duration_hours = int(np.ceil(duration_hours))
    duration_hours = min(duration_hours, horizon)

    for i in range(duration_hours):
        hour_index = (start_hour + i) % horizon
        grid_available[hour_index] = 0

    return grid_available


def get_default_grid_available():
    """
    Return the default 6-hour blackout scenario.

    This default scenario is based on historical piggery demand analysis,
    where long zero-demand periods were treated as blackout candidate events.
    """

    return create_grid_available_from_event(
        start_hour=DEFAULT_BLACKOUT_START_HOUR,
        duration_hours=DEFAULT_BLACKOUT_DURATION_HOURS,
    )


# ============================================================
# UPLOADED NEM DATA MODE
# ============================================================

def get_long_zero_periods_from_uploaded_nem(
    nem_file_path,
    active_energy_registers=None,
):
    """
    Process uploaded NEM data and return long zero-demand periods.

    This calls input_data_processing_v3.prepare_model_inputs(),
    so this module does not duplicate NEM12 parsing logic.
    """

    outputs = prepare_model_inputs(
        file_path=nem_file_path,
        optimisation_freq="1h",
        active_energy_registers=active_energy_registers,
        long_zero_min_duration_minutes=LONG_ZERO_MIN_DURATION_MINUTES,
        return_long_zero_periods=True,
    )

    # outputs = df_raw, df_optimisation, validation, long_zero_periods
    _, _, _, long_zero_periods = outputs

    return long_zero_periods


def choose_blackout_candidate_event(long_zero_periods):
    """
    Choose one long zero-demand period to represent the blackout scenario.

    These are blackout candidate events, not confirmed blackouts.
    """

    if long_zero_periods is None or long_zero_periods.empty:
        return None

    if BLACKOUT_EVENT_RULE == "longest":
        return (
            long_zero_periods
            .sort_values("duration_minutes", ascending=False)
            .iloc[0]
        )

    raise ValueError(
        f"Unknown BLACKOUT_EVENT_RULE: {BLACKOUT_EVENT_RULE}"
    )


def get_grid_available_from_uploaded_nem(
    nem_file_path,
    active_energy_registers=None,
):
    """
    Analyse uploaded NEM data and create a 24-hour grid availability profile.

    If no long zero-demand period is found, return all 1s.
    """

    long_zero_periods = get_long_zero_periods_from_uploaded_nem(
        nem_file_path=nem_file_path,
        active_energy_registers=active_energy_registers,
    )

    event = choose_blackout_candidate_event(long_zero_periods)

    if event is None:
        return np.ones(horizon, dtype=int)

    start_time = pd.Timestamp(event["zero_start"])
    start_hour = start_time.hour
    duration_hours = float(event["duration_hours"])

    return create_grid_available_from_event(
        start_hour=start_hour,
        duration_hours=duration_hours,
    )


# ============================================================
# MAIN FUNCTION FOR INPUTS.PY / UI
# ============================================================

def get_grid_available(
    nem_file_path=None,
    active_energy_registers=None,
):
    """
    Return grid availability array for the optimisation model.

    If nem_file_path is provided:
        Analyse the uploaded NEM data and generate blackout scenario.

    If nem_file_path is not provided:
        Use the default 6-hour blackout scenario.

    Returns
    -------
    numpy.ndarray
        24-hour array where:
        1 = grid available
        0 = blackout / grid unavailable
    """

    if BLACKOUT_MODE == "none":
        return np.ones(horizon, dtype=int)

    if nem_file_path is not None:
        return get_grid_available_from_uploaded_nem(
            nem_file_path=nem_file_path,
            active_energy_registers=active_energy_registers,
        )

    if BLACKOUT_MODE == "default":
        return get_default_grid_available()

    raise ValueError(
        f"Unknown BLACKOUT_MODE: {BLACKOUT_MODE}. "
        "Use 'none' or 'default'."
    )


if __name__ == "__main__":
    print(get_grid_available())